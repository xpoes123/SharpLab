"""Web arcade — solo Countdown (Numbers Round).

Pick six numbers (0–4 "large" from {25,50,75,100}, the rest "small" from two 1..10 sets)
and a target 101..999. The player builds an arithmetic expression from a SUBSET of those
numbers to hit the target. The expression is validated + evaluated **server-side** with a
hand-written tokenizer + shunting-yard parser — NEVER `eval`/`exec` — under Countdown rules
(integer-only arithmetic, exact division only). Rounds are stateless: the chosen numbers +
target ride in a signed, time-limited token. An exact solve pays the shared per-day-capped
activity reward ("countdown_win"), so it can't be farmed.

Also offers a 120-second SPRINT mode (modeled on web/g_zetamac.py / web/g_sequence.py):
/sprint/start hands out a batch of rounds (numbers + target — no signed token needed since
nothing secret rides along, but we still stash server-side via web.gameround to keep the same
opaque-token pattern) and /sprint/submit re-validates every submitted expression with the same
`evaluate_expression` used by the solo /solve endpoint, so solve rules stay identical. Countdown
solves are slow, so 30 rounds is plenty for a 120s run."""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from db import queries
from web import auth, gameround

router = APIRouter(prefix="/api/v1/arcade/countdown")
_round_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="arcade-countdown")
_ROUND_TTL = 300  # seconds a round token stays valid

LARGE = [25, 50, 75, 100]

SPRINT_DURATION = 120    # seconds in the sprint
SPRINT_GRACE = 15        # extra seconds allowed for the submit to land
SPRINT_N_PROBLEMS = 30   # countdown solves are slow — 30 rounds is plenty for 120s
SPRINT_GAME_ID = "countdown"


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _new_round() -> tuple[list[int], int]:
    """Pick 6 numbers (0–4 large, rest small) + a target 101..999."""
    n_large = secrets.randbelow(5)  # 0..4
    large_pool = LARGE[:]
    numbers: list[int] = []
    for _ in range(n_large):
        numbers.append(large_pool.pop(secrets.randbelow(len(large_pool))))
    # smalls drawn from TWO sets of 1..10 (each value available at most twice)
    small_pool = [n for n in range(1, 11) for _ in range(2)]
    for _ in range(6 - n_large):
        numbers.append(small_pool.pop(secrets.randbelow(len(small_pool))))
    secrets.SystemRandom().shuffle(numbers)
    target = 101 + secrets.randbelow(899)  # 101..999
    return numbers, target


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


@router.post("/new")
async def countdown_new(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    numbers, target = _new_round()
    return {"token": _round_signer.dumps([numbers, target]), "numbers": numbers, "target": target}


def _decode(token: str) -> tuple[list[int], int] | None:
    try:
        numbers, target = _round_signer.loads(token, max_age=_ROUND_TTL)
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return None
    if not isinstance(numbers, list) or not isinstance(target, int):
        return None
    return [int(x) for x in numbers], target


class SolveBody(BaseModel):
    token: str
    expression: str


@router.post("/solve")
async def countdown_solve(request: Request, body: SolveBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    decoded = _decode(body.token)
    if decoded is None:
        return JSONResponse({"error": "round expired — start a new one"}, status_code=400)
    numbers, target = decoded
    try:
        value = evaluate_expression(body.expression, numbers)
    except ExprError as e:
        return {"valid": False, "error": str(e)}
    exact = value == target
    delta = abs(value - target)
    res = {"value": value, "target": target, "valid": True, "exact": exact, "delta": delta}
    if exact:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        res["reward"] = await queries.grant_activity_reward(uid, "countdown_win", day)
        res["balance"] = await queries.get_casino_balance(uid) or 0
    return res


class RevealBody(BaseModel):
    token: str


@router.post("/reveal")
async def countdown_reveal(request: Request, body: RevealBody):
    """Give up — echo the numbers/target, no reward, no auto-solver."""
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    decoded = _decode(body.token)
    if decoded is None:
        return JSONResponse({"error": "round expired"}, status_code=400)
    numbers, target = decoded
    return {"numbers": numbers, "target": target,
            "note": "No solution shown — reach the target yourself next time."}


# ── Sprint mode (120s, batch of rounds, highest-solved-wins leaderboard) ────


@router.post("/sprint/start")
async def sprint_start(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    rounds: list[tuple[list[int], int]] = [_new_round() for _ in range(SPRINT_N_PROBLEMS)]
    token = gameround.stash({"rounds": rounds, "started": time.monotonic()})
    problems = [{"numbers": numbers, "target": target} for numbers, target in rounds]
    return {"token": token, "problems": problems, "duration": SPRINT_DURATION}


class SprintSubmitBody(BaseModel):
    token: str
    answers: list[str]


@router.post("/sprint/submit")
async def sprint_submit(request: Request, body: SprintSubmitBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    state = gameround.claim(body.token)  # single-use: can't resubmit to farm
    if not isinstance(state, dict):
        return JSONResponse({"error": "run expired — start a new one"}, status_code=400)
    if time.monotonic() - state["started"] > SPRINT_DURATION + SPRINT_GRACE:
        return JSONResponse({"error": "too slow — that run timed out"}, status_code=400)

    rounds = state["rounds"]
    submitted = body.answers[:len(rounds)]  # ignore anything past the round count
    solved = 0
    for i, (numbers, target) in enumerate(rounds):
        expr = submitted[i] if i < len(submitted) else ""
        if not expr or not expr.strip():
            continue
        try:
            value = evaluate_expression(expr, numbers)
        except ExprError:
            continue
        if value == target:
            solved += 1

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    coins = 0
    for _ in range(solved):
        coins += await queries.grant_activity_reward(uid, "countdown_win", day)
    best, is_new = await queries.record_skill_best(SPRINT_GAME_ID, uid, solved)
    rank = await queries.get_skill_rank(SPRINT_GAME_ID, uid)
    balance = await queries.get_casino_balance(uid) or 0
    return {"correct": solved, "coins": coins, "balance": balance,
            "best": best, "is_new_best": is_new, "rank": rank}


@router.get("/sprint/leaderboard")
async def sprint_leaderboard(request: Request):
    rows = await queries.get_skill_leaderboard(SPRINT_GAME_ID, 50)
    names = await queries.get_display_names([r["discord_user"] for r in rows])
    me = _uid(request)
    return {"game": SPRINT_GAME_ID, "duration": SPRINT_DURATION,
            "top": [{"rank": i + 1,
                     "name": names.get(r["discord_user"]) or f"user-{r['discord_user'][-4:]}",
                     "score": r["best_ms"], "runs": r["runs"],
                     "me": r["discord_user"] == me} for i, r in enumerate(rows)]}
