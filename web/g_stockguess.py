"""Web arcade — Stock Guess (solo). Guess a curated stock's YTD % change; land
within 8 percentage points to win the shared per-day-capped activity reward.

Reuses CURATED_TICKERS + fetch_ytd_change from the Discord cog so the two stay in
sync. Rounds are stateless: the ticker + actual YTD ride in a signed, time-limited
token (no server-side room state), and the coin reward is the shared per-day-capped
activity reward, so it can't be farmed."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from bot.cogs.stockguess import CURATED_TICKERS, fetch_ytd_change
from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/arcade")
_round_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="arcade-stockguess")
_ROUND_TTL = 300  # seconds a round token stays valid
_WIN_DELTA = 8.0  # percentage points — guess within this to win

_TICKERS = list(CURATED_TICKERS.keys())


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


@router.post("/stockguess/new")
async def stockguess_new(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    # Try a few random tickers; some may have no fetchable YTD data.
    tried: set[str] = set()
    for _ in range(4):
        ticker = _TICKERS[secrets.randbelow(len(_TICKERS))]
        if ticker in tried:
            continue
        tried.add(ticker)
        try:
            actual = await fetch_ytd_change(ticker)
        except Exception:
            continue
        token = _round_signer.dumps([ticker, round(float(actual), 2)])
        # The actual % stays server-side in the token — only the ticker leaks.
        return {"token": token, "ticker": ticker, "company": CURATED_TICKERS[ticker]}
    return JSONResponse({"error": "couldn't load a stock — try again"}, status_code=503)


class GuessBody(BaseModel):
    token: str
    guess: float


@router.post("/stockguess/guess")
async def stockguess_guess(request: Request, body: GuessBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    try:
        ticker, actual = _round_signer.loads(body.token, max_age=_ROUND_TTL)
    except (BadSignature, SignatureExpired, ValueError):
        return JSONResponse({"error": "round expired — start a new one"}, status_code=400)

    guess = float(body.guess)
    delta = abs(guess - float(actual))
    close = delta <= _WIN_DELTA

    reward = 0
    balance = await queries.get_casino_balance(uid) or 0
    if close:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        reward = await queries.grant_activity_reward(uid, "stockguess_win", day)
        balance = await queries.get_casino_balance(uid) or 0

    return {
        "actual": float(actual),
        "guess": guess,
        "delta": delta,
        "close": bool(close),
        "reward": reward,
        "balance": balance,
        "ticker": ticker,
        "company": CURATED_TICKERS.get(ticker, ticker),
    }
