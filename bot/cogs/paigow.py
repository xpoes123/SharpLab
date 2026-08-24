"""Pai Gow Poker cog — multiplayer /paigow table."""
import itertools
import random
from collections import Counter
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
import logging

log = logging.getLogger(__name__)
# ── Card helpers ──────────────────────────────────────────────────────────────

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
RANK_VAL: dict[str, int] = {r: i for i, r in enumerate(RANKS, 2)}  # 2..14
RANK_NAME: dict[int, str] = {v: k for k, v in RANK_VAL.items()}
JOKER = "JK"
MAX_PLAYERS = 6


def _new_deck() -> list[str]:
    """53-card deck: 52 normal + 1 joker, shuffled."""
    cards = [f"{r}{s}" for s in SUITS for r in RANKS]
    cards.append(JOKER)
    random.shuffle(cards)
    return cards


def _rank(card: str) -> str:
    if card == JOKER:
        return JOKER
    return card[:-1]


def _suit(card: str) -> str:
    if card == JOKER:
        return ""
    return card[-1]


def _rank_val(card: str) -> int:
    return RANK_VAL.get(_rank(card), 14)  # joker defaults to Ace value


def _is_joker(card: str) -> bool:
    return card == JOKER


def _fmt_card(card: str) -> str:
    if card == JOKER:
        return "`🃏`"
    return f"`{card}`"


def _fmt_hand(hand: list[str]) -> str:
    return " ".join(_fmt_card(c) for c in hand)


def _sort_for_display(cards: list[str]) -> list[str]:
    """Sort cards by rank descending for display."""
    return sorted(cards, key=lambda c: _rank_val(c), reverse=True)


# ── 5-card poker hand evaluation ─────────────────────────────────────────────

def _evaluate_5_no_joker(cards: list[str]) -> tuple[int, ...]:
    """Evaluate a 5-card hand with no joker. Returns a comparable tuple."""
    ranks = sorted([_rank_val(c) for c in cards], reverse=True)
    suits = [_suit(c) for c in cards]
    is_flush = len(set(suits)) == 1

    unique = sorted(set(ranks), reverse=True)
    is_straight = False
    high = ranks[0]

    if len(unique) == 5:
        if unique[0] - unique[4] == 4:
            is_straight = True
            high = unique[0]
        elif unique == [14, 5, 4, 3, 2]:  # ace-low straight
            is_straight = True
            high = 5

    counts = Counter(ranks)
    freq = sorted(counts.values(), reverse=True)

    if is_straight and is_flush:
        return (9, high)  # straight flush (royal = high 14)
    if freq == [4, 1]:
        quad = [r for r, c in counts.items() if c == 4][0]
        kick = [r for r, c in counts.items() if c == 1][0]
        return (7, quad, kick)
    if freq == [3, 2]:
        trip = [r for r, c in counts.items() if c == 3][0]
        pair = [r for r, c in counts.items() if c == 2][0]
        return (6, trip, pair)
    if is_flush:
        return (5,) + tuple(ranks)
    if is_straight:
        return (4, high)
    if freq == [3, 1, 1]:
        trip = [r for r, c in counts.items() if c == 3][0]
        kicks = sorted([r for r, c in counts.items() if c == 1], reverse=True)
        return (3, trip) + tuple(kicks)
    if freq == [2, 2, 1]:
        pairs = sorted([r for r, c in counts.items() if c == 2], reverse=True)
        kick = [r for r, c in counts.items() if c == 1][0]
        return (2, pairs[0], pairs[1], kick)
    if freq == [2, 1, 1, 1]:
        pair = [r for r, c in counts.items() if c == 2][0]
        kicks = sorted([r for r, c in counts.items() if c == 1], reverse=True)
        return (1, pair) + tuple(kicks)
    return (0,) + tuple(ranks)


# All 52 normal cards for joker substitution
_ALL_CARDS = [f"{r}{s}" for s in SUITS for r in RANKS]


def _evaluate_5(cards: list[str]) -> tuple[int, ...]:
    """Evaluate a 5-card hand, handling joker by trying all substitutions."""
    joker_indices = [i for i, c in enumerate(cards) if _is_joker(c)]
    if not joker_indices:
        return _evaluate_5_no_joker(cards)

    # Joker present — try all 52 possible substitutions
    idx = joker_indices[0]
    other_cards = {c for i, c in enumerate(cards) if i != idx}
    best = (0,)
    for sub in _ALL_CARDS:
        if sub in other_cards:
            continue  # can't duplicate a card already in hand
        trial = list(cards)
        trial[idx] = sub
        score = _evaluate_5_no_joker(trial)
        if score > best:
            best = score
    # Five aces: 4 aces + joker
    ace_count = sum(1 for c in cards if _rank(c) == "A")
    if ace_count == 4:
        return (10,)  # five aces — highest possible
    return best


def _evaluate_2(cards: list[str]) -> tuple[int, ...]:
    """Evaluate a 2-card low hand. Joker = Ace."""
    vals = sorted([_rank_val(c) for c in cards], reverse=True)
    # Joker is already valued at 14 (Ace) via _rank_val
    if vals[0] == vals[1]:
        return (1, vals[0])  # pair
    return (0, vals[0], vals[1])


# ── Hand name strings ────────────────────────────────────────────────────────

_RANK_WORD: dict[int, str] = {
    2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven",
    8: "Eight", 9: "Nine", 10: "Ten", 11: "Jack", 12: "Queen", 13: "King", 14: "Ace",
}
_RANK_PLURAL: dict[int, str] = {
    2: "Twos", 3: "Threes", 4: "Fours", 5: "Fives", 6: "Sixes", 7: "Sevens",
    8: "Eights", 9: "Nines", 10: "Tens", 11: "Jacks", 12: "Queens", 13: "Kings", 14: "Aces",
}


def _hand_name_5(score: tuple[int, ...]) -> str:
    tier = score[0]
    if tier == 10:
        return "Five Aces"
    if tier == 9:
        return "Royal Flush" if score[1] == 14 else f"Straight Flush ({_RANK_WORD[score[1]]}-high)"
    if tier == 7:
        return f"Four {_RANK_PLURAL[score[1]]}"
    if tier == 6:
        return f"Full House ({_RANK_PLURAL[score[1]]} over {_RANK_PLURAL[score[2]]})"
    if tier == 5:
        return f"Flush ({_RANK_WORD[score[1]]}-high)"
    if tier == 4:
        return f"Straight ({_RANK_WORD[score[1]]}-high)"
    if tier == 3:
        return f"Three {_RANK_PLURAL[score[1]]}"
    if tier == 2:
        return f"Two Pair ({_RANK_PLURAL[score[1]]} and {_RANK_PLURAL[score[2]]})"
    if tier == 1:
        return f"Pair of {_RANK_PLURAL[score[1]]}"
    return f"{_RANK_WORD[score[1]]}-high"


def _hand_name_2(score: tuple[int, ...]) -> str:
    if score[0] == 1:
        return f"Pair of {_RANK_PLURAL[score[1]]}"
    return f"{_RANK_WORD[score[1]]}-{_RANK_WORD[score[2]]}"


# ── House way (brute-force optimal split) ────────────────────────────────────

def _valid_setting(high: list[str], low: list[str]) -> bool:
    """High hand must rank at least as high as low hand."""
    return _evaluate_5(high) >= _evaluate_2(low)


def _house_way(cards: list[str]) -> tuple[list[str], list[str]]:
    """Set 7 cards into (high_5, low_2) using optimal split."""
    best_score: tuple[tuple[int, ...], tuple[int, ...]] | None = None
    best_split: tuple[list[str], list[str]] | None = None

    for combo in itertools.combinations(range(7), 2):
        low_idx = set(combo)
        low = [cards[i] for i in combo]
        high = [cards[i] for i in range(7) if i not in low_idx]

        if not _valid_setting(high, low):
            continue

        lo_score = _evaluate_2(low)
        hi_score = _evaluate_5(high)
        # Primary: maximise 5-card tier; secondary: maximise 2-card hand;
        # tertiary: maximise 5-card kickers.  This ensures that when all valid
        # splits share the same 5-card tier (e.g. all ace-high no-pair), the
        # algorithm picks the split with the strongest low hand rather than
        # greedily hoarding high kickers in the 5-card hand.
        score = (hi_score[0], lo_score, hi_score)

        if best_score is None or score > best_score:
            best_score = score
            best_split = (high, low)

    # Fallback: should never happen with 7 cards, but just in case
    if best_split is None:
        return cards[:5], cards[5:]
    return best_split


# ── Fortune Bonus ────────────────────────────────────────────────────────────

# Payout = TOTAL-return multiple of the Fortune stake (e.g. 5 → get 5× back).
# Calibrated to THIS engine's best-5-of-7 frequencies, where the joker is FULLY
# wild (_evaluate_5 tries all 52 subs), which roughly doubles textbook 7-card
# hand rates. At those rates the old textbook paytable ran ~+13% for the player;
# this table holds a ~5-6% house edge. Five Aces / Royal / Straight Flush are
# handled explicitly in _fortune_payout (tier 8 never occurs: SF=9, quads=7).
# ponytail: rates come from the wild-joker evaluator, not book odds — recalibrate
# against _best_5_from_7 if the joker rule or evaluator changes.
FORTUNE_TABLE: list[tuple[int, int, str]] = [
    # (min_tier, payout_multiplier, label) — highest tier first
    (7, 20, "Four of a Kind"),
    (6, 5, "Full House"),
    (5, 4, "Flush"),
    (4, 2, "Straight"),
    (3, 2, "Three of a Kind"),
]


def _best_5_from_7(cards: list[str]) -> tuple[int, ...]:
    """Best possible 5-card hand from 7 cards."""
    best = (0,)
    for combo in itertools.combinations(range(7), 5):
        hand = [cards[i] for i in combo]
        score = _evaluate_5(hand)
        if score > best:
            best = score
    return best


def _fortune_payout(cards: list[str], bet: int) -> tuple[int, str]:
    """Returns (total_return, label). Return is bet × multiplier (0 if no qualifying hand)."""
    score = _best_5_from_7(cards)
    tier = score[0]

    if tier == 10:
        return bet * 400, "Five Aces"
    if tier == 9:  # straight flush; royal = high 14
        return (bet * 150, "Royal Flush") if score[1] == 14 else (bet * 50, "Straight Flush")

    for min_tier, mult, label in FORTUNE_TABLE:
        if tier >= min_tier:
            return bet * mult, label

    return 0, ""


# ── Game state ───────────────────────────────────────────────────────────────


@dataclass
class PaiGowSeat:
    user_id: int
    display_name: str
    wager: int
    fortune_bet: int = 0
    cards: list[str] = field(default_factory=list)
    selected: list[int] = field(default_factory=list)
    is_set: bool = False
    player_high: list[str] = field(default_factory=list)
    player_low: list[str] = field(default_factory=list)
    outcome: str = ""  # win | lose | push
    main_payout: int = 0
    fortune_win: int = 0
    fortune_label: str = ""

    @property
    def total_wager(self) -> int:
        return self.wager + self.fortune_bet


@dataclass
class PaiGowTable:
    channel_id: int
    opener_id: int
    opener_name: str
    phase: str = "betting"  # betting | setting | finished
    players: dict[int, PaiGowSeat] = field(default_factory=dict)
    dealer_cards: list[str] = field(default_factory=list)
    dealer_high: list[str] = field(default_factory=list)
    dealer_low: list[str] = field(default_factory=list)
    message: discord.Message | None = None
    round_num: int = 1
    last_bets: dict[int, tuple[str, int, int]] = field(default_factory=dict)

    def all_set(self) -> bool:
        return all(p.is_set for p in self.players.values())


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: PaiGowTable) -> discord.Embed:
    embed = discord.Embed(
        title=f"Pai Gow Poker — Place Your Bets (Round {table.round_num})",
        description="Join the table, then the dealer deals!",
        colour=discord.Colour.blurple(),
    )
    if table.players:
        lines = []
        for seat in table.players.values():
            line = f"🃏 **{seat.display_name}** — {seat.wager}c"
            if seat.fortune_bet > 0:
                line += f" + 🎰 Fortune {seat.fortune_bet}c"
            lines.append(line)
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Players", value="*No players yet — click Join!*", inline=False,
        )
    embed.set_footer(text=f"Dealer: {table.opener_name} | Max {MAX_PLAYERS} players")
    return embed


def _setting_embed(table: PaiGowTable) -> discord.Embed:
    embed = discord.Embed(
        title=f"Pai Gow Poker — Set Your Hands (Round {table.round_num})",
        colour=discord.Colour.blurple(),
    )
    dealer_sorted = _sort_for_display(table.dealer_cards)
    embed.add_field(
        name="🏠 Dealer's Cards",
        value=_fmt_hand(dealer_sorted),
        inline=False,
    )
    lines = []
    for seat in table.players.values():
        if seat.is_set:
            hi_score = _evaluate_5(seat.player_high)
            lo_score = _evaluate_2(seat.player_low)
            lines.append(
                f"✅ **{seat.display_name}** ({seat.wager}c)\n"
                f"  High: {_hand_name_5(hi_score)} · Low: {_hand_name_2(lo_score)}"
            )
        else:
            lines.append(f"⏳ **{seat.display_name}** ({seat.wager}c) — Setting...")
    embed.add_field(name="Players", value="\n".join(lines), inline=False)
    embed.set_footer(
        text="Click 'Set My Hand' to pick your cards, or 'House Way' for auto-set.",
    )
    return embed


def _finished_embed(
    table: PaiGowTable, *, balances: dict[int, int],
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Pai Gow Poker — Round {table.round_num} Complete",
        colour=discord.Colour.gold(),
    )
    d_hi = _evaluate_5(table.dealer_high)
    d_lo = _evaluate_2(table.dealer_low)
    embed.add_field(
        name="🏠 Dealer",
        value=(
            f"High: {_fmt_hand(_sort_for_display(table.dealer_high))} — {_hand_name_5(d_hi)}\n"
            f"Low: {_fmt_hand(_sort_for_display(table.dealer_low))} — {_hand_name_2(d_lo)}"
        ),
        inline=False,
    )

    # One field per player to stay within 1024-char field limit
    for seat in table.players.values():
        p_hi = _evaluate_5(seat.player_high)
        p_lo = _evaluate_2(seat.player_low)
        hi_win = p_hi > d_hi
        lo_win = p_lo > d_lo
        hi_mark = "✅" if hi_win else "❌"
        lo_mark = "✅" if lo_win else "❌"
        emoji = {"win": "🟢", "lose": "🔴", "push": "⚪"}.get(seat.outcome, "⚪")
        label = {"win": "Win!", "lose": "Lose", "push": "Push"}.get(seat.outcome, "?")

        total_payout = seat.main_payout + seat.fortune_win
        net = total_payout - seat.total_wager
        sign = "+" if net > 0 else ""
        bal = balances.get(seat.user_id, 0)

        value = (
            f"High: {_fmt_hand(_sort_for_display(seat.player_high))} — {_hand_name_5(p_hi)} {hi_mark}\n"
            f"Low: {_fmt_hand(_sort_for_display(seat.player_low))} — {_hand_name_2(p_lo)} {lo_mark}"
        )
        if seat.fortune_bet > 0:
            if seat.fortune_win > 0:
                value += f"\n🎰 Fortune: {seat.fortune_label}! +{seat.fortune_win}c"
            else:
                value += f"\n🎰 Fortune: No qualifying hand"
        value += f"\n→ **{sign}{net}c** (bal: {bal}c)"

        embed.add_field(
            name=f"{emoji} {seat.display_name} ({seat.wager}c) — {label}",
            value=value,
            inline=False,
        )

    embed.set_footer(
        text=f"Dealer: {table.opener_name} | Click New Round to continue.",
    )
    return embed


def _hand_setting_embed(seat: PaiGowSeat, dealer_cards: list[str]) -> discord.Embed:
    """Embed for the ephemeral hand-setting view."""
    embed = discord.Embed(
        title="Pai Gow Poker — Set Your Hands",
        colour=discord.Colour.blurple(),
    )
    dealer_sorted = _sort_for_display(dealer_cards)
    embed.add_field(
        name="🏠 Dealer's Cards",
        value=_fmt_hand(dealer_sorted),
        inline=False,
    )
    embed.add_field(
        name="Your Cards",
        value=_fmt_hand(seat.cards),
        inline=False,
    )

    if len(seat.selected) == 2:
        low = [seat.cards[i] for i in seat.selected]
        high = [seat.cards[i] for i in range(7) if i not in seat.selected]
        lo_score = _evaluate_2(low)
        hi_score = _evaluate_5(high)
        valid = _valid_setting(high, low)
        status = "" if valid else " ⚠️ *foul*"
        embed.add_field(
            name="Low Hand (2 cards)",
            value=f"{_fmt_hand(low)} — {_hand_name_2(lo_score)}{status}",
            inline=False,
        )
        embed.add_field(
            name="High Hand (5 cards)",
            value=f"{_fmt_hand(high)} — {_hand_name_5(hi_score)}",
            inline=False,
        )
    else:
        picked = len(seat.selected)
        embed.add_field(
            name="Low Hand",
            value=f"Pick {2 - picked} more card{'s' if 2 - picked > 1 else ''}",
            inline=True,
        )

    bet_line = f"{seat.wager} coins"
    if seat.fortune_bet > 0:
        bet_line += f" (+{seat.fortune_bet} Fortune)"
    embed.add_field(name="Bet", value=bet_line, inline=True)
    embed.set_footer(
        text="Select 2 cards for your Low Hand, then Set Hands. Or click House Way.",
    )
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinPaiGowModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)", placeholder="e.g. 50",
        required=True, max_length=10,
    )

    def __init__(self, table: PaiGowTable, view: "PaiGowTableView") -> None:
        super().__init__(title="Join Pai Gow Table")
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
        self.table.players[uid] = PaiGowSeat(
            user_id=uid, display_name=interaction.user.display_name, wager=amt,
        )
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


class FortuneBetModal(ui.Modal):
    amount = ui.TextInput(
        label="Fortune Bonus bet (coins)", placeholder="e.g. 10",
        required=True, max_length=10,
    )

    def __init__(self, table: PaiGowTable, view: "PaiGowTableView") -> None:
        super().__init__(title="Fortune Bonus Side Bet")
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
        seat = self.table.players.get(uid)
        if seat is None:
            await interaction.response.send_message("Join the table first!", ephemeral=True)
            return
        if self.table.phase != "betting":
            await interaction.response.send_message("Cards already dealt!", ephemeral=True)
            return
        if seat.fortune_bet > 0:
            await interaction.response.send_message(
                "You already have a Fortune bet!", ephemeral=True,
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
        seat.fortune_bet = amt
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


# ── Ephemeral hand-setting view ─────────────────────────────────────────────


class CardSelectButton(ui.Button["HandSettingView"]):
    def __init__(self, card: str, index: int, selected: bool, row: int) -> None:
        label = "🃏" if _is_joker(card) else card
        style = discord.ButtonStyle.primary if selected else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, row=row)
        self.index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        view: HandSettingView = self.view  # type: ignore[assignment]
        seat = view.seat

        if interaction.user.id != seat.user_id:
            await interaction.response.send_message("Not your hand!", ephemeral=True)
            return

        if self.index in seat.selected:
            seat.selected.remove(self.index)
        elif len(seat.selected) < 2:
            seat.selected.append(self.index)
        else:
            # Already 2 selected — swap out the oldest
            seat.selected.pop(0)
            seat.selected.append(self.index)

        view.rebuild()
        embed = _hand_setting_embed(seat, view.table.dealer_cards)
        await interaction.response.edit_message(embed=embed, view=view)


class HandSettingView(ui.View):
    """Ephemeral view for a player to set their Pai Gow hand."""

    def __init__(
        self,
        table: PaiGowTable,
        seat: PaiGowSeat,
        table_view: "PaiGowTableView",
    ) -> None:
        super().__init__(timeout=120)
        self.table = table
        self.seat = seat
        self.table_view = table_view
        self._build()

    def _build(self) -> None:
        # Card buttons (row 0: first 5, row 1: last 2)
        for i, card in enumerate(self.seat.cards):
            selected = i in self.seat.selected
            row = 0 if i < 5 else 1
            self.add_item(CardSelectButton(card, i, selected, row))

        # Action buttons
        can_set = len(self.seat.selected) == 2
        if can_set:
            low = [self.seat.cards[i] for i in self.seat.selected]
            high = [self.seat.cards[i] for i in range(7) if i not in self.seat.selected]
            if not _valid_setting(high, low):
                can_set = False

        set_btn = ui.Button(
            label="Set Hands", style=discord.ButtonStyle.success,
            emoji="✅", row=2, disabled=not can_set,
        )
        set_btn.callback = self._set_hands
        self.add_item(set_btn)

        hw_btn = ui.Button(
            label="House Way", style=discord.ButtonStyle.secondary,
            emoji="🏠", row=2,
        )
        hw_btn.callback = self._house_way
        self.add_item(hw_btn)

    def rebuild(self) -> None:
        """Rebuild all buttons to reflect current selection state."""
        self.clear_items()
        self._build()

    async def _set_hands(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.seat.user_id:
            await interaction.response.send_message("Not your hand!", ephemeral=True)
            return
        if self.seat.is_set:
            await interaction.response.send_message("Already set!", ephemeral=True)
            return
        if len(self.seat.selected) != 2:
            await interaction.response.send_message(
                "Select exactly 2 cards for your low hand.", ephemeral=True,
            )
            return

        low = [self.seat.cards[i] for i in self.seat.selected]
        high = [self.seat.cards[i] for i in range(7) if i not in self.seat.selected]

        if not _valid_setting(high, low):
            await interaction.response.send_message(
                "Foul! Your high hand must rank higher than your low hand.",
                ephemeral=True,
            )
            return

        self.seat.player_high = high
        self.seat.player_low = low
        self.seat.is_set = True

        # Disable ephemeral view
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]

        lo_score = _evaluate_2(low)
        hi_score = _evaluate_5(high)
        embed = discord.Embed(
            title="Hands Set! ✅",
            description=(
                f"**High:** {_fmt_hand(_sort_for_display(high))} — {_hand_name_5(hi_score)}\n"
                f"**Low:** {_fmt_hand(_sort_for_display(low))} — {_hand_name_2(lo_score)}"
            ),
            colour=discord.Colour.green(),
        )
        await interaction.response.edit_message(embed=embed, view=self)

        # Update main table
        await self._sync_main_table()

    async def _house_way(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.seat.user_id:
            await interaction.response.send_message("Not your hand!", ephemeral=True)
            return
        if self.seat.is_set:
            await interaction.response.send_message("Already set!", ephemeral=True)
            return

        high, low = _house_way(self.seat.cards)
        self.seat.player_high = high
        self.seat.player_low = low
        self.seat.is_set = True

        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]

        lo_score = _evaluate_2(low)
        hi_score = _evaluate_5(high)
        embed = discord.Embed(
            title="Hands Set (House Way)! ✅",
            description=(
                f"**High:** {_fmt_hand(_sort_for_display(high))} — {_hand_name_5(hi_score)}\n"
                f"**Low:** {_fmt_hand(_sort_for_display(low))} — {_hand_name_2(lo_score)}"
            ),
            colour=discord.Colour.green(),
        )
        await interaction.response.edit_message(embed=embed, view=self)

        # Update main table
        await self._sync_main_table()

    async def _sync_main_table(self) -> None:
        """Update the main table message after a player sets their hand."""
        if self.table.all_set():
            await self.table_view._resolve_all()
        elif self.table.message:
            self.table_view._update_buttons()
            try:
                await self.table.message.edit(
                    embed=_setting_embed(self.table), view=self.table_view,
                )
            except discord.HTTPException:
                pass


# ── Main table view ──────────────────────────────────────────────────────────


class PaiGowTableView(ui.View):
    def __init__(
        self, table: PaiGowTable, active_tables: dict[int, "PaiGowTable"],
    ) -> None:
        super().__init__(timeout=180)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        setting = phase == "setting"
        finished = phase == "finished"
        has_players = bool(self.table.players)

        # Row 0: Deal, Join, Re-bet, Leave
        self.deal_btn.disabled = not betting or not has_players
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = not betting

        # Row 1: Set My Hand, House Way
        self.set_hand_btn.disabled = not setting
        self.house_way_btn.disabled = not setting

        # Row 2: Fortune Bonus
        self.fortune_btn.disabled = not betting

        # Row 3: New Round, Close Table
        self.new_round_btn.disabled = not finished
        self.close_btn.disabled = False

    # ── Row 0: Deal, Join, Re-bet, Leave ─────────────────────

    @ui.button(label="Deal", style=discord.ButtonStyle.success, emoji="🃏", row=0)
    async def deal_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.opener_id:
            await interaction.response.send_message(
                "Only the table opener can deal!", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message("Already dealt!", ephemeral=True)
            return
        if not self.table.players:
            await interaction.response.send_message("No players yet!", ephemeral=True)
            return
        await self._deal(interaction)

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="🪑", row=0)
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Cards already dealt! Wait for next round.", ephemeral=True,
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
        await interaction.response.send_modal(JoinPaiGowModal(self.table, self))

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
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        name, wager, fortune = last
        total_cost = wager + fortune
        try:
            await queries.update_casino_balance(str(uid), -total_cost)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for {total_cost}c re-bet! (have {bal})",
                ephemeral=True,
            )
            return
        self.table.players[uid] = PaiGowSeat(
            user_id=uid, display_name=name, wager=wager, fortune_bet=fortune,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="🚪", row=0)
    async def leave_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        seat = self.table.players.get(uid)
        if seat is None:
            await interaction.response.send_message(
                "You're not at this table.", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Can't leave after cards are dealt!", ephemeral=True,
            )
            return
        # Opener leaving during betting = abort
        if uid == self.table.opener_id:
            await self._abort(interaction, "Dealer left — all bets refunded.")
            return
        await queries.update_casino_balance(str(uid), seat.total_wager)
        del self.table.players[uid]
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Row 1: Set My Hand, House Way ────────────────────────

    @ui.button(label="Set My Hand", style=discord.ButtonStyle.primary, emoji="✋", row=1)
    async def set_hand_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        seat = self.table.players.get(uid)
        if seat is None:
            await interaction.response.send_message(
                "You're not at this table!", ephemeral=True,
            )
            return
        if self.table.phase != "setting":
            await interaction.response.send_message(
                "Not in the setting phase!", ephemeral=True,
            )
            return
        if seat.is_set:
            await interaction.response.send_message(
                "You already set your hands!", ephemeral=True,
            )
            return
        # Reset selection for a fresh pick
        seat.selected.clear()
        view = HandSettingView(self.table, seat, self)
        embed = _hand_setting_embed(seat, self.table.dealer_cards)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @ui.button(label="House Way", style=discord.ButtonStyle.secondary, emoji="🏠", row=1)
    async def house_way_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        seat = self.table.players.get(uid)
        if seat is None:
            await interaction.response.send_message(
                "You're not at this table!", ephemeral=True,
            )
            return
        if self.table.phase != "setting":
            await interaction.response.send_message(
                "Not in the setting phase!", ephemeral=True,
            )
            return
        if seat.is_set:
            await interaction.response.send_message(
                "You already set your hands!", ephemeral=True,
            )
            return
        # Auto-set using house way
        high, low = _house_way(seat.cards)
        seat.player_high = high
        seat.player_low = low
        seat.is_set = True

        lo_score = _evaluate_2(low)
        hi_score = _evaluate_5(high)
        await interaction.response.send_message(
            f"**Hands set (House Way)!**\n"
            f"High: {_fmt_hand(_sort_for_display(high))} — {_hand_name_5(hi_score)}\n"
            f"Low: {_fmt_hand(_sort_for_display(low))} — {_hand_name_2(lo_score)}",
            ephemeral=True,
        )

        # Update main table
        if self.table.all_set():
            await self._resolve_all()
        elif self.table.message:
            self._update_buttons()
            try:
                await self.table.message.edit(
                    embed=_setting_embed(self.table), view=self,
                )
            except discord.HTTPException:
                pass

    # ── Row 2: Fortune Bonus ─────────────────────────────────

    @ui.button(
        label="Fortune Bonus", style=discord.ButtonStyle.success,
        emoji="🎰", row=2,
    )
    async def fortune_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if self.table.phase != "betting":
            await interaction.response.send_message("Cards already dealt!", ephemeral=True)
            return
        if uid not in self.table.players:
            await interaction.response.send_message("Join the table first!", ephemeral=True)
            return
        seat = self.table.players[uid]
        if seat.fortune_bet > 0:
            await interaction.response.send_message(
                "You already have a Fortune bet!", ephemeral=True,
            )
            return
        await interaction.response.send_modal(FortuneBetModal(self.table, self))

    # ── Row 3: New Round, Close Table ────────────────────────

    @ui.button(label="New Round", style=discord.ButtonStyle.success, emoji="▶️", row=3)
    async def new_round_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.opener_id:
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
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(label="Close Table", style=discord.ButtonStyle.danger, emoji="✖️", row=3)
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.opener_id:
            await interaction.response.send_message(
                "Only the table opener can close!", ephemeral=True,
            )
            return
        if self.table.phase in ("betting", "setting"):
            await self._abort(interaction, "Table closed by dealer. All bets refunded.")
        else:
            await self._close(interaction)

    # ── Deal logic ───────────────────────────────────────────

    async def _deal(self, interaction: discord.Interaction) -> None:
        table = self.table
        deck = _new_deck()

        # Deal 7 cards to each player, then 7 to dealer
        for seat in table.players.values():
            seat.cards = [deck.pop() for _ in range(7)]
            seat.selected.clear()
            seat.is_set = False
        table.dealer_cards = [deck.pop() for _ in range(7)]

        table.phase = "setting"
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_setting_embed(table), view=self,
        )

    # ── Resolve ──────────────────────────────────────────────

    async def _resolve_all(self) -> None:
        table = self.table
        if table.phase == "finished":
            return  # guard against double-resolve

        # Dealer sets house way
        table.dealer_high, table.dealer_low = _house_way(table.dealer_cards)
        d_hi = _evaluate_5(table.dealer_high)
        d_lo = _evaluate_2(table.dealer_low)

        balances: dict[int, int] = {}
        for seat in table.players.values():
            p_hi = _evaluate_5(seat.player_high)
            p_lo = _evaluate_2(seat.player_low)
            hi_win = p_hi > d_hi  # tie goes to dealer
            lo_win = p_lo > d_lo

            if hi_win and lo_win:
                seat.outcome = "win"
                seat.main_payout = seat.wager * 2  # original bet + 1:1 win
            elif hi_win or lo_win:
                seat.outcome = "push"
                seat.main_payout = seat.wager
            else:
                seat.outcome = "lose"
                seat.main_payout = 0

            # Fortune bonus
            if seat.fortune_bet > 0:
                seat.fortune_win, seat.fortune_label = _fortune_payout(
                    seat.cards, seat.fortune_bet,
                )

        # Mark finished and update buttons BEFORE DB calls so the table
        # can't get stuck if a DB call throws.
        table.phase = "finished"

        # Save last bets for re-bet
        for seat in table.players.values():
            table.last_bets[seat.user_id] = (
                seat.display_name, seat.wager, seat.fortune_bet,
            )

        for seat in table.players.values():
            # Credit winnings — catch per-player so one failure doesn't block others.
            credit = seat.main_payout + seat.fortune_win
            try:
                if credit > 0:
                    balances[seat.user_id] = await queries.update_casino_balance(
                        str(seat.user_id), credit,
                    )
                else:
                    balances[seat.user_id] = (
                        await queries.get_casino_balance(str(seat.user_id))
                    ) or 0

                await queries.log_casino_result(
                    str(seat.user_id), "paigow", seat.total_wager, credit,
                )
            except Exception:
                log.exception("paigow: failed to settle player %s", seat.user_id)
                balances[seat.user_id] = 0
        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(
                    embed=_finished_embed(table, balances=balances), view=self,
                )
            except discord.HTTPException:
                pass

    # ── Lifecycle ────────────────────────────────────────────

    def _start_new_round(self) -> None:
        table = self.table
        table.players.clear()
        table.dealer_cards.clear()
        table.dealer_high.clear()
        table.dealer_low.clear()
        table.phase = "betting"
        table.round_num += 1

    async def _abort(self, interaction: discord.Interaction, reason: str) -> None:
        # Mark finished BEFORE refunds and stop so on_timeout() can't double-refund
        # if it fires concurrently.
        self.table.phase = "finished"
        for seat in self.table.players.values():
            try:
                await queries.update_casino_balance(str(seat.user_id), seat.total_wager)
            except Exception:
                log.exception("paigow: failed to refund player %s in _abort", seat.user_id)
        embed = discord.Embed(
            title="Pai Gow Table — Closed",
            description=reason,
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        # Only evict if active_tables still points to THIS table — a new /paigow
        # call may have already replaced the entry.
        if self.active_tables.get(self.table.channel_id) is self.table:
            self.active_tables.pop(self.table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _close(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Pai Gow Table — Closed",
            description=f"Table closed after {self.table.round_num} round(s). Thanks for playing!",
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        # Only evict if active_tables still points to THIS table — a new /paigow
        # call may have already replaced the entry.
        if self.active_tables.get(self.table.channel_id) is self.table:
            self.active_tables.pop(self.table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table
        if table.phase == "finished":
            # Only evict if active_tables still maps to THIS table — a new /paigow
            # call may have already replaced the entry with a different table.
            if self.active_tables.get(table.channel_id) is table:
                self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    embed = discord.Embed(
                        title="Pai Gow Table — Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    log.exception("Unhandled error in paigow.py")
            return
        # Betting or setting — mark finished and remove from active_tables
        # BEFORE refunds so the table can't get stuck if a refund throws.
        table.phase = "finished"
        if self.active_tables.get(table.channel_id) is table:
            self.active_tables.pop(table.channel_id, None)
        for seat in table.players.values():
            try:
                await queries.update_casino_balance(str(seat.user_id), seat.total_wager)
            except Exception:
                log.exception("paigow: failed to refund player %s on timeout", seat.user_id)
        if table.message:
            try:
                embed = discord.Embed(
                    title="Pai Gow Table — Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("Unhandled error in paigow.py")


# ── Cog ──────────────────────────────────────────────────────────────────────


class PaiGowCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, PaiGowTable] = {}

    paigow_group = app_commands.Group(name="paigow", description="Pai Gow Poker commands")

    @paigow_group.command(name="play", description="Open a Pai Gow Poker table (multiplayer)")
    async def paigow(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            if existing.phase != "finished":
                await interaction.response.send_message(
                    "There's already a Pai Gow table in this channel! "
                    "The opener can click **Close Table**, or an admin can run `/paigow close`.",
                    ephemeral=True,
                )
                return
            del self.active_tables[channel_id]

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = PaiGowTable(
            channel_id=channel_id,
            opener_id=interaction.user.id,
            opener_name=interaction.user.display_name,
        )
        view = PaiGowTableView(table, self.active_tables)
        embed = _betting_embed(table)
        try:
            await interaction.response.send_message(embed=embed, view=view)
        except discord.NotFound:
            return  # interaction expired — don't leave a ghost table
        self.active_tables[channel_id] = table
        table.message = await interaction.original_response()

    @paigow_group.command(
        name="close",
        description="Force-close a stuck Pai Gow table and refund all players (admin only)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def paigow_close(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        table = self.active_tables.get(channel_id)
        if table is None:
            await interaction.response.send_message(
                "No active Pai Gow table in this channel.", ephemeral=True,
            )
            return

        # Mark finished and remove BEFORE any async work so the table can't stay stuck
        table.phase = "finished"
        del self.active_tables[channel_id]

        for seat in table.players.values():
            try:
                await queries.update_casino_balance(str(seat.user_id), seat.total_wager)
            except Exception:
                log.exception("paigow close: failed to refund player %s", seat.user_id)

        if table.message:
            try:
                embed = discord.Embed(
                    title="Pai Gow Table \u2014 Closed by Admin",
                    description=(
                        f"Table force-closed by {interaction.user.display_name}. "
                        "All bets refunded."
                    ),
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("paigow close: failed to edit message")

        await interaction.response.send_message(
            "Pai Gow table closed and all bets refunded.", ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PaiGowCog(bot))
