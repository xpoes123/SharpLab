"""Web arcade — Math Sprint. A 60-second solo arithmetic drill that pays coins per
correct answer. Stateless: the batch of correct answers rides in a signed, time-limited
token (no server-side game state), and coin rewards are the shared per-day-capped
activity reward ("mathsprint_win" = 2 coins/correct, capped 200/day), so it can't be
farmed. The client is authoritative only for UX — the server recounts correctness.
Score = number correct → a HIGHEST-wins leaderboard (skill_scores game_id "mathsprint")."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import queries
from web import auth, gameround

router = APIRouter(prefix="/api/v1/arcade/mathsprint")
GAME_ID = "mathsprint"
_N_PROBLEMS = 200      # open-ended — well above what's solvable in 60s, so no cap-out ties


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _rand(lo: int, hi: int) -> int:
    return lo + secrets.randbelow(hi - lo + 1)


def _make_problem() -> tuple[dict, int]:
    """Return ({a, op, b}, answer). Kept easy enough to solve in a couple seconds."""
    op = secrets.choice(["+", "+", "-", "-", "*"])  # weight toward add/sub
    if op == "+":
        # a few 2-digit additions, otherwise small
        if secrets.randbelow(3) == 0:
            a, b = _rand(10, 49), _rand(10, 49)
        else:
            a, b = _rand(2, 12), _rand(2, 12)
        return {"a": a, "op": "+", "b": b}, a + b
    if op == "-":
        a, b = _rand(2, 12), _rand(2, 12)
        if b > a:
            a, b = b, a  # keep the answer non-negative
        return {"a": a, "op": "-", "b": b}, a - b
    a, b = _rand(2, 12), _rand(2, 12)
    return {"a": a, "op": "×", "b": b}, a * b


@router.post("/start")
async def start(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    problems, answers = [], []
    for _ in range(_N_PROBLEMS):
        p, ans = _make_problem()
        problems.append(p)
        answers.append(ans)
    return {"token": gameround.stash(answers), "problems": problems}


class SubmitBody(BaseModel):
    token: str
    answers: list[int]


@router.post("/submit")
async def submit(request: Request, body: SubmitBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    correct_answers = gameround.claim(body.token)  # single-use: can't resubmit to farm
    if not isinstance(correct_answers, list):
        return JSONResponse({"error": "run expired — start a new one"}, status_code=400)

    total = len(correct_answers)
    submitted = body.answers[:total]  # ignore extras beyond the problem count
    correct = sum(1 for i, ans in enumerate(submitted) if ans == correct_answers[i])

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    coins = 0
    for _ in range(correct):
        coins += await queries.grant_activity_reward(uid, "mathsprint_win", day)
    best, is_new = await queries.record_skill_best(GAME_ID, uid, correct)
    rank = await queries.get_skill_rank(GAME_ID, uid)
    balance = await queries.get_casino_balance(uid) or 0
    return {"correct": correct, "total": total, "coins": coins, "balance": balance,
            "best": best, "is_new_best": is_new, "rank": rank}


@router.get("/leaderboard")
async def leaderboard(request: Request):
    rows = await queries.get_skill_leaderboard(GAME_ID, 50)
    names = await queries.get_display_names([r["discord_user"] for r in rows])
    me = _uid(request)
    return {"game": GAME_ID,
            "top": [{"rank": i + 1,
                     "name": names.get(r["discord_user"]) or f"user-{r['discord_user'][-4:]}",
                     "score": r["best_ms"], "runs": r["runs"],
                     "me": r["discord_user"] == me} for i, r in enumerate(rows)]}
