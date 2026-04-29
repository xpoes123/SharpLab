"""Casino cog — multiplayer /blackjack table and /balance commands."""
import asyncio
import logging
import random
import time
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from db import queries

log = logging.getLogger(__name__)

# How often to scan for orphaned games (seconds)
_CLEANUP_INTERVAL_SECS = 120
# Kill any game that has been in active_tables longer than this (seconds)
_MAX_GAME_AGE_SECS = 900  # 15 minutes

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

GAME_LABELS: dict[str, str] = {
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
    "stockguess": "Stock Guess",
    "math24": "Math 24",
    "countdown": "Countdown",
    "mastermind": "Mastermind",
    "liarsdice": "Liar's Dice",
    "slots": "Slots",
    "nbasim": "NBA Sim",
    "nflsim": "NFL Sim",
    "mlbsim": "MLB Sim",
    "tennissim": "Tennis Sim",
    "soccersim": "Soccer Sim",
    "penalties": "Penalties",
    "tictactoe": "Tic Tac Toe",
    "rps": "Rock Paper Scissors",
    "geography": "Geography",
    "wordle": "Wordle",
    "nba-trivia": "NBA Trivia",
    "nfl-trivia": "NFL Trivia",
    "sudoku": "Sudoku Sprint",
    "duel": "Duels",
    "tournament": "Tournaments",
    "figgie": "Figgie",
    "mathsprint": "Math Sprint",
    "pokemon": "Who's That Pokemon?",
    "tradingfloor": "Trading Floor",
    "quizbowl": "Quiz Bowl",
    "valorant": "Valorant Guess",
    "solitaire-chess": "Solitaire Chess",
    "nba": "NBA Player Guess",
    "minesweeper": "Minesweeper Race",
    "sequence": "Sequence",
    "prisoner": "Prisoner's Dilemma",
    "indian-poker": "Indian Poker",
    "battleship": "Battleship",
    "blotto": "Colonel Blotto",
}

GAME_CATEGORIES: list[tuple[str, str]] = [
    ("Card Games", "\U0001f0cf"),
    ("Table & Arcade", "\U0001f3b0"),
    ("Party Games", "\U0001f389"),
    ("Brain Games", "\U0001f9e0"),
    ("Sports Sim", "\U0001f3c6"),
]

MODE_EMOJI: dict[str, str] = {
    "solo": "\U0001f464",
    "duo": "\u2694\ufe0f",
    "party": "\U0001f389",
}

MODE_LABEL: dict[str, str] = {
    "solo": "Solo",
    "duo": "Duo",
    "party": "Party",
}

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
                log.exception("Unhandled error in casino.py")
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
                    log.exception("Unhandled error in casino.py")
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
                log.exception("Unhandled error in casino.py")
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
                log.exception("Unhandled error in casino.py")


# ── Cog ───────────────────────────────────────────────────────────────────────

RANDOM_GAME_CHOICES = [
    app_commands.Choice(name="Solo \u2014 play against the house", value="solo"),
    app_commands.Choice(name="Duo \u2014 grab a friend", value="duo"),
    app_commands.Choice(name="Party \u2014 the more the merrier", value="party"),
]



class CasinoCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, BlackjackTable] = {}
        # channel_id -> monotonic time when first seen by the cleanup loop
        self._table_first_seen: dict[int, float] = {}
        self._cleanup_orphaned_games.start()

    async def cog_unload(self) -> None:
        self._cleanup_orphaned_games.cancel()

    @tasks.loop(seconds=_CLEANUP_INTERVAL_SECS)
    async def _cleanup_orphaned_games(self) -> None:
        """Periodically scan all cogs and kill games stuck in active_tables."""
        now = time.monotonic()
        killed = 0
        for cog in self.bot.cogs.values():
            tables = getattr(cog, "active_tables", None)
            if not isinstance(tables, dict) or not tables:
                continue
            # Snapshot keys — we may mutate the dict
            for channel_id in list(tables.keys()):
                table = tables.get(channel_id)
                if table is None:
                    continue
                # Track first-seen time
                key = (id(cog), channel_id)
                if key not in self._table_first_seen:
                    self._table_first_seen[key] = now
                    continue  # give it at least one full interval
                age = now - self._table_first_seen[key]
                if age < _MAX_GAME_AGE_SECS:
                    continue
                # Game has been running too long — force kill
                log.warning(
                    "Cleanup: killing orphaned game in channel %s (cog=%s, age=%.0fs)",
                    channel_id, type(cog).__name__, age,
                )
                if hasattr(table, "stop_requested"):
                    table.stop_requested = True
                # Wake any event
                for evt_name in ("part_solved", "round_solved", "round_event",
                                 "action_event", "current_event"):
                    evt = getattr(table, evt_name, None)
                    if evt is not None and hasattr(evt, "set"):
                        evt.set()
                        break
                # Cancel any task
                for task_name in ("game_task", "race_task", "sim_task",
                                  "round_task", "_round_task", "trade_task",
                                  "fly_task", "_shot_clock_task",
                                  "_countdown_task"):
                    task = getattr(table, task_name, None)
                    if task is not None and not task.done():
                        task.cancel()
                        break
                if hasattr(table, "phase"):
                    table.phase = "closed"
                # Archive thread if it exists
                thread = getattr(table, "thread", None)
                if thread is not None:
                    try:
                        await thread.send(
                            "\u23f9\ufe0f Game auto-closed after 15 minutes."
                        )
                        await thread.edit(archived=True)
                    except Exception:
                        log.exception("Unhandled error in casino.py")
                tables.pop(channel_id, None)
                self._table_first_seen.pop(key, None)
                killed += 1
        # Clean stale first-seen entries for games that ended normally
        stale_keys = [
            k for k in self._table_first_seen
            if not any(
                k[1] in getattr(cog, "active_tables", {})
                for cog in self.bot.cogs.values()
                if id(cog) == k[0]
            )
        ]
        for k in stale_keys:
            self._table_first_seen.pop(k, None)

    @_cleanup_orphaned_games.before_loop
    async def _before_cleanup(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="blackjack", description="Open a blackjack table (multiplayer)")
    async def blackjack(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            _has_running = any(
                (t := getattr(existing, n, None)) is not None and not t.done()
                for n in ("game_task", "race_task", "sim_task", "round_task", "_round_task", "trade_task", "fly_task", "_shot_clock_task", "_countdown_task")
            )
            if _has_running:
                await interaction.response.send_message(
                    "There's already a blackjack table in this channel! Use the buttons to join.",
                    ephemeral=True,
                )
                return
            del self.active_tables[channel_id]

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

    @app_commands.command(name="stop", description="Force-stop any active game in this channel")
    async def stop_game(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id

        # If invoked from a game thread, also check the parent channel
        parent_id: int | None = None
        if isinstance(interaction.channel, discord.Thread):
            parent_id = interaction.channel.parent_id

        found_cog = None
        found_table = None
        found_key: int | None = None

        for cog in self.bot.cogs.values():
            tables = getattr(cog, "active_tables", None)
            if not isinstance(tables, dict):
                continue
            # Check direct channel match
            if channel_id in tables:
                found_cog = cog
                found_table = tables[channel_id]
                found_key = channel_id
                break
            # Check parent channel (if invoked from a game thread)
            if parent_id and parent_id in tables:
                found_cog = cog
                found_table = tables[parent_id]
                found_key = parent_id
                break
            # Check if this channel is a game thread for any table
            for key, tbl in tables.items():
                thread = getattr(tbl, "thread", None)
                if thread is not None and thread.id == channel_id:
                    found_cog = cog
                    found_table = tbl
                    found_key = key
                    break
            if found_table is not None:
                break

        if found_table is None:
            await interaction.response.send_message(
                "No active game found in this channel.", ephemeral=True,
            )
            return

        phase = getattr(found_table, "phase", None)
        if phase == "closed":
            tables = getattr(found_cog, "active_tables", {})
            tables.pop(found_key, None)
            await interaction.response.send_message(
                "Game was already finished. Cleaned up.", ephemeral=True,
            )
            return

        # Signal graceful stop via stop_requested + wake event
        if hasattr(found_table, "stop_requested"):
            if found_table.stop_requested:
                await interaction.response.send_message(
                    "Game is already ending\u2026", ephemeral=True,
                )
                return
            found_table.stop_requested = True

        # Wake up any waiting event so the game loop notices the stop
        for event_name in ("part_solved", "round_solved", "round_event",
                           "action_event", "current_event"):
            evt = getattr(found_table, event_name, None)
            if evt is not None and hasattr(evt, "set"):
                evt.set()
                break

        # Check if there is actually a running game loop task
        has_running_task = False
        for task_name in ("game_task", "race_task", "sim_task", "round_task",
                          "_round_task", "trade_task", "fly_task",
                          "_shot_clock_task", "_countdown_task"):
            task = getattr(found_table, task_name, None)
            if task is not None and not task.done():
                has_running_task = True
                break

        if has_running_task and hasattr(found_table, "stop_requested"):
            # Game loop is running and supports stop_requested — let it
            # handle cleanup (final results, ELO, archiving).  Cancelling
            # the task races with the graceful-shutdown path.
            await interaction.response.send_message(
                "\u23f9\ufe0f Game force-stopped.", ephemeral=False,
            )
            return

        # No running game loop (lobby phase) or game lacks stop_requested
        # — cancel any task and clean up directly.
        if not has_running_task:
            pass  # nothing to cancel
        else:
            for task_name in ("game_task", "race_task", "sim_task", "round_task",
                              "_round_task", "trade_task", "fly_task",
                              "_shot_clock_task", "_countdown_task"):
                task = getattr(found_table, task_name, None)
                if task is not None and not task.done():
                    task.cancel()
                    break

        found_table.phase = "closed"
        tables = getattr(found_cog, "active_tables", {})
        tables.pop(found_key, None)

        await interaction.response.send_message(
            "\u23f9\ufe0f Game force-stopped.", ephemeral=False,
        )

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
        total_pages = len(GAME_CATEGORIES)
        view = GamesView(page=1)
        embed = _games_page_embed(1, total_pages)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="random-game", description="Pick a random casino game to play")
    @app_commands.describe(mode="Filter by player count")
    @app_commands.choices(mode=RANDOM_GAME_CHOICES)
    async def random_game(
        self, interaction: discord.Interaction, mode: str | None = None,
    ) -> None:
        if mode:
            pool = [(n, d) for n, d, _, m in CASINO_GAMES if m == mode]
            label = MODE_LABEL.get(mode, mode)
        else:
            pool = [(n, d) for n, d, _, _ in CASINO_GAMES]
            label = None
        name, desc = random.choice(pool)
        prefix = f"**[{label}]** " if label else ""
        await interaction.response.send_message(
            f"{prefix}You should play **/{name}** \u2014 {desc}"
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


    @app_commands.command(
        name="casino-leaderboard",
        description="Casino balance leaderboard",
    )
    async def casino_leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        rows = await queries.get_casino_leaderboard(limit=10)
        if not rows:
            await interaction.followup.send("No casino players yet.")
            return

        embed = discord.Embed(title="Casino Leaderboard", colour=discord.Colour.gold())
        lines: list[str] = []
        for i, row in enumerate(rows, 1):
            try:
                member = await self.bot.fetch_user(int(row["discord_user"]))
                name = member.display_name
            except Exception:
                name = f"User {row['discord_user'][:8]}"

            medal = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}.get(i, f"**{i}.**")
            bal = row["balance"]
            net = row["net_profit"]
            rounds = row["rounds"]
            net_str = f"{net:+,}c" if rounds > 0 else "—"
            lines.append(
                f"{medal} **{name}** — `{bal:,}`c | P/L {net_str} | {rounds:,} rounds"
            )

        embed.description = "\n".join(lines)
        await interaction.followup.send(embed=embed)

    # ── /game-leaderboard ────────────────────────────────────────────────────

    async def _game_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        matches = [
            app_commands.Choice(name=label, value=key)
            for key, label in GAME_LABELS.items()
            if current.lower() in label.lower() or current.lower() in key.lower()
        ]
        return matches[:25]

    @app_commands.command(
        name="game-leaderboard",
        description="Leaderboard for a specific casino game",
    )
    @app_commands.describe(game="Which game to show the leaderboard for")
    @app_commands.autocomplete(game=_game_autocomplete)
    async def game_leaderboard(
        self, interaction: discord.Interaction, game: str,
    ) -> None:
        if game not in GAME_LABELS:
            await interaction.response.send_message(
                "Unknown game. Use the autocomplete to pick one.", ephemeral=True,
            )
            return

        await interaction.response.defer()

        rows = await queries.get_casino_game_leaderboard(game, limit=10)
        label = GAME_LABELS[game]

        if not rows:
            await interaction.followup.send(f"No one has played **{label}** yet.")
            return

        embed = discord.Embed(
            title=f"{label} Leaderboard",
            colour=discord.Colour.gold(),
        )
        lines: list[str] = []
        for i, row in enumerate(rows, 1):
            try:
                member = await self.bot.fetch_user(int(row["discord_user"]))
                name = member.display_name
            except Exception:
                name = f"User {row['discord_user'][:8]}"

            medal = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}.get(i, f"**{i}.**")
            net = row["net_profit"]
            rounds = row["rounds"]
            roi = (net / row["total_wagered"] * 100) if row["total_wagered"] > 0 else 0
            lines.append(
                f"{medal} **{name}** — `{net:+,}`c | {roi:+.1f}% ROI | {rounds:,} rounds"
            )

        embed.description = "\n".join(lines)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="explain", description="Learn the rules of any casino game")
    async def explain(self, interaction: discord.Interaction) -> None:
        view = ExplainSelectView()
        await interaction.response.send_message(
            "Pick a game to see how it works:", view=view, ephemeral=True,
        )


# ── /explain dropdown ────────────────────────────────────────────────────────


class _ExplainCategorySelect(ui.Select):
    """One dropdown per game category (avoids Discord's 25-option limit)."""

    def __init__(self, cat_name: str, cat_emoji: str, row: int) -> None:
        options = [
            discord.SelectOption(
                label=GAME_LABELS.get(cmd, cmd),
                value=cmd,
                description=desc[:100],
            )
            for cmd, desc, cat, _ in CASINO_GAMES
            if cat == cat_name
        ]
        super().__init__(
            placeholder=f"{cat_emoji} {cat_name}",
            options=options,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        key = self.values[0]
        label = GAME_LABELS.get(key, key)
        rules = GAME_RULES.get(key, f"No rules written yet for **{label}**.")
        embed = discord.Embed(
            title=f"How to Play: {label}",
            description=rules,
            colour=0xF1C40F,
        )
        embed.set_footer(text=f"Start playing with /{key}")
        await interaction.response.edit_message(embed=embed, view=self.view)


class ExplainSelectView(ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=120)
        for i, (cat_name, cat_emoji) in enumerate(GAME_CATEGORIES):
            self.add_item(_ExplainCategorySelect(cat_name, cat_emoji, row=i))


GAME_RULES: dict[str, str] = {
    "blackjack": (
        "**Blackjack** (2-deck shoe)\n"
        "Beat the dealer by getting closer to 21 without going over.\n\n"
        "**How to play:** Join the table, place your bet, then the dealer deals 2 cards to each "
        "player and themselves (one face-down). Hit to draw cards, Stand to stop, Double Down to "
        "double your bet and take exactly one more card, or Split matching pairs into two hands.\n\n"
        "**Payouts:** Win = 2:1 | Blackjack (21 on first 2 cards) = 3:2 | Push = bet returned\n"
        "**Dealer rule:** Hits on 16 or below, stands on 17+\n"
        "**Side bets:** Perfect Pairs (same rank = 6x/12x/25x) | 21+3 (your 2 + dealer upcard "
        "make poker hands = 5x to 100x)\n"
        "**Card counting:** Running count and true count are displayed on the table."
    ),
    "baccarat": (
        "**Baccarat** (8-deck shoe)\n"
        "Bet on Player, Banker, or Tie before cards are dealt.\n\n"
        "**How to play:** Choose your side and wager. Two cards are dealt to Player and Banker. "
        "Hand values are the last digit of the sum (e.g. 7+8=15 -> 5). Drawing rules are automatic: "
        "Player draws on 0-5, Banker's draw depends on Player's third card.\n\n"
        "**Payouts:** Player = 1:1 | Banker = 1:1 (5% commission) | Tie = 8:1\n"
        "**Side bets:** Panda 8 (Player wins with 8 = 25:1) | Dragon 7 (Banker wins with 7 "
        "using 3 cards = 40:1)\n"
        "**Interactive peel:** Players reveal cards one by one for suspense."
    ),
    "paigow": (
        "**Pai Gow Poker**\n"
        "Beat the dealer by arranging 7 cards into a 5-card high hand and a 2-card low hand.\n\n"
        "**How to play:** You and the dealer each get 7 cards. Set them into a 5-card hand (back) "
        "and a 2-card hand (front). Your high hand must beat your low hand. Win both to win, "
        "lose both to lose, split = push.\n\n"
        "**Payouts:** Win = 1:1 | Push = bet returned\n"
        "**Strategy:** Balance strength between both hands. Don't stack everything in the high hand."
    ),
    "uth": (
        "**Ultimate Texas Hold'em**\n"
        "Poker against the dealer with escalating bet decisions.\n\n"
        "**How to play:** Post an Ante and Blind bet. You get 2 hole cards, then decide: bet 3x-4x "
        "(pre-flop), 2x (after flop), or 1x (after river). You can also check and wait. If you "
        "never bet, you fold and lose Ante + Blind. Best 5-card hand wins.\n\n"
        "**Payouts:** Ante = 1:1 (dealer needs pair+ to qualify) | Blind pays bonus for "
        "Straight+ | Play bet = 1:1"
    ),
    "videopoker": (
        "**Video Poker** (Jacks or Better)\n"
        "Draw poker against a paytable, not other players.\n\n"
        "**How to play:** You're dealt 5 cards. Choose which to hold and which to discard, then "
        "draw replacements. Your final hand pays according to the paytable.\n\n"
        "**Paytable:** Jacks or Better = 1:1 | Two Pair = 2:1 | Three of a Kind = 3:1 | "
        "Straight = 4:1 | Flush = 6:1 | Full House = 9:1 | Four of a Kind = 25:1 | "
        "Straight Flush = 50:1 | Royal Flush = 800:1"
    ),
    "hilo": (
        "**Hi-Lo**\n"
        "Predict whether the next card will be higher or lower.\n\n"
        "**How to play:** A card is shown. Guess Higher or Lower for the next card. "
        "Correct guesses multiply your winnings based on the true odds from the remaining deck. "
        "Cash out anytime to bank your streak. Ties push (no loss).\n\n"
        "**Payouts:** Fair odds based on remaining cards. Riskier guesses = bigger multipliers.\n"
        "**Strategy:** Count what's left in the deck. Cash out before it gets too risky."
    ),
    "roulette": (
        "**Roulette** (American, 0 + 00)\n"
        "Bet on where the ball will land on a 38-number wheel.\n\n"
        "**How to play:** Place bets on numbers, groups, or properties before the spin.\n\n"
        "**Bet types & payouts:**\n"
        "Straight (single number) = 35:1 | Split (2 adjacent) = 17:1 | "
        "Street (row of 3) = 11:1 | Corner (4 numbers) = 8:1\n"
        "Column/Dozen = 2:1 | Red/Black, Odd/Even, High/Low = 1:1\n\n"
        "**House edge:** 5.26% from the 0 and 00 slots."
    ),
    "craps": (
        "**Craps**\n"
        "Dice game with a come-out roll and a point phase.\n\n"
        "**How to play:** The shooter rolls two dice. **Come-out:** 7 or 11 = Pass Line wins, "
        "2/3/12 = Pass Line loses. Any other number sets the Point. **Point phase:** Roll the "
        "point again before a 7 to win.\n\n"
        "**Bets:** Pass Line (1:1) | Don't Pass (1:1, 12=push) | Odds (true odds, 0% edge) | "
        "Field (2:1 / 3:1 on 2&12) | Place bets | Hardways | Come/Don't Come\n\n"
        "**Tip:** Odds bets behind Pass/Don't Pass are the best bet in the casino (0% house edge)."
    ),
    "crash": (
        "**Crash**\n"
        "A multiplier rises until it randomly crashes. Cash out before it does.\n\n"
        "**How to play:** Place your bet. A rocket launches and the multiplier climbs exponentially. "
        "Hit Cash Out to lock in your current multiplier. If the rocket crashes before you cash out, "
        "you lose your bet.\n\n"
        "**Auto-cashout:** Set a target multiplier and it cashes out automatically.\n"
        "**Payouts:** Your bet x your cashout multiplier.\n"
        "**Odds:** ~1% chance of instant crash. Median crash around 2x."
    ),
    "plinko": (
        "**Plinko**\n"
        "Drop a ball through pegs and see which bucket it lands in.\n\n"
        "**How to play:** Choose a risk level (Low, Medium, or High) and place your bet. "
        "The ball drops through 8 rows of pegs and lands in one of 9 buckets, each with a "
        "different multiplier.\n\n"
        "**Risk levels:**\n"
        "Low: 0.5x to 5.6x (safer) | Medium: 0.3x to 13x | High: 0x to 29x (volatile)\n"
        "**Fair game:** Expected value = 1.0x across all risk levels."
    ),
    "slots": (
        "**Fortune Reels** (Slots)\n"
        "5-reel, 3-row slot machine with 20 paylines.\n\n"
        "**How to play:** Place your bet and spin. Matching symbols across paylines win. "
        "Wilds substitute for any symbol. Scatters trigger free spins. Bonus symbols "
        "trigger a pick-a-box mini-game.\n\n"
        "**Free Spins:** 3 Scatters = choose 5 spins at 2x, 10 spins at 1x, or 15 spins.\n"
        "**Bonus Round:** Pick 3 of 8 boxes for coin prizes. 5 are skulls (game over).\n"
        "**Gamble:** After any win, double-or-nothing on a card flip."
    ),
    "bingo": (
        "**Bingo**\n"
        "Classic 75-ball bingo with pattern objectives.\n\n"
        "**How to play:** Buy a card (5x5 grid, free center square). Numbers are called "
        "from 1-75 and auto-marked on your card. First player to complete the target pattern wins.\n\n"
        "**Patterns:** Four Corners | X | Plus | Diamond | T | L\n"
        "**Payouts:** Winner takes the pot (side-pot rules for unequal bets).\n"
        "**Host:** Picks the pattern before starting. Calls come every few seconds."
    ),
    "horserace": (
        "**Horse Race**\n"
        "Bet on horses and watch them race.\n\n"
        "**How to play:** Each horse has different odds. Place your bet on a horse before the "
        "race starts. Horses advance randomly each tick based on their speed ratings. "
        "First horse to the finish line wins.\n\n"
        "**Payouts:** Based on the horse's pre-race odds. Longshots pay more."
    ),
    "stockmarket": (
        "**Stock Market**\n"
        "Buy and sell fictional stocks across multiple rounds.\n\n"
        "**How to play:** You start with cash. Each round, stock prices move randomly "
        "(up, down, crash, boom). Buy low, sell high. The player with the highest portfolio "
        "value at the end wins.\n\n"
        "**Strategy:** Diversify or go all-in. Watch for crash events. Timing is everything."
    ),
    "liarsdice": (
        "**Liar's Dice**\n"
        "Bluffing game with hidden dice.\n\n"
        "**How to play:** Each player rolls 5 dice (hidden). Take turns bidding on how many of "
        "a certain face value exist across ALL players' dice. Each bid must raise the quantity "
        "or face value. Call \"Liar!\" if you think the bid is wrong.\n\n"
        "**Resolution:** If the bid was valid, the challenger loses a die. If wrong, the bidder "
        "loses one. Last player with dice wins.\n"
        "**Tip:** 1s are wild (count as any face) unless someone bids 1s specifically."
    ),
    "penalties": (
        "**Penalty Shootout** (1v1 Duel)\n"
        "Take turns as kicker and goalkeeper.\n\n"
        "**How to play:** Each player takes 5 penalty kicks. As the kicker, choose a direction "
        "(left, center, right). As the keeper, choose where to dive. If the keeper guesses "
        "correctly, the shot is saved.\n\n"
        "**Scoring:** Most goals after 5 rounds wins. Ties go to sudden death.\n"
        "**Strategy:** Mix up your shots. Don't be predictable."
    ),
    "math24": (
        "**Math 24**\n"
        "Use four numbers and basic operations to make exactly 24.\n\n"
        "**How to play:** Four numbers are revealed. Type a valid expression using +, -, *, / "
        "and parentheses that equals exactly 24. Each number must be used exactly once.\n\n"
        "**Example:** Numbers: 1, 2, 3, 4 -> `1*2*3*4` = 24\n"
        "**Tip:** Look for factors of 24 (1x24, 2x12, 3x8, 4x6)."
    ),
    "countdown": (
        "**Countdown** (Numbers Game)\n"
        "Get as close to a target number as possible using 6 numbers.\n\n"
        "**How to play:** You get 6 numbers (mix of small 1-10 and large 25/50/75/100) and a "
        "3-digit target. Use +, -, *, / to reach the target. Each number can only be used once. "
        "Closest answer wins.\n\n"
        "**Scoring:** Exact = full points. Within 5 = partial. Within 10 = small points."
    ),
    "mastermind": (
        "**Mastermind**\n"
        "Crack a secret code by deduction.\n\n"
        "**How to play:** A secret 4-color code is generated. Each turn, guess a combination. "
        "You get feedback: correct color in correct position, or correct color in wrong position. "
        "Use logic to narrow it down.\n\n"
        "**Scoring:** Fewer guesses = better score. Compete to crack it first.\n"
        "**Strategy:** First guess should maximize information. Use process of elimination."
    ),
    "geography": (
        "**Speed Geography**\n"
        "Test your world knowledge across multiple modes!\n\n"
        "**Modes:** Country Capitals, US State Capitals, Country Flags, US State Flags, "
        "Landmarks (guess the country from a photo), or Mixed.\n\n"
        "**How to play:** A question is shown. Type your answer as fast as you can. "
        "First correct answer wins the round. First to 3 round wins takes the game.\n\n"
        "**Scoring:** Points for speed. Spelling must be close (fuzzy matching).\n"
        "**Tip:** Brush up on obscure capitals and world landmarks!"
    ),
    "wordle": (
        "**Wordle Race**\n"
        "Guess the 5-letter word before your opponents.\n\n"
        "**How to play:** Everyone guesses the same hidden word. After each guess, letters are "
        "colored: green = right letter, right spot | yellow = right letter, wrong spot | "
        "gray = not in the word. First to solve it wins.\n\n"
        "**Rounds:** 6 guesses max per player.\n"
        "**Strategy:** Start with vowel-heavy words (CRANE, SLATE, ADIEU)."
    ),
    "nba-trivia": (
        "**NBA Roster Trivia**\n"
        "Identify which NBA team a player belongs to.\n\n"
        "**How to play:** An NBA player's name is shown. Type the team name or abbreviation "
        "as fast as you can. First correct answer wins the round.\n\n"
        "**Scoring:** Points for speed across multiple rounds.\n"
        "**Tip:** Keep up with trades and free agency."
    ),
    "nfl-trivia": (
        "**NFL Roster Trivia**\n"
        "Identify which NFL team a player belongs to.\n\n"
        "**How to play:** An NFL player's name is shown. Type the team name or abbreviation "
        "as fast as you can. First correct answer wins the round.\n\n"
        "**Scoring:** Points for speed across multiple rounds.\n"
        "**Tip:** Know your depth charts."
    ),
    "sudoku": (
        "**Sudoku Sprint** (4x4)\n"
        "Fill the grid fastest to win.\n\n"
        "**How to play:** A partially-filled 4x4 Sudoku grid is shown. Fill in the missing "
        "numbers (1-4) so each row, column, and 2x2 box contains all digits. First to submit "
        "a correct solution wins.\n\n"
        "**Tip:** Start with rows/columns/boxes that have the most clues."
    ),
    "nbasim": (
        "**NBA Sim**\n"
        "Bet on a simulated NBA game.\n\n"
        "**How to play:** A matchup is generated with a spread, moneyline, and total. "
        "Place bets on any market. The game simulates quarter by quarter with live scoring. "
        "Bets resolve against the final score.\n\n"
        "**Markets:** Spread | Moneyline | Over/Under\n"
        "**Payouts:** Standard sportsbook odds. Pushes refunded."
    ),
    "nflsim": (
        "**NFL Sim**\n"
        "Bet on a simulated NFL game.\n\n"
        "**How to play:** A matchup is generated with a spread, moneyline, and total. "
        "Place bets on any market. The game simulates quarter by quarter with live scoring. "
        "Bets resolve against the final score.\n\n"
        "**Markets:** Spread | Moneyline | Over/Under\n"
        "**Payouts:** Standard sportsbook odds. Pushes refunded."
    ),
    "mlbsim": (
        "**MLB Sim**\n"
        "Bet on a simulated MLB game.\n\n"
        "**How to play:** A matchup is generated with a run line, moneyline, and total. "
        "Place bets on any market. The game simulates inning by inning with live scoring. "
        "Bets resolve against the final score.\n\n"
        "**Markets:** Run Line | Moneyline | Over/Under\n"
        "**Payouts:** Standard sportsbook odds. Pushes refunded."
    ),
    "soccersim": (
        "**Soccer Sim**\n"
        "Bet on a simulated soccer match.\n\n"
        "**How to play:** A matchup is generated between two teams with attack, midfield, "
        "defense, and goalkeeper ratings. Pick home or away and bet coins. The match simulates "
        "half by half with goals, cards, and subs. Draws are possible (nobody wins).\n\n"
        "**Tournament mode:** `/soccersim-tournament` runs an 8-team mini cup with group stage "
        "and knockout rounds. Bet on which team wins the whole thing.\n\n"
        "**Payouts:** Based on pre-game win probability. Tournament payouts based on team strength."
    ),
    "figgie": (
        "**Figgie** (Jane Street trading game)\n"
        "Deduce the hidden goal suit and trade to accumulate it.\n\n"
        "**Setup:** 40 cards across 4 suits (\u2660\u2665\u2666\u2663) with an *uneven* distribution: "
        "one suit has 12 cards (the **common suit**), one has 10 (the **goal suit**), and two have 8. "
        "The goal suit is always the same-colour partner of the common suit.\n\n"
        "**How to play:** Each player is dealt cards and starts with 200 trading chips. "
        "Over 6 rounds (45s each), post Buy or Sell orders on the order book. "
        "When a buy price meets a sell price, the trade executes instantly. "
        "Use your hand, the trades, and the order book to deduce which suit is the goal.\n\n"
        "**Scoring:** At game end the goal suit is revealed. "
        "Score = remaining chips + 10 \u00d7 goal-suit cards. Highest score wins the pot.\n\n"
        "**Key insight:** If you see lots of one suit, its same-colour partner is likely the goal!"
    ),
    "pokemon": (
        "**Who's That Pokemon?**\n"
        "Guess the Pokemon from progressive hints!\n\n"
        "**How to play:** Join the table and place your bet. Each round, hints about a "
        "mystery Pokemon are revealed over 30 seconds:\n"
        "\u2022 **0s:** Type(s) and generation\n"
        "\u2022 **10s:** A descriptive clue\n"
        "\u2022 **20s:** First letter and name length\n\n"
        "Type the Pokemon's name in chat \u2014 first correct answer wins the round!\n\n"
        "**Scoring:** First to 3 round wins takes the pot. "
        "Payouts follow the paytable based on player count.\n\n"
        "**Categories:** All Generations, Gen 1 (Kanto), Gen 2\u20134, Gen 5+, "
        "or Legendary & Mythical only.\n"
        "**Tip:** Earlier guesses are harder but more impressive. "
        "The Pokemon's artwork is revealed after each round!"
    ),
    "mathsprint": (
        "**Mental Math Sprint**\n"
        "10 rapid-fire arithmetic problems. Fastest correct answer wins each point.\n\n"
        "**How to play:** Join the table and place your bet. Each round presents a math problem "
        "(multiplication, division, percentages, squares, cubes, powers, roots, remainders, "
        "combinations, GCD, factorials). Click **Answer** and type the number. "
        "First correct answer wins 1 point. Wrong answers can retry!\n\n"
        "**Scoring:** After 10 problems, most points wins. "
        "Ties broken by total solve time (faster wins). "
        "Payouts follow the paytable based on player count.\n\n"
        "**Tip:** You have 20 seconds per problem. Speed and accuracy both matter!"
    ),
    "solitaire-chess": (
        "**Solitaire Chess**\n"
        "A 4\u00d74 board puzzle with chess pieces. Every move must be a capture.\n\n"
        "**How to play:** Join the table, place your bet, and the host picks a difficulty. "
        "A puzzle board is generated with chess pieces. Click **Move** and type coordinates "
        "(e.g. `A1 C3`) to capture one piece with another. Every move must be a capture \u2014 "
        "no non-capture moves allowed. Reduce the board to exactly **1 piece** to win.\n\n"
        "**Pieces:** King (1 sq any dir) | Queen (any dist, any dir) | Rook (straight lines) | "
        "Bishop (diagonals) | Knight (L-shape, jumps) | Pawn (1 sq diagonal, any direction)\n\n"
        "**Difficulty:** Easy (4 pieces) | Medium (5) | Hard (6) | Expert (7)\n"
        "**Tools:** Undo (take back moves) | Hint (suggested move) | Give Up\n"
        "**Multiplayer:** Everyone races on the same puzzle. First to solve wins the pot!"
    ),
    "valorant": (
        "**Valorant Guess**\n"
        "Guess the Valorant agent, weapon, or map from progressive hints!\n\n"
        "**How to play:** Join the table and place your bet. Each round, hints about a "
        "mystery Valorant item are revealed over 30 seconds:\n"
        "\u2022 **0s:** Type (agent/weapon/map) + role/class/location\n"
        "\u2022 **10s:** A descriptive clue\n"
        "\u2022 **20s:** First letter and name length\n\n"
        "Type your answer in chat \u2014 first correct answer wins the round!\n\n"
        "**Scoring:** First to 3 round wins takes the pot. "
        "Payouts follow the paytable based on player count.\n\n"
        "**Categories:** Agents, Weapons, Maps, or Everything mixed.\n"
        "**Tip:** Pay attention to the role and origin \u2014 they narrow it down fast!"
    ),
    "minesweeper": (
        "**Minesweeper Race** (9\u00d79, 10 mines)\n"
        "Race to clear a minesweeper board before your opponents!\n\n"
        "**How to play:** Everyone gets the same mine layout. Click cells to reveal them \u2014 "
        "numbers show how many adjacent mines. Zeros auto-expand (flood fill). "
        "Right-click to place a flag (optional, visual only).\n\n"
        "**Mine hit:** Click a mine and you're eliminated from that round.\n"
        "**Win condition:** First to reveal all 71 safe cells wins the round.\n"
        "**Scoring:** First to 3 round wins takes the pot. 120s time limit per round.\n"
        "**Tip:** Speed matters \u2014 don't waste time flagging, just reveal safe cells!"
    ),
    "sequence": (
        "**Sequence** (5 rounds, 30s each)\n"
        "Guess the next number in a mathematical sequence!\n\n"
        "**How to play:** Each round shows 4\u20136 terms of a sequence (e.g. 2, 6, 12, 20, ?). "
        "Click **Answer** to submit your guess via modal. First correct answer = 3 pts, "
        "second = 2 pts, third = 1 pt.\n\n"
        "**Win condition:** Most points after 5 rounds wins the pot.\n"
        "**Sequences:** Arithmetic, geometric, Fibonacci, primes, squares, cubes, triangular, "
        "and more tricky patterns.\n"
        "**Tip:** Look at the differences between terms \u2014 are they constant, growing, or alternating?"
    ),
    "prisoner": (
        "**Prisoner's Dilemma** (10 rounds)\n"
        "The classic game theory experiment \u2014 cooperate or betray?\n\n"
        "**How to play:** Each round, all players simultaneously choose Cooperate or Defect. "
        "Payoffs are computed pairwise:\n"
        "\u2022 Both Cooperate: 3/3 pts\n"
        "\u2022 You Defect, They Cooperate: 5/0 pts\n"
        "\u2022 Both Defect: 1/1 pts\n\n"
        "**Win condition:** Highest total score after 10 rounds.\n"
        "**Solo:** Play against a tit-for-tat bot.\n"
        "**Tip:** Cooperation is optimal long-term, but can you resist the temptation to defect?"
    ),
    "indian-poker": (
        "**Indian Poker** (sit-and-go)\n"
        "Everyone sees everyone else's card \u2014 but not their own!\n\n"
        "**How to play:** Each player gets one card face-up on their 'forehead'. "
        "Click **View Cards** to see opponents' cards. Bet based on what you think you have. "
        "Check, bet, call, raise, or fold \u2014 standard poker actions.\n\n"
        "**Cards:** Standard deck, ranked A(high) to 2(low). Suits break ties.\n"
        "**Blinds:** Escalate every 5 hands (10/20 \u2192 25/50 \u2192 50/100 \u2192 100/200).\n"
        "**Win condition:** Last player with chips, or most chips after 30 hands.\n"
        "**Tip:** If everyone else has weak cards, you probably have a strong one!"
    ),
    "battleship": (
        "**Battleship** (10\u00d710 grid, 1v1)\n"
        "Sink the enemy fleet before they sink yours!\n\n"
        "**How to play:** Place 5 ships on your grid using the select menus (or click Random). "
        "Then take turns firing at coordinates. Hit all cells of a ship to sink it.\n\n"
        "**Ships:** Carrier (5), Battleship (4), Cruiser (3), Submarine (3), Destroyer (2)\n"
        "**Solo:** Play against Captain Bot (hunt/target AI).\n"
        "**Tip:** Use a checkerboard pattern to find ships efficiently, "
        "then target adjacent cells on a hit!"
    ),
}

CASINO_GAMES: list[tuple[str, str, str, str]] = [
    # (command, description, category, mode)
    # ── Card Games
    ("blackjack", "Blackjack table", "Card Games", "duo"),
    ("baccarat", "Baccarat card game", "Card Games", "solo"),
    ("paigow", "Pai Gow Poker", "Card Games", "duo"),
    ("uth", "Ultimate Texas Hold'em", "Card Games", "solo"),
    ("videopoker", "Video Poker (Jacks or Better)", "Card Games", "solo"),
    ("hilo", "Hi-Lo card guessing game", "Card Games", "solo"),
    ("figgie", "Figgie \u2014 Jane Street trading game", "Card Games", "party"),
    ("indian-poker", "Indian Poker sit-and-go", "Card Games", "party"),
    # ── Table & Arcade
    ("roulette", "American roulette", "Table & Arcade", "solo"),
    ("craps", "Craps with full side bets", "Table & Arcade", "solo"),
    ("crash", "Crash rocket game", "Table & Arcade", "solo"),
    ("plinko", "Plinko ball-drop game", "Table & Arcade", "solo"),
    ("slots", "Fortune Reels \u2014 slots & bonus rounds", "Table & Arcade", "solo"),
    # ── Party Games
    ("bingo", "Bingo", "Party Games", "party"),
    ("horserace", "Horse racing with betting", "Party Games", "party"),
    ("stockmarket", "Stock market investment game", "Party Games", "party"),
    ("liarsdice", "Liar's Dice bluffing game", "Party Games", "duo"),
    ("penalties", "1v1 Penalty Shootout duel", "Party Games", "duo"),
    ("tictactoe", "1v1 Tic Tac Toe with coin wagers", "Party Games", "duo"),
    ("rps", "1v1 Rock Paper Scissors duel", "Party Games", "duo"),
    ("prisoner", "Prisoner's Dilemma tournament", "Party Games", "party"),
    # ── Brain Games
    ("math24", "Make 24 from four numbers", "Brain Games", "party"),
    ("countdown", "Countdown numbers math game", "Brain Games", "party"),
    ("mastermind", "Code-breaking deduction game", "Brain Games", "duo"),
    ("geography", "Speed Geography \u2014 capitals, flags & landmarks", "Brain Games", "party"),
    ("wordle", "Wordle Race \u2014 guess the word first", "Brain Games", "party"),
    ("nba-trivia", "NBA Roster Trivia \u2014 name the team first", "Brain Games", "party"),
    ("nfl-trivia", "NFL Roster Trivia \u2014 name the team first", "Brain Games", "party"),
    ("sudoku", "Sudoku Sprint \u2014 fill the 4\u00d74 grid fastest", "Brain Games", "party"),
    ("stockguess", "Guess a stock's YTD % change \u2014 closest wins!", "Brain Games", "party"),
    ("mathsprint", "Mental Math Sprint \u2014 10 rapid-fire problems", "Brain Games", "party"),
    ("pokemon", "Who's That Pokemon? \u2014 guess from hints", "Brain Games", "party"),
    ("quizbowl", "Quiz Bowl \u2014 3-part bonus trivia", "Brain Games", "party"),
    ("valorant", "Guess the Valorant agent, weapon, or map", "Brain Games", "party"),
    ("solitaire-chess", "Solitaire Chess \u2014 capture until 1 remains", "Brain Games", "party"),
    ("nba", "NBA Player Guess \u2014 name the player from career teams", "Brain Games", "party"),
    ("minesweeper", "Minesweeper Race \u2014 clear the board fastest", "Brain Games", "party"),
    ("sequence", "Guess the next number in the sequence", "Brain Games", "party"),
    ("battleship", "Battleship \u2014 sink the enemy fleet", "Brain Games", "duo"),
    ("blotto", "Colonel Blotto \u2014 deploy armies across battlefields", "Brain Games", "party"),
    # ── Sports Sim
    ("nbasim", "Simulated NBA game betting", "Sports Sim", "solo"),
    ("nflsim", "Simulated NFL game betting", "Sports Sim", "solo"),
    ("mlbsim", "Simulated MLB game betting", "Sports Sim", "solo"),
    ("tennissim", "Simulated tennis match betting", "Sports Sim", "solo"),
    ("soccersim", "Simulated soccer match betting", "Sports Sim", "solo"),
]


# ── Paginated /games ─────────────────────────────────────────────────────────


def _games_page_embed(page: int, total_pages: int) -> discord.Embed:
    cat_name, cat_emoji = GAME_CATEGORIES[page - 1]
    games = [(n, d, m) for n, d, cat, m in CASINO_GAMES if cat == cat_name]

    embed = discord.Embed(
        title=f"{cat_emoji} {cat_name}",
        colour=0xF1C40F,
    )
    lines = []
    for name, desc, mode in games:
        tag = MODE_EMOJI.get(mode, "")
        lines.append(f"{tag} ` /{name} ` \u2014 {desc}")
    embed.description = "\n".join(lines)
    embed.set_footer(
        text=(
            f"Page {page}/{total_pages} \u00b7 "
            f"{len(CASINO_GAMES)} games \u00b7 "
            f"\U0001f464 Solo  \u2694\ufe0f Duo  \U0001f389 Party"
        ),
    )
    return embed


class GamesView(discord.ui.View):
    def __init__(self, page: int = 1) -> None:
        super().__init__(timeout=120)
        self.page = page
        self.total_pages = len(GAME_CATEGORIES)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.prev_btn.disabled = self.page <= 1
        self.next_btn.disabled = self.page >= self.total_pages

    @discord.ui.button(label="\u25c0 Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        self.page = max(1, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=_games_page_embed(self.page, self.total_pages), view=self,
        )

    @discord.ui.button(label="Next \u25b6", style=discord.ButtonStyle.secondary)
    async def next_btn(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        self.page = min(self.total_pages, self.page + 1)
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=_games_page_embed(self.page, self.total_pages), view=self,
        )




async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CasinoCog(bot))
