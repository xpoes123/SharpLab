"""Web arcade — solo Countdown (Numbers Round).

Pick six numbers (0–4 "large" from {25,50,75,100}, the rest "small" from two 1..10 sets)
and a target 101..999. The player builds an arithmetic expression from a SUBSET of those
numbers to hit the target. The expression is validated + evaluated **server-side** with a
hand-written tokenizer + shunting-yard parser — NEVER `eval`/`exec` — under Countdown rules
(integer-only arithmetic, exact division only). Rounds are stateless: the chosen numbers +
target ride in a signed, time-limited token. An exact solve pays the shared per-day-capped
activity reward ("countdown_win"), so it can't be farmed."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/arcade/countdown")
_round_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="arcade-countdown")
_ROUND_TTL = 300  # seconds a round token stays valid

LARGE = [25, 50, 75, 100]


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
