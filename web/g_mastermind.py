"""Web arcade — solo Mastermind (code-breaking) on the casino-coin economy.
Crack a secret 4-peg / 6-color code in 10 guesses. Rounds are stateless: the
secret code rides in a signed, time-limited token (no server-side room state),
and the win reward is the shared per-day-capped activity reward, so it can't be
farmed. Mirrors the arcade.py pattern."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from db import queries
from web import auth, gameround

router = APIRouter(prefix="/api/v1/arcade/mastermind")
_round_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="arcade-mastermind")
_ROUND_TTL = 1800  # seconds a round token stays valid

COLORS = ("red", "orange", "yellow", "green", "blue", "purple")
CODE_LEN = 4


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _code_from_token(token: str) -> list[str] | None:
    code = gameround.peek(token)
    if not isinstance(code, list) or len(code) != CODE_LEN:
        return None
    if not all(c in COLORS for c in code):
        return None
    return code


def _score(secret: list[str], guess: list[str]) -> tuple[int, int]:
    """Standard Mastermind feedback: (black, white).
    black = right color + right position. white = right color, wrong position,
    counted with the min-count rule over the remaining (non-black) pegs so
    colors are never double-counted."""
    black = sum(1 for s, g in zip(secret, guess) if s == g)
    white = 0
    for color in COLORS:
        in_secret = sum(1 for i, s in enumerate(secret) if s == color and guess[i] != color)
        in_guess = sum(1 for i, g in enumerate(guess) if g == color and secret[i] != color)
        white += min(in_secret, in_guess)
    return black, white


@router.post("/new")
async def new(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    code = [secrets.choice(COLORS) for _ in range(CODE_LEN)]
    return {"token": gameround.stash(code)}


class GuessBody(BaseModel):
    token: str
    guess: list[str]


@router.post("/guess")
async def guess(request: Request, body: GuessBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    secret = _code_from_token(body.token)
    if secret is None:
        return JSONResponse({"error": "round expired — start a new one"}, status_code=400)
    if len(body.guess) != CODE_LEN or not all(c in COLORS for c in body.guess):
        return JSONResponse({"error": "guess must be 4 valid colors"}, status_code=400)
    black, white = _score(secret, body.guess)
    solved = black == CODE_LEN
    out = {"black": black, "white": white, "solved": solved}
    if solved:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out["reward"] = await queries.grant_activity_reward(uid, "mastermind_win", day)
        out["balance"] = await queries.get_casino_balance(uid) or 0
    return out


class RevealBody(BaseModel):
    token: str


@router.post("/reveal")
async def reveal(request: Request, body: RevealBody):
    """Give up — reveal the code, no reward."""
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    secret = _code_from_token(body.token)
    if secret is None:
        return JSONResponse({"error": "round expired"}, status_code=400)
    return {"code": secret}
