"""Web arcade — browser versions of the Discord mini-games, on the casino-coin economy.
Phase 1: Who's That Pokémon? (solo). Reuses the Pokédex data + answer-checker from the
Discord cog, so the two stay in sync. Rounds are stateless: the answer index rides in a
signed, time-limited token (no server-side room state), and coin rewards are the shared
per-day-capped activity reward, so it can't be farmed."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from bot.cogs.pokemon import POKEMON, SPRITE_URL, check_pokemon_answer
from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/arcade")
_round_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="arcade-pokemon")
_ROUND_TTL = 900  # seconds a round token stays valid


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _reveal(entry: tuple) -> dict:
    dex, name, _alts, types, gen, _dex_entry, is_legendary = entry
    return {"dex": dex, "name": name, "types": types, "gen": gen,
            "legendary": bool(is_legendary), "sprite": SPRITE_URL.format(dex)}


@router.post("/pokemon/new")
async def pokemon_new(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    idx = secrets.randbelow(len(POKEMON))
    dex = POKEMON[idx][0]
    # Only the sprite + generation leak; the name/types stay server-side in the token.
    return {"token": _round_signer.dumps(idx), "sprite": SPRITE_URL.format(dex), "gen": POKEMON[idx][4]}


class GuessBody(BaseModel):
    token: str
    guess: str


def _entry_from_token(token: str) -> tuple | None:
    try:
        idx = _round_signer.loads(token, max_age=_ROUND_TTL)
    except (BadSignature, SignatureExpired, ValueError):
        return None
    return POKEMON[idx] if 0 <= idx < len(POKEMON) else None


@router.post("/pokemon/guess")
async def pokemon_guess(request: Request, body: GuessBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    entry = _entry_from_token(body.token)
    if entry is None:
        return JSONResponse({"error": "round expired — start a new one"}, status_code=400)
    if not check_pokemon_answer(body.guess, entry):
        return {"correct": False}
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reward = await queries.grant_activity_reward(uid, "pokemon_guess", day)
    balance = await queries.get_casino_balance(uid) or 0
    return {"correct": True, "reward": reward, "balance": balance, **_reveal(entry)}


@router.post("/pokemon/reveal")
async def pokemon_reveal(request: Request, body: GuessBody):
    """Give up — reveal the answer, no reward."""
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    entry = _entry_from_token(body.token)
    if entry is None:
        return JSONResponse({"error": "round expired"}, status_code=400)
    return {"correct": False, "gaveup": True, **_reveal(entry)}
