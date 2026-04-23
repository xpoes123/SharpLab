"""Casino cog — multiplayer /figgie party game (Jane Street trading card game).

40 cards, 4 suits, uneven distribution (12/10/8/8).  One suit is the
"goal suit" worth 10 pts per card at game end.  Players trade cards via
an open order book, trying to deduce and accumulate the goal suit.
"""

import asyncio
import random
import time
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
from bot.cogs._pool import compute_side_pot_payouts

# ── Constants ────────────────────────────────────────────────────────────────

MIN_PLAYERS = 3
MAX_PLAYERS = 5
NUM_ROUNDS = 6
ROUND_SECONDS = 45
STARTING_CHIPS = 200
GOAL_VALUE = 10  # points per goal-suit card at game end

SUITS = ["\u2660", "\u2665", "\u2666", "\u2663"]
SUIT_COLORS = {"\u2660": "black", "\u2663": "black", "\u2665": "red", "\u2666": "red"}
SUIT_NAMES = {"\u2660": "Spades", "\u2663": "Clubs", "\u2665": "Hearts", "\u2666": "Diamonds"}

SUIT_ALIASES: dict[str, str] = {
    "s": "\u2660", "spades": "\u2660", "\u2660": "\u2660",
    "h": "\u2665", "hearts": "\u2665", "\u2665": "\u2665",
    "d": "\u2666", "diamonds": "\u2666", "\u2666": "\u2666",
    "c": "\u2663", "clubs": "\u2663", "\u2663": "\u2663",
}


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class Order:
    player_id: int
    player_name: str
    action: str  # "buy" | "sell"
    suit: str
    price: int


@dataclass
class Trade:
    buyer_name: str
    seller_name: str
    suit: str
    price: int


@dataclass
class FiggiePlayer:
    user_id: int
    display_name: str
    wager: int
    hand: dict[str, int] = field(default_factory=dict)
    chips: int = STARTING_CHIPS
    payout: int = 0


@dataclass
class FiggieTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"
    players: dict[int, FiggiePlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    goal_suit: str = ""
    common_suit: str = ""
    suit_counts: dict[str, int] = field(default_factory=dict)
    round_num: int = 0
    orders: list[Order] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    game_task: asyncio.Task | None = field(default=None, repr=False)
    winners: list[int] = field(default_factory=list)
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    game_num: int = 1
    trade_open: bool = False
    round_end: float = 0.0  # time.monotonic() when current round closes
    hidden_cards: int = 0


# ── Deck & Dealing ───────────────────────────────────────────────────────────


def _setup_deck() -> tuple[str, str, dict[str, int]]:
    """Create a Figgie deck.

    Returns ``(goal_suit, common_suit, suit_counts)``.
    The common suit has 12 cards.  The goal suit is its same-colour
    partner and has 10 cards.  The other two suits have 8 each.
    """
    common = random.choice(SUITS)
    goal = [s for s in SUITS if SUIT_COLORS[s] == SUIT_COLORS[common] and s != common][0]
    others = [s for s in SUITS if s not in (common, goal)]
    return goal, common, {common: 12, goal: 10, others[0]: 8, others[1]: 8}


def _deal_cards(
    suit_counts: dict[str, int], player_ids: list[int],
) -> tuple[dict[int, dict[str, int]], int]:
    """Deal cards to players.

    Returns ``(hands, hidden)`` where *hidden* is how many cards
    were left over (not dealt).
    """
    deck: list[str] = []
    for suit, count in suit_counts.items():
        deck.extend([suit] * count)
    random.shuffle(deck)

    n = len(player_ids)
    per_player = len(deck) // n
    hidden = len(deck) - per_player * n

    hands: dict[int, dict[str, int]] = {}
    for i, pid in enumerate(player_ids):
        dealt = deck[i * per_player : (i + 1) * per_player]
        hand: dict[str, int] = {s: 0 for s in SUITS}
        for c in dealt:
            hand[c] += 1
        hands[pid] = hand
    return hands, hidden


# ── Order Matching ───────────────────────────────────────────────────────────


def _try_match(
    new: Order, book: list[Order], players: dict[int, FiggiePlayer],
) -> Trade | None:
    """Try to match *new* against the resting *book*.

    If matched, the resting order is removed and the trade is returned.
    Trade executes at the **resting** order's price.
    """
    if new.action == "buy":
        candidates = sorted(
            [o for o in book if o.action == "sell" and o.suit == new.suit and o.player_id != new.player_id],
            key=lambda o: o.price,
        )
        for resting in candidates:
            if new.price < resting.price:
                break  # no match possible (sorted ascending)
            seller = players.get(resting.player_id)
            buyer = players.get(new.player_id)
            if not seller or not buyer:
                book.remove(resting)
                continue
            if seller.hand.get(new.suit, 0) < 1:
                book.remove(resting)
                continue
            if buyer.chips < resting.price:
                return None
            # Execute at resting (sell) price
            buyer.chips -= resting.price
            seller.chips += resting.price
            buyer.hand[new.suit] = buyer.hand.get(new.suit, 0) + 1
            seller.hand[new.suit] -= 1
            book.remove(resting)
            return Trade(buyer.display_name, seller.display_name, new.suit, resting.price)
    else:
        candidates = sorted(
            [o for o in book if o.action == "buy" and o.suit == new.suit and o.player_id != new.player_id],
            key=lambda o: o.price,
            reverse=True,
        )
        for resting in candidates:
            if new.price > resting.price:
                break
            buyer = players.get(resting.player_id)
            seller = players.get(new.player_id)
            if not buyer or not seller:
                book.remove(resting)
                continue
            if seller.hand.get(new.suit, 0) < 1:
                return None
            if buyer.chips < resting.price:
                book.remove(resting)
                continue
            buyer.chips -= resting.price
            seller.chips += resting.price
            buyer.hand[new.suit] = buyer.hand.get(new.suit, 0) + 1
            seller.hand[new.suit] -= 1
            book.remove(resting)
            return Trade(buyer.display_name, seller.display_name, new.suit, resting.price)
    return None


def _clean_stale_orders(table: FiggieTable) -> None:
    """Remove orders that can no longer be fulfilled."""
    table.orders = [
        o for o in table.orders
        if (
            (o.action == "buy" and table.players.get(o.player_id, FiggiePlayer(0, "", 0)).chips >= o.price)
            or (o.action == "sell" and table.players.get(o.player_id, FiggiePlayer(0, "", 0)).hand.get(o.suit, 0) >= 1)
        )
        and o.player_id in table.players
    ]


# ── Embeds ───────────────────────────────────────────────────────────────────


def _hand_str(hand: dict[str, int]) -> str:
    return "  ".join(f"{s}{hand.get(s, 0)}" for s in SUITS)


def _betting_embed(table: FiggieTable) -> discord.Embed:
    pot = sum(p.wager for p in table.players.values())
    embed = discord.Embed(
        title=f"\U0001f0cf Figgie \u2014 Join the Table (Game {table.game_num})",
        description=(
            "**Jane Street\u2019s trading card game!**\n"
            "40 cards across 4 suits (uneven distribution). "
            "One suit is the **goal suit** \u2014 worth **10 pts/card** at game end.\n"
            "Trade cards with other players.  Deduce which suit is valuable.  "
            "Accumulate it.  Highest score wins the pot!\n\n"
            f"*{MIN_PLAYERS}\u2013{MAX_PLAYERS} players*"
        ),
        colour=discord.Colour.dark_teal(),
    )
    if pot:
        embed.add_field(name="Pot", value=f"{pot}c", inline=True)
    if table.players:
        lines = [f"\U0001f4b5 **{p.display_name}** \u2014 {p.wager}c" for p in table.players.values()]
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Players", value="*No players yet \u2014 click Join!*", inline=False)
    embed.set_footer(text=f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} players")
    return embed


def _trading_embed(table: FiggieTable) -> discord.Embed:
    remaining = max(0, int(table.round_end - time.monotonic())) if table.trade_open else 0
    embed = discord.Embed(
        title=f"\U0001f0cf Figgie \u2014 Round {table.round_num}/{NUM_ROUNDS}",
        colour=discord.Colour.teal() if table.trade_open else discord.Colour.dark_grey(),
    )

    # Players: total cards + chips (per-suit breakdown is private)
    plines: list[str] = []
    for p in table.players.values():
        total_cards = sum(p.hand.values())
        plines.append(f"**{p.display_name}** \u2014 {total_cards} cards \u2014 {p.chips} chips")
    embed.add_field(name="\U0001f464 Players", value="\n".join(plines), inline=False)

    # Order book (last 10)
    if table.orders:
        blines: list[str] = []
        for o in table.orders[-10:]:
            tag = "\U0001f7e2" if o.action == "buy" else "\U0001f534"
            blines.append(f"{tag} {o.action.upper()} {o.suit} @ {o.price} \u2014 {o.player_name}")
        embed.add_field(name="\U0001f4cb Order Book", value="\n".join(blines), inline=False)
    else:
        embed.add_field(name="\U0001f4cb Order Book", value="*Empty \u2014 post buy/sell orders!*", inline=False)

    # Recent trades (last 5)
    if table.trades:
        tlines: list[str] = []
        for t in table.trades[-5:]:
            tlines.append(f"{t.buyer_name} \u2190 {t.suit} @ {t.price} \u2190 {t.seller_name}")
        embed.add_field(name="\U0001f4dd Trades", value="\n".join(tlines), inline=False)

    # Timer / status
    if table.trade_open and remaining > 0:
        embed.add_field(name="\u23f1\ufe0f Trading", value=f"**{remaining}s** remaining", inline=False)
    else:
        embed.add_field(name="\u23f1\ufe0f Trading", value="**CLOSED** \u2014 next round\u2026", inline=False)

    hidden = table.hidden_cards
    footer = f"Host: {table.host_name} \u2502 Click \U0001f441 Hand to see your cards"
    if hidden:
        footer += f" \u2502 {hidden} card{'s' if hidden != 1 else ''} hidden"
    embed.set_footer(text=footer)
    return embed


def _final_embed(table: FiggieTable, *, balances: dict[int, int] | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"\U0001f0cf Figgie \u2014 Results (Game {table.game_num})",
        colour=discord.Colour.gold(),
    )
    embed.description = (
        f"## \U0001f3af Goal Suit: {table.goal_suit} {SUIT_NAMES[table.goal_suit]}\n"
        f"*({table.common_suit} {SUIT_NAMES[table.common_suit]} had 12 cards \u2014 "
        f"same-colour partner is the goal)*\n"
        f"Each {table.goal_suit} card = **{GOAL_VALUE} pts**"
    )

    scored: list[tuple[int, FiggiePlayer, int]] = []
    for uid, p in table.players.items():
        gc = p.hand.get(table.goal_suit, 0)
        scored.append((p.chips + gc * GOAL_VALUE, p, gc))
    scored.sort(key=lambda x: x[0], reverse=True)

    rlines: list[str] = []
    for i, (score, p, gc) in enumerate(scored):
        net = p.payout - p.wager
        sign = "+" if net >= 0 else ""
        bal = (balances or {}).get(p.user_id, 0)
        hand = _hand_str(p.hand)

        if p.user_id in table.winners:
            medal = "\U0001f3c6" if i == 0 else "\U0001f91d"
        elif p.payout > 0:
            medal = "\U0001f4b0"
        else:
            medal = "\u274c"

        rlines.append(
            f"{medal} **{p.display_name}** \u2014 **{score} pts** "
            f"({p.chips} chips + {gc}\u00d7{table.goal_suit}={gc * GOAL_VALUE})\n"
            f"    Hand: `{hand}` \u2502 "
            f"{p.wager}c \u2192 {p.payout}c ({sign}{net}c) \u2014 bal: {bal}c"
        )

    embed.add_field(name="Scores", value="\n".join(rlines), inline=False)

    dist = "  ".join(f"{s}{table.suit_counts.get(s, 0)}" for s in SUITS)
    embed.add_field(name="\U0001f4ca Deck Distribution", value=f"`{dist}`", inline=False)

    if table.trades:
        embed.add_field(
            name="\U0001f4dd Trade Log",
            value=f"{len(table.trades)} trades completed this game",
            inline=False,
        )

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinFiggieModal(ui.Modal):
    amount = ui.TextInput(label="Buy-in amount (coins)", placeholder="e.g. 100", required=True, max_length=10)

    def __init__(self, table: FiggieTable, view: "FiggieTableView", balance: int) -> None:
        super().__init__(title="Join Figgie")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 100 (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if amt < 1:
            await interaction.response.send_message("Must be at least 1 coin.", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in this game!", ephemeral=True)
            return
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(f"Not enough coins! (have {bal}c)", ephemeral=True)
            return
        self.table.players[uid] = FiggiePlayer(user_id=uid, display_name=interaction.user.display_name, wager=amt)
        self.table_view._update_buttons()
        await interaction.response.edit_message(embed=_betting_embed(self.table), view=self.table_view)


class OrderModal(ui.Modal):
    suit_input = ui.TextInput(
        label="Suit (S, H, D, or C)",
        placeholder="S = \u2660  H = \u2665  D = \u2666  C = \u2663",
        required=True,
        max_length=10,
    )
    price_input = ui.TextInput(label="Price (chips)", placeholder="e.g. 8", required=True, max_length=5)

    def __init__(self, table: FiggieTable, view: "FiggieTableView", action: str) -> None:
        super().__init__(title="Buy Cards" if action == "buy" else "Sell Cards")
        self.table = table
        self.table_view = view
        self.action = action

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not self.table.trade_open:
            await interaction.response.send_message("Trading is closed!", ephemeral=True)
            return
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message("You're not in this game!", ephemeral=True)
            return

        suit = SUIT_ALIASES.get(self.suit_input.value.strip().lower())
        if suit is None:
            await interaction.response.send_message(
                f"Invalid suit `{self.suit_input.value}`. Use S, H, D, or C.", ephemeral=True,
            )
            return
        try:
            price = int(self.price_input.value)
        except ValueError:
            await interaction.response.send_message("Price must be a whole number.", ephemeral=True)
            return
        if price < 1:
            await interaction.response.send_message("Price must be at least 1.", ephemeral=True)
            return

        if self.action == "buy" and player.chips < price:
            await interaction.response.send_message(f"Not enough chips! You have {player.chips}.", ephemeral=True)
            return
        if self.action == "sell" and player.hand.get(suit, 0) < 1:
            await interaction.response.send_message(f"You have no {suit} cards to sell!", ephemeral=True)
            return

        order = Order(uid, player.display_name, self.action, suit, price)
        trade = _try_match(order, self.table.orders, self.table.players)

        if trade:
            self.table.trades.append(trade)
            _clean_stale_orders(self.table)
            if self.table.message:
                try:
                    await self.table.message.edit(embed=_trading_embed(self.table), view=self.table_view)
                except discord.HTTPException:
                    pass
            await interaction.response.send_message(
                f"**Trade!** {trade.buyer_name} bought {trade.suit} from {trade.seller_name} @ {trade.price}",
                ephemeral=True,
            )
        else:
            self.table.orders.append(order)
            if self.table.message:
                try:
                    await self.table.message.edit(embed=_trading_embed(self.table), view=self.table_view)
                except discord.HTTPException:
                    pass
            await interaction.response.send_message(
                f"Order posted: {self.action.upper()} {suit} @ {price}", ephemeral=True,
            )


# ── View ─────────────────────────────────────────────────────────────────────


class FiggieTableView(ui.View):
    def __init__(self, table: FiggieTable, active_tables: dict[int, FiggieTable]) -> None:
        super().__init__(timeout=600)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        playing = phase == "playing"
        finished = phase == "finished"
        trading = playing and self.table.trade_open

        self.start_btn.disabled = not betting or len(self.table.players) < MIN_PLAYERS
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = playing

        self.buy_btn.disabled = not trading
        self.sell_btn.disabled = not trading
        self.hand_btn.disabled = not playing

        self.new_game_btn.disabled = not finished
        self.close_btn.disabled = playing

    # ── Row 0 ────────────────────────────────────────────────────────────────

    @ui.button(label="Start", style=discord.ButtonStyle.success, emoji="\u25b6\ufe0f", row=0)
    async def start_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message("Only the host can start!", ephemeral=True)
            return
        if self.table.phase != "betting":
            await interaction.response.send_message("Already started!", ephemeral=True)
            return
        if len(self.table.players) < MIN_PLAYERS:
            await interaction.response.send_message(
                f"Need at least {MIN_PLAYERS} players!", ephemeral=True,
            )
            return
        await self._start_game(interaction)

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f4b5", row=0)
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Game in progress! Wait for the next one.", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        bal = await queries.get_or_create_casino_wallet(str(uid))
        if bal < 1:
            await interaction.response.send_message("You have no coins!", ephemeral=True)
            return
        await interaction.response.send_modal(JoinFiggieModal(self.table, self, bal))

    @ui.button(label="Re-bet", style=discord.ButtonStyle.primary, emoji="\U0001f504", row=0)
    async def rebet_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Game in progress!", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return
        last = self.table.last_bets.get(uid)
        if last is None:
            await interaction.response.send_message("No previous bet \u2014 use Join instead.", ephemeral=True)
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        name, amt = last
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for {amt}c re-bet! (have {bal}c)", ephemeral=True,
            )
            return
        self.table.players[uid] = FiggiePlayer(user_id=uid, display_name=name, wager=amt)
        self._update_buttons()
        await interaction.response.edit_message(embed=_betting_embed(self.table), view=self)

    @ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=0)
    async def leave_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message("You're not at this table.", ephemeral=True)
            return
        if self.table.phase == "playing":
            await interaction.response.send_message("Can't leave mid-game!", ephemeral=True)
            return
        if self.table.phase == "betting":
            await queries.update_casino_balance(str(uid), player.wager)
            del self.table.players[uid]
            self._update_buttons()
            await interaction.response.edit_message(embed=_betting_embed(self.table), view=self)
            return
        await interaction.response.send_message("Game is over. Wait for New Game or close.", ephemeral=True)

    # ── Row 1 ────────────────────────────────────────────────────────────────

    @ui.button(label="Buy", style=discord.ButtonStyle.success, emoji="\U0001f7e2", row=1)
    async def buy_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not self.table.trade_open:
            await interaction.response.send_message("Trading is closed!", ephemeral=True)
            return
        if interaction.user.id not in self.table.players:
            await interaction.response.send_message("You're not in this game!", ephemeral=True)
            return
        await interaction.response.send_modal(OrderModal(self.table, self, "buy"))

    @ui.button(label="Sell", style=discord.ButtonStyle.danger, emoji="\U0001f534", row=1)
    async def sell_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not self.table.trade_open:
            await interaction.response.send_message("Trading is closed!", ephemeral=True)
            return
        if interaction.user.id not in self.table.players:
            await interaction.response.send_message("You're not in this game!", ephemeral=True)
            return
        await interaction.response.send_modal(OrderModal(self.table, self, "sell"))

    @ui.button(label="Hand", style=discord.ButtonStyle.secondary, emoji="\U0001f441", row=1)
    async def hand_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message("You're not in this game!", ephemeral=True)
            return
        total = sum(player.hand.values())
        hand = _hand_str(player.hand)
        # Show open orders too
        my_orders = [o for o in self.table.orders if o.player_id == uid]
        parts = [
            f"**Your Hand** ({total} cards):",
            f"`{hand}`",
            f"**Chips:** {player.chips}",
        ]
        if my_orders:
            parts.append("\n**Your Open Orders:**")
            for o in my_orders:
                tag = "\U0001f7e2 BUY" if o.action == "buy" else "\U0001f534 SELL"
                parts.append(f"  {tag} {o.suit} @ {o.price}")
        await interaction.response.send_message("\n".join(parts), ephemeral=True)

    # ── Row 2 ────────────────────────────────────────────────────────────────

    @ui.button(label="New Game", style=discord.ButtonStyle.success, emoji="\u25b6\ufe0f", row=2)
    async def new_game_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message("Only the host can start a new game!", ephemeral=True)
            return
        if self.table.phase != "finished":
            await interaction.response.send_message("Game still in progress!", ephemeral=True)
            return
        self._reset_table()
        self._update_buttons()
        await interaction.response.edit_message(embed=_betting_embed(self.table), view=self)

    @ui.button(label="Close Table", style=discord.ButtonStyle.danger, emoji="\u2716\ufe0f", row=2)
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message("Only the host can close the table!", ephemeral=True)
            return
        if self.table.phase == "playing":
            await interaction.response.send_message("Can't close mid-game!", ephemeral=True)
            return
        if self.table.phase == "betting":
            for p in self.table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.wager)
                except Exception:
                    pass
        await self._close(interaction, "Table closed by host.")

    # ── Game Logic ───────────────────────────────────────────────────────────

    async def _start_game(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "playing"

        table.goal_suit, table.common_suit, table.suit_counts = _setup_deck()
        player_ids = list(table.players.keys())
        hands, table.hidden_cards = _deal_cards(table.suit_counts, player_ids)
        for uid, hand in hands.items():
            table.players[uid].hand = hand
            table.players[uid].chips = STARTING_CHIPS

        table.round_num = 0
        table.orders.clear()
        table.trades.clear()
        table.trade_open = False

        self._update_buttons()
        await interaction.response.edit_message(embed=_trading_embed(table), view=self)
        table.game_task = asyncio.create_task(self._game_loop())

    async def _game_loop(self) -> None:
        table = self.table
        try:
            await asyncio.sleep(1)
            for round_idx in range(NUM_ROUNDS):
                table.round_num = round_idx + 1
                await self._run_round()
            await self._finalize()
        except asyncio.CancelledError:
            pass
        except Exception:
            if table.phase == "playing":
                table.phase = "finished"
                await self._refund_all()

    async def _run_round(self) -> None:
        table = self.table

        # Open trading
        table.orders.clear()
        table.trade_open = True
        table.round_end = time.monotonic() + ROUND_SECONDS
        self._update_buttons()

        if table.message:
            try:
                await table.message.edit(embed=_trading_embed(table), view=self)
            except discord.HTTPException:
                pass

        # Countdown — update every 10s
        elapsed = 0
        while elapsed < ROUND_SECONDS:
            step = min(10, ROUND_SECONDS - elapsed)
            await asyncio.sleep(step)
            elapsed += step
            if table.message and (ROUND_SECONDS - elapsed) > 0:
                try:
                    await table.message.edit(embed=_trading_embed(table), view=self)
                except discord.HTTPException:
                    pass

        # Close trading
        table.trade_open = False
        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(embed=_trading_embed(table), view=self)
            except discord.HTTPException:
                pass

        if table.round_num < NUM_ROUNDS:
            await asyncio.sleep(3)

    async def _finalize(self) -> None:
        table = self.table
        table.phase = "finished"
        table.trade_open = False

        scores: dict[int, int] = {}
        for uid, p in table.players.items():
            gc = p.hand.get(table.goal_suit, 0)
            scores[uid] = p.chips + gc * GOAL_VALUE

        if not scores:
            await self._refund_all()
            return

        max_score = max(scores.values())
        table.winners = [uid for uid, s in scores.items() if s == max_score]

        bets = {uid: p.wager for uid, p in table.players.items()}
        payouts = compute_side_pot_payouts(bets, table.winners)
        for uid, p in table.players.items():
            p.payout = payouts.get(uid, 0)

        balances: dict[int, int] = {}
        for uid, p in table.players.items():
            if p.payout > 0:
                balances[uid] = await queries.update_casino_balance(str(uid), p.payout)
            else:
                bal = await queries.get_casino_balance(str(uid))
                balances[uid] = bal or 0
            await queries.log_casino_result(str(uid), "figgie", p.wager, p.payout)

        for uid, p in table.players.items():
            table.last_bets[uid] = (p.display_name, p.wager)

        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(embed=_final_embed(table, balances=balances), view=self)
            except discord.HTTPException:
                pass

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _reset_table(self) -> None:
        table = self.table
        table.players.clear()
        table.phase = "betting"
        table.game_num += 1
        table.round_num = 0
        table.orders.clear()
        table.trades.clear()
        table.goal_suit = ""
        table.common_suit = ""
        table.suit_counts.clear()
        table.winners.clear()
        table.game_task = None
        table.trade_open = False
        table.hidden_cards = 0

    async def _refund_all(self) -> None:
        for p in self.table.players.values():
            try:
                await queries.update_casino_balance(str(p.user_id), p.wager)
            except Exception:
                pass

    async def _close(self, interaction: discord.Interaction, reason: str) -> None:
        embed = discord.Embed(
            title="\U0001f0cf Figgie Table \u2014 Closed",
            description=reason,
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(self.table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table
        if table.game_task and not table.game_task.done():
            table.game_task.cancel()
        if table.phase == "finished":
            self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    await table.message.edit(
                        embed=discord.Embed(
                            title="\U0001f0cf Figgie Table \u2014 Timed Out",
                            description="Table timed out between games.",
                            colour=discord.Colour.dark_grey(),
                        ),
                        view=None,
                    )
                except Exception:
                    pass
            return
        await self._refund_all()
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                await table.message.edit(
                    embed=discord.Embed(
                        title="\U0001f0cf Figgie Table \u2014 Timed Out",
                        description="Table timed out. All buy-ins refunded.",
                        colour=discord.Colour.dark_grey(),
                    ),
                    view=None,
                )
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class FiggieCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, FiggieTable] = {}

    @app_commands.command(name="figgie", description="Open a Figgie table (Jane Street trading card game)")
    async def figgie(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a Figgie table in this channel!", ephemeral=True,
            )
            return
        await queries.get_or_create_casino_wallet(str(interaction.user.id))
        table = FiggieTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table
        view = FiggieTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FiggieCog(bot))
