"""Web arcade — solo Valorant Agent Guess. Guess the mystery agent from progressive
clues (role → origin → an ability line). Reuses the AGENTS roster + answer-checker from
the Discord cog so the two stay in sync. Rounds are stateless: the agent's index rides in
a signed, time-limited token (no server-side room state), and the coin reward is the shared
per-day-capped activity reward ("valorant_guess"), so it can't be farmed.

Also offers a 120-second SPRINT mode (modeled on web/g_zetamac.py / web/g_sequence.py):
/sprint/start hands out a batch of agents (clues only — the answer indices stay server-side
under an opaque token) and /sprint/submit recounts correctness with check_answer server-side
and rejects late submits, same batch-model tradeoffs as zetamac/sequence."""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bot.cogs.valorant import AGENTS, check_answer
from db import queries
from web import auth, gameround

router = APIRouter(prefix="/api/v1/arcade/valorant")

# AGENTS entry shape: (id, name, [alt_names], role, origin, ability_hint)

SPRINT_DURATION = 120   # seconds in the sprint
SPRINT_GRACE = 15       # extra seconds allowed for the submit to land
SPRINT_N_PROBLEMS = 50  # roster is small (repeats OK) — plenty for 120s of "read + type" guesses
SPRINT_GAME_ID = "valorant"


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
    idx = gameround.peek(token)
    if not isinstance(idx, int):
        return None
    return AGENTS[idx] if 0 <= idx < len(AGENTS) else None


@router.post("/new")
async def valorant_new(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    idx = secrets.randbelow(len(AGENTS))
    # Only the clues leak; the name/alts stay server-side in the signed token.
    return {"token": gameround.stash(idx), "clues": _clues(AGENTS[idx])}


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


# ── Sprint mode (120s, batch of agents, highest-correct-wins leaderboard) ─────


@router.post("/sprint/start")
async def sprint_start(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    idxs = [secrets.randbelow(len(AGENTS)) for _ in range(SPRINT_N_PROBLEMS)]
    problems = [{"clues": _clues(AGENTS[i])} for i in idxs]
    token = gameround.stash({"idxs": idxs, "started": time.monotonic()})
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

    idxs = state["idxs"]
    submitted = body.answers[:len(idxs)]  # ignore anything past the problem count
    correct = sum(
        1 for k, guess in enumerate(submitted)
        if check_answer(guess, ("agent", *AGENTS[idxs[k]]))
    )

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    coins = 0
    for _ in range(correct):
        coins += await queries.grant_activity_reward(uid, "valorant_guess", day)
    best, is_new = await queries.record_skill_best(SPRINT_GAME_ID, uid, correct)
    rank = await queries.get_skill_rank(SPRINT_GAME_ID, uid)
    balance = await queries.get_casino_balance(uid) or 0
    return {"correct": correct, "coins": coins, "balance": balance,
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
