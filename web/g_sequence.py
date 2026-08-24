"""Web arcade — solo "guess the next number in the sequence".

A random integer sequence (arithmetic, geometric, squares, Fibonacci-like, …) is shown minus
its final term; the player names the next term. The answer stays SERVER-SIDE via gameround
(never in the client token — an itsdangerous token is signed but base64-readable). A correct
guess pays the shared per-day-capped activity reward ("sequence_win"), single-use so a solved
round can't be replayed. The sequence bank is reused from the Discord cog (bot.cogs.sequence).

Also offers a 120-second SPRINT mode (modeled on web/g_zetamac.py): /sprint/start hands out a
batch of puzzles (terms only — answers stay server-side under an opaque token) and /sprint/submit
recounts correctness server-side and rejects late submits, same batch-model tradeoffs as zetamac."""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bot.cogs.sequence import SEQUENCE_BANK
from db import queries
from web import auth, gameround

router = APIRouter(prefix="/api/v1/arcade/sequence")

SPRINT_DURATION = 120   # seconds in the sprint
SPRINT_GRACE = 15       # extra seconds allowed for the submit to land
SPRINT_N_PROBLEMS = 120 # bank is small (repeats OK) — plenty for 120s of "think a bit" puzzles
SPRINT_GAME_ID = "sequence"


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


# ── Sprint mode (120s, batch of puzzles, highest-correct-wins leaderboard) ────


@router.post("/sprint/start")
async def sprint_start(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    problems, answers = [], []
    for _ in range(SPRINT_N_PROBLEMS):
        terms, answer, _name = SEQUENCE_BANK[secrets.randbelow(len(SEQUENCE_BANK))]
        problems.append(list(terms))
        answers.append(int(answer))
    token = gameround.stash({"answers": answers, "started": time.monotonic()})
    return {"token": token, "problems": problems, "duration": SPRINT_DURATION}


class SprintSubmitBody(BaseModel):
    token: str
    answers: list[int]


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

    answers = state["answers"]
    submitted = body.answers[:len(answers)]  # ignore anything past the problem count
    correct = sum(1 for i, ans in enumerate(submitted) if ans == answers[i])

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    coins = 0
    for _ in range(correct):
        coins += await queries.grant_activity_reward(uid, "sequence_win", day)
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
