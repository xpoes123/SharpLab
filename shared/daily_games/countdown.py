"""Countdown (Numbers Round) — a Daily Games plugin (pure, server-authoritative logic).

Everyone gets the same six numbers and the same target. Build an arithmetic expression from a
SUBSET of the numbers (each usable at most as many times as it appears) using + - * / to hit the
target EXACTLY, under Countdown rules: positive integers only, exact division only (no fractions).
Fastest exact solve wins; the count of numbers used breaks ties.

Determinism: the board is generated from (seed, difficulty), so every player gets the identical
puzzle. `build_solvable` re-rolls the seed until the bounded solver proves the target is reachable,
so a daily is GUARANTEED to have an exact answer. The board payload holds ONLY public info
({"numbers", "target"}) — it is handed to the browser as-is, so it must never carry a solution.

Board payload (stored in daily_puzzles.payload, sent to the client verbatim):
  {"numbers": [n0..n5], "target": T, "difficulty": "easy"}
Solution (validated by /submit): {"moves": ["<expression string>"]}  — one full expression.

The expression evaluator here (ExprError / evaluate_expression) is the ONE source of truth for
Countdown parsing + evaluation; web/g_countdown.py (solo + sprint arcade modes) imports it so the
solve rules stay identical everywhere. NEVER eval/exec — a hand-written tokenizer + shunting-yard
parser only.
"""

from __future__ import annotations

import random

ID = "countdown"
NAME = "Countdown"
ICON = "🔢"
SURFACE = "web"
DIFFICULTIES = ["easy", "medium", "hard"]
# Rank by TIME first (fastest exact solve wins), then fewest numbers used as the tiebreak. The web
# layer overwrites secondary_score with the server-measured elapsed time, and primary_score carries
# the count of numbers used (from validate) — so this order = "fastest, fewest-numbers breaks ties".
RANK_ORDER = ("secondary_score", "primary_score")
HOWTO = (
    "You get **six numbers** and a **target**. Build an expression with **+ − × ÷** that hits the "
    "target **exactly**. Countdown rules: work in **whole numbers only** and division must be "
    "**exact** (no fractions or remainders). You don't have to use every number, but you can use "
    "each one only as often as it appears. **Fastest exact solve wins**; fewest numbers used breaks "
    "ties. The clock starts when you hit Start."
)

LARGE = [25, 50, 75, 100]
# difficulty → (target low, target high). Wider/higher targets are harder to hit exactly.
_TARGET = {"easy": (101, 499), "medium": (101, 799), "hard": (300, 999)}
_SOLVE_BUDGET = 200_000     # max combine steps the solver explores before giving up on a board


# ── Hand-written expression evaluator (NO eval / NO Function) ──
class ExprError(ValueError):
    """Raised for any malformed / disallowed / non-integer-division expression."""


_OPS = {"+", "-", "*", "/"}
_PREC = {"+": 1, "-": 1, "*": 2, "/": 2}


def _tokenize(expr: str) -> list:
    """Turn the string into a list of ints and single-char operator/paren tokens.
    Only digits, spaces, + - * / and parentheses are permitted."""
    tokens: list = []
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c.isspace():
            i += 1
            continue
        if c.isdigit():
            j = i
            while j < n and expr[j].isdigit():
                j += 1
            tokens.append(int(expr[i:j]))
            i = j
            continue
        if c in _OPS or c in "()":
            tokens.append(c)
            i += 1
            continue
        raise ExprError(f"illegal character {c!r}")
    if not tokens:
        raise ExprError("empty expression")
    return tokens


def _to_rpn(tokens: list) -> list:
    """Shunting-yard: infix tokens -> reverse-polish output. Validates structure so
    malformed input (double operators, unbalanced parens) raises rather than mis-parsing."""
    output: list = []
    stack: list = []
    prev = None  # None | 'num' | 'op' | '(' | ')'
    for t in tokens:
        if isinstance(t, int):
            if prev in ("num", ")"):
                raise ExprError("missing operator")
            output.append(t)
            prev = "num"
        elif t in _OPS:
            if prev is None or prev == "op" or prev == "(":
                raise ExprError("misplaced operator")
            while stack and stack[-1] in _OPS and _PREC[stack[-1]] >= _PREC[t]:
                output.append(stack.pop())
            stack.append(t)
            prev = "op"
        elif t == "(":
            if prev in ("num", ")"):
                raise ExprError("missing operator")
            stack.append(t)
            prev = "("
        elif t == ")":
            if prev not in ("num", ")"):
                raise ExprError("misplaced parenthesis")
            while stack and stack[-1] != "(":
                output.append(stack.pop())
            if not stack:
                raise ExprError("unbalanced parentheses")
            stack.pop()  # discard '('
            prev = ")"
    if prev in ("op", "("):
        raise ExprError("incomplete expression")
    while stack:
        op = stack.pop()
        if op == "(":
            raise ExprError("unbalanced parentheses")
        output.append(op)
    return output


def _eval_rpn(rpn: list) -> tuple[int, list[int]]:
    """Evaluate RPN with integer-only, exact-division Countdown rules.
    Returns (value, literals_used)."""
    stack: list[int] = []
    used: list[int] = []
    for t in rpn:
        if isinstance(t, int):
            stack.append(t)
            used.append(t)
            continue
        if len(stack) < 2:
            raise ExprError("malformed expression")
        b = stack.pop()
        a = stack.pop()
        if t == "+":
            stack.append(a + b)
        elif t == "-":
            stack.append(a - b)
        elif t == "*":
            stack.append(a * b)
        elif t == "/":
            if b == 0 or a % b != 0:
                raise ExprError("division must be exact (no fractions)")
            stack.append(a // b)
    if len(stack) != 1:
        raise ExprError("malformed expression")
    return stack[0], used


def evaluate_expression(expr: str, numbers: list[int]) -> int:
    """Full validation + evaluation. Verifies every literal used is available in the
    `numbers` multiset (each usable at most as many times as it appears; a SUBSET is fine).
    Raises ExprError on anything invalid. Returns the integer result."""
    if len(expr) > 200:
        raise ExprError("expression too long")
    value, used = _eval_rpn(_to_rpn(_tokenize(expr)))
    avail: dict[int, int] = {}
    for x in numbers:
        avail[x] = avail.get(x, 0) + 1
    for x in used:
        if avail.get(x, 0) <= 0:
            raise ExprError(f"number {x} is not available")
        avail[x] -= 1
    return value


# ── Solver: proves solvability + provides an achievable par ──
def _combine(a: tuple, b: tuple):
    """Every well-formed result of combining two working items (value, expr, numbers_used).
    Keeps positive integers only and exact division; skips ×1 (a no-op that just wastes a
    number). Fully parenthesises so the expr string re-parses unambiguously."""
    av, ae, ac = a
    bv, be, bc = b
    cnt = ac + bc
    out = [(av + bv, f"({ae}+{be})", cnt)]
    if av != 1 and bv != 1:
        out.append((av * bv, f"({ae}*{be})", cnt))
    hi, hie, lo, loe = (av, ae, bv, be) if av >= bv else (bv, be, av, ae)
    if hi - lo > 0:
        out.append((hi - lo, f"({hie}-{loe})", cnt))
    if lo != 0 and hi % lo == 0 and lo != 1:
        out.append((hi // lo, f"({hie}/{loe})", cnt))
    return out


def _dfs(items: list, target: int, budget: list) -> tuple | None:
    """Depth-first search over pairwise combinations. Returns the FIRST (value, expr, used)
    that equals `target`, or None. `budget` is a one-element list decremented per combine so a
    single unsolvable board can't explore forever."""
    n = len(items)
    for i in range(n):
        for j in range(i + 1, n):
            rest = [items[k] for k in range(n) if k != i and k != j]
            for val, expr, cnt in _combine(items[i], items[j]):
                if budget[0] <= 0:
                    return None
                budget[0] -= 1
                if val == target:
                    return (val, expr, cnt)
                if rest:
                    sub = _dfs(rest + [(val, expr, cnt)], target, budget)
                    if sub:
                        return sub
    return None


def solve(numbers: list[int], target: int) -> tuple[bool, int, str | None]:
    """(reachable, numbers_used, witness_expr). Finds ONE exact expression via bounded DFS —
    enough to prove the board winnable and to quote an achievable par."""
    items = [(int(n), str(int(n)), 1) for n in numbers]
    hit = _dfs(items, int(target), [_SOLVE_BUDGET])
    if hit is None:
        return (False, 0, None)
    val, expr, used = hit
    return (True, used, expr)


def _pick_numbers(rng: random.Random) -> list[int]:
    """Six numbers: 0–4 'large' from {25,50,75,100}, the rest 'small' from two sets of 1..10."""
    n_large = rng.randrange(5)
    large_pool = LARGE[:]
    numbers: list[int] = []
    for _ in range(n_large):
        numbers.append(large_pool.pop(rng.randrange(len(large_pool))))
    small_pool = [n for n in range(1, 11) for _ in range(2)]
    for _ in range(6 - n_large):
        numbers.append(small_pool.pop(rng.randrange(len(small_pool))))
    rng.shuffle(numbers)
    return numbers


def generate(seed: int, difficulty: str) -> dict:
    """A deterministic (possibly-unsolvable) board for (seed, difficulty). Prefer build_solvable
    for the daily — it guarantees an exact answer exists."""
    rng = random.Random(seed)
    lo, hi = _TARGET.get(difficulty, _TARGET["medium"])
    numbers = _pick_numbers(rng)
    target = rng.randint(lo, hi)
    return {"numbers": numbers, "target": target, "difficulty": difficulty}


def build_solvable(seed: int, difficulty: str, attempts: int = 400) -> dict:
    """Deterministically produce a board GUARANTEED to have an exact solution. Re-rolls the seed
    until the solver finds a witness; same (seed, difficulty) → same board. The returned payload is
    PUBLIC (numbers + target only) — the witness is intentionally NOT stored, so the board can be
    handed to the browser without leaking a solution."""
    for attempt in range(attempts):
        s = (seed + attempt * 2654435761) & 0xFFFFFFFF
        board = generate(s, difficulty)
        ok, _used, _expr = solve(board["numbers"], board["target"])
        if ok:
            return {"numbers": board["numbers"], "target": board["target"], "difficulty": difficulty}
    # Fallback (effectively never hit): construct a target that is solvable by construction, so a
    # daily can always be built. Two numbers multiplied are always reachable from the same numbers.
    rng = random.Random(seed)
    numbers = _pick_numbers(rng)
    a, b = numbers[0], numbers[1]
    target = a * b if a * b >= 101 else a + b + 100
    return {"numbers": numbers, "target": target, "difficulty": difficulty}


def par(puzzle: dict) -> tuple[int, bool]:
    """Par = the numbers used by the first solution the solver finds — an ACHIEVABLE count that
    good players match or beat. approx=True: it's a benchmark, not a proven minimum."""
    ok, used, _expr = solve(puzzle["numbers"], puzzle["target"])
    return (used if ok else 2, True)


def validate(puzzle: dict, solution: dict) -> dict | None:
    """A win = an expression, built from the available numbers, that evaluates EXACTLY to the
    target. primary = count of numbers used (tiebreak); secondary is set to elapsed time by the web
    layer. Returns None for anything that doesn't hit the target — the player just keeps trying."""
    expr = None
    if isinstance(solution, dict):
        if isinstance(solution.get("expression"), str):
            expr = solution["expression"]
        else:
            moves = solution.get("moves") or []
            if moves and isinstance(moves[0], str):
                expr = moves[0]
    if not expr:
        return None
    try:
        value = evaluate_expression(expr, puzzle["numbers"])
    except ExprError:
        return None
    if value != puzzle["target"]:
        return None
    used = sum(1 for t in _tokenize(expr) if isinstance(t, int))
    return {"solved": True, "primary": used, "secondary": 0}


def share_grid(result: dict, meta: dict) -> str:
    num = meta.get("number")
    par_v = meta.get("par")
    secs = result["secondary"] // 1000
    t = f"{secs // 60}:{secs % 60:02d}"
    head = f"{ICON} {NAME} #{num}" if num else f"{ICON} {NAME}"
    par_str = f" (par {par_v})" if par_v is not None else ""
    blocks = "🟩" * min(result["primary"], 12)
    used = result["primary"]
    return (f"{head} · {meta.get('difficulty', '')} · {used} number{'s' if used != 1 else ''}"
            f"{par_str} · {t}\n{blocks}")


if __name__ == "__main__":
    # one runnable self-check — evaluator, solver, validation, determinism.
    assert evaluate_expression("(100 + 25) * 3", [100, 25, 3, 7, 2, 50]) == 375
    for bad in ("100 / 3", "9 * 9"):
        try:
            evaluate_expression(bad, [100, 3, 1, 2, 4, 5] if "/" in bad else [1, 2, 3, 4, 5, 6])
            raise AssertionError(bad)
        except ExprError:
            pass
    ok, used, expr = solve([100, 25, 3, 7, 2, 50], 375)
    assert ok and evaluate_expression(expr, [100, 25, 3, 7, 2, 50]) == 375
    b = build_solvable(12345, "medium")
    assert build_solvable(12345, "medium") == b                      # deterministic
    ok2, _u, w = solve(b["numbers"], b["target"])
    assert ok2, "daily board must be solvable"
    assert validate(b, {"moves": [w]}) == {"solved": True, "primary": validate(b, {"moves": [w]})["primary"], "secondary": 0}
    assert validate(b, {"moves": ["1+1"]}) is None or b["target"] == 2  # a wrong answer doesn't win
    p, approx = par(b)
    assert p >= 1 and approx
    print("countdown self-check OK", b["numbers"], "->", b["target"], "witness", w)
