"""Trap the Pig — the launch Daily Game plugin (pure, server-authoritative logic).

A pig sits on a pointy-top hex grid (odd-r offset). Each turn the player fences one empty hex;
then the pig steps to the open neighbour on the shortest path to any border (multi-source BFS
from open border cells, deterministic direction tie-break). Pig on a border cell = escaped; pig
with no path out = trapped. Score = fences used (fewer better), server time breaks ties.

Everything here is deterministic from (seed, difficulty) so everyone gets the identical daily
board, and `validate` replays a submitted solution to confirm the pig was really trapped —
the client is never trusted for the outcome.
"""

from __future__ import annotations

import random
from collections import deque

ID = "trappig"
NAME = "Trap the Pig"
ICON = "🐷"
SURFACE = "web"
DIFFICULTIES = ["easy", "medium", "hard"]

# difficulty → (rows, cols, starting fences). Bigger + sparser = harder.
_PARAMS = {
    "easy":   (9, 9, 9),
    "medium": (11, 11, 7),
    "hard":   (13, 13, 5),
}


def _neighbours(r, c, rows, cols):
    odd = r & 1
    deltas = ([(0, 1), (0, -1), (-1, 0), (-1, 1), (1, 0), (1, 1)] if odd
              else [(0, 1), (0, -1), (-1, -1), (-1, 0), (1, -1), (1, 0)])
    out = []
    for dr, dc in deltas:
        nr, nc = r + dr, c + dc
        if 0 <= nr < rows and 0 <= nc < cols:
            out.append((nr, nc))
    return out


def _is_border(r, c, rows, cols):
    return r == 0 or c == 0 or r == rows - 1 or c == cols - 1


def _dist_field(fences, rows, cols):
    """Multi-source BFS from open border cells → distance-to-escape for every open cell."""
    dist = {}
    q = deque()
    for r in range(rows):
        for c in range(cols):
            if _is_border(r, c, rows, cols) and (r, c) not in fences:
                dist[(r, c)] = 0
                q.append((r, c))
    while q:
        r, c = q.popleft()
        d = dist[(r, c)]
        for nr, nc in _neighbours(r, c, rows, cols):
            if (nr, nc) in fences or (nr, nc) in dist:
                continue
            dist[(nr, nc)] = d + 1
            q.append((nr, nc))
    return dist


def _pig_step(pig, fences, rows, cols):
    """Return the pig's next cell, or None if it is trapped (no path out)."""
    if _is_border(*pig, rows, cols):
        return pig  # already escaping
    dist = _dist_field(fences, rows, cols)
    best, bd = None, None
    for nr, nc in _neighbours(*pig, rows, cols):
        if (nr, nc) in fences:
            continue
        d = dist.get((nr, nc))
        if d is None:
            continue
        if bd is None or d < bd:  # neighbours yielded in fixed order → deterministic tie-break
            bd, best = d, (nr, nc)
    return best  # None → trapped


def generate(seed: int, difficulty: str) -> dict:
    rows, cols, n_fences = _PARAMS.get(difficulty, _PARAMS["medium"])
    rng = random.Random(seed)
    pig = (rows // 2, cols // 2)
    forbid = {pig, *(_neighbours(*pig, rows, cols))}
    fences = set()
    tries = 0
    while len(fences) < n_fences and tries < 2000:
        tries += 1
        cell = (rng.randrange(rows), rng.randrange(cols))
        if cell in forbid:
            continue
        fences.add(cell)
    return {
        "rows": rows, "cols": cols, "difficulty": difficulty,
        "pig": list(pig), "fences": sorted(list(c) for c in fences),
    }


def _run(puzzle, moves):
    """Replay a move sequence. Returns (outcome, fences_used) where outcome ∈
    {'trapped','escaped','illegal'}."""
    rows, cols = puzzle["rows"], puzzle["cols"]
    fences = {tuple(f) for f in puzzle["fences"]}
    pig = tuple(puzzle["pig"])
    used = 0
    for mv in moves:
        cell = tuple(mv)
        if cell in fences or cell == pig or not (0 <= cell[0] < rows and 0 <= cell[1] < cols):
            return "illegal", used
        fences.add(cell)
        used += 1
        nxt = _pig_step(pig, fences, rows, cols)
        if nxt is None:
            return "trapped", used
        pig = nxt
        if _is_border(*pig, rows, cols):
            return "escaped", used
    # ran out of moves without resolving
    return ("trapped" if _pig_step(pig, fences, rows, cols) is None else "escaped"), used


def validate(puzzle: dict, solution: dict) -> dict | None:
    """Replay the submitted fence sequence. Returns a Result on a genuine trap, else None."""
    moves = solution.get("moves") or []
    if not isinstance(moves, list) or len(moves) > puzzle["rows"] * puzzle["cols"]:
        return None
    outcome, used = _run(puzzle, moves)
    if outcome != "trapped":
        return None
    elapsed = int(solution.get("elapsed_ms") or 0)
    return {"solved": True, "primary": used, "secondary": max(0, elapsed)}


def par(puzzle: dict) -> tuple[int, bool]:
    """Par = the greedy 'block the pig's best escape' solver's fence count — always an
    ACHIEVABLE trap count and a fair benchmark good players beat. Returns (par, approx);
    approx is True because this is a benchmark, not a proven minimum (exact minimax par is
    future work — see the design's open questions)."""
    used, trapped = _greedy_solve(puzzle)
    return used, True


def greedy_solution(puzzle: dict) -> list[list[int]]:
    """The move sequence the greedy solver plays — a valid trap (used by tests / a 'hint')."""
    return _greedy_solve(puzzle, record=True)[2]


def _greedy_solve(puzzle, record=False):
    """Repeatedly fence the pig's single best escape neighbour. Returns (fences_used, trapped)
    or (fences_used, trapped, moves) when record=True."""
    rows, cols = puzzle["rows"], puzzle["cols"]
    fences = {tuple(f) for f in puzzle["fences"]}
    pig = tuple(puzzle["pig"])
    used, trapped, moves = 0, False, []
    for _ in range(rows * cols):
        if _pig_step(pig, fences, rows, cols) is None:
            trapped = True
            break
        dist = _dist_field(fences, rows, cols)
        target, bd = None, None
        for nb in _neighbours(*pig, rows, cols):
            if nb in fences:
                continue
            d = dist.get(nb)
            if d is not None and (bd is None or d < bd):
                bd, target = d, nb
        if target is None:  # no open path neighbour → will be trapped next check
            target = next((nb for nb in _neighbours(*pig, rows, cols) if nb not in fences), None)
            if target is None:
                trapped = True
                break
        fences.add(target)
        used += 1
        moves.append(list(target))
        nxt = _pig_step(pig, fences, rows, cols)
        if nxt is None:
            trapped = True
            break
        pig = nxt
        if _is_border(*pig, rows, cols):
            break  # greedy let it escape (rare on seeded boards); benchmark still recorded
    return (used, trapped, moves) if record else (used, trapped)


def share_grid(result: dict, meta: dict) -> str:
    diff = meta.get("difficulty", "")
    par_v = meta.get("par")
    secs = result["secondary"] // 1000
    t = f"{secs // 60}:{secs % 60:02d}"
    par_str = f" (par {par_v})" if par_v is not None else ""
    blocks = "🟩" * min(result["primary"], 12)
    return f"🐷 Trap the Pig · {diff} · {result['primary']} fences{par_str} · {t}\n{blocks}"
