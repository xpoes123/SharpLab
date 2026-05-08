"""Ultimate Texas Hold'em cog — multiplayer /uth table game."""
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
MAX_PLAYERS = 5


def _new_deck() -> list[str]:
    """Standard 52-card deck, shuffled. Fresh deck each hand."""
    cards = [f"{r}{s}" for s in SUITS for r in RANKS]
    random.shuffle(cards)
    return cards


def _fmt_card(card: str) -> str:
    return f"`{card}`"


def _fmt_hand(hand: list[str]) -> str:
    return " ".join(_fmt_card(c) for c in hand)


def _sort_for_display(cards: list[str]) -> list[str]:
    return sorted(cards, key=lambda c: RANK_VAL.get(c[:-1], 0), reverse=True)


# ── 5-card poker hand evaluation ─────────────────────────────────────────────

_RANK_WORD: dict[int, str] = {
    2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven",
    8: "Eight", 9: "Nine", 10: "Ten", 11: "Jack", 12: "Queen", 13: "King", 14: "Ace",
}
_RANK_PLURAL: dict[int, str] = {
    2: "Twos", 3: "Threes", 4: "Fours", 5: "Fives", 6: "Sixes", 7: "Sevens",
    8: "Eights", 9: "Nines", 10: "Tens", 11: "Jacks", 12: "Queens", 13: "Kings", 14: "Aces",
}


def _evaluate_5(cards: list[str]) -> tuple[int, ...]:
    """Evaluate a 5-card hand. Returns a comparable tuple (higher = better)."""
    ranks = sorted([RANK_VAL[c[:-1]] for c in cards], reverse=True)
    suits = [c[-1] for c in cards]
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
        return (9, high)  # straight flush / royal flush
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


def _best_5_from_7(cards: list[str]) -> tuple[int, ...]:
    """Best possible 5-card hand from 7 cards."""
    best: tuple[int, ...] = (0,)
    for combo in itertools.combinations(range(7), 5):
        hand = [cards[i] for i in combo]
        score = _evaluate_5(hand)
        if score > best:
            best = score
    return best


def _hand_name(score: tuple[int, ...]) -> str:
    tier = score[0]
    if tier == 9:
        return "Royal Flush" if score[1] == 14 else f"Straight Flush ({_RANK_WORD[score[1]]}-high)"
    if tier == 7:
        return f"Four {_RANK_PLURAL[score[1]]}"
    if tier == 6:
        return f"Full House ({_RANK_PLURAL[score[1]]} full of {_RANK_PLURAL[score[2]]})"
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


# ── Payout tables ────────────────────────────────────────────────────────────

def _blind_payout(score: tuple[int, ...], blind: int) -> tuple[int, str]:
    """Blind bonus winnings for a winning hand. Returns (winnings, label)."""
    tier = score[0]
    if tier == 9:
        if score[1] == 14:
            return blind * 500, "Royal Flush"
        return blind * 50, "Straight Flush"
    if tier == 7:
        return blind * 10, "Four of a Kind"
    if tier == 6:
        return blind * 3, "Full House"
    if tier == 5:
        return blind * 3 // 2, "Flush"
    if tier == 4:
        return blind * 1, "Straight"
    return 0, ""  # push — blind returned, no bonus


def _trips_payout(score: tuple[int, ...], trips_bet: int) -> tuple[int, str]:
    """Trips side-bet winnings. Returns (winnings, label). 0 = loss."""
    tier = score[0]
    if tier == 9:
        if score[1] == 14:
            return trips_bet * 50, "Royal Flush"
        return trips_bet * 40, "Straight Flush"
    if tier == 7:
        return trips_bet * 30, "Four of a Kind"
    if tier == 6:
        return trips_bet * 8, "Full House"
    if tier == 5:
        return trips_bet * 6, "Flush"
    if tier == 4:
        return trips_bet * 5, "Straight"
    if tier == 3:
        return trips_bet * 3, "Three of a Kind"
    return 0, ""  # loss


# ── Game state ────────────────────────────────────────────────────────────────


@dataclass
class UTHPlayer:
    user_id: int
    display_name: str
    ante: int
    blind: int              # always == ante
    trips_bet: int = 0
    play_bet: int = 0       # set when player raises
    hole_cards: list[str] = field(default_factory=list)
    folded: bool = False
    decided: bool = False   # acted this phase (resets each phase)
    raised: bool = False    # placed a Play bet (sticky)
    best_hand: tuple[int, ...] = ()
    best_hand_name: str = ""
    payout: int = 0
    result_lines: list[str] = field(default_factory=list)

    @property
    def total_wagered(self) -> int:
        return self.ante + self.blind + self.trips_bet + self.play_bet


@dataclass
class UTHTable:
    channel_id: int
    dealer_id: int
    dealer_name: str
    deck: list[str] = field(default_factory=list)
    phase: str = "betting"  # betting | preflop | flop | river | finished
    community: list[str] = field(default_factory=list)
    dealer_hand: list[str] = field(default_factory=list)
    players: dict[int, UTHPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    last_bets: dict[int, tuple[str, int, int]] = field(default_factory=dict)

    def draw(self) -> str:
        return self.deck.pop()

    def all_decided(self) -> bool:
        return all(
            p.folded or p.raised or p.decided
            for p in self.players.values()
        )

    def any_active(self) -> bool:
        return any(not p.folded for p in self.players.values())


# ── Embeds ────────────────────────────────────────────────────────────────────


def _table_embed(
    table: UTHTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    phase = table.phase

    if phase == "betting":
        colour = discord.Colour.blurple()
        title = f"Ultimate Texas Hold'em — Place Your Bets (Round {table.round_num})"
    elif phase == "finished":
        colour = discord.Colour.gold()
        title = f"Ultimate Texas Hold'em — Round {table.round_num} Complete"
    else:
        phase_names = {"preflop": "Pre-Flop", "flop": "Flop", "river": "Turn & River"}
        colour = discord.Colour.blurple()
        title = f"Ultimate Texas Hold'em — {phase_names.get(phase, phase.title())} (Round {table.round_num})"

    embed = discord.Embed(title=title, colour=colour)
    embed.set_footer(text=f"Dealer: {table.dealer_name}")

    if phase == "betting":
        embed.description = "Join the table, then the dealer deals!"
    elif phase == "finished":
        embed.description = "Click **New Round** to continue or **Close Table** to end."

    # Community cards
    if phase == "betting":
        pass
    elif phase == "preflop":
        embed.add_field(name="Board", value="🂠 🂠 🂠 🂠 🂠", inline=False)
    elif phase == "flop":
        shown = _fmt_hand(table.community[:3])
        embed.add_field(name="Board", value=f"{shown}  🂠 🂠", inline=False)
    elif phase in ("river", "finished"):
        shown = _fmt_hand(table.community)
        embed.add_field(name="Board", value=shown, inline=False)

    # Dealer hand
    if phase in ("preflop", "flop", "river"):
        embed.add_field(name="Dealer", value="`??` `??`", inline=False)
    elif phase == "finished":
        dealer_7 = table.dealer_hand + table.community
        dealer_score = _best_5_from_7(dealer_7)
        qualifies = dealer_score[0] >= 1
        qual_str = "" if qualifies else " — **Does NOT qualify**"
        embed.add_field(
            name="Dealer",
            value=f"{_fmt_hand(table.dealer_hand)}  ({_hand_name(dealer_score)}){qual_str}",
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
            if phase == "betting":
                bet_str = f"Ante: {p.ante}c, Blind: {p.blind}c"
                if p.trips_bet > 0:
                    bet_str += f" + Trips {p.trips_bet}c"
                lines.append(f"🃏 **{p.display_name}** — {bet_str}")

            elif phase in ("preflop", "flop", "river"):
                cards = _fmt_hand(p.hole_cards)
                bet_info = f"{p.ante}c"
                if p.folded:
                    emoji = "💀"
                    status = "Folded"
                elif p.raised:
                    emoji = "✅"
                    status = f"Play {p.play_bet}c"
                elif p.decided:
                    emoji = "✋"
                    status = "Check"
                else:
                    emoji = "🟦"
                    status = "waiting..."
                lines.append(f"{emoji} **{p.display_name}** ({bet_info}): {cards} — {status}")

            else:  # finished
                cards = _fmt_hand(p.hole_cards)
                hand_str = p.best_hand_name or "—"
                net = p.payout - p.total_wagered
                sign = "+" if net > 0 else ""
                bal = balances.get(p.user_id, 0) if balances else 0

                detail = " | ".join(p.result_lines) if p.result_lines else ""
                lines.append(
                    f"**{p.display_name}** ({p.ante}c Ante): {cards}\n"
                    f"  {hand_str}\n"
                    f"  {detail}\n"
                    f"  → **{sign}{net}c** (bal: {bal}c)"
                )

        if lines:
            embed.add_field(name="Players", value="\n".join(lines), inline=False)

    return embed


# ── Modal ─────────────────────────────────────────────────────────────────────


class JoinUTHModal(ui.Modal):
    ante_input = ui.TextInput(
        label="Ante amount (Blind auto-matches)",
        placeholder="e.g. 50",
        required=True, max_length=10,
    )
    trips_input = ui.TextInput(
        label="Trips side bet (optional, 0 to skip)",
        placeholder="0",
        required=False, max_length=10, default="0",
    )

    def __init__(self, table: UTHTable, view: "UTHTableView", balance: int) -> None:
        super().__init__(title="Join UTH Table")
        self.table = table
        self.table_view = view
        self.ante_input.placeholder = f"e.g. 50 (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            ante = int(self.ante_input.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number for Ante.", ephemeral=True)
            return
        if ante < 1:
            await interaction.response.send_message("Ante must be at least 1 coin.", ephemeral=True)
            return

        trips = 0
        if self.trips_input.value:
            try:
                trips = int(self.trips_input.value)
            except ValueError:
                await interaction.response.send_message("Enter a whole number for Trips.", ephemeral=True)
                return
            if trips < 0:
                await interaction.response.send_message("Trips can't be negative.", ephemeral=True)
                return

        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already at the table!", ephemeral=True)
            return

        total_cost = ante * 2 + trips  # ante + blind + trips
        try:
            await queries.update_casino_balance(str(uid), -total_cost)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! Need {total_cost}c (have {bal}c).", ephemeral=True,
            )
            return

        self.table.players[uid] = UTHPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            ante=ante,
            blind=ante,
            trips_bet=trips,
        )
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self.table_view,
        )


# ── View ──────────────────────────────────────────────────────────────────────


class UTHTableView(ui.View):
    def __init__(
        self, table: UTHTable, active_tables: dict[int, "UTHTable"],
    ) -> None:
        super().__init__(timeout=180)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        preflop = phase == "preflop"
        flop = phase == "flop"
        river = phase == "river"
        finished = phase == "finished"
        playing = preflop or flop or river

        # Row 0
        self.deal_btn.disabled = not betting or not self.table.players
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = playing

        # Row 1: Preflop
        self.check_pre_btn.disabled = not preflop
        self.bet_4x_btn.disabled = not preflop
        self.bet_3x_btn.disabled = not preflop

        # Row 2: Flop + River
        self.check_flop_btn.disabled = not flop
        self.bet_2x_btn.disabled = not flop
        self.bet_1x_btn.disabled = not river
        self.fold_btn.disabled = not river

        # Row 3
        self.new_round_btn.disabled = not finished
        self.close_btn.disabled = playing

    def _get_undecided(self, interaction: discord.Interaction) -> UTHPlayer | None:
        """Return the player if they're at the table and haven't decided yet."""
        p = self.table.players.get(interaction.user.id)
        if p is None or p.folded or p.raised or p.decided:
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
            await interaction.response.send_message("You're already at the table!", ephemeral=True)
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        bal = await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(JoinUTHModal(self.table, self, bal))

    @ui.button(label="Re-bet", style=discord.ButtonStyle.primary, emoji="🔄", row=0)
    async def rebet_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Cards already dealt!", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already at the table!", ephemeral=True)
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
        name, ante, trips = last
        total_cost = ante * 2 + trips
        try:
            await queries.update_casino_balance(str(uid), -total_cost)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for re-bet ({total_cost}c)! Have {bal}c.", ephemeral=True,
            )
            return
        self.table.players[uid] = UTHPlayer(
            user_id=uid, display_name=name, ante=ante, blind=ante, trips_bet=trips,
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
            await interaction.response.send_message("You're not at this table.", ephemeral=True)
            return

        if self.table.phase in ("preflop", "flop", "river") and not player.folded:
            await interaction.response.send_message(
                "Can't leave mid-hand! Finish your action first.", ephemeral=True,
            )
            return

        if uid == self.table.dealer_id and self.table.phase == "betting":
            await self._abort(interaction, "Dealer left — all bets refunded.")
            return

        if self.table.phase == "betting":
            refund = player.ante + player.blind + player.trips_bet
            await queries.update_casino_balance(str(uid), refund)
            del self.table.players[uid]
            self._update_buttons()
            await interaction.response.edit_message(
                embed=_table_embed(self.table), view=self,
            )
            return

        await interaction.response.send_message(
            "You'll see results when the round ends.", ephemeral=True,
        )

    # ── Row 1: Pre-flop actions ──────────────────────────────────

    @ui.button(label="Check", style=discord.ButtonStyle.secondary, emoji="✋", row=1)
    async def check_pre_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_check(interaction)

    @ui.button(label="Bet 4x", style=discord.ButtonStyle.success, emoji="💰", row=1)
    async def bet_4x_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_raise(interaction, 4)

    @ui.button(label="Bet 3x", style=discord.ButtonStyle.primary, emoji="💵", row=1)
    async def bet_3x_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_raise(interaction, 3)

    # ── Row 2: Flop + River actions ──────────────────────────────

    @ui.button(label="Check", style=discord.ButtonStyle.secondary, emoji="✋", row=2)
    async def check_flop_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_check(interaction)

    @ui.button(label="Bet 2x", style=discord.ButtonStyle.success, emoji="💰", row=2)
    async def bet_2x_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_raise(interaction, 2)

    @ui.button(label="Bet 1x", style=discord.ButtonStyle.success, emoji="💵", row=2)
    async def bet_1x_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_raise(interaction, 1)

    @ui.button(label="Fold", style=discord.ButtonStyle.danger, emoji="🏳️", row=2)
    async def fold_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        player = self._get_undecided(interaction)
        if player is None:
            await interaction.response.send_message(
                "You can't act right now!", ephemeral=True,
            )
            return
        player.folded = True
        player.decided = True
        if self.table.all_decided():
            await self._advance_phase(interaction)
        else:
            await interaction.response.edit_message(
                embed=_table_embed(self.table), view=self,
            )

    # ── Row 3: New Round / Close Table ───────────────────────────

    @ui.button(label="New Round", style=discord.ButtonStyle.success, emoji="▶️", row=3)
    async def new_round_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.dealer_id:
            await interaction.response.send_message(
                "Only the table opener can start a new round!", ephemeral=True,
            )
            return
        if self.table.phase != "finished":
            await interaction.response.send_message("Round still in progress!", ephemeral=True)
            return
        self._start_new_round()
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self,
        )

    @ui.button(label="Close Table", style=discord.ButtonStyle.danger, emoji="✖️", row=3)
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.dealer_id:
            await interaction.response.send_message(
                "Only the table opener can close!", ephemeral=True,
            )
            return
        if self.table.phase in ("preflop", "flop", "river"):
            await interaction.response.send_message("Can't close mid-round!", ephemeral=True)
            return
        if self.table.phase == "betting":
            await self._abort(interaction, "Table closed by dealer. All bets refunded.")
        else:
            await self._close(interaction)

    # ── Shared action handlers ───────────────────────────────────

    async def _handle_check(self, interaction: discord.Interaction) -> None:
        player = self._get_undecided(interaction)
        if player is None:
            await interaction.response.send_message(
                "You can't act right now!", ephemeral=True,
            )
            return
        player.decided = True
        if self.table.all_decided():
            await self._advance_phase(interaction)
        else:
            await interaction.response.edit_message(
                embed=_table_embed(self.table), view=self,
            )

    async def _handle_raise(self, interaction: discord.Interaction, multiplier: int) -> None:
        player = self._get_undecided(interaction)
        if player is None:
            await interaction.response.send_message(
                "You can't act right now!", ephemeral=True,
            )
            return

        cost = player.ante * multiplier
        try:
            await queries.update_casino_balance(str(player.user_id), -cost)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(player.user_id))
            await interaction.response.send_message(
                f"Not enough coins for {multiplier}x bet ({cost}c)! Have {bal}c.",
                ephemeral=True,
            )
            return

        player.play_bet = cost
        player.raised = True
        player.decided = True

        if self.table.all_decided():
            await self._advance_phase(interaction)
        else:
            await interaction.response.edit_message(
                embed=_table_embed(self.table), view=self,
            )

    # ── Deal logic ───────────────────────────────────────────────

    async def _deal(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "preflop"
        table.deck = _new_deck()

        # Deal 2 hole cards to each player
        for p in table.players.values():
            p.hole_cards = [table.draw(), table.draw()]

        # Deal 2 to dealer
        table.dealer_hand = [table.draw(), table.draw()]

        # Pre-deal 5 community cards (revealed progressively)
        table.community = [table.draw() for _ in range(5)]

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(table), view=self,
        )

    # ── Phase advancement ────────────────────────────────────────

    async def _advance_phase(self, interaction: discord.Interaction) -> None:
        table = self.table

        if table.phase == "preflop":
            table.phase = "flop"
            # Reset decided for players who haven't raised
            for p in table.players.values():
                if not p.folded and not p.raised:
                    p.decided = False
            # Auto-skip if everyone already raised or folded
            if not table.any_active() or table.all_decided():
                return await self._advance_phase(interaction)

        elif table.phase == "flop":
            table.phase = "river"
            for p in table.players.values():
                if not p.folded and not p.raised:
                    p.decided = False
            if not table.any_active() or table.all_decided():
                return await self._advance_phase(interaction)

        elif table.phase == "river":
            await self._resolve_showdown(interaction)
            return

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(table), view=self,
        )

    # ── Showdown ─────────────────────────────────────────────────

    async def _resolve_showdown(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "finished"

        # Evaluate dealer
        dealer_7 = table.dealer_hand + table.community
        dealer_score = _best_5_from_7(dealer_7)
        dealer_qualifies = dealer_score[0] >= 1  # pair or better

        balances: dict[int, int] = {}

        for p in table.players.values():
            credit = 0
            p.result_lines = []

            # Evaluate player hand (even folded players need it for Trips)
            player_7 = p.hole_cards + table.community
            p.best_hand = _best_5_from_7(player_7)
            p.best_hand_name = _hand_name(p.best_hand)

            if p.folded:
                p.result_lines.append("Folded")
                # Trips still evaluated
                if p.trips_bet > 0:
                    tw, tl = _trips_payout(p.best_hand, p.trips_bet)
                    if tw > 0:
                        credit += p.trips_bet + tw
                        p.result_lines.append(f"Trips: {tl} +{tw}c")
                    else:
                        p.result_lines.append(f"Trips: Loss")
            else:
                player_wins = p.best_hand > dealer_score
                player_ties = p.best_hand == dealer_score

                # Play bet
                if player_ties:
                    credit += p.play_bet  # push
                    p.result_lines.append(f"Play: Push")
                elif player_wins:
                    credit += p.play_bet * 2  # 1:1
                    p.result_lines.append(f"Play: Win +{p.play_bet}c")
                else:
                    p.result_lines.append(f"Play: Loss")

                # Ante
                if player_ties:
                    credit += p.ante  # push
                    p.result_lines.append(f"Ante: Push")
                elif player_wins:
                    if dealer_qualifies:
                        credit += p.ante * 2  # 1:1
                        p.result_lines.append(f"Ante: Win +{p.ante}c")
                    else:
                        credit += p.ante  # push (DNQ)
                        p.result_lines.append(f"Ante: Push (DNQ)")
                else:
                    p.result_lines.append(f"Ante: Loss")

                # Blind
                if player_ties:
                    credit += p.blind  # push
                    p.result_lines.append(f"Blind: Push")
                elif player_wins:
                    bw, bl = _blind_payout(p.best_hand, p.blind)
                    credit += p.blind + bw  # return blind + bonus
                    if bw > 0:
                        p.result_lines.append(f"Blind: {bl} +{bw}c")
                    else:
                        p.result_lines.append(f"Blind: Push")
                else:
                    p.result_lines.append(f"Blind: Loss")

                # Trips
                if p.trips_bet > 0:
                    tw, tl = _trips_payout(p.best_hand, p.trips_bet)
                    if tw > 0:
                        credit += p.trips_bet + tw
                        p.result_lines.append(f"Trips: {tl} +{tw}c")
                    else:
                        p.result_lines.append(f"Trips: Loss")

            p.payout = credit
            if credit > 0:
                balances[p.user_id] = await queries.update_casino_balance(
                    str(p.user_id), credit,
                )
            else:
                balances[p.user_id] = (
                    await queries.get_casino_balance(str(p.user_id))
                ) or 0
            await queries.log_casino_result(
                str(p.user_id), "uth", p.total_wagered, p.payout,
            )

        # Save last bets for re-bet
        for p in table.players.values():
            table.last_bets[p.user_id] = (p.display_name, p.ante, p.trips_bet)

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(table, balances=balances), view=self,
        )

    # ── Lifecycle ────────────────────────────────────────────────

    def _start_new_round(self) -> None:
        table = self.table
        table.players.clear()
        table.dealer_hand.clear()
        table.community.clear()
        table.phase = "betting"
        table.round_num += 1

    async def _abort(self, interaction: discord.Interaction, reason: str) -> None:
        for p in self.table.players.values():
            refund = p.ante + p.blind + p.trips_bet + p.play_bet
            if refund > 0:
                try:
                    await queries.update_casino_balance(str(p.user_id), refund)
                except Exception:
                    log.exception("Unhandled error in uth.py")
        embed = discord.Embed(
            title="UTH Table — Closed",
            description=reason,
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(self.table.channel_id, None)
        await queries.unregister_discord_table(self.table.channel_id)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _close(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="UTH Table — Closed",
            description=f"Table closed after {self.table.round_num} round(s). Thanks for playing!",
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(self.table.channel_id, None)
        await queries.unregister_discord_table(self.table.channel_id)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table
        # Identity check: don't touch a replacement table that was opened in
        # the same channel after this view's table was superseded.
        if self.active_tables.get(table.channel_id) is not table:
            return
        self.active_tables.pop(table.channel_id, None)
        await queries.unregister_discord_table(table.channel_id)
        if table.phase == "finished":
            if table.message:
                try:
                    embed = discord.Embed(
                        title="UTH Table — Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    log.exception("Unhandled error in uth.py")
            return
        # Active phase — refund everything
        for p in table.players.values():
            refund = p.ante + p.blind + p.trips_bet + p.play_bet
            if refund > 0:
                try:
                    await queries.update_casino_balance(str(p.user_id), refund)
                except Exception:
                    log.exception("Unhandled error in uth.py")
        if table.message:
            try:
                embed = discord.Embed(
                    title="UTH Table — Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("Unhandled error in uth.py")


# ── Cog ───────────────────────────────────────────────────────────────────────


class UTHCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, UTHTable] = {}

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """On bot startup, close any UTH tables left open from a previous session."""
        stale = await queries.get_stale_discord_tables("uth")
        for row in stale:
            channel_id = row["channel_id"]
            message_id = row["message_id"]
            try:
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    channel = await self.bot.fetch_channel(channel_id)
                if channel is not None and message_id is not None:
                    try:
                        msg = await channel.fetch_message(message_id)
                        await msg.edit(
                            embed=discord.Embed(
                                title="UTH Table — Closed",
                                description="Table closed: bot restarted.",
                                colour=discord.Colour.dark_grey(),
                            ),
                            view=None,
                        )
                    except Exception:
                        log.warning("Could not edit stale UTH message in channel %s", channel_id)
            except Exception:
                log.warning("Could not fetch channel %s during UTH startup cleanup", channel_id)
            finally:
                await queries.unregister_discord_table(channel_id)

    async def uth(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            # Only allow replacing a fully-finished table; block everything else
            # to prevent silently overwriting an active game mid-round.
            if existing.phase != "finished":
                await interaction.response.send_message(
                    "There's already a UTH table in this channel! Use the buttons to join.",
                    ephemeral=True,
                )
                return
            del self.active_tables[channel_id]

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = UTHTable(
            channel_id=channel_id,
            dealer_id=interaction.user.id,
            dealer_name=interaction.user.display_name,
        )
        view = UTHTableView(table, self.active_tables)
        embed = _table_embed(table)
        try:
            await interaction.response.send_message(embed=embed, view=view)
        except discord.NotFound:
            return  # interaction expired — don't leave a ghost table
        self.active_tables[channel_id] = table
        table.message = await interaction.original_response()
        await queries.register_discord_table(channel_id, table.message.id, "uth")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UTHCog(bot))
