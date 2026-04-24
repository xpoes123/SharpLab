"""Pure Sudoku logic shared between the Discord cog and the web game engine."""

import random
from itertools import groupby

# ── Types ────────────────────────────────────────────────────────────────────

Grid = list[list[int]]  # 4x4, 0 = blank

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 1
ROUND_TIME = 90  # seconds per round
ROUND_DELAY = 5  # seconds between rounds
WINS_TO_WIN = 3  # first to N wins
MAX_ROUNDS = 15  # safety cap
BLANKS = 8  # cells to remove from the 4x4 grid (out of 16)

PAYTABLE: dict[int, list[float]] = {
    1: [1.0],
    2: [1.0],
    3: [0.70, 0.30],
    4: [0.55, 0.30, 0.15],
    5: [0.45, 0.25, 0.18, 0.12],
    6: [0.40, 0.24, 0.16, 0.12, 0.08],
    7: [0.36, 0.22, 0.16, 0.12, 0.08, 0.06],
    8: [0.33, 0.21, 0.16, 0.12, 0.08, 0.06, 0.04],
}

MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]

# ── Grid generation ──────────────────────────────────────────────────────────


def generate_grid() -> Grid:
    """Generate a random valid completed 4x4 Sudoku grid."""
    grid: Grid = [[0] * 4 for _ in range(4)]

    def _valid(g: Grid, r: int, c: int, num: int) -> bool:
        if num in g[r]:
            return False
        if any(g[row][c] == num for row in range(4)):
            return False
        br, bc = (r // 2) * 2, (c // 2) * 2
        for dr in range(2):
            for dc in range(2):
                if g[br + dr][bc + dc] == num:
                    return False
        return True

    def _fill(g: Grid, pos: int = 0) -> bool:
        if pos == 16:
            return True
        r, c = divmod(pos, 4)
        nums = [1, 2, 3, 4]
        random.shuffle(nums)
        for n in nums:
            if _valid(g, r, c, n):
                g[r][c] = n
                if _fill(g, pos + 1):
                    return True
                g[r][c] = 0
        return False

    _fill(grid)
    return grid


def count_solutions(puzzle: Grid, limit: int = 2) -> int:
    """Count solutions for a puzzle, stopping early at *limit*."""
    count = 0

    def _valid(g: Grid, r: int, c: int, num: int) -> bool:
        if num in g[r]:
            return False
        if any(g[row][c] == num for row in range(4)):
            return False
        br, bc = (r // 2) * 2, (c // 2) * 2
        for dr in range(2):
            for dc in range(2):
                if g[br + dr][bc + dc] == num:
                    return False
        return True

    blanks = [(r, c) for r in range(4) for c in range(4) if puzzle[r][c] == 0]

    def _solve(idx: int) -> None:
        nonlocal count
        if count >= limit:
            return
        if idx == len(blanks):
            count += 1
            return
        r, c = blanks[idx]
        for n in range(1, 5):
            if _valid(puzzle, r, c, n):
                puzzle[r][c] = n
                _solve(idx + 1)
                puzzle[r][c] = 0

    _solve(0)
    return count


def make_puzzle(solution: Grid, n_blanks: int = BLANKS) -> Grid:
    """Remove *n_blanks* cells from a completed grid, ensuring unique solution."""
    puzzle: Grid = [row[:] for row in solution]
    cells = [(r, c) for r in range(4) for c in range(4)]
    random.shuffle(cells)
    removed = 0
    for r, c in cells:
        if removed >= n_blanks:
            break
        saved = puzzle[r][c]
        puzzle[r][c] = 0
        if count_solutions([row[:] for row in puzzle]) == 1:
            removed += 1
        else:
            puzzle[r][c] = saved
    return puzzle


def find_error(grid: Grid) -> str:
    """Return a brief hint about what's wrong with a submitted grid."""
    for r in range(4):
        if sorted(grid[r]) != [1, 2, 3, 4]:
            return f"Row {r + 1} doesn't have 1\u20134 exactly once."
    for c in range(4):
        col = [grid[r][c] for r in range(4)]
        if sorted(col) != [1, 2, 3, 4]:
            return f"Column {c + 1} doesn't have 1\u20134 exactly once."
    for br in (0, 2):
        for bc in (0, 2):
            box = [grid[br + dr][bc + dc] for dr in range(2) for dc in range(2)]
            if sorted(box) != [1, 2, 3, 4]:
                label = (
                    "top-left" if (br, bc) == (0, 0) else
                    "top-right" if (br, bc) == (0, 2) else
                    "bottom-left" if (br, bc) == (2, 0) else "bottom-right"
                )
                return f"The {label} box doesn't have 1\u20134 exactly once."
    return "Something's off."


def format_grid(puzzle: Grid) -> str:
    """Pretty-print a 4x4 Sudoku grid as a code block (for Discord)."""
    lines: list[str] = []
    lines.append("\u250c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u252c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510")
    for r in range(4):
        cells = []
        for c in range(4):
            v = puzzle[r][c]
            cells.append(str(v) if v != 0 else "_")
        row_str = f"\u2502 {cells[0]}   {cells[1]} \u2502 {cells[2]}   {cells[3]} \u2502"
        lines.append(row_str)
        if r == 1:
            lines.append("\u251c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u253c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2524")
    lines.append("\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2534\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518")
    return "```\n" + "\n".join(lines) + "\n```"


def row_display(puzzle: Grid, row: int) -> str:
    """Display a single row with blanks as underscores."""
    return "".join(str(puzzle[row][c]) if puzzle[row][c] != 0 else "_" for c in range(4))


# ── Payout helpers ───────────────────────────────────────────────────────────


def compute_payouts(
    players: dict, prize_pool: int, n_players: int,
) -> dict:
    """Compute per-player payouts using the paytable.

    *players* must be a dict where values have `user_id` (or key) and
    `rounds_won` attributes. Returns {user_id: payout_amount}.
    """
    pct_table = PAYTABLE.get(n_players, PAYTABLE[8])

    in_money = sorted(
        [p for p in players.values() if p.rounds_won > 0],
        key=lambda p: p.rounds_won,
        reverse=True,
    )

    payouts: dict = {uid: 0 for uid in players}

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

    total_paid = sum(payouts.values())
    leftover = prize_pool - total_paid
    if leftover > 0 and in_money:
        top_wins = in_money[0].rounds_won
        top_group = [p for p in in_money if p.rounds_won == top_wins]
        extra = leftover // len(top_group)
        for p in top_group:
            payouts[p.user_id] += extra

    return payouts
