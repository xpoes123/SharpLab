"""Web arcade — solo Valorant Agent Guess. Guess the mystery agent from progressive
clues (role → origin → an ability line). Reuses the AGENTS roster + answer-checker from
the Discord cog so the two stay in sync. Rounds are stateless: the agent's index rides in
a signed, time-limited token (no server-side room state), and the coin reward is the shared
per-day-capped activity reward ("valorant_guess"), so it can't be farmed."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from bot.cogs.valorant import AGENTS, check_answer
from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/arcade/valorant")
_round_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="arcade-valorant")
_ROUND_TTL = 900  # seconds a round token stays valid

# AGENTS entry shape: (id, name, [alt_names], role, origin, ability_hint)


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _clues(entry: tuple) -> list[str]:
    """Progressive hints derived from the entry — never leak the name.
    role → origin → an ability line (the cog's long ability description)."""
    _id, _name, _alts, role, origin, ability = entry
    origin_line = (
        f"Origin: {origin}" if origin and origin != "Unknown"
        else "Origin: classified / unknown"
    )
    return [f"Role: {role}", origin_line, f"Ability profile: {ability}"]


def _entry_from_token(token: str) -> tuple | None:
    try:
        idx = _round_signer.loads(token, max_age=_ROUND_TTL)
    except (BadSignature, SignatureExpired, ValueError):
        return None
    return AGENTS[idx] if 0 <= idx < len(AGENTS) else None


@router.post("/new")
async def valorant_new(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    idx = secrets.randbelow(len(AGENTS))
    # Only the clues leak; the name/alts stay server-side in the signed token.
    return {"token": _round_signer.dumps(idx), "clues": _clues(AGENTS[idx])}


class GuessBody(BaseModel):
    token: str
    guess: str


class TokenBody(BaseModel):
    token: str


@router.post("/guess")
async def valorant_guess(request: Request, body: GuessBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    entry = _entry_from_token(body.token)
    if entry is None:
        return JSONResponse({"error": "round expired — start a new one"}, status_code=400)
    # check_answer expects (entry_type, id, name, alts, ...) — prepend the cog's type tag.
    if not check_answer(body.guess, ("agent", *entry)):
        return {"correct": False}
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reward = await queries.grant_activity_reward(uid, "valorant_guess", day)
    balance = await queries.get_casino_balance(uid) or 0
    return {"correct": True, "name": entry[1], "reward": reward, "balance": balance}


@router.post("/reveal")
async def valorant_reveal(request: Request, body: TokenBody):
    """Give up — reveal the answer, no reward."""
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    entry = _entry_from_token(body.token)
    if entry is None:
        return JSONResponse({"error": "round expired"}, status_code=400)
    return {"name": entry[1]}
