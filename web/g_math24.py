"""Web arcade — Math 24. Deal 4 numbers, build an expression using ALL four (with
+ − × ÷ and parentheses) that equals 24. All game logic (dealing solvable numbers,
validating an expression) is reused verbatim from the Discord cog (bot.cogs.math24)
— no eval(), no reimplementation.

Numbers are NOT secret — validate_expression is a deterministic pure function, so
there's nothing for the client to cheat by seeing them. The round token exists to
bound *which* four numbers a given submission is judged against (so a client can't
claim a solve against numbers it wasn't dealt) and to make a solved round single-use
(gameround.claim), same pattern as web/g_sequence.py.

Also offers a 120-second SPRINT mode (modeled on web/g_zetamac.py / g_sequence.py):
/sprint/start hands out a batch of puzzles and /sprint/submit recounts correctness
server-side and rejects late submits."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bot.cogs.math24 import generate_solvable_numbers, validate_expression
from db import queries
from web import auth, gameround

router = APIRouter(prefix="/api/v1/arcade/math24")

SPRINT_DURATION = 120   # seconds in the sprint
SPRINT_GRACE = 15       # extra seconds allowed for the submit to land
SPRINT_N_PROBLEMS = 20  # each puzzle takes real thought — 20 is plenty for 120s
GAME_ID = "math24"


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


@router.post("/new")
async def math24_new(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    numbers, _solution = generate_solvable_numbers()
    token = gameround.stash({"numbers": numbers})
    return {"token": token, "numbers": numbers}


class GuessBody(BaseModel):
    token: str
    expr: str


@router.post("/guess")
async def math24_guess(request: Request, body: GuessBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    data = gameround.peek(body.token)
    if not isinstance(data, dict):
        return JSONResponse({"error": "round expired — start a new one"}, status_code=400)
    numbers = data["numbers"]
    ok, msg, _val = validate_expression(body.expr, numbers)
    if not ok:
        return {"correct": False, "msg": msg}
    # Correct → claim so it can't be replayed for more coins, then pay the capped reward.
    gameround.claim(body.token)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reward = await queries.grant_activity_reward(uid, "math24_win", day)
    balance = await queries.get_casino_balance(uid) or 0
    return {"correct": True, "reward": reward, "balance": balance}


# ── Sprint mode (120s, batch of puzzles, highest-solved-wins leaderboard) ────


@router.post("/sprint/start")
async def sprint_start(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    rounds = []
    for _ in range(SPRINT_N_PROBLEMS):
        numbers, _solution = generate_solvable_numbers()
        rounds.append(numbers)
    token = gameround.stash({"rounds": rounds, "started": time.monotonic()})
    return {"token": token, "problems": rounds, "duration": SPRINT_DURATION}


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

    rounds = state["rounds"]
    submitted = body.answers[:len(rounds)]  # ignore anything past the problem count
    solved = 0
    for i, numbers in enumerate(rounds):
        expr = submitted[i] if i < len(submitted) else ""
        if not expr:
            continue
        ok, _msg, _val = validate_expression(expr, numbers)
        if ok:
            solved += 1

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    coins = 0
    for _ in range(solved):
        coins += await queries.grant_activity_reward(uid, "math24_win", day)
    best, is_new = await queries.record_skill_best(GAME_ID, uid, solved)
    rank = await queries.get_skill_rank(GAME_ID, uid)
    balance = await queries.get_casino_balance(uid) or 0
    return {"correct": solved, "coins": coins, "balance": balance,
            "best": best, "is_new_best": is_new, "rank": rank}


@router.get("/sprint/leaderboard")
async def sprint_leaderboard(request: Request):
    rows = await queries.get_skill_leaderboard(GAME_ID, 50)
    names = await queries.get_display_names([r["discord_user"] for r in rows])
    me = _uid(request)
    return {"game": GAME_ID, "duration": SPRINT_DURATION,
            "top": [{"rank": i + 1,
                     "name": names.get(r["discord_user"]) or f"user-{r['discord_user'][-4:]}",
                     "score": r["best_ms"], "runs": r["runs"],
                     "me": r["discord_user"] == me} for i, r in enumerate(rows)]}
