"""Web arcade — Pokédle (Wordle-style solo Pokémon guesser).

Guess a mystery Pokémon; each guess returns attribute feedback (Type1, Type2,
Generation, Legendary) with match/no-match + a gen up/down arrow. Reuses the
Pokédex data + answer-checker from the Discord cog so the two stay in sync.

Rounds are stateless: the answer index rides in a signed, time-limited token (no
server-side room state), and coin rewards use the shared per-day-capped activity
reward, so it can't be farmed."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bot.cogs.pokemon import POKEMON, SPRITE_URL, check_pokemon_answer
from db import queries
from web import auth, gameround

router = APIRouter(prefix="/api/v1/arcade/pokedle")

# Answer pool = recognizable Pokémon (Gen 1–3). Guessing is unrestricted (any
# Pokémon in POKEMON validates), but the mystery answer is always a famous one.
_ANSWER_POOL = [i for i, e in enumerate(POKEMON) if e[4] <= 3]


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _find_entry(guess: str) -> tuple | None:
    """Resolve a guessed name to its POKEMON entry (None if not a real Pokémon)."""
    for entry in POKEMON:
        if check_pokemon_answer(guess, entry):
            return entry
    return None


def _types(entry: tuple) -> tuple[str | None, str | None]:
    types = entry[3] or []
    t1 = types[0] if len(types) >= 1 else None
    t2 = types[1] if len(types) >= 2 else None
    return t1, t2


def _feedback(guess_entry: tuple, answer_entry: tuple) -> dict:
    g_t1, g_t2 = _types(guess_entry)
    a_t1, a_t2 = _types(answer_entry)
    g_gen, a_gen = guess_entry[4], answer_entry[4]
    g_leg, a_leg = bool(guess_entry[6]), bool(answer_entry[6])
    # gen arrow: guess lower than answer → up (aim higher), higher → down.
    gen_dir = "up" if g_gen < a_gen else "down" if g_gen > a_gen else ""
    return {
        "name": guess_entry[1],
        "sprite": SPRITE_URL.format(guess_entry[0]),
        "type1": {"val": g_t1 or "—", "match": g_t1 == a_t1},
        "type2": {"val": g_t2 or "—", "match": g_t2 == a_t2},
        "gen": {"val": g_gen, "match": g_gen == a_gen, "dir": gen_dir},
        "legendary": {"val": g_leg, "match": g_leg == a_leg},
    }


_ALL_NAMES = sorted(e[1] for e in POKEMON)


@router.get("/names")
async def pokedle_names():
    """Full name list for the guess-input datalist (no answer leak)."""
    return {"names": _ALL_NAMES}


@router.post("/new")
async def pokedle_new(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    idx = _ANSWER_POOL[secrets.randbelow(len(_ANSWER_POOL))]
    return {"token": gameround.stash(idx)}


class GuessBody(BaseModel):
    token: str
    guess: str


class RevealBody(BaseModel):
    token: str


def _entry_from_token(token: str) -> tuple | None:
    idx = gameround.peek(token)
    if not isinstance(idx, int):
        return None
    return POKEMON[idx] if 0 <= idx < len(POKEMON) else None


@router.post("/guess")
async def pokedle_guess(request: Request, body: GuessBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    answer = _entry_from_token(body.token)
    if answer is None:
        return JSONResponse({"error": "round expired — start a new one"}, status_code=400)
    guess_entry = _find_entry(body.guess)
    if guess_entry is None:
        return JSONResponse({"error": "unknown Pokémon"}, status_code=400)
    solved = check_pokemon_answer(body.guess, answer)
    out = {"feedback": _feedback(guess_entry, answer), "solved": solved}
    if solved:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out["reward"] = await queries.grant_activity_reward(uid, "pokedle_win", day)
        out["balance"] = await queries.get_casino_balance(uid) or 0
        out["name"] = answer[1]
        out["sprite"] = SPRITE_URL.format(answer[0])
    return out


@router.post("/reveal")
async def pokedle_reveal(request: Request, body: RevealBody):
    """Give up — reveal the answer, no reward."""
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    answer = _entry_from_token(body.token)
    if answer is None:
        return JSONResponse({"error": "round expired"}, status_code=400)
    return {"name": answer[1], "sprite": SPRITE_URL.format(answer[0])}
