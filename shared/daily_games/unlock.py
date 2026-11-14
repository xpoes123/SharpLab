"""Unlock — redraw an Android unlock pattern (pure, server-authoritative logic).

You see a finished pattern on an n×n grid of dots — just the lines, no order. Find an order of
dots that draws exactly that picture under Android rules:
  · you can only move TO a dot you haven't used yet;
  · a stroke may pass OVER a used dot freely, but passing over an unused dot selects it.
The trap (per the "Android pattern password" puzzle): every segment gets drawn end-to-end in one
motion, so the obvious trace usually covers a used dot's segment twice or strands a midpoint.
Generation keeps the candidate with the FEWEST valid orders, so there's little room to bluff.

Dots are indexed r*n + c. The picture is the set of PRIMITIVE edges (adjacent collinear lattice
points along each stroke), so a 2-long stroke and two 1-long strokes over the same dots are equal.
Board payload: {"n": 4, "edges": [[a, b], ...]}   (a < b, sorted)
Solution:      {"moves": [d0, d1, ...]}           (dots in the order they're selected; auto-selected
                                                   pass-over dots may be included or left implicit)
"""

from __future__ import annotations

import random
from math import gcd

ID = "unlock"
NAME = "Unlock"
ICON = "🔓"
SURFACE = "web"
DIFFICULTIES = ["easy", "medium", "hard"]
RANK_ORDER = ("secondary_score", "primary_score")   # time first; dot count is fixed per board
HOWTO = (
    "Here's someone's phone **unlock pattern** — the lines, but not the order. Redraw it exactly. "
    "Tap or drag dot to dot. You can't land on a dot you've already used, but a line can pass "
    "**over** a used dot; passing over an **unused** dot picks it up. Every line in the picture has "
    "to be drawn, and nothing extra. Tap **Undo** to step back. Fastest unlock wins."
)

# difficulty → (grid size, (min, max) dots in the pattern, candidates tried)
_PARAMS = {
    "easy":   (3, (5, 7), 40),
    "medium": (4, (7, 10), 40),
    "hard":   (5, (9, 13), 30),
}
_SOLVE_BUDGET = 200_000     # DFS nodes per count — a 5×5 pattern prunes far below this


def _step(n: int, a: int, b: int) -> list[int]:
    """Every lattice dot from a to b inclusive, in order."""
    (r0, c0), (r1, c1) = divmod(a, n), divmod(b, n)
    k = gcd(abs(r1 - r0), abs(c1 - c0))
    dr, dc = (r1 - r0) // k, (c1 - c0) // k
    return [(r0 + i * dr) * n + (c0 + i * dc) for i in range(k + 1)]


def _edge(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def replay(n: int, moves: list) -> tuple[list[int], set] | None:
    """Expand a move list under Android rules → (selected dots in order, primitive edge set), or
    None if a move is illegal (off-grid, repeats the current dot, or lands on a used dot)."""
    if not moves or not all(isinstance(m, int) and 0 <= m < n * n for m in moves):
        return None
    seq, used, edges = [moves[0]], {moves[0]}, set()
    for t in moves[1:]:
        if t in used:
            return None
        path = _step(n, seq[-1], t)
        for p in path[1:-1]:
            if p not in used:                 # pass-over of an unused dot selects it
                used.add(p)
                seq.append(p)
        for p, q in zip(path, path[1:]):
            edges.add(_edge(p, q))
        used.add(t)
        seq.append(t)
    return seq, edges


def _next_moves(n: int, cur: int, used: set) -> list[int]:
    """Legal "next selected dot" targets: unused dots whose stroke crosses only used dots (a
    stroke over an unused dot is the same as selecting that dot first, so it isn't a new move)."""
    return [t for t in range(n * n)
            if t not in used and all(p in used for p in _step(n, cur, t)[1:-1])]


def count_solutions(n: int, edges: set, cap: int = 50) -> int:
    """How many dot orders draw exactly `edges` (stops at `cap`). DFS pruned by: every edge a move
    draws must be in the target."""
    dots = {d for e in edges for d in e}
    found, budget = [0], [_SOLVE_BUDGET]

    def dfs(cur, used, drawn):
        budget[0] -= 1
        if found[0] >= cap or budget[0] < 0:
            return
        if drawn == edges:
            found[0] += 1                     # all dots are on edges, so used == dots here
        for t in _next_moves(n, cur, used):
            if t not in dots:
                continue
            path = _step(n, cur, t)
            new = {_edge(p, q) for p, q in zip(path, path[1:])}
            if new <= edges:
                dfs(t, used | {t}, drawn | new)

    for d in sorted(dots):
        dfs(d, {d}, frozenset())
    return found[0]


def _random_pattern(rng: random.Random, n: int, length: int) -> list[int]:
    seq = [rng.randrange(n * n)]
    used = {seq[0]}
    while len(seq) < length:
        opts = _next_moves(n, seq[-1], used)
        if not opts:
            break
        t = rng.choice(opts)
        used.add(t)
        seq.append(t)
    return seq


def build_solvable(seed: int, difficulty: str) -> dict:
    """Random walks are solvable by construction (the walk is a witness); keep the one with the
    fewest valid orders, tie-break on more lines. `_witness` is stripped before shipping."""
    n, (lo, hi), tries = _PARAMS.get(difficulty, _PARAMS["medium"])
    rng = random.Random(seed)
    best = None
    for _ in range(tries):
        walk = _random_pattern(rng, n, rng.randint(lo, hi))
        if len(walk) < lo:
            continue
        _, edges = replay(n, walk)
        key = (count_solutions(n, edges), -len(edges))
        if best is None or key < best[0]:
            best = (key, walk, edges)
    _, walk, edges = best
    return {"n": n, "edges": sorted(list(e) for e in edges), "_witness": walk}


def generate(seed: int, difficulty: str) -> dict:
    return build_solvable(seed, difficulty)


def par(puzzle: dict) -> tuple[int, bool]:
    """Par = dots in the pattern (every solution selects exactly these)."""
    return len({d for e in puzzle["edges"] for d in e}), False


def validate(puzzle: dict, solution: dict) -> dict | None:
    moves = solution.get("moves")
    if not isinstance(moves, list) or len(moves) > puzzle["n"] ** 2:
        return None
    out = replay(puzzle["n"], moves)
    if out is None or out[1] != {tuple(e) for e in puzzle["edges"]}:
        return None
    return {"solved": True, "primary": len(out[0]), "secondary": 0}


def share_grid(result: dict, meta: dict) -> str:
    num = meta.get("number")
    secs = result["secondary"] // 1000
    head = f"{ICON} {NAME} #{num}" if num else f"{ICON} {NAME}"
    return f"{head} · {meta.get('difficulty', '')} · {result['primary']} dots · {secs // 60}:{secs % 60:02d}"


if __name__ == "__main__":
    # The video's "easy" square (3×3 ring, all 8 outer dots): clockwise works, and so does
    # starting from a side's midpoint.
    ring = [0, 1, 2, 5, 8, 7, 6, 3]
    _, ring_edges = replay(3, ring)
    assert len(ring_edges) == 7
    assert replay(3, [0, 2]) == ([0, 1, 2], {(0, 1), (1, 2)})          # pass-over auto-selects
    assert replay(3, [1, 0, 2]) == ([1, 0, 2], {(0, 1), (1, 2)})       # pass over a USED dot is fine
    assert replay(3, [0, 1, 0]) is None                                 # can't land on a used dot
    assert count_solutions(3, {(0, 1), (1, 2)}) == 4                    # 0→2, 2→0, 1→0→2, 1→2→0
    p = build_solvable(7, "easy")
    assert validate(p, {"moves": p["_witness"]})["solved"]
    assert validate(p, {"moves": p["_witness"][:-1]}) is None
    print("unlock self-check OK", p["n"], len(p["edges"]), "edges")
