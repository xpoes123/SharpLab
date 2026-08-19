"""Web arcade — solo "guess the next number in the sequence".

A random integer sequence (arithmetic, geometric, squares, Fibonacci-like, …) is shown minus
its final term; the player names the next term. The answer stays SERVER-SIDE via gameround
(never in the client token — an itsdangerous token is signed but base64-readable). A correct
guess pays the shared per-day-capped activity reward ("sequence_win"), single-use so a solved
round can't be replayed. The sequence bank is reused from the Discord cog (bot.cogs.sequence)."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bot.cogs.sequence import SEQUENCE_BANK
from db import queries
from web import auth, gameround

router = APIRouter(prefix="/api/v1/arcade/sequence")


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


@router.post("/new")
async def sequence_new(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    terms, answer, name = SEQUENCE_BANK[secrets.randbelow(len(SEQUENCE_BANK))]
    # Stash the answer (and the pattern name, revealed only after a guess) server-side.
    token = gameround.stash({"answer": int(answer), "name": name})
    return {"token": token, "terms": list(terms)}


class GuessBody(BaseModel):
    token: str
    guess: int


@router.post("/guess")
async def sequence_guess(request: Request, body: GuessBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    data = gameround.peek(body.token)
    if not isinstance(data, dict):
        return JSONResponse({"error": "round expired — start a new one"}, status_code=400)
    if int(body.guess) != data["answer"]:
        return {"correct": False}
    # Correct → claim so it can't be replayed for more coins, then pay the capped reward.
    gameround.claim(body.token)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reward = await queries.grant_activity_reward(uid, "sequence_win", day)
    balance = await queries.get_casino_balance(uid) or 0
    return {"correct": True, "answer": data["answer"], "name": data["name"],
            "reward": reward, "balance": balance}


class RevealBody(BaseModel):
    token: str


@router.post("/reveal")
async def sequence_reveal(request: Request, body: RevealBody):
    """Give up — reveal the answer + pattern name, no reward, round consumed."""
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    data = gameround.claim(body.token)
    if not isinstance(data, dict):
        return JSONResponse({"error": "round expired"}, status_code=400)
    return {"answer": data["answer"], "name": data["name"]}
