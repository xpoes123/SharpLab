"""Casino cog — multiplayer /countdown (Countdown Numbers) game."""

import asyncio
import random
import re
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
from bot.cogs._elo_helpers import update_elo_multiplayer
from bot.cogs._pool import compute_side_pot_payouts
import logging

log = logging.getLogger(__name__)
# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 1
ROUND_TIME = 30  # seconds

LARGE_NUMBERS = [25, 50, 75, 100]
SMALL_NUMBERS = [1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10]

LARGE_SPLIT_LABELS = {
    0: "0 Large (6 Small)",
    1: "1 Large (5 Small)",
    2: "2 Large (4 Small)",
    3: "3 Large (3 Small)",
    4: "4 Large (2 Small)",
}


# ── Safe Expression Evaluator ────────────────────────────────────────────────
#
# Tokenize → parse (recursive descent) → evaluate.
# Countdown rules: all intermediate results must be positive integers.
# Division only when it divides evenly. Subtraction only when result > 0.


class ExprError(Exception):
    """Raised when the expression is invalid."""


# Token types
_TOK_NUM = "NUM"
_TOK_OP = "OP"
_TOK_LPAREN = "LPAREN"
_TOK_RPAREN = "RPAREN"
_TOK_END = "END"

_TOKEN_RE = re.compile(r"\s*(?:(\d+)|([+\-*/])|(\()|(\)))")


def _tokenize(expr: str) -> list[tuple[str, str]]:
    """Tokenize an expression string into a list of (type, value) pairs."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if m is None:
            ch = expr[pos]
            if ch.isspace():
                pos += 1
                continue
            raise ExprError(f"Unexpected character: '{ch}'")
        if m.group(1) is not None:
            tokens.append((_TOK_NUM, m.group(1)))
        elif m.group(2) is not None:
            tokens.append((_TOK_OP, m.group(2)))
        elif m.group(3) is not None:
            tokens.append((_TOK_LPAREN, "("))
        elif m.group(4) is not None:
            tokens.append((_TOK_RPAREN, ")"))
        pos = m.end()
    tokens.append((_TOK_END, ""))
    return tokens


class _Parser:
    """Recursive-descent parser & evaluator for arithmetic expressions.

    Grammar:
        expr   -> term (('+' | '-') term)*
        term   -> factor (('*' | '/') factor)*
        factor -> NUMBER | '(' expr ')'

    All intermediate results must be positive integers (Countdown rules).
    """

    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.used_numbers: list[int] = []

    def _peek(self) -> tuple[str, str]:
        return self.tokens[self.pos]

    def _consume(self, expected_type: str | None = None) -> tuple[str, str]:
        tok = self.tokens[self.pos]
        if expected_type and tok[0] != expected_type:
            raise ExprError(f"Expected {expected_type}, got {tok[0]} ('{tok[1]}')")
        self.pos += 1
        return tok

    def parse(self) -> int:
        result = self._expr()
        if self._peek()[0] != _TOK_END:
            raise ExprError(
                f"Unexpected token after expression: '{self._peek()[1]}'"
            )
        return result

    def _expr(self) -> int:
        left = self._term()
        while self._peek()[0] == _TOK_OP and self._peek()[1] in ("+", "-"):
            op = self._consume()[1]
            right = self._term()
            if op == "+":
                left = left + right
            else:
                left = left - right
                if left <= 0:
                    raise ExprError(
                        f"Subtraction result must be positive (got {left})"
                    )
        return left

    def _term(self) -> int:
        left = self._factor()
        while self._peek()[0] == _TOK_OP and self._peek()[1] in ("*", "/"):
            op = self._consume()[1]
            right = self._factor()
            if op == "*":
                left = left * right
            else:
                if right == 0:
                    raise ExprError("Division by zero")
                if left % right != 0:
                    raise ExprError(
                        f"Division must be exact ({left} / {right} = {left / right})"
                    )
                left = left // right
        return left

    def _factor(self) -> int:
        tok_type, tok_val = self._peek()
        if tok_type == _TOK_NUM:
            self._consume()
            val = int(tok_val)
            if val <= 0:
                raise ExprError("Numbers must be positive")
            self.used_numbers.append(val)
            return val
        if tok_type == _TOK_LPAREN:
            self._consume()
            result = self._expr()
            self._consume(_TOK_RPAREN)
            return result
        raise ExprError(f"Unexpected token: '{tok_val}'")


def evaluate_expression(
    expr: str, available: list[int],
) -> tuple[int, list[int]]:
    """Evaluate an expression and validate against available numbers.

    Returns (result, used_numbers).
    Raises ExprError on invalid expression, bad arithmetic, or number misuse.
    """
    tokens = _tokenize(expr)
    parser = _Parser(tokens)
    result = parser.parse()

    used = parser.used_numbers
    if not used:
        raise ExprError("Expression must use at least one number")

    # Verify each used number comes from the available pool (each at most once)
    pool = list(available)
    for n in used:
        if n not in pool:
            raise ExprError(
                f"Number {n} is not available (or used too many times)"
            )
        pool.remove(n)

    return result, used


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class CountdownPlayer:
    user_id: int
    display_name: str
    bet: int
    total_points: int = 0
    submission: str | None = None
    result: int | None = None
    distance: int | None = None
    round_points: int = 0


@dataclass
class CountdownTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | picking | playing | finished
    players: dict[int, CountdownPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    numbers: list[int] = field(default_factory=list)
    target: int = 0
    round_task: asyncio.Task | None = field(default=None, repr=False)
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    num_large: int = 2


# ── Helpers ──────────────────────────────────────────────────────────────────


def _draw_numbers(num_large: int) -> list[int]:
    """Draw 6 numbers based on the large/small split."""
    num_small = 6 - num_large
    large_pool = list(LARGE_NUMBERS)
    small_pool = list(SMALL_NUMBERS)
    random.shuffle(large_pool)
    random.shuffle(small_pool)
    drawn = large_pool[:num_large] + small_pool[:num_small]
    random.shuffle(drawn)
    return drawn


def _generate_target() -> int:
    """Generate a random 3-digit target (100-999)."""
    return random.randint(100, 999)


def _score_distance(distance: int) -> int:
    """Return points for a given distance from target."""
    if distance == 0:
        return 10
    if distance <= 5:
        return 7
    if distance <= 10:
        return 5
    return 0


def _score_label(distance: int) -> str:
    """Return a human-readable label for scoring tier."""
    if distance == 0:
        return "EXACT! (10 pts)"
    if distance <= 5:
        return f"within 5 (7 pts)"
    if distance <= 10:
        return f"within 10 (5 pts)"
    return "too far (0 pts)"


def _numbers_display(numbers: list[int]) -> str:
    """Format the 6 available numbers prominently."""
    return "  ".join(f"**{n}**" for n in sorted(numbers))


def _leaderboard_lines(
    players: dict[int, CountdownPlayer],
) -> list[str]:
    """Build leaderboard lines sorted by total points descending."""
    ranked = sorted(players.values(), key=lambda p: p.total_points, reverse=True)
    lines: list[str] = []
    for i, p in enumerate(ranked, 1):
        if i == 1:
            medal = "\U0001f947"
        elif i == 2:
            medal = "\U0001f948"
        elif i == 3:
            medal = "\U0001f949"
        else:
            medal = f"#{i}"
        lines.append(f"{medal} **{p.display_name}** \u2014 {p.total_points} pts")
    return lines


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: CountdownTable) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values())
    embed = discord.Embed(
        title=f"\U0001f522 Countdown Numbers \u2014 Join the Table (Round {table.round_num})",
        description=(
            "Get as close to the target number as possible using +, -, *, / "
            "and any subset of the 6 drawn numbers.\n"
            "Exact = 10 pts | Within 5 = 7 pts | Within 10 = 5 pts"
        ),
        colour=discord.Colour.teal(),
    )
    if pot:
        embed.add_field(name="Pot", value=f"{pot}c", inline=True)
    if table.players:
        lines = [
            f"\U0001f3af **{p.display_name}** \u2014 {p.bet}c"
            + (f" ({p.total_points} pts)" if p.total_points > 0 else "")
            for p in table.players.values()
        ]
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet \u2014 click Join!*",
            inline=False,
        )
    if table.round_num > 1:
        lb = _leaderboard_lines(table.players)
        if lb:
            embed.add_field(
                name="Leaderboard", value="\n".join(lb), inline=False,
            )
    embed.set_footer(text=f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} players")
    return embed


def _picking_embed(table: CountdownTable) -> discord.Embed:
    embed = discord.Embed(
        title=f"\U0001f522 Countdown Numbers \u2014 Pick Numbers (Round {table.round_num})",
        description=(
            "**How many large numbers?**\n\n"
            "Large numbers: **25, 50, 75, 100**\n"
            "Small numbers: **1\u201310** (each appears twice in the pool)\n\n"
            "Host picks the split, then numbers are drawn and the clock starts!"
        ),
        colour=discord.Colour.dark_teal(),
    )
    embed.set_footer(text=f"Host: {table.host_name} \u2502 Waiting for host to pick...")
    return embed


def _playing_embed(table: CountdownTable, time_left: int | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"\U0001f522 Countdown Numbers \u2014 Round {table.round_num}",
        colour=discord.Colour.green(),
    )

    # Target prominently displayed
    embed.description = f"# Target: **{table.target}**"

    # Available numbers
    embed.add_field(
        name="Your Numbers",
        value=_numbers_display(table.numbers),
        inline=False,
    )

    # Timer
    if time_left is not None:
        if time_left <= 10:
            timer_text = f"\u23f0 **{time_left}s remaining!**"
        else:
            timer_text = f"\u23f1\ufe0f {time_left}s remaining"
        embed.add_field(name="Timer", value=timer_text, inline=True)

    # Submission status
    status_lines: list[str] = []
    for p in table.players.values():
        if p.submission is not None:
            status_lines.append(f"\u2705 **{p.display_name}** \u2014 submitted")
        else:
            status_lines.append(f"\u23f3 **{p.display_name}** \u2014 thinking...")
    embed.add_field(name="Players", value="\n".join(status_lines), inline=False)

    embed.set_footer(
        text=(
            f"Host: {table.host_name} \u2502 "
            "Use +, -, *, / and parentheses. Each number used at most once."
        ),
    )
    return embed


def _round_result_embed(table: CountdownTable) -> discord.Embed:
    embed = discord.Embed(
        title=f"\U0001f522 Countdown Numbers \u2014 Round {table.round_num} Results",
        description=f"Target: **{table.target}**\nNumbers: {_numbers_display(table.numbers)}",
        colour=discord.Colour.gold(),
    )

    # Sort players by distance (None = no submission, sorts last)
    ranked = sorted(
        table.players.values(),
        key=lambda p: (p.distance if p.distance is not None else 99999),
    )

    result_lines: list[str] = []
    for p in ranked:
        if p.submission is None:
            result_lines.append(
                f"\u274c **{p.display_name}** \u2014 no submission (0 pts)"
            )
        elif p.distance == 0:
            result_lines.append(
                f"\U0001f3af **{p.display_name}** \u2014 `{p.submission}` = "
                f"**{p.result}** \u2014 {_score_label(p.distance)} "
                f"(+{p.round_points})"
            )
        else:
            result_lines.append(
                f"\U0001f4ca **{p.display_name}** \u2014 `{p.submission}` = "
                f"**{p.result}** (off by {p.distance}) \u2014 "
                f"{_score_label(p.distance)} (+{p.round_points})"
            )

    embed.add_field(name="Submissions", value="\n".join(result_lines), inline=False)

    # Leaderboard
    lb = _leaderboard_lines(table.players)
    embed.add_field(name="Leaderboard", value="\n".join(lb), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _final_embed(
    table: CountdownTable,
    winner_uids: list[int],
    payouts: dict[int, int],
    balances: dict[int, int],
) -> discord.Embed:
    if len(winner_uids) == 1:
        winner = table.players[winner_uids[0]]
        desc = f"**{winner.display_name}** wins **{payouts.get(winner_uids[0], 0)}c**!"
    else:
        winner_pays = [payouts.get(uid, 0) for uid in winner_uids]
        names = [table.players[uid].display_name for uid in winner_uids]
        if len(set(winner_pays)) == 1:
            desc = f"**{' & '.join(names)}** tie and split the pot! ({winner_pays[0]}c each)"
        else:
            parts = [
                f"**{table.players[uid].display_name}** {payouts.get(uid, 0)}c"
                for uid in winner_uids
            ]
            desc = f"{', '.join(parts)} split the pot!"

    embed = discord.Embed(
        title="\U0001f522 Countdown Numbers \u2014 Final Results",
        description=desc,
        colour=discord.Colour.gold(),
    )

    # Leaderboard with payouts
    ranked = sorted(
        table.players.values(), key=lambda p: p.total_points, reverse=True,
    )
    winner_set = set(winner_uids)
    lines: list[str] = []
    for p in ranked:
        payout = payouts.get(p.user_id, 0)
        bal = balances.get(p.user_id, 0)
        net = payout - p.bet
        sign = "+" if net >= 0 else ""
        if p.user_id in winner_set:
            lines.append(
                f"\U0001f3c6 **{p.display_name}** \u2014 {p.total_points} pts \u2014 "
                f"{p.bet}c \u2192 {payout}c (**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        elif payout > 0:
            lines.append(
                f"\U0001f4b0 **{p.display_name}** \u2014 {p.total_points} pts \u2014 "
                f"{p.bet}c \u2192 {payout}c (**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        else:
            lines.append(
                f"\u274c **{p.display_name}** \u2014 {p.total_points} pts \u2014 "
                f"{p.bet}c \u2192 0c (**-{p.bet}c**) \u2014 bal: {bal}c"
            )

    embed.add_field(name="Final Standings", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinCountdownModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(
        self, table: CountdownTable, view: "CountdownTableView", balance: int,
    ) -> None:
        super().__init__(title="Join Countdown Numbers")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 100 (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message(
                "Enter a whole number.", ephemeral=True,
            )
            return
        if amt < 1:
            await interaction.response.send_message(
                "Must be at least 1 coin.", ephemeral=True,
            )
            return

        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in this game!", ephemeral=True,
            )
            return

        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        self.table.players[uid] = CountdownPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


class SubmitExpressionModal(ui.Modal):
    expression = ui.TextInput(
        label="Your expression",
        placeholder="e.g. (100 + 3) * 5 - 25",
        required=True,
        max_length=200,
        style=discord.TextStyle.short,
    )

    def __init__(
        self, table: CountdownTable, view: "CountdownTableView",
    ) -> None:
        super().__init__(title="Submit Your Answer")
        self.table = table
        self.table_view = view
        # Show the target and numbers in the placeholder
        nums = ", ".join(str(n) for n in sorted(self.table.numbers))
        self.expression.placeholder = f"Target: {self.table.target} | Numbers: {nums}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "Time is up!", ephemeral=True,
            )
            return

        expr_str = self.expression.value.strip()

        # Validate and evaluate
        try:
            result, _used = evaluate_expression(expr_str, self.table.numbers)
        except ExprError as e:
            await interaction.response.send_message(
                f"Invalid expression: {e}", ephemeral=True,
            )
            return

        distance = abs(result - self.table.target)
        points = _score_distance(distance)

        player.submission = expr_str
        player.result = result
        player.distance = distance
        player.round_points = points

        # Confirm to the player
        if distance == 0:
            msg = f"**EXACT!** `{expr_str}` = **{result}** (+10 pts)"
        else:
            msg = (
                f"Submitted: `{expr_str}` = **{result}** "
                f"(off by {distance}, {_score_label(distance)})"
            )

        await interaction.response.send_message(msg, ephemeral=True)

        # Update the main embed to show submission checkmarks
        if self.table.message:
            try:
                await self.table.message.edit(
                    embed=_playing_embed(self.table),
                    view=self.table_view,
                )
            except discord.HTTPException:
                pass


# ── View ─────────────────────────────────────────────────────────────────────


class CountdownTableView(ui.View):
    def __init__(
        self, table: CountdownTable, active_tables: dict[int, CountdownTable],
    ) -> None:
        super().__init__(timeout=600)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        picking = phase == "picking"
        playing = phase == "playing"
        finished = phase == "finished"

        # Row 0: Start, Join, Re-bet, Leave
        self.start_btn.disabled = (
            not betting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = playing or picking

        # Row 1: Large number picker buttons (only active during picking phase)
        for btn in (
            self.pick_0_btn, self.pick_1_btn, self.pick_2_btn,
            self.pick_3_btn, self.pick_4_btn,
        ):
            btn.disabled = not picking

        # Row 2: Submit, My Numbers
        self.submit_btn.disabled = not playing
        self.my_numbers_btn.disabled = not playing

        # Row 3: New Round, Close Table
        self.new_round_btn.disabled = not finished
        self.close_btn.disabled = playing or picking

    # ── Row 0 ────────────────────────────────────────────────────────────────

    @ui.button(
        label="Start", style=discord.ButtonStyle.success,
        emoji="\u25b6\ufe0f", row=0,
    )
    async def start_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can start!", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Already started!", ephemeral=True,
            )
            return
        if len(self.table.players) < MIN_PLAYERS:
            await interaction.response.send_message(
                f"Need at least {MIN_PLAYERS} players!", ephemeral=True,
            )
            return
        # Move to picking phase
        self.table.phase = "picking"
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_picking_embed(self.table), view=self,
        )

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary,
        emoji="\U0001f3af", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Game in progress! Wait for the next round.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in!", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message(
                "Table is full!", ephemeral=True,
            )
            return
        bal = await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(
            JoinCountdownModal(self.table, self, bal),
        )

    @ui.button(
        label="Re-bet", style=discord.ButtonStyle.primary,
        emoji="\U0001f504", row=0,
    )
    async def rebet_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Game in progress!", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in!", ephemeral=True,
            )
            return
        last = self.table.last_bets.get(uid)
        if last is None:
            await interaction.response.send_message(
                "No previous bet \u2014 use Join instead.", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message(
                "Table is full!", ephemeral=True,
            )
            return
        name, amt = last
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for {amt}c re-bet! (have {bal}c)",
                ephemeral=True,
            )
            return
        self.table.players[uid] = CountdownPlayer(
            user_id=uid, display_name=name, bet=amt,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Leave", style=discord.ButtonStyle.secondary,
        emoji="\U0001f6aa", row=0,
    )
    async def leave_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message(
                "You're not at this table.", ephemeral=True,
            )
            return
        if self.table.phase in ("playing", "picking"):
            await interaction.response.send_message(
                "Can't leave mid-game!", ephemeral=True,
            )
            return
        if self.table.phase == "betting":
            await queries.update_casino_balance(str(uid), player.bet)
            del self.table.players[uid]
            self._update_buttons()
            await interaction.response.edit_message(
                embed=_betting_embed(self.table), view=self,
            )
            return
        await interaction.response.send_message(
            "Round is over. Wait for New Round or close.", ephemeral=True,
        )

    # ── Row 1: Large/Small picker ────────────────────────────────────────────

    async def _handle_pick(
        self, interaction: discord.Interaction, num_large: int,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host picks the numbers!", ephemeral=True,
            )
            return
        if self.table.phase != "picking":
            await interaction.response.send_message(
                "Not in picking phase!", ephemeral=True,
            )
            return
        self.table.num_large = num_large
        await self._start_round(interaction)

    @ui.button(label="0 Large", style=discord.ButtonStyle.secondary, row=1)
    async def pick_0_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._handle_pick(interaction, 0)

    @ui.button(label="1 Large", style=discord.ButtonStyle.secondary, row=1)
    async def pick_1_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._handle_pick(interaction, 1)

    @ui.button(label="2 Large", style=discord.ButtonStyle.primary, row=1)
    async def pick_2_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._handle_pick(interaction, 2)

    @ui.button(label="3 Large", style=discord.ButtonStyle.secondary, row=1)
    async def pick_3_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._handle_pick(interaction, 3)

    @ui.button(label="4 Large", style=discord.ButtonStyle.secondary, row=1)
    async def pick_4_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._handle_pick(interaction, 4)

    # ── Row 2: Submit, My Numbers ────────────────────────────────────────────

    @ui.button(
        label="Submit", style=discord.ButtonStyle.success,
        emoji="\u270d\ufe0f", row=2,
    )
    async def submit_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "Not in playing phase!", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        player = self.table.players[uid]
        if player.submission is not None:
            await interaction.response.send_message(
                f"You already submitted: `{player.submission}` = **{player.result}**\n"
                "You can submit again to overwrite.",
                ephemeral=True,
            )
        await interaction.response.send_modal(
            SubmitExpressionModal(self.table, self),
        )

    @ui.button(
        label="My Numbers", style=discord.ButtonStyle.secondary,
        emoji="\U0001f440", row=2,
    )
    async def my_numbers_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "No round in progress.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        nums = ", ".join(str(n) for n in sorted(self.table.numbers))
        player = self.table.players[uid]
        msg = f"**Target:** {self.table.target}\n**Numbers:** {nums}"
        if player.submission is not None:
            msg += f"\n**Your submission:** `{player.submission}` = **{player.result}**"
        await interaction.response.send_message(msg, ephemeral=True)

    # ── Row 3: New Round, Close Table ────────────────────────────────────────

    @ui.button(
        label="New Round", style=discord.ButtonStyle.success,
        emoji="\u25b6\ufe0f", row=3,
    )
    async def new_round_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can start a new round!", ephemeral=True,
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
            embed=_picking_embed(self.table), view=self,
        )

    @ui.button(
        label="Close Table", style=discord.ButtonStyle.danger,
        emoji="\u2716\ufe0f", row=3,
    )
    async def close_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can close the table!", ephemeral=True,
            )
            return
        if self.table.phase in ("playing", "picking"):
            await interaction.response.send_message(
                "Can't close mid-game!", ephemeral=True,
            )
            return

        # If still betting, refund everyone
        if self.table.phase == "betting":
            for p in self.table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    log.exception("Unhandled error in countdown.py")
            await self._close(interaction, "Table closed by host. All bets refunded.")
            return

        # Finished phase — resolve and pay out
        await self._resolve_final(interaction)

    # ── Game logic ───────────────────────────────────────────────────────────

    async def _start_round(self, interaction: discord.Interaction) -> None:
        """Draw numbers, set target, begin the 30-second timer."""
        table = self.table
        table.numbers = _draw_numbers(table.num_large)
        table.target = _generate_target()
        table.phase = "playing"

        # Reset per-round state for each player
        for p in table.players.values():
            p.submission = None
            p.result = None
            p.distance = None
            p.round_points = 0

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(table, ROUND_TIME), view=self,
        )
        table.round_task = asyncio.create_task(self._timer_loop())

    async def _timer_loop(self) -> None:
        """30-second countdown. Updates embed at 20s and 10s. Ends the round."""
        table = self.table
        try:
            # Wait 10s, then update at 20s remaining
            await asyncio.sleep(10)
            if table.phase == "playing" and table.message:
                try:
                    await table.message.edit(
                        embed=_playing_embed(table, 20), view=self,
                    )
                except discord.HTTPException:
                    pass

            # Wait 10s, then update at 10s remaining
            await asyncio.sleep(10)
            if table.phase == "playing" and table.message:
                try:
                    await table.message.edit(
                        embed=_playing_embed(table, 10), view=self,
                    )
                except discord.HTTPException:
                    pass

            # Wait final 10s
            await asyncio.sleep(10)

            if table.phase != "playing":
                return

            # Time's up — score the round
            await self._score_round()

        except asyncio.CancelledError:
            pass
        except Exception:
            # Failsafe: if something goes wrong, try to end gracefully
            if table.phase == "playing":
                table.phase = "finished"

    async def _score_round(self) -> None:
        """Score all submissions and show results."""
        table = self.table
        table.phase = "finished"

        # Award points
        for p in table.players.values():
            if p.submission is not None and p.distance is not None:
                points = _score_distance(p.distance)
                p.round_points = points
                p.total_points += points
            else:
                p.round_points = 0

        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(
                    embed=_round_result_embed(table), view=self,
                )
            except discord.HTTPException:
                pass

    async def _resolve_final(self, interaction: discord.Interaction) -> None:
        """Pay out based on total points and close the table."""
        table = self.table

        # Determine winner(s) — highest total points
        if not table.players:
            await self._close(interaction, "Table closed. No players.")
            return

        max_points = max(p.total_points for p in table.players.values())
        winner_uids = [
            uid for uid, p in table.players.items()
            if p.total_points == max_points
        ]

        # Side-pot payouts
        bets = {uid: p.bet for uid, p in table.players.items()}
        payouts = compute_side_pot_payouts(bets, winner_uids)

        # Credit payouts, log results
        balances: dict[int, int] = {}
        for uid, player in table.players.items():
            payout = payouts.get(uid, 0)
            if payout > 0:
                balances[uid] = await queries.update_casino_balance(
                    str(uid), payout,
                )
            else:
                bal = await queries.get_casino_balance(str(uid))
                balances[uid] = bal or 0
            await queries.log_casino_result(
                str(uid), "countdown", player.bet, payout,
            )

        if len(table.players) >= 2:
            sorted_p = sorted(table.players.values(), key=lambda p: p.total_points, reverse=True)
            finish_order = [p.user_id for p in sorted_p]
            try:
                await update_elo_multiplayer(finish_order, "countdown", "countdown", scores={p.user_id: p.total_points for p in sorted_p})
            except Exception:
                log.exception("Unhandled error in countdown.py")

        embed = _final_embed(table, winner_uids, payouts, balances)

        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _start_new_round(self) -> None:
        """Reset for a new round, keeping players and their total points."""
        table = self.table
        table.phase = "picking"
        table.round_num += 1
        table.numbers.clear()
        table.target = 0
        table.round_task = None

        # Save last bets
        for uid, player in table.players.items():
            table.last_bets[uid] = (player.display_name, player.bet)

        # Reset per-round player state but keep total_points and bet
        for p in table.players.values():
            p.submission = None
            p.result = None
            p.distance = None
            p.round_points = 0

    async def _refund_all(self) -> None:
        for p in self.table.players.values():
            try:
                await queries.update_casino_balance(str(p.user_id), p.bet)
            except Exception:
                log.exception("Unhandled error in countdown.py")

    async def _close(
        self, interaction: discord.Interaction, reason: str,
    ) -> None:
        embed = discord.Embed(
            title="\U0001f522 Countdown Numbers \u2014 Closed",
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

        if table.round_task and not table.round_task.done():
            table.round_task.cancel()

        if table.phase == "finished":
            self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    embed = discord.Embed(
                        title="\U0001f522 Countdown Numbers \u2014 Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    log.exception("Unhandled error in countdown.py")
            return

        # Betting, picking, or playing — refund all
        await self._refund_all()
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="\U0001f522 Countdown Numbers \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("Unhandled error in countdown.py")


# ── Cog ──────────────────────────────────────────────────────────────────────


class CountdownCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, CountdownTable] = {}

    async def countdown(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            _has_running = any(
                (t := getattr(existing, n, None)) is not None and not t.done()
                for n in ("game_task", "race_task", "sim_task", "round_task", "_round_task", "trade_task", "fly_task", "_shot_clock_task", "_countdown_task")
            )
            if _has_running:
                await interaction.response.send_message(
                    "There's already a Countdown Numbers table in this channel!",
                    ephemeral=True,
                )
                return
            del self.active_tables[channel_id]

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = CountdownTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        view = CountdownTableView(table, self.active_tables)
        embed = _betting_embed(table)
        try:
            await interaction.response.send_message(embed=embed, view=view)
        except discord.NotFound:
            return  # interaction expired — don't leave a ghost table
        self.active_tables[channel_id] = table
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CountdownCog(bot))
