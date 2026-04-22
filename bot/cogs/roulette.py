"""Roulette cog — American roulette with full table betting (no corner/edge)."""
import asyncio
import random
from dataclasses import dataclass, field
from enum import Enum

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Wheel constants ────────────────────────────────────────────────────────

RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
BLACK_NUMBERS = {2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35}

WHEEL_NUMBERS: list[str] = ["0", "00"] + [str(i) for i in range(1, 37)]

# Pre-computed number sets for outside bets
COLUMN_1 = frozenset(str(n) for n in range(1, 37) if (n - 1) % 3 == 0)
COLUMN_2 = frozenset(str(n) for n in range(1, 37) if (n - 1) % 3 == 1)
COLUMN_3 = frozenset(str(n) for n in range(1, 37) if (n - 1) % 3 == 2)
DOZEN_1 = frozenset(str(n) for n in range(1, 13))
DOZEN_2 = frozenset(str(n) for n in range(13, 25))
DOZEN_3 = frozenset(str(n) for n in range(25, 37))
RED_SET = frozenset(str(n) for n in RED_NUMBERS)
BLACK_SET = frozenset(str(n) for n in BLACK_NUMBERS)
ODD_SET = frozenset(str(n) for n in range(1, 37) if n % 2 == 1)
EVEN_SET = frozenset(str(n) for n in range(1, 37) if n % 2 == 0)
LOW_SET = frozenset(str(n) for n in range(1, 19))
HIGH_SET = frozenset(str(n) for n in range(19, 37))

MAX_PLAYERS = 8

# ── Bet types & payouts ───────────────────────────────────────────────────


class BetCategory(Enum):
    STRAIGHT = "straight"
    SPLIT = "split"
    STREET = "street"
    COLUMN = "column"
    DOZEN = "dozen"
    RED = "red"
    BLACK = "black"
    ODD = "odd"
    EVEN = "even"
    LOW = "low"
    HIGH = "high"


PAYOUTS: dict[BetCategory, int] = {
    BetCategory.STRAIGHT: 35,
    BetCategory.SPLIT: 17,
    BetCategory.STREET: 11,
    BetCategory.COLUMN: 2,
    BetCategory.DOZEN: 2,
    BetCategory.RED: 1,
    BetCategory.BLACK: 1,
    BetCategory.ODD: 1,
    BetCategory.EVEN: 1,
    BetCategory.LOW: 1,
    BetCategory.HIGH: 1,
}


# ── Adjacency for split bets ──────────────────────────────────────────────

# Standard roulette grid (12 rows × 3 columns):
#  Row 0:  1  2  3
#  Row 1:  4  5  6
#  ...
#  Row 11: 34 35 36
#
# Two numbers are adjacent if they share a horizontal or vertical edge.
# Special: 0-1, 0-2, 00-2, 00-3, 0-00 are all valid splits.


def _are_adjacent(a: str, b: str) -> bool:
    pair = {a, b}
    if pair == {"0", "00"}:
        return True
    if "0" in pair:
        other = (pair - {"0"}).pop()
        return other in ("1", "2", "00")
    if "00" in pair:
        other = (pair - {"00"}).pop()
        return other in ("2", "3")
    try:
        na, nb = int(a), int(b)
    except ValueError:
        return False
    if na < 1 or na > 36 or nb < 1 or nb > 36:
        return False
    ra, ca = divmod(na - 1, 3)
    rb, cb = divmod(nb - 1, 3)
    # Same row, adjacent columns
    if ra == rb and abs(ca - cb) == 1:
        return True
    # Same column, adjacent rows
    if ca == cb and abs(ra - rb) == 1:
        return True
    return False


# ── Data model ─────────────────────────────────────────────────────────────


@dataclass
class RouletteBet:
    category: BetCategory
    wager: int
    numbers: frozenset[str]
    label: str


@dataclass
class RoulettePlayer:
    user_id: int
    display_name: str
    bets: list[RouletteBet] = field(default_factory=list)


@dataclass
class RouletteTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | spinning | finished
    result: str = ""
    players: dict[int, RoulettePlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    last_bets: dict[int, list[tuple[str, int, tuple[str, ...], str]]] = field(
        default_factory=dict,
    )
    spin_history: list[str] = field(default_factory=list)


# ── Result helpers ─────────────────────────────────────────────────────────


def _result_color(result: str) -> str:
    if result in ("0", "00"):
        return "\U0001f7e2"
    if int(result) in RED_NUMBERS:
        return "\U0001f534"
    return "\u26ab"


def _result_properties(result: str) -> list[str]:
    if result in ("0", "00"):
        return ["Green"]
    n = int(result)
    props: list[str] = []
    props.append("Red" if n in RED_NUMBERS else "Black")
    props.append("Odd" if n % 2 == 1 else "Even")
    props.append("Low" if n <= 18 else "High")
    if n <= 12:
        props.append("1st 12")
    elif n <= 24:
        props.append("2nd 12")
    else:
        props.append("3rd 12")
    col = (n - 1) % 3 + 1
    props.append(f"Col {col}")
    return props


def _resolve_bet(bet: RouletteBet, result: str) -> int:
    """Returns total payout (0 = loss, wager + winnings = hit)."""
    if result in bet.numbers:
        return bet.wager + bet.wager * PAYOUTS[bet.category]
    return 0


# ── Embed builders ─────────────────────────────────────────────────────────


def _fmt_history(history: list[str]) -> str:
    parts = []
    for r in history[-10:]:
        parts.append(f"{_result_color(r)}{r}")
    return "  ".join(parts)


def _player_bets_summary(player: RoulettePlayer) -> str:
    if not player.bets:
        return "*(no bets yet)*"
    from collections import Counter
    counts: Counter[str] = Counter()
    for b in player.bets:
        key = f"{b.label} {b.wager}c"
        counts[key] += 1
    parts = []
    for key, count in counts.items():
        parts.append(f"{key} x{count}" if count > 1 else key)
    return ", ".join(parts)


def _table_embed(
    table: RouletteTable,
    *,
    balances: dict[int, int] | None = None,
    spin_display: str = "",
) -> discord.Embed:
    finished = table.phase == "finished"
    spinning = table.phase == "spinning"

    if finished:
        colour = discord.Colour.gold()
        props = " | ".join(_result_properties(table.result))
        title = f"Roulette \u2014 {_result_color(table.result)} {table.result}! (Round {table.round_num})"
    elif spinning:
        colour = discord.Colour.blurple()
        title = f"Roulette \u2014 Spinning... (Round {table.round_num})"
    else:
        colour = discord.Colour.blurple()
        title = f"Roulette \u2014 Place Your Bets (Round {table.round_num})"

    embed = discord.Embed(title=title, colour=colour)
    embed.set_footer(text=f"Host: {table.host_name} \u2022 Round {table.round_num}")

    if spin_display:
        embed.add_field(name="Wheel", value=spin_display, inline=False)

    if finished:
        props = " | ".join(_result_properties(table.result))
        embed.add_field(
            name="Result",
            value=f"\U0001f3af **{table.result}** {_result_color(table.result)} {props}",
            inline=False,
        )

    # Players
    if table.players:
        lines = []
        for p in table.players.values():
            total_wagered = sum(b.wager for b in p.bets)
            summary = _player_bets_summary(p)
            line = f"\U0001f4b0 **{p.display_name}** \u2014 {summary}"
            if finished and balances is not None:
                total_payout = sum(_resolve_bet(b, table.result) for b in p.bets)
                net = total_payout - total_wagered
                sign = "+" if net > 0 else ""
                bal = balances.get(p.user_id, 0)
                # Per-bet breakdown
                bet_lines = []
                for b in p.bets:
                    payout = _resolve_bet(b, table.result)
                    if payout > 0:
                        win = payout - b.wager
                        bet_lines.append(f"{b.label} {b.wager}c: **+{win}c**")
                    else:
                        bet_lines.append(f"{b.label} {b.wager}c: Miss")
                line = f"\U0001f4b0 **{p.display_name}** \u2014 {', '.join(bet_lines)}"
                line += f"\n\u2003\u2192 **{sign}{net}c** (bal: {bal}c)"
            lines.append(line)
        embed.add_field(name="Players", value="\n".join(lines[:8]), inline=False)
    elif not finished:
        embed.add_field(
            name="Players",
            value="*No bets yet \u2014 use the buttons below!*",
            inline=False,
        )

    # History
    if table.spin_history:
        embed.add_field(name="History", value=_fmt_history(table.spin_history), inline=False)

    return embed


# ── Modals ─────────────────────────────────────────────────────────────────


class OutsideBetModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)", placeholder="e.g. 50",
        required=True, max_length=10,
    )

    def __init__(
        self, table: RouletteTable, category: BetCategory,
        numbers: frozenset[str], label: str, view: "RouletteView", balance: int,
    ) -> None:
        super().__init__(title=f"{label} ({PAYOUTS[category]}:1)")
        self.table = table
        self.category = category
        self.numbers = numbers
        self.bet_label = label
        self.table_view = view
        self.amount.placeholder = f"e.g. 50 (bal: {balance}c)"

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
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(f"Not enough coins! (have {bal})", ephemeral=True)
            return

        player = self.table.players.get(uid)
        if player is None:
            player = RoulettePlayer(user_id=uid, display_name=interaction.user.display_name)
            self.table.players[uid] = player
        player.bets.append(RouletteBet(
            category=self.category, wager=amt,
            numbers=self.numbers, label=self.bet_label,
        ))
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self.table_view,
        )


class StraightBetModal(ui.Modal):
    number = ui.TextInput(
        label="Number (0, 00, or 1\u201336)",
        placeholder="e.g. 17", required=True, max_length=2,
    )
    amount = ui.TextInput(
        label="Bet amount (coins)", placeholder="e.g. 25",
        required=True, max_length=10,
    )

    def __init__(self, table: RouletteTable, view: "RouletteView", balance: int) -> None:
        super().__init__(title="Straight Up (35:1)")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 25 (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        num = self.number.value.strip()
        if num not in WHEEL_NUMBERS:
            await interaction.response.send_message(
                "Enter 0, 00, or a number 1\u201336.", ephemeral=True,
            )
            return
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if amt < 1:
            await interaction.response.send_message("Must be at least 1 coin.", ephemeral=True)
            return
        uid = interaction.user.id
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(f"Not enough coins! (have {bal})", ephemeral=True)
            return

        player = self.table.players.get(uid)
        if player is None:
            player = RoulettePlayer(user_id=uid, display_name=interaction.user.display_name)
            self.table.players[uid] = player
        player.bets.append(RouletteBet(
            category=BetCategory.STRAIGHT, wager=amt,
            numbers=frozenset([num]), label=f"#{num}",
        ))
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self.table_view,
        )


class SplitBetModal(ui.Modal):
    numbers_input = ui.TextInput(
        label="Two adjacent numbers (e.g. '1 4' or '0 00')",
        placeholder="e.g. 1 4", required=True, max_length=10,
    )
    amount = ui.TextInput(
        label="Bet amount (coins)", placeholder="e.g. 25",
        required=True, max_length=10,
    )

    def __init__(self, table: RouletteTable, view: "RouletteView", balance: int) -> None:
        super().__init__(title="Split Bet (17:1)")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 25 (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        parts = self.numbers_input.value.strip().split()
        if len(parts) != 2:
            await interaction.response.send_message(
                "Enter exactly two numbers separated by a space.", ephemeral=True,
            )
            return
        a, b = parts[0], parts[1]
        if a not in WHEEL_NUMBERS or b not in WHEEL_NUMBERS:
            await interaction.response.send_message(
                "Both must be valid roulette numbers (0, 00, 1\u201336).", ephemeral=True,
            )
            return
        if a == b:
            await interaction.response.send_message(
                "Must be two different numbers.", ephemeral=True,
            )
            return
        if not _are_adjacent(a, b):
            await interaction.response.send_message(
                f"{a} and {b} are not adjacent on the roulette layout.", ephemeral=True,
            )
            return
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if amt < 1:
            await interaction.response.send_message("Must be at least 1 coin.", ephemeral=True)
            return
        uid = interaction.user.id
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(f"Not enough coins! (have {bal})", ephemeral=True)
            return

        player = self.table.players.get(uid)
        if player is None:
            player = RoulettePlayer(user_id=uid, display_name=interaction.user.display_name)
            self.table.players[uid] = player
        player.bets.append(RouletteBet(
            category=BetCategory.SPLIT, wager=amt,
            numbers=frozenset([a, b]), label=f"{a}/{b}",
        ))
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self.table_view,
        )


class StreetBetModal(ui.Modal):
    row_input = ui.TextInput(
        label="First number of the row (1, 4, 7, ... 34)",
        placeholder="e.g. 1 for row 1-2-3", required=True, max_length=2,
    )
    amount = ui.TextInput(
        label="Bet amount (coins)", placeholder="e.g. 25",
        required=True, max_length=10,
    )

    def __init__(self, table: RouletteTable, view: "RouletteView", balance: int) -> None:
        super().__init__(title="Street Bet (11:1)")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 25 (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            start = int(self.row_input.value.strip())
        except ValueError:
            await interaction.response.send_message("Enter a valid number.", ephemeral=True)
            return
        if start < 1 or start > 34 or (start - 1) % 3 != 0:
            await interaction.response.send_message(
                "Must be the first number of a row: 1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, or 34.",
                ephemeral=True,
            )
            return
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if amt < 1:
            await interaction.response.send_message("Must be at least 1 coin.", ephemeral=True)
            return
        uid = interaction.user.id
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(f"Not enough coins! (have {bal})", ephemeral=True)
            return

        nums = frozenset(str(n) for n in range(start, start + 3))
        label = f"St {start}-{start + 2}"
        player = self.table.players.get(uid)
        if player is None:
            player = RoulettePlayer(user_id=uid, display_name=interaction.user.display_name)
            self.table.players[uid] = player
        player.bets.append(RouletteBet(
            category=BetCategory.STREET, wager=amt,
            numbers=nums, label=label,
        ))
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self.table_view,
        )


# ── View ───────────────────────────────────────────────────────────────────


class RouletteView(ui.View):
    def __init__(self, table: RouletteTable, active_tables: dict[int, "RouletteTable"]) -> None:
        super().__init__(timeout=180)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        betting = self.table.phase == "betting"
        finished = self.table.phase == "finished"
        has_bets = bool(self.table.players)
        has_last = bool(self.table.last_bets)

        self.spin_btn.disabled = not betting or not has_bets
        self.rebet_btn.disabled = not betting or not has_last
        self.leave_btn.disabled = finished

        # Bet buttons — only active during betting
        for btn_name in (
            "red_btn", "black_btn", "odd_btn", "even_btn",
            "low_btn", "high_btn", "dozen1_btn", "dozen2_btn", "dozen3_btn",
            "col1_btn", "col2_btn", "col3_btn",
            "straight_btn", "split_btn", "street_btn",
        ):
            getattr(self, btn_name).disabled = not betting

        self.clear_btn.disabled = not betting
        self.new_round_btn.disabled = not finished
        self.close_btn.disabled = not finished and not betting

    # ── Row 0: Spin, Re-bet, Leave ──────────────────────────────────

    @ui.button(label="Spin", style=discord.ButtonStyle.success, emoji="\U0001f3b0", row=0)
    async def spin_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message("Only the host can spin!", ephemeral=True)
            return
        if not self.table.players:
            await interaction.response.send_message("No bets on the table!", ephemeral=True)
            return

        # Pre-determine result
        result = random.choice(WHEEL_NUMBERS)
        self.table.result = result
        self.table.phase = "spinning"

        # Disable all controls during animation
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]

        # Frame 1: random number
        fake1 = random.choice(WHEEL_NUMBERS)
        await interaction.response.edit_message(
            embed=_table_embed(
                self.table,
                spin_display=f"\U0001f3b0 *spinning...* \u2014 {_result_color(fake1)} {fake1}",
            ),
            view=self,
        )

        # Frame 2: different random number
        await asyncio.sleep(0.7)
        while True:
            fake2 = random.choice(WHEEL_NUMBERS)
            if fake2 != result:
                break
        await interaction.edit_original_response(
            embed=_table_embed(
                self.table,
                spin_display=f"\U0001f3b0 *spinning...* \u2014 {_result_color(fake2)} {fake2}",
            ),
            view=self,
        )

        # Frame 3: actual result — resolve
        await asyncio.sleep(0.7)
        await self._resolve(interaction)

    @ui.button(label="Re-bet", style=discord.ButtonStyle.primary, emoji="\U0001f501", row=0)
    async def rebet_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        saved = self.table.last_bets.get(uid)
        if not saved:
            await interaction.response.send_message("No previous bets to repeat.", ephemeral=True)
            return
        total_cost = sum(wager for _, wager, _, _ in saved)
        try:
            await queries.update_casino_balance(str(uid), -total_cost)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! Need {total_cost}, have {bal}.", ephemeral=True,
            )
            return

        player = self.table.players.get(uid)
        if player is None:
            player = RoulettePlayer(user_id=uid, display_name=interaction.user.display_name)
            self.table.players[uid] = player
        for cat_val, wager, nums_tuple, label in saved:
            player.bets.append(RouletteBet(
                category=BetCategory(cat_val), wager=wager,
                numbers=frozenset(nums_tuple), label=label,
            ))
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self,
        )

    @ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=0)
    async def leave_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message("You're not at this table.", ephemeral=True)
            return

        if uid == self.table.host_id:
            await self._abort(interaction, "Host left \u2014 all bets refunded.")
            return

        refund = sum(b.wager for b in player.bets)
        if refund > 0:
            await queries.update_casino_balance(str(uid), refund)
        del self.table.players[uid]

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self,
        )

    # ── Row 1: Red, Black, Odd, Even ────────────────────────────────

    @ui.button(label="Red", style=discord.ButtonStyle.danger, row=1)
    async def red_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._outside_bet(interaction, BetCategory.RED, RED_SET, "Red")

    @ui.button(label="Black", style=discord.ButtonStyle.secondary, row=1)
    async def black_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._outside_bet(interaction, BetCategory.BLACK, BLACK_SET, "Black")

    @ui.button(label="Odd", style=discord.ButtonStyle.primary, row=1)
    async def odd_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._outside_bet(interaction, BetCategory.ODD, ODD_SET, "Odd")

    @ui.button(label="Even", style=discord.ButtonStyle.primary, row=1)
    async def even_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._outside_bet(interaction, BetCategory.EVEN, EVEN_SET, "Even")

    # ── Row 2: Low, High, Dozens ────────────────────────────────────

    @ui.button(label="Low 1-18", style=discord.ButtonStyle.secondary, row=2)
    async def low_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._outside_bet(interaction, BetCategory.LOW, LOW_SET, "Low 1-18")

    @ui.button(label="High 19-36", style=discord.ButtonStyle.secondary, row=2)
    async def high_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._outside_bet(interaction, BetCategory.HIGH, HIGH_SET, "High 19-36")

    @ui.button(label="1st 12", style=discord.ButtonStyle.secondary, row=2)
    async def dozen1_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._outside_bet(interaction, BetCategory.DOZEN, DOZEN_1, "1st 12")

    @ui.button(label="2nd 12", style=discord.ButtonStyle.secondary, row=2)
    async def dozen2_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._outside_bet(interaction, BetCategory.DOZEN, DOZEN_2, "2nd 12")

    @ui.button(label="3rd 12", style=discord.ButtonStyle.secondary, row=2)
    async def dozen3_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._outside_bet(interaction, BetCategory.DOZEN, DOZEN_3, "3rd 12")

    # ── Row 3: Columns, Straight, Split ─────────────────────────────

    @ui.button(label="Col 1", style=discord.ButtonStyle.secondary, row=3)
    async def col1_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._outside_bet(interaction, BetCategory.COLUMN, COLUMN_1, "Col 1")

    @ui.button(label="Col 2", style=discord.ButtonStyle.secondary, row=3)
    async def col2_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._outside_bet(interaction, BetCategory.COLUMN, COLUMN_2, "Col 2")

    @ui.button(label="Col 3", style=discord.ButtonStyle.secondary, row=3)
    async def col3_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._outside_bet(interaction, BetCategory.COLUMN, COLUMN_3, "Col 3")

    @ui.button(label="Straight #", style=discord.ButtonStyle.success, row=3)
    async def straight_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        bal = await self._check_can_bet(interaction)
        if interaction.response.is_done():
            return
        await interaction.response.send_modal(StraightBetModal(self.table, self, bal))

    @ui.button(label="Split", style=discord.ButtonStyle.success, row=3)
    async def split_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        bal = await self._check_can_bet(interaction)
        if interaction.response.is_done():
            return
        await interaction.response.send_modal(SplitBetModal(self.table, self, bal))

    # ── Row 4: Street, Clear, New Round, Close ──────────────────────

    @ui.button(label="Street", style=discord.ButtonStyle.success, row=4)
    async def street_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        bal = await self._check_can_bet(interaction)
        if interaction.response.is_done():
            return
        await interaction.response.send_modal(StreetBetModal(self.table, self, bal))

    @ui.button(label="Clear Bets", style=discord.ButtonStyle.danger, emoji="\U0001f5d1\ufe0f", row=4)
    async def clear_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None or not player.bets:
            await interaction.response.send_message("You have no bets to clear.", ephemeral=True)
            return
        refund = sum(b.wager for b in player.bets)
        player.bets.clear()
        if refund > 0:
            await queries.update_casino_balance(str(uid), refund)
        # Remove player if they have no bets
        del self.table.players[uid]
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self,
        )

    @ui.button(label="New Round", style=discord.ButtonStyle.success, row=4)
    async def new_round_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "finished":
            return
        # Save bets for re-bet
        for uid, player in self.table.players.items():
            if player.bets:
                self.table.last_bets[uid] = [
                    (b.category.value, b.wager, tuple(b.numbers), b.label)
                    for b in player.bets
                ]
        self.table.players.clear()
        self.table.result = ""
        self.table.phase = "betting"
        self.table.round_num += 1
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_table_embed(self.table), view=self,
        )

    @ui.button(label="Close Table", style=discord.ButtonStyle.danger, row=4)
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message("Only the host can close.", ephemeral=True)
            return
        # Refund any active bets if closing during betting
        if self.table.phase == "betting":
            for player in self.table.players.values():
                refund = sum(b.wager for b in player.bets)
                if refund > 0:
                    try:
                        await queries.update_casino_balance(str(player.user_id), refund)
                    except Exception:
                        pass
        embed = discord.Embed(
            title="Roulette \u2014 Table Closed",
            description="Thanks for playing!",
            colour=discord.Colour.dark_grey(),
        )
        if self.table.spin_history:
            embed.add_field(name="History", value=_fmt_history(self.table.spin_history), inline=False)
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(self.table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    # ── Helpers ──────────────────────────────────────────────────────

    async def _check_can_bet(self, interaction: discord.Interaction) -> int:
        uid = interaction.user.id
        if self.table.phase != "betting":
            await interaction.response.send_message("Bets are closed!", ephemeral=True)
            return 0
        if len(self.table.players) >= MAX_PLAYERS and uid not in self.table.players:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return 0
        return await queries.get_or_create_casino_wallet(str(uid))

    async def _outside_bet(
        self, interaction: discord.Interaction,
        category: BetCategory, numbers: frozenset[str], label: str,
    ) -> None:
        bal = await self._check_can_bet(interaction)
        if interaction.response.is_done():
            return
        await interaction.response.send_modal(
            OutsideBetModal(self.table, category, numbers, label, self, bal),
        )

    async def _resolve(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "finished"
        table.spin_history.append(table.result)

        balances: dict[int, int] = {}
        for player in table.players.values():
            total_payout = sum(_resolve_bet(b, table.result) for b in player.bets)
            total_wagered = sum(b.wager for b in player.bets)
            if total_payout > 0:
                balances[player.user_id] = await queries.update_casino_balance(
                    str(player.user_id), total_payout,
                )
            else:
                balances[player.user_id] = (
                    await queries.get_casino_balance(str(player.user_id))
                ) or 0
            await queries.log_casino_result(
                str(player.user_id), "roulette", total_wagered, total_payout,
            )

        self._update_buttons()
        await interaction.edit_original_response(
            embed=_table_embed(table, balances=balances), view=self,
        )

    async def _abort(self, interaction: discord.Interaction, reason: str) -> None:
        table = self.table
        for player in table.players.values():
            refund = sum(b.wager for b in player.bets)
            if refund > 0:
                try:
                    await queries.update_casino_balance(str(player.user_id), refund)
                except Exception:
                    pass
        embed = discord.Embed(
            title="Roulette \u2014 Closed",
            description=reason,
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table
        if table.phase == "finished":
            self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    embed = discord.Embed(
                        title="Roulette \u2014 Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    pass
            return
        # Refund all bets
        for player in table.players.values():
            refund = sum(b.wager for b in player.bets)
            if refund > 0:
                try:
                    await queries.update_casino_balance(str(player.user_id), refund)
                except Exception:
                    pass
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="Roulette \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ────────────────────────────────────────────────────────────────────


class RouletteCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, RouletteTable] = {}

    @app_commands.command(name="roulette", description="Open an American roulette table")
    async def roulette(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a roulette table in this channel!",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = RouletteTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = RouletteView(table, self.active_tables)
        embed = _table_embed(table)
        embed.description = (
            "Place your bets using the buttons below, "
            "then the host spins the wheel!\n"
            "**Inside:** Straight (35:1) \u2022 Split (17:1) \u2022 Street (11:1)\n"
            "**Outside:** Red/Black \u2022 Odd/Even \u2022 High/Low (1:1) \u2022 Dozens/Columns (2:1)"
        )
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RouletteCog(bot))
