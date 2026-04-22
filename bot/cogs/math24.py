"""Casino cog — multiplayer /math24 race game.

Players race to solve Math 24 puzzles by typing expressions directly in chat.
Auto-advancing rounds, paytable based on player count.
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from itertools import groupby, permutations, product

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MAX_BET = 500
MIN_PLAYERS = 2
HOUSE_EDGE = 0.05
ROUND_TIME = 45  # seconds per round
ROUND_DELAY = 4  # seconds between rounds
WINS_TO_WIN = 4  # first to N wins
MAX_ROUNDS = 30  # safety cap — game ends after this many rounds regardless
TARGET = 24
NUM_COUNT = 4
NUM_MIN = 1
NUM_MAX = 13
EPSILON = 1e-9

OPS = ["+", "-", "*", "/"]

# Paytable: fraction of prize pool by finishing position, keyed by player count
PAYTABLE: dict[int, list[float]] = {
    2: [1.0],
    3: [0.70, 0.30],
    4: [0.55, 0.30, 0.15],
    5: [0.45, 0.25, 0.18, 0.12],
    6: [0.40, 0.24, 0.16, 0.12, 0.08],
    7: [0.36, 0.22, 0.16, 0.12, 0.08, 0.06],
    8: [0.33, 0.21, 0.16, 0.12, 0.08, 0.06, 0.04],
}


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
        return f"(({a} {o1} {b}) {o2} {c}) {o3} {d}"
    if tree == 1:
        return f"({a} {o1} ({b} {o2} {c})) {o3} {d}"
    if tree == 2:
        return f"({a} {o1} {b}) {o2} ({c} {o3} {d})"
    if tree == 3:
        return f"{a} {o1} (({b} {o2} {c}) {o3} {d})"
    if tree == 4:
        return f"{a} {o1} ({b} {o2} ({c} {o3} {d}))"
    return ""


def _eval_tree(nums: tuple[float, ...], ops: tuple[str, ...], tree: int) -> float | None:
    """Evaluate one of the 5 binary-tree structures for 4 numbers and 3 operators."""
    a, b, c, d = nums
    o1, o2, o3 = ops

    if tree == 0:
        r = _try_eval(a, o1, b)
        if r is None:
            return None
        r = _try_eval(r, o2, c)
        if r is None:
            return None
        return _try_eval(r, o3, d)

    if tree == 1:
        r = _try_eval(b, o2, c)
        if r is None:
            return None
        r = _try_eval(a, o1, r)
        if r is None:
            return None
        return _try_eval(r, o3, d)

    if tree == 2:
        left = _try_eval(a, o1, b)
        if left is None:
            return None
        right = _try_eval(c, o3, d)
        if right is None:
            return None
        return _try_eval(left, o2, right)

    if tree == 3:
        r = _try_eval(b, o2, c)
        if r is None:
            return None
        r = _try_eval(r, o3, d)
        if r is None:
            return None
        return _try_eval(a, o1, r)

    if tree == 4:
        r = _try_eval(c, o3, d)
        if r is None:
            return None
        r = _try_eval(b, o2, r)
        if r is None:
            return None
        return _try_eval(a, o1, r)

    return None


def find_all_solutions(numbers: list[int]) -> list[str]:
    """Find all distinct solutions for a set of 4 numbers that equal 24."""
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


# ── Race helpers ─────────────────────────────────────────────────────────────


def _compute_payouts(
    players: dict[int, "Math24Player"], prize_pool: int, n_players: int,
) -> dict[int, int]:
    """Compute per-player payouts using the paytable.

    Only players with rounds_won > 0 are in the money.
    Ties split the combined shares for occupied positions.
    Unused paid positions roll up to first place.
    """
    pct_table = PAYTABLE.get(n_players, PAYTABLE[8])

    in_money = sorted(
        [p for p in players.values() if p.rounds_won > 0],
        key=lambda p: p.rounds_won,
        reverse=True,
    )

    payouts: dict[int, int] = {uid: 0 for uid in players}

    if not in_money:
        return payouts

    paid_positions = len(pct_table)
    pos = 0
    for _wins, group_iter in groupby(in_money, key=lambda p: p.rounds_won):
        group = list(group_iter)
        if pos >= paid_positions:
            break
        end = min(pos + len(group), paid_positions)
        combined_share = sum(pct_table[pos:end])
        per_player = int(prize_pool * combined_share / len(group))
        for p in group:
            payouts[p.user_id] = per_player
        pos += len(group)

    # Unused positions roll up to first place
    total_paid = sum(payouts.values())
    leftover = prize_pool - total_paid
    if leftover > 0 and in_money:
        top_wins = in_money[0].rounds_won
        top_group = [p for p in in_money if p.rounds_won == top_wins]
        extra = leftover // len(top_group)
        for p in top_group:
            payouts[p.user_id] += extra

    return payouts


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
    phase: str = "betting"  # betting | playing | between_rounds | closed
    players: dict[int, Math24Player] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 0
    numbers: list[int] = field(default_factory=list)
    solution: str = ""
    round_start_time: float = 0.0
    round_winner: int | None = None
    race_task: asyncio.Task | None = field(default=None, repr=False)
    round_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    total_rounds_played: int = 0


# ── Embeds ───────────────────────────────────────────────────────────────────

MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]  # gold, silver, bronze


def _numbers_display(numbers: list[int]) -> str:
    """Format the 4 numbers in a big, bold, visually clear way."""
    return "   ".join(f"**[ {n} ]**" for n in numbers)


def _scoreboard(table: Math24Table) -> str:
    """Build a sorted scoreboard of rounds won."""
    sorted_players = sorted(
        table.players.values(), key=lambda p: p.rounds_won, reverse=True,
    )
    lines: list[str] = []
    for i, p in enumerate(sorted_players):
        prefix = MEDALS[i] if i < len(MEDALS) and p.rounds_won > 0 else "\u25aa\ufe0f"
        line = f"{prefix} **{p.display_name}** \u2014 {p.rounds_won}/{WINS_TO_WIN}"
        if p.rounds_won == WINS_TO_WIN - 1:
            line += " *(match point!)*"
        lines.append(line)
    return "\n".join(lines) if lines else "No scores yet"


def _betting_embed(table: Math24Table) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values())
    n = len(table.players)

    embed = discord.Embed(
        title="Math 24 \u2014 Race",
        description=(
            f"Race to solve Math 24 puzzles! **First to {WINS_TO_WIN} wins** takes the pot.\n"
            "Use all 4 numbers with `+ - * / ( )` to make **24**.\n"
            "Type answers directly in chat \u2014 fastest correct answer wins each round!"
        ),
        colour=discord.Colour.blue(),
    )

    if pot:
        embed.add_field(name="Pot", value=f"{pot}c (5% rake)", inline=True)
    embed.add_field(name="Goal", value=f"First to {WINS_TO_WIN}", inline=True)

    # Paytable preview
    if n >= MIN_PLAYERS:
        pt = PAYTABLE.get(n, PAYTABLE[8])
        pt_parts = [
            f"{MEDALS[i] if i < 3 else chr(0x25aa) + chr(0xfe0f)} {int(s * 100)}%"
            for i, s in enumerate(pt)
        ]
        embed.add_field(name="Paytable", value=" | ".join(pt_parts), inline=True)

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


def _playing_embed(table: Math24Table, remaining: int | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"Math 24 \u2014 Round {table.round_num} (First to {WINS_TO_WIN})",
        colour=discord.Colour.gold(),
    )

    embed.description = (
        f"# {_numbers_display(table.numbers)}\n"
        f"*Numbers: {', '.join(str(n) for n in table.numbers)}*\n\n"
        "**Type your answer in chat!**\n"
        "Use all 4 numbers with `+ - * / ( )` to make **24**."
    )

    secs = remaining if remaining is not None else ROUND_TIME
    embed.add_field(name="\u23f1\ufe0f Time", value=f"**{secs}s**", inline=True)

    pot = sum(p.bet for p in table.players.values())
    embed.add_field(name="Pot", value=f"{pot}c", inline=True)

    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _round_result_embed(table: Math24Table) -> discord.Embed:
    winner = table.players[table.round_winner]
    solve_time = winner.answer_time - table.round_start_time
    is_last = winner.rounds_won >= WINS_TO_WIN or table.round_num >= MAX_ROUNDS

    embed = discord.Embed(
        title=f"Math 24 \u2014 Round {table.round_num} \u2705",
        colour=discord.Colour.green(),
    )
    embed.description = (
        f"\U0001f3c6 **{winner.display_name}** solved it in **{solve_time:.1f}s**!\n\n"
        f"Numbers: {', '.join(str(n) for n in table.numbers)}\n"
        f"Expression: `{winner.answer}`"
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round complete \u2014 calculating results\u2026")
    return embed


def _timeout_embed(table: Math24Table) -> discord.Embed:
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)
    is_last = max_wins >= WINS_TO_WIN or table.round_num >= MAX_ROUNDS

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
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round complete \u2014 calculating results\u2026")
    return embed


def _final_embed(
    table: Math24Table,
    *,
    payouts: dict[int, int],
    balances: dict[int, int],
) -> discord.Embed:
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)
    is_refund = max_wins == 0

    embed = discord.Embed(
        title="Math 24 \u2014 Race Results",
        colour=discord.Colour.gold() if not is_refund else discord.Colour.dark_grey(),
    )

    if is_refund:
        embed.description = "No rounds were won \u2014 all bets refunded!"
    else:
        sorted_p = sorted(
            table.players.values(), key=lambda p: p.rounds_won, reverse=True,
        )
        winner = sorted_p[0]
        rw = winner.rounds_won
        embed.description = (
            f"\U0001f3c6 **{winner.display_name}** wins with "
            f"**{rw}** round{'s' if rw != 1 else ''}!"
        )

    sorted_players = sorted(
        table.players.values(), key=lambda p: p.rounds_won, reverse=True,
    )
    lines: list[str] = []
    for i, p in enumerate(sorted_players):
        payout = payouts.get(p.user_id, 0)
        bal = balances.get(p.user_id, 0)
        net = payout - p.bet
        sign = "+" if net >= 0 else ""
        medal = MEDALS[i] if i < len(MEDALS) and p.rounds_won > 0 else "\u25aa\ufe0f"
        lines.append(
            f"{medal} **{p.display_name}** ({p.rounds_won}W) \u2014 "
            f"{p.bet}c \u2192 {payout}c "
            f"(**{sign}{net}c**) \u2014 bal: {bal}c"
        )
    embed.add_field(name="Results", value="\n".join(lines), inline=False)

    if not is_refund:
        n = len(table.players)
        pt = PAYTABLE.get(n, PAYTABLE[8])
        pt_parts = [
            f"{MEDALS[i] if i < 3 else chr(0x25aa) + chr(0xfe0f)} {int(s * 100)}%"
            for i, s in enumerate(pt)
        ]
        embed.add_field(
            name=f"Paytable ({n} players)",
            value=" | ".join(pt_parts),
            inline=True,
        )

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

        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "Round is not active!", ephemeral=True,
            )
            return
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

        # Signal the race loop
        self.table.round_solved.set()

        await interaction.response.send_message(
            "\u2705 Correct!", ephemeral=True,
        )


# ── View ─────────────────────────────────────────────────────────────────────


class Math24TableView(ui.View):
    def __init__(
        self, table: Math24Table, active_tables: dict[int, Math24Table],
    ) -> None:
        super().__init__(timeout=900)  # 15 min — enough for longest race
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        playing = phase == "playing"
        racing = playing or phase == "between_rounds"

        # Row 0: betting controls
        self.start_btn.disabled = (
            not betting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = not betting

        # Row 1: race controls
        self.answer_btn.disabled = not playing
        self.hint_btn.disabled = not playing
        self.close_btn.disabled = racing

    # ── Row 0: Betting ────────────────────────────────────────────────────

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
        await self._start_race(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary,
        emoji="\U0001f9ee", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Race in progress! Wait for the next game.", ephemeral=True,
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
                "Race in progress!", ephemeral=True,
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
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Can't leave during a race!", ephemeral=True,
            )
            return
        await queries.update_casino_balance(str(uid), player.bet)
        del self.table.players[uid]
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Row 1: Race controls ──────────────────────────────────────────────

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

    @ui.button(
        label="Close Table", style=discord.ButtonStyle.danger,
        emoji="\u2716\ufe0f", row=1,
    )
    async def close_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can close the table!", ephemeral=True,
            )
            return
        if self.table.phase in ("playing", "between_rounds"):
            await interaction.response.send_message(
                "Can't close during a race! Wait for it to finish.",
                ephemeral=True,
            )
            return
        await self._close_table(interaction)

    # ── Race logic ────────────────────────────────────────────────────────

    async def _start_race(self, interaction: discord.Interaction) -> None:
        """Start the race: set up round 1, show it, then launch the race loop."""
        table = self.table

        # Save last bets for re-bet
        for uid, p in table.players.items():
            table.last_bets[uid] = (p.display_name, p.bet)

        # Set up round 1
        nums, solution = generate_solvable_numbers()
        table.numbers = nums
        table.solution = solution
        table.round_num = 1
        table.round_winner = None
        table.round_solved.clear()
        table.phase = "playing"
        table.round_start_time = time.monotonic()

        for p in table.players.values():
            p.answer = None
            p.answer_time = None

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(table), view=self,
        )

        # Launch the race loop
        table.race_task = asyncio.create_task(self._race_loop())

    async def _wait_for_solve_or_timeout(self) -> bool:
        """Wait for someone to solve the round or for timeout.

        Updates the embed with remaining time every 15 seconds.
        Returns True if solved.
        """
        table = self.table
        deadline = table.round_start_time + ROUND_TIME

        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                return table.round_winner is not None

            wait = min(15.0, remaining)
            try:
                await asyncio.wait_for(table.round_solved.wait(), timeout=wait)
                return True
            except asyncio.TimeoutError:
                if table.round_winner is not None:
                    return True
                # Update embed with remaining time
                now = time.monotonic()
                secs_left = max(0, int(deadline - now))
                if secs_left > 0 and table.message:
                    try:
                        await table.message.edit(
                            embed=_playing_embed(table, remaining=secs_left),
                            view=self,
                        )
                    except discord.HTTPException:
                        pass

    async def _race_loop(self) -> None:
        """Main race loop. Round 1 is already dealt by _start_race."""
        table = self.table
        try:
            rnd = 0
            while True:
                rnd += 1

                # Round 1 was set up by _start_race; rounds 2+ dealt here
                if rnd > 1:
                    nums, solution = generate_solvable_numbers()
                    table.numbers = nums
                    table.solution = solution
                    table.round_num = rnd
                    table.round_winner = None
                    table.round_solved.clear()
                    table.phase = "playing"
                    table.round_start_time = time.monotonic()

                    for p in table.players.values():
                        p.answer = None
                        p.answer_time = None

                    self._update_buttons()
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_playing_embed(table), view=self,
                            )
                        except discord.HTTPException:
                            pass

                # Wait for solve or timeout
                solved = await self._wait_for_solve_or_timeout()
                table.total_rounds_played += 1

                if solved and table.round_winner is not None:
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_round_result_embed(table), view=self,
                            )
                        except discord.HTTPException:
                            pass
                else:
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_timeout_embed(table), view=self,
                            )
                        except discord.HTTPException:
                            pass

                # Check if someone hit the win target or safety cap
                if any(p.rounds_won >= WINS_TO_WIN for p in table.players.values()):
                    break
                if rnd >= MAX_ROUNDS:
                    break

                # Delay before next round
                table.phase = "between_rounds"
                await asyncio.sleep(ROUND_DELAY)

            # Race complete — pay out
            await self._end_game()

        except asyncio.CancelledError:
            pass
        except Exception:
            table.phase = "closed"
            self.active_tables.pop(table.channel_id, None)

    async def _compute_and_apply_payouts(
        self,
    ) -> tuple[dict[int, int], dict[int, int]]:
        """Compute payouts, apply balance changes, log results.

        Returns (payouts, balances).
        """
        table = self.table
        n_players = len(table.players)
        pot = sum(p.bet for p in table.players.values())
        max_wins = max((p.rounds_won for p in table.players.values()), default=0)

        if max_wins == 0:
            # Nobody won — refund
            payouts = {uid: p.bet for uid, p in table.players.items()}
            for uid, refund in payouts.items():
                try:
                    await queries.update_casino_balance(str(uid), refund)
                except Exception:
                    pass
        else:
            payouts = _compute_payouts(table.players, pot, n_players)
            for uid, payout in payouts.items():
                if payout > 0:
                    try:
                        await queries.update_casino_balance(str(uid), payout)
                    except Exception:
                        pass

        balances: dict[int, int] = {}
        for uid in table.players:
            bal = await queries.get_casino_balance(str(uid))
            balances[uid] = bal or 0

        for uid, p in table.players.items():
            payout = payouts.get(uid, 0)
            await queries.log_casino_result(str(uid), "math24", p.bet, payout)

        return payouts, balances

    async def _end_game(self) -> None:
        """End the race: compute payouts and show final results."""
        table = self.table
        table.phase = "closed"

        payouts, balances = await self._compute_and_apply_payouts()
        embed = _final_embed(table, payouts=payouts, balances=balances)

        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)

        if table.message:
            try:
                await table.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    async def _close_table(self, interaction: discord.Interaction) -> None:
        """Close from betting phase or after race ends."""
        table = self.table

        if table.total_rounds_played == 0:
            # No rounds played — refund everyone
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

        # Had rounds played — pay out based on standings
        table.phase = "closed"
        payouts, balances = await self._compute_and_apply_payouts()
        embed = _final_embed(table, payouts=payouts, balances=balances)

        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table

        if table.race_task and not table.race_task.done():
            table.race_task.cancel()

        if table.phase == "closed":
            return

        # Refund everyone
        for p in table.players.values():
            try:
                await queries.update_casino_balance(str(p.user_id), p.bet)
            except Exception:
                pass

        table.phase = "closed"
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
        name="math24", description="Open a Math 24 race table (multiplayer)",
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
