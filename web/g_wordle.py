"""Web arcade — solo Wordle, on the casino-coin economy.

Guess the hidden 5-letter word in 6 tries. Reuses the Discord cog's word list
(bot.cogs.wordle.WORDS) so the two stay in sync. Rounds are stateless: the
answer index rides in a signed, time-limited token (the answer never reaches the
client until solve/reveal), and the coin reward is the shared per-day-capped
"wordle_win" activity reward, so it can't be farmed."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from bot.cogs.wordle import WORDS
from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/arcade/wordle")
_round_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="arcade-wordle")
_ROUND_TTL = 1800  # seconds a round token stays valid

# The cog ships one curated list; use it for both answers and the valid-guess
# set. Keep only clean ASCII 5-letter words (the cog's list carries a couple of
# non-ASCII typos that its own len/isalpha filter lets through).
ANSWERS: list[str] = sorted(
    {w.upper() for w in WORDS if len(w) == 5 and w.isascii() and w.isalpha()}
)
ALLOWED: set[str] = set(ANSWERS)


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def score_guess(guess: str, secret: str) -> list[str]:
    """Standard Wordle per-letter feedback, duplicate-safe.

    Returns 5 strings: "correct" | "present" | "absent".
    """
    guess = guess.upper()
    secret = secret.upper()
    result = ["absent"] * 5
    remaining = list(secret)

    # First pass: greens (consume the matched secret letter).
    for i in range(5):
        if guess[i] == secret[i]:
            result[i] = "correct"
            remaining[i] = ""

    # Second pass: yellows against what's left.
    for i in range(5):
        if result[i] == "correct":
            continue
        if guess[i] in remaining:
            result[i] = "present"
            remaining[remaining.index(guess[i])] = ""

    return result


@router.post("/new")
async def wordle_new(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    idx = secrets.randbelow(len(ANSWERS))
    # Only the signed token leaves the server; the answer stays hidden.
    return {"token": _round_signer.dumps(idx)}


def _answer_from_token(token: str) -> str | None:
    try:
        idx = _round_signer.loads(token, max_age=_ROUND_TTL)
    except (BadSignature, SignatureExpired, ValueError):
        return None
    return ANSWERS[idx] if 0 <= idx < len(ANSWERS) else None


class GuessBody(BaseModel):
    token: str
    guess: str


class RevealBody(BaseModel):
    token: str


@router.post("/guess")
async def wordle_guess(request: Request, body: GuessBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)

    answer = _answer_from_token(body.token)
    if answer is None:
        return JSONResponse({"error": "round expired — start a new one"}, status_code=400)

    guess = (body.guess or "").strip().upper()
    if len(guess) != 5 or not guess.isalpha():
        return JSONResponse({"error": "not a word"}, status_code=400)
    if guess not in ALLOWED:
        return JSONResponse({"error": "not a word"}, status_code=400)

    result = score_guess(guess, answer)
    solved = guess == answer
    if not solved:
        return {"result": result, "solved": False}

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reward = await queries.grant_activity_reward(uid, "wordle_win", day)
    balance = await queries.get_casino_balance(uid) or 0
    return {"result": result, "solved": True, "reward": reward,
            "balance": balance, "answer": answer}


@router.post("/reveal")
async def wordle_reveal(request: Request, body: RevealBody):
    """Give up — reveal the answer, no reward."""
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    answer = _answer_from_token(body.token)
    if answer is None:
        return JSONResponse({"error": "round expired"}, status_code=400)
    return {"answer": answer}
