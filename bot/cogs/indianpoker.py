"""Casino cog — /indian-poker  Sit-and-go Indian Poker."""

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
from bot.cogs._elo_helpers import update_elo_multiplayer, fmt_elo_change
from bot.cogs._pool import compute_side_pot_payouts
import logging

log = logging.getLogger(__name__)
# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 6
MIN_PLAYERS = 2
MAX_HANDS = 30
ACTION_TIME = 30  # seconds per action
MAX_BET = 500
STARTING_CHIPS = 1000

# Blind schedule: (small, big) — escalates every 5 hands
BLIND_SCHEDULE = [
    (10, 20),
    (25, 50),
    (50, 100),
    (100, 200),
    (200, 400),
    (500, 1000),
]

# Card values and suits
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
RANK_VALUE = {r: i for i, r in enumerate(RANKS)}
SUITS = ["\u2660", "\u2665", "\u2666", "\u2663"]  # spade, heart, diamond, club
SUIT_VALUE = {s: i for i, s in enumerate(reversed(SUITS))}  # spade highest

SUIT_NAME = {"\u2660": "Spades", "\u2665": "Hearts", "\u2666": "Diamonds", "\u2663": "Clubs"}


# ── Card ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, order=False)
class Card:
    rank: str
    suit: str

    @property
    def value(self) -> tuple[int, int]:
        """(rank_value, suit_value) for comparison. Higher = better."""
        return (RANK_VALUE[self.rank], SUIT_VALUE[self.suit])

    def __str__(self) -> str:
        return f"{self.rank}{self.suit}"

    def __gt__(self, other: "Card") -> bool:
        return self.value > other.value

    def __lt__(self, other: "Card") -> bool:
        return self.value < other.value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Card):
            return NotImplemented
        return self.value == other.value

    def __hash__(self) -> int:
        return hash((self.rank, self.suit))


def _make_deck() -> list[Card]:
    deck = [Card(r, s) for r in RANKS for s in SUITS]
    random.shuffle(deck)
    return deck


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class IPPlayer:
    user_id: int
    display_name: str
    coin_bet: int  # real coin wager for the game
    chips: int = STARTING_CHIPS
    card: Card | None = None
    folded: bool = False
    hand_bet: int = 0  # chips wagered this hand
    acted: bool = False
    eliminated: bool = False


@dataclass
class IPTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | showdown | finished
    players: dict[int, IPPlayer] = field(default_factory=dict)
    seat_order: list[int] = field(default_factory=list)  # uid order
    dealer_idx: int = 0
    hand_num: int = 0
    pot: int = 0
    current_bet: int = 0  # current bet to match
    action_idx: int = 0  # index in seat_order for current action
    deck: list[Card] = field(default_factory=list)
    message: discord.Message | None = None
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    action_event: asyncio.Event = field(default_factory=asyncio.Event)
    last_raiser: int | None = None  # uid of last raiser (to stop betting round)


def _get_blinds(hand_num: int) -> tuple[int, int]:
    idx = min(hand_num // 5, len(BLIND_SCHEDULE) - 1)
    return BLIND_SCHEDULE[idx]


def _active_players(table: IPTable) -> list[IPPlayer]:
    return [table.players[uid] for uid in table.seat_order if not table.players[uid].eliminated]


def _hand_players(table: IPTable) -> list[IPPlayer]:
    return [
        table.players[uid] for uid in table.seat_order
        if not table.players[uid].eliminated and not table.players[uid].folded
    ]


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: IPTable) -> discord.Embed:
    pot = sum(p.coin_bet for p in table.players.values())
    embed = discord.Embed(
        title="\U0001f0cf Indian Poker \u2014 Join",
        description=(
            "Everyone sees everyone else's card \u2014 but **not their own**.\n"
            "Bet on whether you have the best card based on what you see!\n\n"
            f"**{STARTING_CHIPS}** starting chips \u2022 "
            f"Blinds escalate every 5 hands \u2022 Max {MAX_HANDS} hands"
        ),
        colour=discord.Colour.dark_purple(),
    )
    if pot:
        embed.add_field(name="Pot", value=f"{pot}c", inline=True)
    if table.players:
        lines = [
            f"\U0001f0cf **{p.display_name}** \u2014 {p.coin_bet}c"
            for p in table.players.values()
        ]
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet \u2014 click Join!*",
            inline=False,
        )
    embed.set_footer(text=f"Host: {table.host_name} \u2502 {MIN_PLAYERS}-{MAX_PLAYERS} players")
    return embed


def _hand_embed(table: IPTable, status: str = "") -> discord.Embed:
    small, big = _get_blinds(table.hand_num)
    embed = discord.Embed(
        title=f"\U0001f0cf Indian Poker \u2014 Hand {table.hand_num}/{MAX_HANDS}",
        colour=discord.Colour.purple(),
    )

    if status:
        embed.description = status

    # Player list with chips + status
    lines = []
    active = _active_players(table)
    current_uid = None
    hand_active = _hand_players(table)
    if hand_active and table.phase == "playing":
        active_uids = [p.user_id for p in hand_active]
        if table.action_idx < len(active_uids):
            current_uid = active_uids[table.action_idx % len(active_uids)]

    for uid in table.seat_order:
        p = table.players[uid]
        if p.eliminated:
            lines.append(f"\u274c ~~{p.display_name}~~ \u2014 eliminated")
            continue
        marker = "\u25b6 " if uid == current_uid else ""
        fold_str = " (folded)" if p.folded else ""
        card_str = f" [{p.card}]" if p.card and table.phase == "showdown" else ""
        lines.append(
            f"{marker}**{p.display_name}** \u2014 {p.chips} chips"
            f" (bet: {p.hand_bet}){fold_str}{card_str}"
        )
    embed.add_field(name="Players", value="\n".join(lines), inline=False)
    embed.add_field(name="Pot", value=f"{table.pot} chips", inline=True)
    embed.add_field(name="Blinds", value=f"{small}/{big}", inline=True)
    embed.add_field(name="Current Bet", value=f"{table.current_bet} chips", inline=True)
    return embed


def _showdown_embed(table: IPTable, winner: IPPlayer) -> discord.Embed:
    embed = discord.Embed(
        title=f"\U0001f0cf Indian Poker \u2014 Hand {table.hand_num} Showdown",
        colour=discord.Colour.gold(),
    )
    lines = []
    for uid in table.seat_order:
        p = table.players[uid]
        if p.eliminated:
            continue
        if p.folded:
            lines.append(f"\U0001f6ab **{p.display_name}** \u2014 folded")
        else:
            star = " \U0001f451" if p.user_id == winner.user_id else ""
            lines.append(f"\U0001f0cf **{p.display_name}** \u2014 **{p.card}**{star}")
    embed.add_field(name="Cards", value="\n".join(lines), inline=False)
    embed.add_field(
        name="Winner",
        value=f"**{winner.display_name}** wins **{table.pot}** chips!",
        inline=False,
    )
    return embed


def _final_embed(table: IPTable, payouts: dict[int, int]) -> discord.Embed:
    sorted_players = sorted(
        table.players.values(), key=lambda p: p.chips, reverse=True,
    )
    embed = discord.Embed(
        title="\U0001f3c6 Indian Poker \u2014 Final Results",
        colour=discord.Colour.gold(),
    )
    lines = []
    for i, p in enumerate(sorted_players):
        medal = {0: "\U0001f947", 1: "\U0001f948", 2: "\U0001f949"}.get(i, f"**{i+1}.**")
        payout = payouts.get(p.user_id, 0)
        net = payout - p.coin_bet
        net_str = f" ({net:+,}c)" if net != 0 else ""
        lines.append(
            f"{medal} **{p.display_name}** \u2014 {p.chips} chips \u2014 {payout}c{net_str}"
        )
    embed.description = "\n".join(lines)
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinIPModal(ui.Modal):
    amount = ui.TextInput(
        label="Buy-in (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(self, table: IPTable, view: "IPView", balance: int) -> None:
        super().__init__(title="Join Indian Poker")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 100 (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if amt < 1 or amt > MAX_BET:
            await interaction.response.send_message(f"Buy-in must be 1\u2013{MAX_BET}c.", ephemeral=True)
            return

        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return

        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        self.table.players[uid] = IPPlayer(
            user_id=uid, display_name=interaction.user.display_name, coin_bet=amt,
        )
        self.table.last_bets[uid] = (interaction.user.display_name, amt)
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


class BetModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet/Raise amount (chips)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(self, table: IPTable, view: "IPView", min_bet: int, max_bet: int) -> None:
        super().__init__(title="Place Bet")
        self.table = table
        self.table_view = view
        self.min_bet = min_bet
        self.max_bet = max_bet
        self.amount.placeholder = f"{min_bet}\u2013{max_bet} chips"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if not player:
            await interaction.response.send_message("You're not in!", ephemeral=True)
            return

        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a number.", ephemeral=True)
            return

        if amt < self.min_bet:
            await interaction.response.send_message(
                f"Minimum bet is {self.min_bet} chips.", ephemeral=True,
            )
            return
        if amt > self.max_bet:
            amt = self.max_bet  # cap at all-in

        # Place the bet
        additional = amt - player.hand_bet  # what they still need to put in
        if additional > player.chips:
            additional = player.chips  # all-in
            amt = player.hand_bet + additional

        player.chips -= additional
        player.hand_bet = amt
        self.table.pot += additional
        if amt > self.table.current_bet:
            self.table.current_bet = amt
            self.table.last_raiser = uid
        player.acted = True

        await interaction.response.send_message(
            f"\U0001f4b0 You bet **{amt}** chips.", ephemeral=True,
        )
        self.table.action_event.set()


# ── View ─────────────────────────────────────────────────────────────────────


class IPView(ui.View):
    def __init__(self, table: IPTable, active_tables: dict[int, IPTable]) -> None:
        super().__init__(timeout=900)  # 15 min total timeout
        self.table = table
        self.active_tables = active_tables
        self._game_task: asyncio.Task | None = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        betting = self.table.phase == "betting"
        playing = self.table.phase == "playing"
        finished = self.table.phase == "finished"

        self.start_btn.disabled = not betting or len(self.table.players) < MIN_PLAYERS
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = not betting

        self.check_call_btn.disabled = not playing
        self.bet_raise_btn.disabled = not playing
        self.fold_btn.disabled = not playing
        self.view_cards_btn.disabled = not playing

        self.new_game_btn.disabled = not finished
        self.close_btn.disabled = playing

    def _current_uid(self) -> int | None:
        hand_active = _hand_players(self.table)
        if not hand_active:
            return None
        idx = self.table.action_idx % len(hand_active)
        return hand_active[idx].user_id

    # ── Betting buttons (row 0) ──────────────────────────────────────────

    @ui.button(label="Start", style=discord.ButtonStyle.success, emoji="\u25b6", row=0)
    async def start_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Already started!", ephemeral=True)
            return
        if len(self.table.players) < MIN_PLAYERS:
            await interaction.response.send_message(
                f"Need at least {MIN_PLAYERS} players!", ephemeral=True,
            )
            return

        self.table.seat_order = list(self.table.players.keys())
        random.shuffle(self.table.seat_order)
        self.table.phase = "playing"
        self._update_buttons()
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="\U0001f0cf Indian Poker",
                description="Dealing cards...",
                colour=discord.Colour.purple(),
            ),
            view=self,
        )
        self._game_task = asyncio.create_task(self._run_game())

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f3b2", row=0)
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Game in progress!", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        bal = await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(JoinIPModal(self.table, self, bal))

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
            await interaction.response.send_message(
                "No previous bet \u2014 use Join instead.", ephemeral=True,
            )
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
                f"Not enough coins for {amt}c! (have {bal}c)", ephemeral=True,
            )
            return
        self.table.players[uid] = IPPlayer(
            user_id=uid, display_name=name, coin_bet=amt,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=0)
    async def leave_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Can't leave mid-game!", ephemeral=True)
            return
        uid = interaction.user.id
        player = self.table.players.pop(uid, None)
        if not player:
            await interaction.response.send_message("You're not in!", ephemeral=True)
            return
        await queries.update_casino_balance(str(uid), player.coin_bet)
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Playing buttons (row 1) ──────────────────────────────────────────

    @ui.button(label="Check/Call", style=discord.ButtonStyle.success, emoji="\u2705", row=1)
    async def check_call_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid != self._current_uid():
            await interaction.response.send_message("Not your turn!", ephemeral=True)
            return
        player = self.table.players[uid]
        to_call = self.table.current_bet - player.hand_bet
        if to_call > 0:
            # Call
            actual = min(to_call, player.chips)
            player.chips -= actual
            player.hand_bet += actual
            self.table.pot += actual
            await interaction.response.send_message(
                f"\u2705 You called **{actual}** chips.", ephemeral=True,
            )
        else:
            await interaction.response.send_message("\u2705 You checked.", ephemeral=True)
        player.acted = True
        self.table.action_event.set()

    @ui.button(label="Bet/Raise", style=discord.ButtonStyle.primary, emoji="\U0001f4b0", row=1)
    async def bet_raise_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid != self._current_uid():
            await interaction.response.send_message("Not your turn!", ephemeral=True)
            return
        player = self.table.players[uid]
        _, big = _get_blinds(self.table.hand_num)
        min_raise = max(self.table.current_bet * 2, big)
        max_raise = player.hand_bet + player.chips  # all-in
        if max_raise <= self.table.current_bet:
            await interaction.response.send_message(
                "You don't have enough chips to raise! Use Check/Call.", ephemeral=True,
            )
            return
        min_raise = max(min_raise, self.table.current_bet + 1)
        await interaction.response.send_modal(
            BetModal(self.table, self, min_raise, max_raise),
        )

    @ui.button(label="Fold", style=discord.ButtonStyle.danger, emoji="\U0001f6ab", row=1)
    async def fold_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid != self._current_uid():
            await interaction.response.send_message("Not your turn!", ephemeral=True)
            return
        player = self.table.players[uid]
        player.folded = True
        player.acted = True
        await interaction.response.send_message("\U0001f6ab You folded.", ephemeral=True)
        self.table.action_event.set()

    @ui.button(label="View Cards", style=discord.ButtonStyle.secondary, emoji="\U0001f440", row=2)
    async def view_cards_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid not in self.table.players:
            await interaction.response.send_message("You're not in this game!", ephemeral=True)
            return

        lines = []
        for seat_uid in self.table.seat_order:
            p = self.table.players[seat_uid]
            if p.eliminated:
                continue
            if seat_uid == uid:
                lines.append(f"**{p.display_name}** (you): ???")
            elif p.folded:
                lines.append(f"**{p.display_name}**: folded")
            elif p.card:
                lines.append(f"**{p.display_name}**: **{p.card}**")
            else:
                lines.append(f"**{p.display_name}**: \u2014")
        await interaction.response.send_message(
            "\U0001f440 **Cards on foreheads:**\n" + "\n".join(lines),
            ephemeral=True,
        )

    # ── Finished buttons (row 3) ─────────────────────────────────────────

    @ui.button(label="New Game", style=discord.ButtonStyle.success, emoji="\U0001f504", row=3)
    async def new_game_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "finished":
            await interaction.response.send_message("Game not over yet!", ephemeral=True)
            return
        for uid, p in self.table.players.items():
            self.table.last_bets[uid] = (p.display_name, p.coin_bet)
        self.table.players.clear()
        self.table.seat_order.clear()
        self.table.phase = "betting"
        self.table.hand_num = 0
        self.table.pot = 0
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(label="Close", style=discord.ButtonStyle.danger, emoji="\u2716", row=3)
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase == "playing":
            await interaction.response.send_message("Can't close mid-game!", ephemeral=True)
            return
        if self.table.phase == "betting":
            for p in self.table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.coin_bet)
                except Exception:
                    log.exception("Unhandled error in indianpoker.py")
        self.active_tables.pop(self.table.channel_id, None)
        embed = discord.Embed(
            title="\u2716 Indian Poker \u2014 Table Closed",
            colour=discord.Colour.dark_grey(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    # ── Game loop ────────────────────────────────────────────────────────

    async def _run_game(self) -> None:
        table = self.table
        msg = table.message
        if not msg:
            return

        for hand in range(1, MAX_HANDS + 1):
            table.hand_num = hand

            # Check if only 1 player left
            active = _active_players(table)
            if len(active) <= 1:
                break

            # Reset hand state
            table.pot = 0
            table.current_bet = 0
            table.last_raiser = None
            table.deck = _make_deck()
            for p in active:
                p.card = None
                p.folded = False
                p.hand_bet = 0
                p.acted = False

            # Post blinds
            small_blind, big_blind = _get_blinds(hand)
            active_uids = [p.user_id for p in active]
            sb_idx = table.dealer_idx % len(active_uids)
            bb_idx = (table.dealer_idx + 1) % len(active_uids)

            sb_player = table.players[active_uids[sb_idx]]
            bb_player = table.players[active_uids[bb_idx]]

            sb_amount = min(small_blind, sb_player.chips)
            sb_player.chips -= sb_amount
            sb_player.hand_bet = sb_amount
            table.pot += sb_amount

            bb_amount = min(big_blind, bb_player.chips)
            bb_player.chips -= bb_amount
            bb_player.hand_bet = bb_amount
            table.pot += bb_amount

            table.current_bet = bb_amount

            # Deal cards
            for p in active:
                p.card = table.deck.pop()

            # Action starts after big blind
            start_idx = (table.dealer_idx + 2) % len(active_uids)

            # Show hand
            status = (
                f"**{sb_player.display_name}** posts SB ({sb_amount}), "
                f"**{bb_player.display_name}** posts BB ({bb_amount})"
            )
            self._update_buttons()
            embed = _hand_embed(table, status)
            try:
                await msg.edit(embed=embed, view=self)
            except Exception:
                return

            # Betting round
            await self._run_betting_round(active_uids, start_idx)

            # Showdown
            remaining = _hand_players(table)
            if len(remaining) == 1:
                winner = remaining[0]
            else:
                # Best card wins
                winner = max(remaining, key=lambda p: p.card.value)

            winner.chips += table.pot

            # Show showdown
            table.phase = "showdown"
            embed = _showdown_embed(table, winner)
            try:
                await msg.edit(embed=embed, view=self)
            except Exception:
                return

            await asyncio.sleep(4)

            # Eliminate players with 0 chips
            for p in table.players.values():
                if p.chips <= 0 and not p.eliminated:
                    p.eliminated = True

            # Advance dealer
            table.dealer_idx = (table.dealer_idx + 1) % len(active_uids)
            table.phase = "playing"

            # Check if only 1 player left
            active = _active_players(table)
            if len(active) <= 1:
                break

        # ── Game over ────────────────────────────────────────────────────
        table.phase = "finished"

        # Rank by chips
        all_players = sorted(table.players.values(), key=lambda p: p.chips, reverse=True)
        winner_uids = []
        if all_players:
            top_chips = all_players[0].chips
            winner_uids = [p.user_id for p in all_players if p.chips == top_chips]

        bets = {p.user_id: p.coin_bet for p in table.players.values()}
        payouts = compute_side_pot_payouts(bets, winner_uids)

        for uid, payout in payouts.items():
            if payout > 0:
                await queries.update_casino_balance(str(uid), payout)

        for p in table.players.values():
            await queries.log_casino_result(
                str(p.user_id), "indian-poker", p.coin_bet, payouts.get(p.user_id, 0),
            )

        # ELO
        elo_changes: dict[int, tuple[float, float]] = {}
        if len(all_players) >= 2:
            finish_order = [p.user_id for p in all_players]
            try:
                elo_changes = await update_elo_multiplayer(
                    finish_order, "indian_poker", "indian_poker",
                )
            except Exception:
                log.exception("Unhandled error in indianpoker.py")

        embed = _final_embed(table, payouts)

        if elo_changes:
            elo_lines = []
            for p in all_players:
                change = elo_changes.get(p.user_id)
                if change:
                    old_r, new_r = change
                    elo_lines.append(f"**{p.display_name}**: {fmt_elo_change(old_r, new_r)}")
            if elo_lines:
                embed.add_field(name="ELO", value="\n".join(elo_lines), inline=False)

        self._update_buttons()
        try:
            await msg.edit(embed=embed, view=self)
        except Exception:
            log.exception("Unhandled error in indianpoker.py")

    async def _run_betting_round(self, active_uids: list[int], start_idx: int) -> None:
        """Run one round of betting. Returns when betting is complete."""
        table = self.table
        msg = table.message

        # Reset acted flags
        for uid in active_uids:
            p = table.players[uid]
            if not p.folded and not p.eliminated:
                p.acted = False

        # BB and SB already acted by posting blinds — but they get a chance to raise
        idx = start_idx
        num_active = len(active_uids)
        consecutive_checks = 0

        while True:
            hand_active = _hand_players(table)
            if len(hand_active) <= 1:
                break

            # Find next non-folded, non-eliminated player
            current_uid = active_uids[idx % num_active]
            current_player = table.players[current_uid]

            if current_player.folded or current_player.eliminated or current_player.chips == 0:
                idx += 1
                consecutive_checks += 1
                if consecutive_checks >= num_active:
                    break
                continue

            # Check if everyone has acted and no one raised
            remaining_to_act = [
                p for p in hand_active
                if not p.acted and p.chips > 0
            ]
            all_matched = all(
                p.hand_bet >= table.current_bet or p.chips == 0
                for p in hand_active
            )
            if not remaining_to_act and all_matched:
                break

            # If this player already acted and matched current bet, skip
            if current_player.acted and current_player.hand_bet >= table.current_bet:
                idx += 1
                consecutive_checks += 1
                if consecutive_checks >= num_active:
                    break
                continue

            consecutive_checks = 0
            table.action_idx = hand_active.index(current_player) if current_player in hand_active else 0
            table.action_event.clear()

            # Update display
            to_call = table.current_bet - current_player.hand_bet
            action_str = f"**{current_player.display_name}**'s turn"
            if to_call > 0:
                action_str += f" (call {to_call} or raise)"
            else:
                action_str += " (check or bet)"

            self._update_buttons()
            embed = _hand_embed(table, action_str)
            try:
                await msg.edit(embed=embed, view=self)
            except Exception:
                return

            # Wait for action
            try:
                await asyncio.wait_for(table.action_event.wait(), timeout=ACTION_TIME)
            except asyncio.TimeoutError:
                # Auto-fold on timeout if there's a bet to call, else check
                if table.current_bet > current_player.hand_bet:
                    current_player.folded = True
                current_player.acted = True

            # If someone raised, everyone needs to act again
            if table.last_raiser == current_uid:
                # Reset acted for everyone except the raiser
                for p in hand_active:
                    if p.user_id != current_uid:
                        p.acted = False
                table.last_raiser = None

            idx += 1

    # ── Timeout ──────────────────────────────────────────────────────────

    async def on_timeout(self) -> None:
        if self._game_task and not self._game_task.done():
            self._game_task.cancel()

        if self.table.phase == "betting":
            for p in self.table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.coin_bet)
                except Exception:
                    log.exception("Unhandled error in indianpoker.py")

        self.active_tables.pop(self.table.channel_id, None)
        if self.table.message:
            try:
                embed = discord.Embed(
                    title="\u2716 Indian Poker \u2014 Timed Out",
                    description="Table timed out. Buy-ins refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await self.table.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("Unhandled error in indianpoker.py")


# ── Cog ──────────────────────────────────────────────────────────────────────


class IndianPokerCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, IPTable] = {}

    async def indian_poker(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            _has_running = any(
                (t := getattr(existing, n, None)) is not None and not t.done()
                for n in ("game_task", "race_task", "sim_task", "round_task", "_round_task", "trade_task", "fly_task", "_shot_clock_task", "_countdown_task")
            )
            if _has_running:
                await interaction.response.send_message(
                    "There's already an Indian Poker game here!", ephemeral=True,
                )
                return
            del self.active_tables[channel_id]

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = IPTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        view = IPView(table, self.active_tables)
        embed = _betting_embed(table)
        try:
            await interaction.response.send_message(embed=embed, view=view)
        except discord.NotFound:
            return  # interaction expired — don't leave a ghost table
        self.active_tables[channel_id] = table
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(IndianPokerCog(bot))
