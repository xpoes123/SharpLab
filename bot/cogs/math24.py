"""Casino cog — multiplayer /math24 party game."""

import asyncio
import random
import time
from dataclasses import dataclass, field
from itertools import permutations, product

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MAX_BET = 500
MIN_PLAYERS = 2
HOUSE_EDGE = 0.05
ROUND_TIME = 60  # seconds per round
TARGET = 24
NUM_COUNT = 4
NUM_MIN = 1
NUM_MAX = 13
EPSILON = 1e-9

OPS = ["+", "-", "*", "/"]


# ── Safe Expression Evaluator ────────────────────────────────────────────────
# Tokenize → shunting-yard → postfix evaluate.  No eval().


class ExprError(Exception):
    """Raised when the player's expression is invalid."""


def _tokenize(expr: str) -> list[str]:
    """Break an expression string into number/operator/paren tokens.

    Rejects any character that isn't a digit, operator, parenthesis, or whitespace.
    Supports multi-digit numbers (1-13).
    """
    tokens: list[str] = []
    i = 0
    allowed_chars = set("0123456789+-*/() ")
    while i < len(expr):
        ch = expr[i]
        if ch not in allowed_chars:
            raise ExprError(f"Invalid character: `{ch}`")
        if ch == " ":
            i += 1
            continue
        if ch.isdigit():
            j = i
            while j < len(expr) and expr[j].isdigit():
                j += 1
            tokens.append(expr[i:j])
            i = j
        else:
            tokens.append(ch)
            i += 1
    return tokens


_PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2}


def _shunting_yard(tokens: list[str]) -> list[str]:
    """Convert infix tokens to postfix (RPN) via shunting-yard."""
    output: list[str] = []
    op_stack: list[str] = []
    prev_token: str | None = None

    for tok in tokens:
        # Handle unary minus: if '-' appears at start or after '(' or after an operator
        if tok == "-" and (prev_token is None or prev_token == "(" or prev_token in _PRECEDENCE):
            # Treat as unary: push a 0 so "- X" becomes "0 X -"
            output.append("0")
            op_stack.append("-")
            prev_token = tok
            continue

        if tok.isdigit() or (len(tok) > 1 and tok.isdigit()):
            output.append(tok)
        elif tok in _PRECEDENCE:
            while (
                op_stack
                and op_stack[-1] != "("
                and op_stack[-1] in _PRECEDENCE
                and _PRECEDENCE[op_stack[-1]] >= _PRECEDENCE[tok]
            ):
                output.append(op_stack.pop())
            op_stack.append(tok)
        elif tok == "(":
            op_stack.append(tok)
        elif tok == ")":
            while op_stack and op_stack[-1] != "(":
                output.append(op_stack.pop())
            if not op_stack:
                raise ExprError("Mismatched parentheses.")
            op_stack.pop()  # discard the '('
        else:
            raise ExprError(f"Unexpected token: `{tok}`")
        prev_token = tok

    while op_stack:
        top = op_stack.pop()
        if top == "(":
            raise ExprError("Mismatched parentheses.")
        output.append(top)
    return output


def _eval_rpn(rpn: list[str]) -> float:
    """Evaluate a postfix expression. Returns the numeric result."""
    stack: list[float] = []
    for tok in rpn:
        if tok in _PRECEDENCE:
            if len(stack) < 2:
                raise ExprError("Malformed expression.")
            b = stack.pop()
            a = stack.pop()
            if tok == "+":
                stack.append(a + b)
            elif tok == "-":
                stack.append(a - b)
            elif tok == "*":
                stack.append(a * b)
            elif tok == "/":
                if abs(b) < EPSILON:
                    raise ExprError("Division by zero.")
                stack.append(a / b)
        else:
            try:
                stack.append(float(tok))
            except ValueError:
                raise ExprError(f"Bad number: `{tok}`")
    if len(stack) != 1:
        raise ExprError("Malformed expression.")
    return stack[0]


def _extract_numbers(tokens: list[str]) -> list[int]:
    """Pull out all integer tokens from the tokenized expression."""
    nums: list[int] = []
    for tok in tokens:
        if tok.isdigit() or (len(tok) > 1 and all(c.isdigit() for c in tok)):
            nums.append(int(tok))
    return nums


def validate_expression(expr: str, dealt: list[int]) -> tuple[bool, str, float | None]:
    """Validate a player's expression against the dealt numbers.

    Returns (ok, message, result).
    - ok=True if expression is valid and equals 24.
    - message explains what went wrong or confirms success.
    """
    try:
        tokens = _tokenize(expr)
    except ExprError as e:
        return False, str(e), None

    # Extract numbers used
    used_nums = _extract_numbers(tokens)

    # Must use exactly 4 numbers
    if len(used_nums) != NUM_COUNT:
        return False, f"Must use exactly {NUM_COUNT} numbers (you used {len(used_nums)}).", None

    # Each number must match one of the dealt numbers exactly once
    dealt_sorted = sorted(dealt)
    used_sorted = sorted(used_nums)
    if used_sorted != dealt_sorted:
        return (
            False,
            f"Must use the dealt numbers {dealt} exactly once each. You used {used_nums}.",
            None,
        )

    # Parse and evaluate
    try:
        rpn = _shunting_yard(tokens)
        result = _eval_rpn(rpn)
    except ExprError as e:
        return False, str(e), None

    # Check result
    if abs(result - TARGET) < EPSILON:
        return True, "Correct!", result
    return False, f"Expression equals {result:.4g}, not {TARGET}.", result


# ── Solvability Checker ──────────────────────────────────────────────────────
# Brute-force: 4! permutations x 4^3 operator combos x 5 tree structures.


def _try_eval(a: float, op: str, b: float) -> float | None:
    """Apply a single binary op, return None if division by zero."""
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        if abs(b) < EPSILON:
            return None
        return a / b
    return None


def _format_solution(nums: tuple[int, ...], ops: tuple[str, ...], tree: int) -> str:
    """Format a solution as a human-readable expression string."""
    a, b, c, d = nums
    o1, o2, o3 = ops

    if tree == 0:
        # ((a o1 b) o2 c) o3 d
        return f"(({a} {o1} {b}) {o2} {c}) {o3} {d}"
    if tree == 1:
        # (a o1 (b o2 c)) o3 d
        return f"({a} {o1} ({b} {o2} {c})) {o3} {d}"
    if tree == 2:
        # (a o1 b) o2 (c o3 d)
        return f"({a} {o1} {b}) {o2} ({c} {o3} {d})"
    if tree == 3:
        # a o1 ((b o2 c) o3 d)
        return f"{a} {o1} (({b} {o2} {c}) {o3} {d})"
    if tree == 4:
        # a o1 (b o2 (c o3 d))
        return f"{a} {o1} ({b} {o2} ({c} {o3} {d}))"
    return ""


def _eval_tree(nums: tuple[float, ...], ops: tuple[str, ...], tree: int) -> float | None:
    """Evaluate one of the 5 binary-tree structures for 4 numbers and 3 operators."""
    a, b, c, d = nums
    o1, o2, o3 = ops

    if tree == 0:
        # ((a o1 b) o2 c) o3 d
        r = _try_eval(a, o1, b)
        if r is None:
            return None
        r = _try_eval(r, o2, c)
        if r is None:
            return None
        return _try_eval(r, o3, d)

    if tree == 1:
        # (a o1 (b o2 c)) o3 d
        r = _try_eval(b, o2, c)
        if r is None:
            return None
        r = _try_eval(a, o1, r)
        if r is None:
            return None
        return _try_eval(r, o3, d)

    if tree == 2:
        # (a o1 b) o2 (c o3 d)
        left = _try_eval(a, o1, b)
        if left is None:
            return None
        right = _try_eval(c, o3, d)
        if right is None:
            return None
        return _try_eval(left, o2, right)

    if tree == 3:
        # a o1 ((b o2 c) o3 d)
        r = _try_eval(b, o2, c)
        if r is None:
            return None
        r = _try_eval(r, o3, d)
        if r is None:
            return None
        return _try_eval(a, o1, r)

    if tree == 4:
        # a o1 (b o2 (c o3 d))
        r = _try_eval(c, o3, d)
        if r is None:
            return None
        r = _try_eval(b, o2, r)
        if r is None:
            return None
        return _try_eval(a, o1, r)

    return None


def find_all_solutions(numbers: list[int]) -> list[str]:
    """Find all distinct solutions for a set of 4 numbers that equal 24.

    Returns a list of expression strings.  We deduplicate by expression text.
    """
    solutions: set[str] = set()
    for perm in permutations(numbers):
        for ops in product(OPS, repeat=3):
            for tree in range(5):
                result = _eval_tree(
                    (float(perm[0]), float(perm[1]), float(perm[2]), float(perm[3])),
                    ops,
                    tree,
                )
                if result is not None and abs(result - TARGET) < EPSILON:
                    expr = _format_solution(perm, ops, tree)
                    solutions.add(expr)
    return list(solutions)


def has_solution(numbers: list[int]) -> bool:
    """Quick check: can these 4 numbers make 24?"""
    for perm in permutations(numbers):
        for ops in product(OPS, repeat=3):
            for tree in range(5):
                result = _eval_tree(
                    (float(perm[0]), float(perm[1]), float(perm[2]), float(perm[3])),
                    ops,
                    tree,
                )
                if result is not None and abs(result - TARGET) < EPSILON:
                    return True
    return False


def generate_solvable_numbers() -> tuple[list[int], str]:
    """Generate 4 random numbers (1-13) that have at least one solution.

    Returns (numbers, one_solution_string).
    """
    while True:
        nums = [random.randint(NUM_MIN, NUM_MAX) for _ in range(NUM_COUNT)]
        solutions = find_all_solutions(nums)
        if solutions:
            return nums, solutions[0]


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class Math24Player:
    user_id: int
    display_name: str
    bet: int
    rounds_won: int = 0
    answer: str | None = None
    answer_time: float | None = None


@dataclass
class Math24Table:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | finished
    players: dict[int, Math24Player] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    numbers: list[int] = field(default_factory=list)
    solution: str = ""
    round_start_time: float = 0.0
    round_winner: int | None = None
    round_task: asyncio.Task | None = field(default=None, repr=False)
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    total_rounds_played: int = 0


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: Math24Table) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values())
    embed = discord.Embed(
        title=f"Math 24 \u2014 Join the Table (Round {table.round_num})",
        description=(
            "Race to find a math expression using all 4 numbers that equals **24**!\n"
            "Use `+`, `-`, `*`, `/`, and parentheses. First correct answer wins the round."
        ),
        colour=discord.Colour.blue(),
    )
    if pot:
        embed.add_field(name="Pot", value=f"{pot}c (5% house rake)", inline=True)
    if table.players:
        lines = [
            f"\U0001f9ee **{p.display_name}** \u2014 {p.bet}c"
            + (f" ({p.rounds_won}W)" if p.rounds_won > 0 else "")
            for p in table.players.values()
        ]
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet \u2014 click Join!*",
            inline=False,
        )
    embed.set_footer(
        text=(
            f"Host: {table.host_name} \u2502 "
            f"Max bet {MAX_BET}c \u2502 "
            f"Min {MIN_PLAYERS} players"
        ),
    )
    return embed


def _numbers_display(numbers: list[int]) -> str:
    """Format the 4 numbers in a big, bold, visually clear way."""
    card_map = {
        1: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7",
        8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K",
    }
    cards = [card_map.get(n, str(n)) for n in numbers]
    return "   ".join(f"**[ {c} ]**" for c in cards)


def _scoreboard(table: Math24Table) -> str:
    """Build a sorted scoreboard of rounds won."""
    sorted_players = sorted(
        table.players.values(), key=lambda p: p.rounds_won, reverse=True,
    )
    lines: list[str] = []
    for i, p in enumerate(sorted_players):
        prefix = "\U0001f451" if i == 0 and p.rounds_won > 0 else "\U0001f9ee"
        lines.append(f"{prefix} **{p.display_name}** \u2014 {p.rounds_won}W")
    return "\n".join(lines) if lines else "No scores yet"


def _playing_embed(table: Math24Table, remaining: int | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"Math 24 \u2014 Round {table.round_num}",
        colour=discord.Colour.gold(),
    )

    # Numbers display
    embed.description = (
        f"# {_numbers_display(table.numbers)}\n"
        f"*Numbers (raw): {', '.join(str(n) for n in table.numbers)}*\n\n"
        "Use all 4 numbers exactly once with `+  -  *  /  ( )` to make **24**.\n"
        "Click **Answer** to submit your expression!"
    )

    # Time remaining
    if remaining is not None:
        embed.add_field(name="Time Remaining", value=f"{remaining}s", inline=True)
    else:
        embed.add_field(name="Time Remaining", value=f"{ROUND_TIME}s", inline=True)

    pot = sum(p.bet for p in table.players.values())
    embed.add_field(name="Pot", value=f"{pot}c", inline=True)

    # Scoreboard
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)

    embed.set_footer(
        text=f"Host: {table.host_name} \u2502 Round {table.round_num}",
    )
    return embed


def _round_result_embed(table: Math24Table) -> discord.Embed:
    winner = table.players[table.round_winner]
    solve_time = winner.answer_time - table.round_start_time

    embed = discord.Embed(
        title=f"Math 24 \u2014 Round {table.round_num} Winner!",
        colour=discord.Colour.green(),
    )
    embed.description = (
        f"\U0001f3c6 **{winner.display_name}** solved it in **{solve_time:.1f}s**!\n\n"
        f"Numbers: {', '.join(str(n) for n in table.numbers)}\n"
        f"Expression: `{winner.answer}`"
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(
        text=f"Host: {table.host_name} \u2502 Host can start a New Round or Close Table",
    )
    return embed


def _timeout_embed(table: Math24Table) -> discord.Embed:
    embed = discord.Embed(
        title=f"Math 24 \u2014 Round {table.round_num} (Time's Up!)",
        colour=discord.Colour.dark_grey(),
    )
    embed.description = (
        f"Nobody solved it in {ROUND_TIME} seconds!\n\n"
        f"Numbers: {', '.join(str(n) for n in table.numbers)}\n"
        f"One solution was: `{table.solution}`"
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(
        text=f"Host: {table.host_name} \u2502 No coins awarded this round",
    )
    return embed


def _final_embed(
    table: Math24Table, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    # Find the winner(s) — most rounds won
    max_wins = max(p.rounds_won for p in table.players.values())
    winners = [p for p in table.players.values() if p.rounds_won == max_wins]

    pot = sum(p.bet for p in table.players.values())
    house_take = max(1, int(pot * HOUSE_EDGE))
    prize_pool = pot - house_take

    if max_wins == 0:
        # Nobody won any rounds — full refund
        desc = "No rounds were won \u2014 all bets refunded!"
        payout_each = 0  # handled by refund logic
    elif len(winners) == 1:
        w = winners[0]
        desc = f"\U0001f3c6 **{w.display_name}** wins the pot of **{prize_pool}c**! ({w.rounds_won}W)"
        payout_each = prize_pool
    else:
        payout_each = prize_pool // len(winners)
        names = " & ".join(f"**{w.display_name}**" for w in winners)
        desc = (
            f"\U0001f3c6 {names} tied with **{max_wins}W** each \u2014 "
            f"splitting **{prize_pool}c** ({payout_each}c each)!"
        )

    embed = discord.Embed(
        title="Math 24 \u2014 Final Results",
        description=desc,
        colour=discord.Colour.gold(),
    )

    lines: list[str] = []
    for p in sorted(table.players.values(), key=lambda x: x.rounds_won, reverse=True):
        bal = balances.get(p.user_id, 0) if balances else 0
        is_winner = p.rounds_won == max_wins and max_wins > 0
        if is_winner:
            net = payout_each - p.bet
            sign = "+" if net >= 0 else ""
            lines.append(
                f"\U0001f3c6 **{p.display_name}** ({p.rounds_won}W) \u2014 "
                f"{p.bet}c \u2192 {payout_each}c "
                f"(**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        else:
            lines.append(
                f"\u274c **{p.display_name}** ({p.rounds_won}W) \u2014 "
                f"{p.bet}c \u2192 0c "
                f"(**-{p.bet}c**) \u2014 bal: {bal}c"
            )
    embed.add_field(name="Results", value="\n".join(lines), inline=False)
    embed.add_field(
        name="Rounds Played", value=str(table.total_rounds_played), inline=True,
    )
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinMath24Modal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(
        self, table: Math24Table, view: "Math24TableView", balance: int,
    ) -> None:
        super().__init__(title="Join Math 24")
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
        if amt > MAX_BET:
            await interaction.response.send_message(
                f"Max bet is {MAX_BET}c.", ephemeral=True,
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

        self.table.players[uid] = Math24Player(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


class AnswerModal(ui.Modal):
    expression = ui.TextInput(
        label="Your expression (e.g. (8-2)*(5-1))",
        placeholder="Use all 4 numbers with + - * / ( )",
        required=True,
        max_length=100,
        style=discord.TextStyle.short,
    )

    def __init__(self, table: Math24Table, view: "Math24TableView") -> None:
        super().__init__(title="Math 24 \u2014 Answer")
        self.table = table
        self.table_view = view
        nums_str = ", ".join(str(n) for n in table.numbers)
        self.expression.placeholder = f"Numbers: {nums_str}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id

        # Must be in the game
        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return

        # Must be in playing phase
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "Round is not active!", ephemeral=True,
            )
            return

        # Already won this round?
        if self.table.round_winner is not None:
            await interaction.response.send_message(
                "Someone already solved this round!", ephemeral=True,
            )
            return

        expr = self.expression.value.strip()
        ok, message, _result = validate_expression(expr, self.table.numbers)

        if not ok:
            await interaction.response.send_message(
                f"Incorrect: {message}", ephemeral=True,
            )
            return

        # Winner!
        now = time.monotonic()
        player = self.table.players[uid]
        player.answer = expr
        player.answer_time = now
        player.rounds_won += 1

        self.table.round_winner = uid
        self.table.phase = "finished"
        self.table.total_rounds_played += 1

        # Cancel the timeout task
        if self.table.round_task and not self.table.round_task.done():
            self.table.round_task.cancel()

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_round_result_embed(self.table), view=self.table_view,
        )


# ── View ─────────────────────────────────────────────────────────────────────


class Math24TableView(ui.View):
    def __init__(
        self, table: Math24Table, active_tables: dict[int, Math24Table],
    ) -> None:
        super().__init__(timeout=600)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        playing = phase == "playing"
        finished = phase == "finished"

        # Row 0: Start, Join, Re-bet, Leave
        self.start_btn.disabled = (
            not betting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = playing

        # Row 1: Answer, Hint
        self.answer_btn.disabled = not playing
        self.hint_btn.disabled = not playing

        # Row 2: New Round, Close Table
        self.new_round_btn.disabled = not finished
        self.close_btn.disabled = playing

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
        await self._start_round(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary,
        emoji="\U0001f9ee", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Round in progress! Wait for the next one.", ephemeral=True,
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
            JoinMath24Modal(self.table, self, bal),
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
                "Round in progress!", ephemeral=True,
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
        self.table.players[uid] = Math24Player(
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
        if self.table.phase == "playing":
            await interaction.response.send_message(
                "Can't leave mid-round!", ephemeral=True,
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

    # ── Row 1 ────────────────────────────────────────────────────────────────

    @ui.button(
        label="Answer", style=discord.ButtonStyle.success,
        emoji="\u270d\ufe0f", row=1,
    )
    async def answer_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "No round in progress!", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if self.table.round_winner is not None:
            await interaction.response.send_message(
                "Someone already solved this round!", ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            AnswerModal(self.table, self),
        )

    @ui.button(
        label="Hint", style=discord.ButtonStyle.secondary,
        emoji="\U0001f4a1", row=1,
    )
    async def hint_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "No round in progress!", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        solutions = find_all_solutions(self.table.numbers)
        count = len(solutions)
        if count == 1:
            msg = "There is **1** possible solution. Good luck!"
        else:
            msg = f"There are **{count}** possible solutions. Keep trying!"
        await interaction.response.send_message(msg, ephemeral=True)

    # ── Row 2 ────────────────────────────────────────────────────────────────

    @ui.button(
        label="New Round", style=discord.ButtonStyle.success,
        emoji="\u25b6\ufe0f", row=2,
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
        self._prepare_new_round()
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(self.table), view=self,
        )
        # Start the timer
        self.table.round_start_time = time.monotonic()
        self.table.round_task = asyncio.create_task(self._round_timer())

    @ui.button(
        label="Close Table", style=discord.ButtonStyle.danger,
        emoji="\u2716\ufe0f", row=2,
    )
    async def close_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can close the table!", ephemeral=True,
            )
            return
        if self.table.phase == "playing":
            await interaction.response.send_message(
                "Can't close mid-round! Wait for the timer to expire.", ephemeral=True,
            )
            return
        await self._close_table(interaction)

    # ── Game logic ───────────────────────────────────────────────────────────

    async def _start_round(self, interaction: discord.Interaction) -> None:
        """Start the first round from betting phase."""
        table = self.table
        table.phase = "playing"

        # Generate solvable numbers
        nums, solution = generate_solvable_numbers()
        table.numbers = nums
        table.solution = solution
        table.round_winner = None
        table.round_start_time = time.monotonic()

        # Reset per-round player state
        for p in table.players.values():
            p.answer = None
            p.answer_time = None

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(table), view=self,
        )
        table.round_task = asyncio.create_task(self._round_timer())

    def _prepare_new_round(self) -> None:
        """Prepare state for a new round (players stay, new numbers dealt)."""
        table = self.table
        table.phase = "playing"
        table.round_num += 1

        # Generate solvable numbers
        nums, solution = generate_solvable_numbers()
        table.numbers = nums
        table.solution = solution
        table.round_winner = None

        # Reset per-round player state
        for p in table.players.values():
            p.answer = None
            p.answer_time = None

    async def _round_timer(self) -> None:
        """Countdown timer for the round. Updates embed at intervals."""
        table = self.table
        try:
            # Update at 45s, 30s, 15s remaining
            checkpoints = [15, 30, 45]
            for seconds_in in checkpoints:
                remaining_at_checkpoint = ROUND_TIME - seconds_in
                elapsed = time.monotonic() - table.round_start_time
                wait = seconds_in - elapsed
                if wait > 0:
                    await asyncio.sleep(wait)

                # Check if round was already won
                if table.phase != "playing" or table.round_winner is not None:
                    return

                # Update the embed with remaining time
                if table.message:
                    try:
                        await table.message.edit(
                            embed=_playing_embed(table, remaining=remaining_at_checkpoint),
                            view=self,
                        )
                    except discord.HTTPException:
                        pass

            # Wait for remaining time
            elapsed = time.monotonic() - table.round_start_time
            remaining = ROUND_TIME - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)

            # Check if round was already won
            if table.phase != "playing" or table.round_winner is not None:
                return

            # Timeout — nobody answered
            table.phase = "finished"
            table.total_rounds_played += 1

            self._update_buttons()
            if table.message:
                try:
                    await table.message.edit(
                        embed=_timeout_embed(table), view=self,
                    )
                except discord.HTTPException:
                    pass

        except asyncio.CancelledError:
            pass
        except Exception:
            # Safety net: don't leave the game stuck
            if table.phase == "playing":
                table.phase = "finished"

    async def _close_table(self, interaction: discord.Interaction) -> None:
        """Close the table: determine overall winner and pay out."""
        table = self.table

        # If still in betting phase (no rounds played), refund everyone
        if table.phase == "betting" or table.total_rounds_played == 0:
            for p in table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass
            embed = discord.Embed(
                title="Math 24 Table \u2014 Closed",
                description="Table closed. All bets refunded.",
                colour=discord.Colour.dark_grey(),
            )
            for child in self.children:
                child.disabled = True  # type: ignore[union-attr]
            self.stop()
            self.active_tables.pop(table.channel_id, None)
            await interaction.response.edit_message(embed=embed, view=self)
            return

        # Determine winner(s) — most rounds won
        max_wins = max(p.rounds_won for p in table.players.values())

        pot = sum(p.bet for p in table.players.values())

        if max_wins == 0:
            # Nobody won any rounds — refund everyone
            for p in table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass
            balances: dict[int, int] = {}
            for p in table.players.values():
                bal = await queries.get_casino_balance(str(p.user_id))
                balances[p.user_id] = bal or 0
        else:
            winners = [p for p in table.players.values() if p.rounds_won == max_wins]
            house_take = max(1, int(pot * HOUSE_EDGE))
            prize_pool = pot - house_take
            payout_each = prize_pool // len(winners)

            balances = {}
            for p in table.players.values():
                if p.rounds_won == max_wins:
                    balances[p.user_id] = await queries.update_casino_balance(
                        str(p.user_id), payout_each,
                    )
                else:
                    bal = await queries.get_casino_balance(str(p.user_id))
                    balances[p.user_id] = bal or 0

        # Log casino results for all players
        for p in table.players.values():
            payout = 0
            if max_wins > 0 and p.rounds_won == max_wins:
                wlist = [x for x in table.players.values() if x.rounds_won == max_wins]
                house_take = max(1, int(pot * HOUSE_EDGE))
                payout = (pot - house_take) // len(wlist)
            elif max_wins == 0:
                payout = p.bet  # refunded
            await queries.log_casino_result(
                str(p.user_id), "math24", p.bet, payout,
            )

        # Save last bets for re-bet in case table is reopened
        for uid, player in table.players.items():
            table.last_bets[uid] = (player.display_name, player.bet)

        embed = _final_embed(table, balances=balances)

        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)
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
                        title="Math 24 Table \u2014 Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    pass
            return

        # Betting or playing — refund all
        for p in table.players.values():
            try:
                await queries.update_casino_balance(str(p.user_id), p.bet)
            except Exception:
                pass
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="Math 24 Table \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class Math24Cog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, Math24Table] = {}

    @app_commands.command(
        name="math24", description="Open a Math 24 table (multiplayer)",
    )
    async def math24(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a Math 24 table in this channel!",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = Math24Table(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = Math24TableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Math24Cog(bot))
