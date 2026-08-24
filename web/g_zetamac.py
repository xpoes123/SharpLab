"""Web arcade — Zetamac. A 120-second solo arithmetic sprint: solve as many
add/sub/mul/div problems as you can. Score = number correct → a HIGHEST-wins
leaderboard (skill_scores game_id "zetamac") + coins per correct answer
("zetamac_win" = 2 coins, capped 300/day, so it can't be farmed).

Server-authoritative: /start stashes the answer key under an opaque token (answers
never reach the client) and records the start time; /submit recounts correctness and
rejects late submits (beyond DURATION + grace), so a client can't take extra time or
claim answers it wasn't given. ponytail: batch model (problems out, answers validated
+ time-window enforced) — a determined cheater could precompute offline, acceptable
for a play-money board; upgrade to per-problem WS if it's ever gamed."""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import queries
from web import auth, gameround

router = APIRouter(prefix="/api/v1/arcade/zetamac")

DURATION = 120          # seconds in the sprint
GRACE = 15              # extra seconds allowed for the submit to land
_N_PROBLEMS = 300       # plenty — even ~1.5 solves/sec tops out well under this
GAME_ID = "zetamac"


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _rand(lo: int, hi: int) -> int:
    return lo + secrets.randbelow(hi - lo + 1)


def _make_problem() -> tuple[str, int]:
    """Zetamac defaults: add/sub over [2,100], mul/div with a small factor in [2,12].
    Sub/div are the inverse of add/mul so answers stay clean (non-negative, no remainder)."""
    kind = secrets.choice(["add", "sub", "mul", "div"])
    if kind == "add":
        a, b = _rand(2, 100), _rand(2, 100)
        return f"{a} + {b}", a + b
    if kind == "sub":
        a, b = _rand(2, 100), _rand(2, 100)
        return f"{a + b} − {b}", a           # (a+b) − b = a
    a, b = _rand(2, 12), _rand(2, 100)
    if kind == "mul":
        return f"{a} × {b}", a * b
    return f"{a * b} ÷ {b}", a               # (a*b) ÷ b = a


@router.post("/start")
async def start(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    problems, answers = [], []
    for _ in range(_N_PROBLEMS):
        text, ans = _make_problem()
        problems.append(text)
        answers.append(ans)
    token = gameround.stash({"answers": answers, "started": time.monotonic()})
    return {"token": token, "problems": problems, "duration": DURATION}


class SubmitBody(BaseModel):
    token: str
    answers: list[int]


@router.post("/submit")
async def submit(request: Request, body: SubmitBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    state = gameround.claim(body.token)  # single-use: can't resubmit to farm
    if not isinstance(state, dict):
        return JSONResponse({"error": "run expired — start a new one"}, status_code=400)
    if time.monotonic() - state["started"] > DURATION + GRACE:
        return JSONResponse({"error": "too slow — that run timed out"}, status_code=400)

    answers = state["answers"]
    submitted = body.answers[:len(answers)]  # ignore anything past the problem count
    correct = sum(1 for i, ans in enumerate(submitted) if ans == answers[i])

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    coins = 0
    for _ in range(correct):
        coins += await queries.grant_activity_reward(uid, "zetamac_win", day)
    best, is_new = await queries.record_skill_best(GAME_ID, uid, correct)
    rank = await queries.get_skill_rank(GAME_ID, uid)
    balance = await queries.get_casino_balance(uid) or 0
    return {"correct": correct, "coins": coins, "balance": balance,
            "best": best, "is_new_best": is_new, "rank": rank}


@router.get("/leaderboard")
async def leaderboard(request: Request):
    rows = await queries.get_skill_leaderboard(GAME_ID, 50)
    names = await queries.get_display_names([r["discord_user"] for r in rows])
    me = _uid(request)
    return {"game": GAME_ID, "duration": DURATION,
            "top": [{"rank": i + 1,
                     "name": names.get(r["discord_user"]) or f"user-{r['discord_user'][-4:]}",
                     "score": r["best_ms"], "runs": r["runs"],
                     "me": r["discord_user"] == me} for i, r in enumerate(rows)]}
