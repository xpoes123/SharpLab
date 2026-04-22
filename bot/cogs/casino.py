"""Casino cog — multiplayer /blackjack table and /balance commands."""
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Card helpers ──────────────────────────────────────────────────────────────

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
RANK_VALUES = {
    "A": 11, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
    "8": 8, "9": 9, "10": 10, "J": 10, "Q": 10, "K": 10,
}
SHOE_DECKS = 2
RESHUFFLE_THRESHOLD = 20
MAX_PLAYERS = 5

# Hi-Lo counting values
HI_LO: dict[str, int] = {
    "2": 1, "3": 1, "4": 1, "5": 1, "6": 1,
    "7": 0, "8": 0, "9": 0,
    "10": -1, "J": -1, "Q": -1, "K": -1, "A": -1,
}


def _new_shoe() -> list[str]:
    """Create and shuffle a 2-deck shoe."""
    cards = [f"{r}{s}" for s in SUITS for r in RANKS] * SHOE_DECKS
    random.shuffle(cards)
    return cards


def _hand_value(hand: list[str]) -> int:
    """Best blackjack value for a hand (aces count 11 or 1)."""
    total = 0
    aces = 0
    for card in hand:
        rank = card[:-1]  # strip suit symbol
        total += RANK_VALUES[rank]
        if rank == "A":
            aces += 1
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _fmt_card(card: str) -> str:
    return f"`{card}`"


def _fmt_hand(hand: list[str]) -> str:
    return " ".join(_fmt_card(c) for c in hand)


def _is_blackjack(hand: list[str]) -> bool:
    return len(hand) == 2 and _hand_value(hand) == 21


def _hi_lo_value(card: str) -> int:
    rank = card[:-1]
    return HI_LO[rank]


def _card_rank(card: str) -> str:
    return card[:-1]


def _card_suit(card: str) -> str:
    return card[-1]


_RED_SUITS = {"♥", "♦"}

_POKER_RANK = {
    "A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
    "8": 8, "9": 9, "10": 10, "J": 11, "Q": 12, "K": 13,
}


def _eval_perfect_pairs(c1: str, c2: str) -> tuple[str, int]:
    """Evaluate Perfect Pairs. Returns (label, multiplier) or ('', 0)."""
    r1, r2 = _card_rank(c1), _card_rank(c2)
    if r1 != r2:
        return "", 0
    s1, s2 = _card_suit(c1), _card_suit(c2)
    if s1 == s2:
        return "Perfect Pair", 25
    col1 = "red" if s1 in _RED_SUITS else "black"
    col2 = "red" if s2 in _RED_SUITS else "black"
    if col1 == col2:
        return "Colored Pair", 12
    return "Mixed Pair", 6


def _eval_21_plus_3(c1: str, c2: str, c3: str) -> tuple[str, int]:
    """Evaluate 21+3 (player's 2 cards + dealer upcard).
    Returns (label, multiplier) or ('', 0)."""
    ranks = [_card_rank(c) for c in (c1, c2, c3)]
    suits = [_card_suit(c) for c in (c1, c2, c3)]

    is_flush = suits[0] == suits[1] == suits[2]
    same_rank = ranks[0] == ranks[1] == ranks[2]

    vals = sorted(_POKER_RANK[r] for r in ranks)
    is_straight = vals[2] - vals[1] == 1 and vals[1] - vals[0] == 1
    if not is_straight:
        # Ace-high wrap: Q-K-A
        ace_vals = sorted(14 if r == "A" else _POKER_RANK[r] for r in ranks)
        is_straight = ace_vals[2] - ace_vals[1] == 1 and ace_vals[1] - ace_vals[0] == 1

    if same_rank and is_flush:
        return "Suited Trips", 100
    if is_straight and is_flush:
        return "Straight Flush", 40
    if same_rank:
        return "Three of a Kind", 30
    if is_straight:
        return "Straight", 10
    if is_flush:
        return "Flush", 5
    return "", 0


# ── Game state ────────────────────────────────────────────────────────────────


@dataclass
class PlayerHand:
    user_id: int
    display_name: str
    bet: int
    original_bet: int = 0  # pre-double/split amount for re-bet
    hand: list[str] = field(default_factory=list)
    stood: bool = False
    busted: bool = False
    doubled: bool = False
    blackjack: bool = False
    payout: int = 0
    # Split
    has_split: bool = False
    split_hand: list[str] = field(default_factory=list)
    split_stood: bool = False
    split_busted: bool = False
    split_bet: int = 0
    split_payout: int = 0
    active_hand: int = 0  # 0 = main, 1 = split
    # Side bets
    pairs_wager: int = 0
    twentyone3_wager: int = 0
    pairs_payout: int = 0
    pairs_label: str = ""
    twentyone3_payout: int = 0
    twentyone3_label: str = ""

    @property
    def done(self) -> bool:
        if self.blackjack:
            return True
        main_done = self.stood or self.busted
        if not self.has_split:
            return main_done
        split_done = self.split_stood or self.split_busted
        return main_done and split_done

    @property
    def side_wager(self) -> int:
        return self.pairs_wager + self.twentyone3_wager

    @property
    def can_split(self) -> bool:
        if self.has_split or len(self.hand) != 2:
            return False
        return _card_rank(self.hand[0]) == _card_rank(self.hand[1])

    @property
    def active_cards(self) -> list[str]:
        if self.has_split and self.active_hand == 1:
            return self.split_hand
        return self.hand

    def hit_active(self, card: str) -> None:
        """Add a card to the active hand and check bust/21."""
        if self.has_split and self.active_hand == 1:
            self.split_hand.append(card)
            val = _hand_value(self.split_hand)
            if val > 21:
                self.split_busted = True
            elif val == 21:
                self.split_stood = True
        else:
            self.hand.append(card)
            val = _hand_value(self.hand)
            if val > 21:
                self.busted = True
            elif val == 21:
                self.stood = True
        self._advance()

    def stand_active(self) -> None:
        """Stand on the active hand."""
        if self.has_split and self.active_hand == 1:
            self.split_stood = True
        else:
            self.stood = True
        self._advance()

    def _advance(self) -> None:
        """If main hand is done and split exists, move to split hand."""
        if not self.has_split or self.active_hand == 1:
            return
        if self.stood or self.busted:
            self.active_hand = 1


@dataclass
class BlackjackTable:
    channel_id: int
    dealer_id: int
    dealer_name: str
    shoe: list[str]
    dealer_hand: list[str] = field(default_factory=list)
    players: dict[int, PlayerHand] = field(default_factory=dict)
    phase: str = "betting"  # betting | playing | finished
    message: discord.Message | None = None
    running_count: int = 0
    total_cards: int = 0  # total cards when shoe was created
    round_num: int = 1
    last_bets: dict[int, tuple[str, int, int, int]] = field(default_factory=dict)
    reshuffled: bool = False

    def draw(self) -> str:
        """Draw a card from the shoe and update the running count."""
        card = self.shoe.pop()
        self.running_count += _hi_lo_value(card)
        return card

    def all_done(self) -> bool:
        return all(p.done for p in self.players.values())

    def decks_remaining(self) -> float:
        return len(self.shoe) / 52

    def true_count(self) -> float:
        dr = self.decks_remaining()
        if dr < 0.25:
            return float(self.running_count)
        return self.running_count / dr

    def check_reshuffle(self) -> None:
        """Reshuffle if shoe is low. Resets the count."""
        if len(self.shoe) < RESHUFFLE_THRESHOLD:
            self.shoe = _new_shoe()
            self.total_cards = len(self.shoe)
            self.running_count = 0
            self.reshuffled = True


# ── Embed helpers ─────────────────────────────────────────────────────────────


def _hand_status(stood: bool, busted: bool, val: int) -> str:
    if busted:
        return "Bust!"
    if stood:
        return f"stands ({val})"
    return f"({val})"


def _hand_outcome(payout: int, bet: int) -> str:
    if payout == 0:
        return "Dealer wins"
    if payout == bet:
        return "Push"
    return "Win!"


# ── Embeds ────────────────────────────────────────────────────────────────────


def _table_embed(
    table: BlackjackTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    phase = table.phase

    if phase == "betting":
        colour = discord.Colour.blurple()
        title = f"Blackjack Table — Place Your Bets (Round {table.round_num})"
    elif phase == "finished":
        colour = discord.Colour.gold()
        title = f"Blackjack Table — Round {table.round_num} Complete"
    else:
        colour = discord.Colour.blurple()
        title = f"Blackjack Table — Round {table.round_num}"

    embed = discord.Embed(title=title, colour=colour)

    tc = table.true_count()
    footer = (
        f"Dealer: {table.dealer_name} | Shoe: {len(table.shoe)}/{table.total_cards}"
        f" | RC {table.running_count:+d} · TC {tc:+.1f}"
    )
    embed.set_footer(text=footer)

    # Reshuffle notice
    if table.reshuffled:
        embed.description = "🔀 **Shoe reshuffled!**"
        table.reshuffled = False
    elif phase == "betting":
        embed.description = "Join the table, then the dealer deals!"
    elif phase == "finished":
        embed.description = "Click **New Round** to continue or **Close Table** to end."

    # Dealer hand
    if phase == "betting":
        pass
    elif phase == "playing":
        embed.add_field(
            name="Dealer",
            value=f"{_fmt_card(table.dealer_hand[0])} `??`",
            inline=False,
        )
    else:  # finished
        dval = _hand_value(table.dealer_hand)
        bust = " — Bust!" if dval > 21 else ""
        embed.add_field(
            name="Dealer",
            value=f"{_fmt_hand(table.dealer_hand)}  ({dval}){bust}",
            inline=False,
        )

    # Players
    if not table.players:
        if phase == "betting":
            embed.add_field(
                name="Players",
                value="*No players yet — click Join!*",
                inline=False,
            )
    else:
        lines: list[str] = []
        for p in table.players.values():
            # Side bet label
            side_parts: list[str] = []
            if p.pairs_wager > 0:
                side_parts.append(f"PP {p.pairs_wager}c")
            if p.twentyone3_wager > 0:
                side_parts.append(f"21+3 {p.twentyone3_wager}c")
            side_str = f" + {' + '.join(side_parts)}" if side_parts else ""

            if phase == "betting":
                lines.append(f"🃏 **{p.display_name}** — {p.bet}c{side_str}")

            elif phase == "playing":
                _append_playing_line(lines, p)

            else:  # finished
                _append_finished_line(lines, p, balances)

        if lines:
            embed.add_field(name="Players", value="\n".join(lines), inline=False)

    return embed


def _side_bet_inline(p: PlayerHand) -> str:
    """Side bet results for inline display."""
    parts: list[str] = []
    if p.pairs_wager > 0:
        parts.append(f"PP: {p.pairs_label} ✔" if p.pairs_payout > 0 else "PP ✘")
    if p.twentyone3_wager > 0:
        parts.append(f"21+3: {p.twentyone3_label} ✔" if p.twentyone3_payout > 0 else "21+3 ✘")
    return f" | {' · '.join(parts)}" if parts else ""


def _append_playing_line(lines: list[str], p: PlayerHand) -> None:
    side_line = _side_bet_inline(p)

    if not p.has_split:
        val = _hand_value(p.hand)
        cards = _fmt_hand(p.hand)
        if p.blackjack:
            emoji, status = "✅", "Blackjack! ✨"
        elif p.busted:
            emoji, status = "💥", "Bust!"
        elif p.stood:
            emoji, status = "✋", f"stands ({val})"
        else:
            emoji, status = "🟦", f"({val})"
        lines.append(f"{emoji} **{p.display_name}** ({p.bet}c): {cards} — {status}{side_line}")
        return

    # Split hands — multi-line
    bet_label = f"{p.bet}c + {p.split_bet}c"
    val1 = _hand_value(p.hand)
    val2 = _hand_value(p.split_hand)
    cards1 = _fmt_hand(p.hand)
    cards2 = _fmt_hand(p.split_hand)

    active1 = p.active_hand == 0 and not p.stood and not p.busted
    active2 = p.active_hand == 1 and not p.split_stood and not p.split_busted

    arrow1 = "▶ " if active1 else "  "
    arrow2 = "▶ " if active2 else "  "
    s1 = _hand_status(p.stood, p.busted, val1)
    s2 = _hand_status(p.split_stood, p.split_busted, val2)

    emoji = "🟦" if (active1 or active2) else "✋"
    if p.busted and p.split_busted:
        emoji = "💥"

    lines.append(
        f"{emoji} **{p.display_name}** ({bet_label}):{side_line}\n"
        f"{arrow1}Hand 1: {cards1} — {s1}\n"
        f"{arrow2}Hand 2: {cards2} — {s2}"
    )


def _append_finished_line(
    lines: list[str], p: PlayerHand, balances: dict[int, int] | None,
) -> None:
    # Side bet result lines
    side_results: list[str] = []
    if p.pairs_wager > 0:
        if p.pairs_payout > 0:
            side_results.append(f"PP: {p.pairs_label} +{p.pairs_payout - p.pairs_wager}c")
        else:
            side_results.append("PP ✘")
    if p.twentyone3_wager > 0:
        if p.twentyone3_payout > 0:
            side_results.append(f"21+3: {p.twentyone3_label} +{p.twentyone3_payout - p.twentyone3_wager}c")
        else:
            side_results.append("21+3 ✘")
    side_line = f"\n  {' · '.join(side_results)}" if side_results else ""

    # Total P&L
    total_payout = p.payout + p.split_payout + p.pairs_payout + p.twentyone3_payout
    total_cost = p.bet + p.split_bet + p.pairs_wager + p.twentyone3_wager
    net = total_payout - total_cost
    sign = "+" if net > 0 else ""
    bal = balances.get(p.user_id, 0) if balances else 0

    if not p.has_split:
        val = _hand_value(p.hand)
        cards = _fmt_hand(p.hand)
        if p.blackjack:
            outcome = "Blackjack!"
        else:
            outcome = _hand_outcome(p.payout, p.bet)
        lines.append(
            f"**{p.display_name}** ({p.bet}c): {cards} ({val}) — {outcome}"
            f"{side_line}\n  → **{sign}{net}c** (bal: {bal}c)"
        )
        return

    # Split — show both hands
    val1 = _hand_value(p.hand)
    val2 = _hand_value(p.split_hand)
    cards1 = _fmt_hand(p.hand)
    cards2 = _fmt_hand(p.split_hand)
    o1 = _hand_outcome(p.payout, p.bet)
    o2 = _hand_outcome(p.split_payout, p.split_bet)

    lines.append(
        f"**{p.display_name}** ({p.bet}c + {p.split_bet}c):"
        f"\n  Hand 1: {cards1} ({val1}) — {o1}"
        f"\n  Hand 2: {cards2} ({val2}) — {o2}"
        f"{side_line}\n  → **{sign}{net}c** (bal: {bal}c)"
    )


# ── Modals ────────────────────────────────────────────────────────────────────


class JoinBlackjackModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)", placeholder="e.g. 50",
        required=True, max_length=10,
    )

    def __init__(self, table: BlackjackTable, view: "BlackjackTableView") -> None:
        super().__init__(title="Join Blackjack Table")
        self.table = table
        self.table_view = view

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
            await interaction.response.send_message("You're already at the table!", ephemeral=True)
            return
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal})", ephemeral=True,
            )
            return
        self.table.players[uid] = PlayerHand(
            user_id=uid, display_name=interaction.user.display_name,
            bet=amt, original_bet=amt,
        )
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self.table_view,
        )


class BJSideBetModal(ui.Modal):
    amount = ui.TextInput(
        label="Side bet amount (coins)", placeholder="e.g. 10",
        required=True, max_length=10,
    )

    def __init__(
        self, table: BlackjackTable, side: str, view: "BlackjackTableView",
    ) -> None:
        label = "Perfect Pairs" if side == "pairs" else "21+3"
        super().__init__(title=f"Side Bet — {label}")
        self.table = table
        self.side = side
        self.table_view = view

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
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message("Join the table first!", ephemeral=True)
            return
        if self.table.phase != "betting":
            await interaction.response.send_message("Cards already dealt!", ephemeral=True)
            return
        if self.side == "pairs" and player.pairs_wager > 0:
            await interaction.response.send_message(
                "You already have a Perfect Pairs bet!", ephemeral=True,
            )
            return
        if self.side == "twentyone3" and player.twentyone3_wager > 0:
            await interaction.response.send_message(
                "You already have a 21+3 bet!", ephemeral=True,
            )
            return
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal})", ephemeral=True,
            )
            return

        if self.side == "pairs":
            player.pairs_wager = amt
        else:
            player.twentyone3_wager = amt

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self.table_view,
        )


# ── View ──────────────────────────────────────────────────────────────────────


class BlackjackTableView(ui.View):
    def __init__(
        self, table: BlackjackTable, active_tables: dict[int, "BlackjackTable"],
    ) -> None:
        super().__init__(timeout=180)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        playing = phase == "playing"
        finished = phase == "finished"

        # Row 0: Deal, Join, Re-bet, Leave
        self.deal_btn.disabled = not betting
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = finished

        # Row 1: Hit, Stand, Double Down, Split
        self.hit_btn.disabled = not playing
        self.stand_btn.disabled = not playing
        self.double_btn.disabled = not playing
        self.split_btn.disabled = not playing

        # Row 2: New Round, Count, Close Table
        self.new_round_btn.disabled = not finished
        # Count is always enabled
        self.close_btn.disabled = playing

        # Row 3: Side bets — only during betting
        self.pairs_btn.disabled = not betting
        self.twentyone3_btn.disabled = not betting

    def _get_active_player(self, interaction: discord.Interaction) -> PlayerHand | None:
        """Return the player if they're at the table and still playing."""
        p = self.table.players.get(interaction.user.id)
        if p is None:
            return None
        if p.done:
            return None
        return p

    # ── Row 0: Deal / Join / Re-bet / Leave ──────────────────────

    @ui.button(label="Deal", style=discord.ButtonStyle.success, emoji="🃏", row=0)
    async def deal_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.dealer_id:
            await interaction.response.send_message(
                "Only the table opener can deal!", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message("Already dealt!", ephemeral=True)
            return
        if not self.table.players:
            await interaction.response.send_message(
                "No players yet! Someone needs to join first.", ephemeral=True,
            )
            return
        await self._deal(interaction)

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="🪑", row=0)
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Cards already dealt! Wait for the next round.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already at the table!", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(JoinBlackjackModal(self.table, self))

    @ui.button(label="Re-bet", style=discord.ButtonStyle.primary, emoji="🔄", row=0)
    async def rebet_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Cards already dealt!", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already at the table!", ephemeral=True,
            )
            return
        last = self.table.last_bets.get(uid)
        if last is None:
            await interaction.response.send_message(
                "No previous bet — use Join instead.", ephemeral=True,
            )
            return
        name, amt, pp, t3 = last
        total_cost = amt + pp + t3
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        try:
            await queries.update_casino_balance(str(uid), -total_cost)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for {total_cost}c re-bet! (have {bal})", ephemeral=True,
            )
            return
        self.table.players[uid] = PlayerHand(
            user_id=uid, display_name=name, bet=amt, original_bet=amt,
            pairs_wager=pp, twentyone3_wager=t3,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self,
        )

    @ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="🚪", row=0)
    async def leave_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message(
                "You're not at this table.", ephemeral=True,
            )
            return

        if self.table.phase == "playing" and not player.done:
            await interaction.response.send_message(
                "Can't leave mid-hand! Hit or Stand first.", ephemeral=True,
            )
            return

        # Opener leaving during betting = abort
        if uid == self.table.dealer_id and self.table.phase == "betting":
            await self._abort(interaction, "Dealer left — all bets refunded.")
            return

        # Refund if still betting (main + side bets)
        if self.table.phase == "betting":
            await queries.update_casino_balance(
                str(uid), player.bet + player.side_wager,
            )
            del self.table.players[uid]
            self._update_buttons()
            await interaction.response.edit_message(
                embed=_table_embed(self.table), view=self,
            )
            return

        # Playing phase but player is done — just acknowledge
        await interaction.response.send_message(
            "You'll see results when the round ends.", ephemeral=True,
        )

    # ── Row 1: Hit / Stand / Double Down / Split ─────────────────

    @ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="👊", row=1)
    async def hit_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        player = self._get_active_player(interaction)
        if player is None:
            await interaction.response.send_message(
                "You're not playing or already done!", ephemeral=True,
            )
            return
        player.hit_active(self.table.draw())

        if self.table.all_done():
            await self._dealer_play_and_finish(interaction)
        else:
            await interaction.response.edit_message(
                embed=_table_embed(self.table), view=self,
            )

    @ui.button(label="Stand", style=discord.ButtonStyle.secondary, emoji="✋", row=1)
    async def stand_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        player = self._get_active_player(interaction)
        if player is None:
            await interaction.response.send_message(
                "You're not playing or already done!", ephemeral=True,
            )
            return
        player.stand_active()

        if self.table.all_done():
            await self._dealer_play_and_finish(interaction)
        else:
            await interaction.response.edit_message(
                embed=_table_embed(self.table), view=self,
            )

    @ui.button(label="Double Down", style=discord.ButtonStyle.success, emoji="💰", row=1)
    async def double_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        player = self._get_active_player(interaction)
        if player is None:
            await interaction.response.send_message(
                "You're not playing or already done!", ephemeral=True,
            )
            return
        if len(player.active_cards) != 2:
            await interaction.response.send_message(
                "Can only double down on first two cards!", ephemeral=True,
            )
            return

        # Determine which bet to double
        if player.has_split and player.active_hand == 1:
            cost = player.split_bet
        else:
            cost = player.bet

        try:
            await queries.update_casino_balance(str(player.user_id), -cost)
        except ValueError:
            await interaction.response.send_message(
                "Not enough coins to double down!", ephemeral=True,
            )
            return

        if player.has_split and player.active_hand == 1:
            player.split_bet *= 2
        else:
            player.bet *= 2
        player.doubled = True

        # Draw one card, auto-stand (or bust)
        player.hit_active(self.table.draw())
        # hit_active handles bust/21/advance, but if not busted we force stand
        if player.has_split and player.active_hand == 1:
            if not player.split_busted and not player.split_stood:
                player.stand_active()
        elif player.active_hand == 0 or not player.has_split:
            if not player.busted and not player.stood:
                player.stand_active()

        if self.table.all_done():
            await self._dealer_play_and_finish(interaction)
        else:
            await interaction.response.edit_message(
                embed=_table_embed(self.table), view=self,
            )

    @ui.button(label="Split", style=discord.ButtonStyle.primary, emoji="✂️", row=1)
    async def split_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        player = self._get_active_player(interaction)
        if player is None:
            await interaction.response.send_message(
                "You're not playing or already done!", ephemeral=True,
            )
            return
        if not player.can_split:
            await interaction.response.send_message(
                "Can't split — need two cards of the same rank!", ephemeral=True,
            )
            return

        # Deduct split bet (equal to original bet)
        split_cost = player.original_bet
        try:
            await queries.update_casino_balance(str(player.user_id), -split_cost)
        except ValueError:
            await interaction.response.send_message(
                "Not enough coins to split!", ephemeral=True,
            )
            return

        # Split the hand
        player.has_split = True
        player.split_bet = split_cost
        player.split_hand = [player.hand.pop()]  # move second card to split
        # Deal one card to each hand
        player.hand.append(self.table.draw())
        player.split_hand.append(self.table.draw())

        # Split aces: one card each, auto-stand both
        if _card_rank(player.hand[0]) == "A":
            player.stood = True
            player.split_stood = True
            # Check for busts (impossible with 2 cards but be safe)
            if _hand_value(player.hand) > 21:
                player.busted = True
            if _hand_value(player.split_hand) > 21:
                player.split_busted = True
        else:
            # Auto-stand if either hand hits 21
            if _hand_value(player.hand) == 21:
                player.stood = True
                player._advance()
            if _hand_value(player.split_hand) == 21:
                player.split_stood = True

        if self.table.all_done():
            await self._dealer_play_and_finish(interaction)
        else:
            await interaction.response.edit_message(
                embed=_table_embed(self.table), view=self,
            )

    # ── Row 2: New Round / Count / Close Table ───────────────────

    @ui.button(label="New Round", style=discord.ButtonStyle.success, emoji="▶️", row=2)
    async def new_round_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.dealer_id:
            await interaction.response.send_message(
                "Only the table opener can start a new round!", ephemeral=True,
            )
            return
        if self.table.phase != "finished":
            await interaction.response.send_message(
                "Round still in progress!", ephemeral=True,
            )
            return
        self._start_new_round()
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self,
        )

    @ui.button(label="Close Table", style=discord.ButtonStyle.danger, emoji="✖️", row=2)
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.dealer_id:
            await interaction.response.send_message(
                "Only the table opener can close!", ephemeral=True,
            )
            return
        if self.table.phase == "playing":
            await interaction.response.send_message(
                "Can't close mid-round!", ephemeral=True,
            )
            return
        if self.table.phase == "betting":
            await self._abort(interaction, "Table closed by dealer. All bets refunded.")
        else:
            await self._close(interaction)

    # ── Row 3: Side Bets ─────────────────────────────────────────

    @ui.button(
        label="Perfect Pairs", style=discord.ButtonStyle.success,
        emoji="👯", row=3,
    )
    async def pairs_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_side_bet(interaction, "pairs")

    @ui.button(
        label="21+3", style=discord.ButtonStyle.success,
        emoji="🃏", row=3,
    )
    async def twentyone3_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_side_bet(interaction, "twentyone3")

    # ── Side bet handler ─────────────────────────────────────────

    async def _handle_side_bet(
        self, interaction: discord.Interaction, side: str,
    ) -> None:
        uid = interaction.user.id
        if self.table.phase != "betting":
            await interaction.response.send_message("Cards already dealt!", ephemeral=True)
            return
        if uid not in self.table.players:
            await interaction.response.send_message("Join the table first!", ephemeral=True)
            return
        player = self.table.players[uid]
        if side == "pairs" and player.pairs_wager > 0:
            await interaction.response.send_message(
                "You already have a Perfect Pairs bet!", ephemeral=True,
            )
            return
        if side == "twentyone3" and player.twentyone3_wager > 0:
            await interaction.response.send_message(
                "You already have a 21+3 bet!", ephemeral=True,
            )
            return
        await interaction.response.send_modal(BJSideBetModal(self.table, side, self))

    # ── Deal logic ───────────────────────────────────────────────

    async def _deal(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "playing"

        # Deal 2 cards to each player, then 2 to dealer
        for p in table.players.values():
            p.hand = [table.draw(), table.draw()]
        table.dealer_hand = [table.draw(), table.draw()]

        dealer_bj = _is_blackjack(table.dealer_hand)
        dealer_upcard = table.dealer_hand[0]

        # Resolve side bets immediately (independent of hand outcome)
        for p in table.players.values():
            if p.pairs_wager > 0:
                label, mult = _eval_perfect_pairs(p.hand[0], p.hand[1])
                if mult > 0:
                    p.pairs_label = label
                    p.pairs_payout = p.pairs_wager + mult * p.pairs_wager
                    await queries.update_casino_balance(str(p.user_id), p.pairs_payout)
            if p.twentyone3_wager > 0:
                label, mult = _eval_21_plus_3(p.hand[0], p.hand[1], dealer_upcard)
                if mult > 0:
                    p.twentyone3_label = label
                    p.twentyone3_payout = p.twentyone3_wager + mult * p.twentyone3_wager
                    await queries.update_casino_balance(str(p.user_id), p.twentyone3_payout)

        # Check player naturals
        for p in table.players.values():
            if _is_blackjack(p.hand):
                p.blackjack = True
                if dealer_bj:
                    p.payout = p.bet  # push
                else:
                    p.payout = p.bet + (p.bet * 3 // 2)  # 3:2
                    await queries.update_casino_balance(str(p.user_id), p.payout)

        # If dealer has BJ, everyone without BJ loses
        if dealer_bj:
            for p in table.players.values():
                if not p.blackjack:
                    p.busted = True
                    p.payout = 0
                else:
                    # Push — refund bet
                    await queries.update_casino_balance(str(p.user_id), p.payout)

        if table.all_done():
            await self._finish_round(interaction)
            return

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(table), view=self,
        )

    # ── Dealer play + finish ─────────────────────────────────────

    async def _dealer_play_and_finish(self, interaction: discord.Interaction) -> None:
        table = self.table

        # Dealer only plays if at least one hand is standing (not busted)
        any_standing = False
        for p in table.players.values():
            if p.stood and not p.busted:
                any_standing = True
            if p.has_split and p.split_stood and not p.split_busted:
                any_standing = True

        if any_standing:
            while _hand_value(table.dealer_hand) < 17:
                table.dealer_hand.append(table.draw())

        dval = _hand_value(table.dealer_hand)

        # Resolve each player's hand(s)
        for p in table.players.values():
            if p.blackjack:
                continue  # already paid at deal time

            # Main hand
            if p.busted:
                p.payout = 0
            else:
                pval = _hand_value(p.hand)
                if dval > 21:
                    p.payout = p.bet * 2
                elif pval > dval:
                    p.payout = p.bet * 2
                elif pval == dval:
                    p.payout = p.bet
                else:
                    p.payout = 0

            # Split hand
            if p.has_split:
                if p.split_busted:
                    p.split_payout = 0
                else:
                    sval = _hand_value(p.split_hand)
                    if dval > 21:
                        p.split_payout = p.split_bet * 2
                    elif sval > dval:
                        p.split_payout = p.split_bet * 2
                    elif sval == dval:
                        p.split_payout = p.split_bet
                    else:
                        p.split_payout = 0

        await self._finish_round(interaction)

    async def _finish_round(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "finished"
        balances: dict[int, int] = {}

        # Save original bets for re-bet next round
        for p in table.players.values():
            table.last_bets[p.user_id] = (
                p.display_name, p.original_bet, p.pairs_wager, p.twentyone3_wager,
            )

        for p in table.players.values():
            # Credit main hand payout
            if p.blackjack:
                # BJ payouts already credited at deal time
                pass
            elif p.payout > 0:
                await queries.update_casino_balance(str(p.user_id), p.payout)

            # Credit split hand payout
            if p.has_split and p.split_payout > 0:
                await queries.update_casino_balance(str(p.user_id), p.split_payout)

            # Log casino history
            total_w = p.bet + p.split_bet + p.pairs_wager + p.twentyone3_wager
            total_p = p.payout + p.split_payout + p.pairs_payout + p.twentyone3_payout
            await queries.log_casino_result(str(p.user_id), "blackjack", total_w, total_p)

            balances[p.user_id] = (
                await queries.get_casino_balance(str(p.user_id))
            ) or 0

        embed = _table_embed(table, balances=balances)
        self._update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    def _start_new_round(self) -> None:
        """Reset table for the next round. Shoe and count persist."""
        table = self.table
        table.players.clear()
        table.dealer_hand.clear()
        table.phase = "betting"
        table.round_num += 1
        table.check_reshuffle()

    # ── Abort / close / timeout ──────────────────────────────────

    async def _abort(self, interaction: discord.Interaction, reason: str) -> None:
        for p in self.table.players.values():
            try:
                refund = p.bet + p.split_bet + p.side_wager
                await queries.update_casino_balance(str(p.user_id), refund)
            except Exception:
                pass
        embed = discord.Embed(
            title="Blackjack Table — Closed",
            description=reason,
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(self.table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _close(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Blackjack Table — Closed",
            description=f"Table closed after {self.table.round_num} round(s). Thanks for playing!",
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(self.table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table
        if table.phase == "finished":
            self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    embed = discord.Embed(
                        title="Blackjack Table — Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    pass
            return
        # Betting: refund main + split + side. Playing: side already resolved.
        for p in table.players.values():
            try:
                if table.phase == "betting":
                    refund = p.bet + p.side_wager
                else:
                    refund = p.bet + p.split_bet  # split bet not yet resolved
                await queries.update_casino_balance(str(p.user_id), refund)
            except Exception:
                pass
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="Blackjack Table — Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ───────────────────────────────────────────────────────────────────────


class CasinoCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, BlackjackTable] = {}

    @app_commands.command(name="blackjack", description="Open a blackjack table (multiplayer)")
    async def blackjack(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a blackjack table in this channel! Use the buttons to join.",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        shoe = _new_shoe()
        table = BlackjackTable(
            channel_id=channel_id,
            dealer_id=interaction.user.id,
            dealer_name=interaction.user.display_name,
            shoe=shoe,
            total_cards=len(shoe),
        )
        self.active_tables[channel_id] = table

        view = BlackjackTableView(table, self.active_tables)
        embed = _table_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()

    @app_commands.command(name="balance", description="Check your coin balance")
    @app_commands.describe(user="Check another user's balance (optional)")
    async def balance(
        self, interaction: discord.Interaction, user: discord.User | None = None
    ) -> None:
        target = user or interaction.user
        is_self = target.id == interaction.user.id

        if is_self:
            bal = await queries.get_or_create_casino_wallet(str(target.id))
            msg = f"**{target.display_name}** has **{bal}** casino coins."
        else:
            bal = await queries.get_casino_balance(str(target.id))
            if bal is None:
                msg = f"**{target.display_name}** hasn't played yet."
            else:
                msg = f"**{target.display_name}** has **{bal}** casino coins."

        await interaction.response.send_message(msg)

    @app_commands.command(name="give-coins", description="Give casino coins to a user (admin)")
    @app_commands.describe(user="User to give coins to", amount="Number of coins to give")
    async def give_coins(
        self, interaction: discord.Interaction, user: discord.User, amount: int,
    ) -> None:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Admin only.", ephemeral=True)
            return
        if amount < 1:
            await interaction.response.send_message("Amount must be at least 1.", ephemeral=True)
            return
        new_balance = await queries.give_casino_coins(str(user.id), amount)
        await interaction.response.send_message(
            f"Gave **{amount}** casino coins to **{user.display_name}**. "
            f"Their balance: **{new_balance}** coins."
        )

    @app_commands.command(name="games", description="List all available casino games")
    async def games(self, interaction: discord.Interaction) -> None:
        mp_tag = " `MP`"
        lines = [
            f"` /{name} ` — {desc}{mp_tag if mp else ''}"
            for name, desc, mp in CASINO_GAMES
        ]
        embed = discord.Embed(
            title="Casino Games",
            description="\n".join(lines),
            color=0xF1C40F,
        )
        embed.set_footer(text=f"{len(CASINO_GAMES)} games available  ·  /random-game to pick one")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="random-game", description="Pick a random casino game to play")
    async def random_game(self, interaction: discord.Interaction) -> None:
        name, desc, _mp = random.choice(CASINO_GAMES)
        await interaction.response.send_message(
            f"You should play **/{name}** — {desc}"
        )

    @app_commands.command(name="casino-stats", description="View casino PnL stats")
    @app_commands.describe(user="View another user's stats (optional)")
    async def casino_stats(
        self, interaction: discord.Interaction, user: discord.User | None = None,
    ) -> None:
        target = user or interaction.user
        uid = str(target.id)

        overall = await queries.get_casino_stats(uid)
        by_game = await queries.get_casino_stats_by_game(uid)

        if overall["rounds"] == 0:
            await interaction.response.send_message(
                f"**{target.display_name}** hasn't played any casino games yet.",
                ephemeral=True,
            )
            return

        net = overall["net_profit"]
        roi = overall["roi"]
        colour = (
            discord.Colour.green() if net > 0
            else discord.Colour.red() if net < 0
            else discord.Colour.light_grey()
        )

        embed = discord.Embed(
            title=f"Casino Stats — {target.display_name}",
            colour=colour,
        )
        embed.add_field(
            name="Overview",
            value=(
                f"**Rounds:** {overall['rounds']:,}\n"
                f"**Wagered:** {overall['total_wagered']:,}c\n"
                f"**Won:** {overall['total_payout']:,}c\n"
                f"**Net:** {net:+,}c\n"
                f"**ROI:** {roi:+.1f}%"
            ),
            inline=False,
        )

        if by_game:
            GAME_LABELS = {
                "blackjack": "Blackjack",
                "plinko": "Plinko",
                "craps": "Craps",
                "hilo": "Hi-Lo",
                "roulette": "Roulette",
                "crash": "Crash",
                "videopoker": "Video Poker",
                "uth": "Texas Hold'em",
                "baccarat": "Baccarat",
                "paigow": "Pai Gow",
                "bingo": "Bingo",
                "horserace": "Horse Race",
                "stockmarket": "Stock Market",
                "math24": "Math 24",
                "countdown": "Countdown",
                "mastermind": "Mastermind",
                "liarsdice": "Liar's Dice",
                "slots": "Slots",
                "nbasim": "NBA Sim",
                "nflsim": "NFL Sim",
                "penalties": "Penalties",
            }
            lines = []
            for row in by_game:
                g_net = row["net_profit"]
                dot = "\U0001f7e2" if g_net > 0 else "\U0001f534" if g_net < 0 else "\u26aa"
                label = GAME_LABELS.get(row["game"], row["game"].capitalize())
                lines.append(
                    f"{dot} **{label}** — {row['rounds']} rounds — {g_net:+,}c"
                )
            embed.add_field(name="Per Game", value="\n".join(lines), inline=False)

        bal = await queries.get_casino_balance(uid)
        if bal is not None:
            embed.set_footer(text=f"Current balance: {bal:,}c")

        await interaction.response.send_message(embed=embed)


CASINO_GAMES: list[tuple[str, str, bool]] = [
    # (command_name, description, is_multiplayer)
    ("blackjack", "Blackjack table", True),
    ("roulette", "American roulette", False),
    ("craps", "Craps with full side bets", False),
    ("baccarat", "Baccarat card game", False),
    ("paigow", "Pai Gow Poker", True),
    ("uth", "Ultimate Texas Hold'em", False),
    ("videopoker", "Video Poker (Jacks or Better)", False),
    ("crash", "Crash rocket game", False),
    ("plinko", "Plinko ball-drop game", False),
    ("hilo", "Hi-Lo card guessing game", False),
    ("bingo", "Bingo", True),
    ("slots", "Fortune Reels — slots with free spins & bonus rounds", False),
    ("horserace", "Horse racing with betting", True),
    ("stockmarket", "Stock market investment game", True),
    ("math24", "Make 24 from four numbers", True),
    ("countdown", "Countdown numbers math game", True),
    ("mastermind", "Code-breaking deduction game", True),
    ("liarsdice", "Liar's Dice bluffing game", True),
    ("nbasim", "Simulated NBA game betting", False),
    ("nflsim", "Simulated NFL game betting", False),
    ("penalties", "1v1 Penalty Shootout duel", True),
]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CasinoCog(bot))
