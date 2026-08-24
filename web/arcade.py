"""Web arcade — browser versions of the Discord mini-games, on the casino-coin economy.
Phase 1: Who's That Pokémon? (solo). Reuses the Pokédex data + answer-checker from the
Discord cog, so the two stay in sync. Rounds are stateless: the answer index rides in a
signed, time-limited token (no server-side room state), and coin rewards are the shared
per-day-capped activity reward, so it can't be farmed.

Also offers a 120-second SPRINT mode (modeled on web/g_zetamac.py / web/g_sequence.py):
/pokemon/sprint/start hands out a batch of sprite URLs (the pokemon index — not the name —
stays server-side under an opaque token) and /pokemon/sprint/submit recounts correctness
with check_pokemon_answer and rejects late submits, same batch-model tradeoffs as the other
sprints. Leaderboard uses skill_scores game_id "whosthat" (highest-correct-wins)."""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bot.cogs.pokemon import POKEMON, SPRITE_URL, check_pokemon_answer
from db import queries
from web import auth, gameround

router = APIRouter(prefix="/api/v1/arcade")

SPRINT_DURATION = 120   # seconds in the sprint
SPRINT_GRACE = 15       # extra seconds allowed for the submit to land
SPRINT_N_PROBLEMS = 60  # dex is a few hundred entries — plenty for 120s of naming sprites
SPRINT_GAME_ID = "whosthat"


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
    return {"token": gameround.stash(idx), "sprite": SPRITE_URL.format(dex), "gen": POKEMON[idx][4]}


class GuessBody(BaseModel):
    token: str
    guess: str


def _entry_from_token(token: str) -> tuple | None:
    idx = gameround.peek(token)
    if not isinstance(idx, int):
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


# ── Sprint mode (120s, batch of silhouettes, highest-correct-wins leaderboard) ────


@router.post("/pokemon/sprint/start")
async def pokemon_sprint_start(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    idxs = [secrets.randbelow(len(POKEMON)) for _ in range(SPRINT_N_PROBLEMS)]
    problems = [{"image": SPRITE_URL.format(POKEMON[i][0])} for i in idxs]
    token = gameround.stash({"idxs": idxs, "started": time.monotonic()})
    return {"token": token, "problems": problems, "duration": SPRINT_DURATION}


class SprintSubmitBody(BaseModel):
    token: str
    answers: list[str]


@router.post("/pokemon/sprint/submit")
async def pokemon_sprint_submit(request: Request, body: SprintSubmitBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    state = gameround.claim(body.token)  # single-use: can't resubmit to farm
    if not isinstance(state, dict):
        return JSONResponse({"error": "run expired — start a new one"}, status_code=400)
    if time.monotonic() - state["started"] > SPRINT_DURATION + SPRINT_GRACE:
        return JSONResponse({"error": "too slow — that run timed out"}, status_code=400)

    idxs = state["idxs"]
    submitted = body.answers[:len(idxs)]  # ignore anything past the problem count
    correct = sum(
        1 for i, guess in enumerate(submitted)
        if check_pokemon_answer(guess, POKEMON[idxs[i]])
    )

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    coins = 0
    for _ in range(correct):
        coins += await queries.grant_activity_reward(uid, "pokemon_guess", day)
    best, is_new = await queries.record_skill_best(SPRINT_GAME_ID, uid, correct)
    rank = await queries.get_skill_rank(SPRINT_GAME_ID, uid)
    balance = await queries.get_casino_balance(uid) or 0
    return {"correct": correct, "coins": coins, "balance": balance,
            "best": best, "is_new_best": is_new, "rank": rank}


@router.get("/pokemon/sprint/leaderboard")
async def pokemon_sprint_leaderboard(request: Request):
    rows = await queries.get_skill_leaderboard(SPRINT_GAME_ID, 50)
    names = await queries.get_display_names([r["discord_user"] for r in rows])
    me = _uid(request)
    return {"game": SPRINT_GAME_ID, "duration": SPRINT_DURATION,
            "top": [{"rank": i + 1,
                     "name": names.get(r["discord_user"]) or f"user-{r['discord_user'][-4:]}",
                     "score": r["best_ms"], "runs": r["runs"],
                     "me": r["discord_user"] == me} for i, r in enumerate(rows)]}
