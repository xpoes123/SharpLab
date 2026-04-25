"""Minesweeper Race — pure game logic (no external deps)."""

import random

# ── Constants ────────────────────────────────────────────────────────────────

ROWS = 9
COLS = 9
MINES = 10
SAFE_CELLS = ROWS * COLS - MINES  # 71

MAX_PLAYERS = 8
MIN_PLAYERS = 1
WINS_TO_WIN = 3
MAX_ROUNDS = 15
ROUND_TIME = 120  # seconds per round
ROUND_DELAY = 5   # seconds between rounds

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

# ── Board generation ─────────────────────────────────────────────────────────


def generate_board(
    rows: int = ROWS, cols: int = COLS, mines: int = MINES,
) -> tuple[list[list[int]], set[tuple[int, int]]]:
    """Generate a minesweeper board.

    Returns (grid, mine_set) where grid[r][c] is -1 for mine or 0-8 for
    adjacent mine count, and mine_set is the set of (r, c) mine positions.
    """
    all_cells = [(r, c) for r in range(rows) for c in range(cols)]
    mine_positions = set(random.sample(all_cells, mines))

    grid: list[list[int]] = [[0] * cols for _ in range(rows)]

    for r, c in mine_positions:
        grid[r][c] = -1

    for r in range(rows):
        for c in range(cols):
            if grid[r][c] == -1:
                continue
            count = 0
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) in mine_positions:
                        count += 1
            grid[r][c] = count

    return grid, mine_positions


def flood_fill(
    grid: list[list[int]],
    revealed: set[tuple[int, int]],
    r: int,
    c: int,
) -> list[tuple[int, int, int]]:
    """Reveal cell (r, c) and flood-fill zeros.

    Returns list of (row, col, value) for all newly revealed cells.
    Returns empty list if cell is already revealed or out of bounds.
    Does NOT check for mines — caller must check first.
    """
    rows = len(grid)
    cols = len(grid[0])

    if r < 0 or r >= rows or c < 0 or c >= cols:
        return []
    if (r, c) in revealed:
        return []

    result: list[tuple[int, int, int]] = []
    stack = [(r, c)]

    while stack:
        cr, cc = stack.pop()
        if (cr, cc) in revealed:
            continue
        revealed.add((cr, cc))
        val = grid[cr][cc]
        result.append((cr, cc, val))

        if val == 0:
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = cr + dr, cc + dc
                    if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in revealed:
                        stack.append((nr, nc))

    return result


# ── Payouts ──────────────────────────────────────────────────────────────────


def compute_payouts(
    players: dict[str, dict],
    n_players: int,
) -> dict[str, int]:
    """Compute coin payouts from PAYTABLE.

    players: {uid: {"rounds_won": int, "wager": int, ...}}
    Returns {uid: payout_amount}.
    """
    pool = sum(p["wager"] for p in players.values())
    if pool == 0:
        return {uid: 0 for uid in players}

    pct_table = PAYTABLE.get(n_players, PAYTABLE[8])

    # Sort by rounds_won descending
    ranked = sorted(
        players.items(),
        key=lambda x: x[1]["rounds_won"],
        reverse=True,
    )

    payouts: dict[str, int] = {uid: 0 for uid in players}

    # Group by rounds_won for tie handling
    pos = 0
    i = 0
    while i < len(ranked):
        # Find all players tied at this position
        tied = [ranked[i]]
        j = i + 1
        while j < len(ranked) and ranked[j][1]["rounds_won"] == ranked[i][1]["rounds_won"]:
            tied.append(ranked[j])
            j += 1

        # Only pay players who won at least 1 round
        if ranked[i][1]["rounds_won"] == 0:
            i = j
            continue

        # Sum the pct shares for these tied positions
        total_pct = 0.0
        for k in range(pos, min(pos + len(tied), len(pct_table))):
            total_pct += pct_table[k]

        # Split evenly among tied players
        per_player = int(pool * total_pct / len(tied))
        for uid, _ in tied:
            payouts[uid] = per_player

        pos += len(tied)
        i = j

    return payouts
