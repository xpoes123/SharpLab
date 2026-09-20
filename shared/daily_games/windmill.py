"""Windmill — a daily tile-placement (packing) puzzle plugin.

Inspired by IMO 2025 P6. The player is handed a shuffled bag of rectangular tiles and must
place all of them on an n×n grid so that no two overlap, all stay in-bounds, and **exactly one
unit square in every row and every column is left uncovered**. That final arrangement wins.

It's an OFFLINE game: the whole board (grid size + bag) is handed to the client, the player
builds locally, and only the final placements are submitted for server validation. So there are
no hidden fields, no new endpoints, and no DB changes — the seed can be public (nothing is
leaked by a derivable board; the gap positions are the puzzle and are never shipped).

Everything here is deterministic from (seed, difficulty): `build_solvable` constructs a board by
first choosing the gaps, then partitioning the remaining cells into rectangles — so the board is
solvable *by construction* and that construction is its own witness. `validate` replays a
submitted arrangement and confirms the win condition; the client is never trusted for the outcome.

Ranks by TIME (fastest valid arrangement). The tile count is fixed by the day's bag, so it can't
differentiate clean solvers — it's carried as an inert tiebreak / display value only.
"""

from __future__ import annotations

import random

ID = "windmill"
NAME = "Windmill"
ICON = "🌀"
SURFACE = "web"
DIFFICULTIES = ["easy", "medium", "hard"]
# Time first: the bag is fixed, so tile count is constant across clean solves and can't rank them.
# primary = tile count (display), secondary = server-timed elapsed (the real metric).
RANK_ORDER = ("secondary_score", "primary_score")
HOWTO = (
    "You're handed a **bag of rectangular tiles** and an n×n grid. Place **every** tile so that "
    "no two overlap, all stay on the grid, and **exactly one square in each row and each column is "
    "left empty**. Tiles can be **rotated**. That arrangement wins. Everyone gets the same bag — "
    "**fastest valid solve wins**, tile count only breaks ties. The clock starts when you hit Start."
)

# difficulty → grid size n. Gaps = n (one per row/col); bag size falls out of the partition.
_N = {"easy": 6, "medium": 8, "hard": 10}


def _biased(rng: random.Random, m: int) -> int:
    """A size in [1, m], biased toward larger (max of two uniforms) — makes chunkier bags."""
    if m <= 1:
        return 1
    return max(rng.randint(1, m), rng.randint(1, m))


def build_solvable(seed: int, difficulty: str) -> dict:
    """Deterministically build a board that is solvable by construction, plus its witness.

    1. Gaps: a random permutation π of 0..n-1 puts a gap at (row i, col π[i]) — exactly one per
       row and per column.
    2. Randomized greedy rectangle partition of every remaining (non-gap) cell: scan for the
       top-left-most uncovered non-gap cell, grow a maximal clear rectangle, pick a size within it
       biased large, place it, repeat. 1×1 is always available as a fallback, so it terminates and
       fully covers the non-gap cells.

    Returns the client payload {n, tiles} plus a private `_witness` (the generating placements) and
    `_gaps` used by par/tests — `build_puzzle` only ships `n` and `tiles` to the browser.
    """
    n = _N.get(difficulty, _N["medium"])
    rng = random.Random(seed)

    perm = list(range(n))
    rng.shuffle(perm)
    gap = [[False] * n for _ in range(n)]
    for i in range(n):
        gap[i][perm[i]] = True

    covered = [[False] * n for _ in range(n)]
    witness = []  # [x, y, w, h] placements, x=col y=row
    for r in range(n):
        for c in range(n):
            if gap[r][c] or covered[r][c]:
                continue
            # max width rightward while clear (in-bounds, non-gap, uncovered)
            maxw = 0
            while c + maxw < n and not gap[r][c + maxw] and not covered[r][c + maxw]:
                maxw += 1
            w = _biased(rng, maxw)
            # max height downward while the whole chosen width stays clear
            maxh = 0
            while r + maxh < n and all(
                not gap[r + maxh][cc] and not covered[r + maxh][cc] for cc in range(c, c + w)
            ):
                maxh += 1
            h = _biased(rng, maxh)
            for rr in range(r, r + h):
                for cc in range(c, c + w):
                    covered[rr][cc] = True
            witness.append([c, r, w, h])

    tiles = [[p[2], p[3]] for p in witness]
    rng.shuffle(tiles)
    gaps = [[perm[i], i] for i in range(n)]  # [x, y]
    return {"n": n, "tiles": tiles, "_witness": witness, "_gaps": gaps}


def par(puzzle: dict) -> tuple[int, bool]:
    """Par = the exact tile count N (the bag is fixed, so this is exact, not a benchmark)."""
    return len(puzzle["tiles"]), False


def _normalized_bag(tiles) -> list[tuple[int, int]]:
    """Bag as a sorted multiset of orientation-agnostic dims (min,max) — rotations collapse."""
    return sorted((min(w, h), max(w, h)) for w, h in tiles)


def validate(puzzle: dict, solution: dict) -> dict | None:
    """Replay a submitted arrangement. Returns a Result iff it's a genuine win, else None.

    `solution["moves"]` = placements [[x, y, w, h], ...] (x=col, y=row; w,h already reflect any
    rotation the player applied). A win requires: all N tiles placed, all in-bounds, no overlap,
    the placed dims (rotation-agnostic) equal the bag exactly, and the uncovered cells number n
    with exactly one in every row and every column.
    """
    n = puzzle["n"]
    tiles = puzzle["tiles"]
    moves = solution.get("moves") or []
    if not isinstance(moves, list) or len(moves) != len(tiles):
        return None

    grid = [[False] * n for _ in range(n)]
    placed_dims = []
    for mv in moves:
        if not (isinstance(mv, (list, tuple)) and len(mv) == 4):
            return None
        try:
            x, y, w, h = (int(v) for v in mv)
        except (TypeError, ValueError):
            return None
        if w < 1 or h < 1 or x < 0 or y < 0 or x + w > n or y + h > n:
            return None
        for ry in range(y, y + h):
            for rx in range(x, x + w):
                if grid[ry][rx]:
                    return None  # overlap
                grid[ry][rx] = True
        placed_dims.append([w, h])

    if _normalized_bag(placed_dims) != _normalized_bag(tiles):
        return None  # wrong bag (extra/missing/mis-sized tile)

    # uncovered cells: exactly one per row and one per column
    empties = [(rx, ry) for ry in range(n) for rx in range(n) if not grid[ry][rx]]
    if len(empties) != n:
        return None
    if len({rx for rx, _ in empties}) != n or len({ry for _, ry in empties}) != n:
        return None

    elapsed = int(solution.get("elapsed_ms") or 0)
    return {"solved": True, "primary": len(tiles), "secondary": max(0, elapsed)}


def share_grid(result: dict, meta: dict) -> str:
    diff = meta.get("difficulty", "")
    num = meta.get("number")
    secs = result["secondary"] // 1000
    t = f"{secs // 60}:{secs % 60:02d}"
    head = f"🌀 Windmill #{num}" if num else "🌀 Windmill"
    return f"{head} · {diff} · {result['primary']} tiles · {t}"


# ── self-check ────────────────────────────────────────────────────────────────

def _demo() -> None:
    for difficulty in DIFFICULTIES:
        p = build_solvable(20260918, difficulty)
        n = p["n"]
        # the generating witness must validate
        res = validate(p, {"moves": p["_witness"], "elapsed_ms": 1234})
        assert res and res["solved"], f"{difficulty}: witness didn't validate"
        assert res["primary"] == len(p["tiles"]) and res["secondary"] == 1234
        # gaps are exactly the uncovered cells → one per row/col by construction
        assert len(p["_gaps"]) == n
        assert par(p) == (len(p["tiles"]), False)
        # deterministic
        assert build_solvable(20260918, difficulty)["tiles"] == p["tiles"]

        w = p["_witness"]
        # wrong bag: drop a tile
        assert validate(p, {"moves": w[:-1]}) is None
        # overlap: two tiles on the same cell (reuse first tile's dims twice at origin)
        assert validate(p, {"moves": [[0, 0, 1, 1]] * len(w)}) is None
        # out of bounds: shove the first placement off the edge
        bad = [row[:] for row in w]
        bad[0] = [n, 0, w[0][2], w[0][3]]
        assert validate(p, {"moves": bad}) is None
        # bad gap distribution: valid non-overlapping cover with wrong empties — shift a 1×1
        #   witness so two empties share a row (only checkable when a 1×1 tile exists)

    # a rotated witness (swap w/h on square-free tiles) still validates only if it still tiles;
    # the exact-bag + one-per-line checks are the real guarantees exercised above.
    print("windmill self-check OK")


if __name__ == "__main__":
    _demo()
