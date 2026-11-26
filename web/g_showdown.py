"""Showdown web API — the hand-reading speed game's daily Time Trial (page: /showdown.html).

Same deals for everyone per (puzzle-day, variant, hands); one ranked run each. Server-authoritative
like web/daily.py: the deals are withheld until POST /start, which anchors the clock to the user's
FIRST start (a reload resumes it, never resets it); /submit grades the picks with the Python
evaluator and times the run server-side. Score = time + 10 s per miss, lowest wins. Discord members
and web guests (the pokehoops `ph_guest` cookie) both play and show on the board. Practice mode is
client-only (web/static/showdown.js deals locally) and never touches this API.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from db import queries
from shared import daily, showdown
from web import auth
from web import g_pokehoops as ph

router = APIRouter(prefix="/api/v1/arcade/showdown")
_TTL = 6 * 3600
_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="showdown-start")
_cache: dict[tuple[str, str, int], list[dict]] = {}


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else ph._guest(request)


def _deals(day: str, variant: str, hands: int) -> list[dict]:
    key = (day, variant, hands)
    if key not in _cache:
        if len(_cache) > 64:
            _cache.clear()
        _cache[key] = showdown.daily_deals(day, variant, hands)
    return _cache[key]


def _bad_table(variant: str, hands: int):
    if not showdown.valid_table(variant, hands):
        return JSONResponse({"error": "unknown game or table size"}, status_code=400)
    return None


def _elapsed_ms(started_at: str) -> int:
    t = datetime.fromisoformat(started_at)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - t).total_seconds() * 1000))


def _result(run: dict) -> dict:
    return {"elapsed_ms": run["elapsed_ms"], "wrong": run["wrong"], "score_ms": run["score_ms"]}


@router.get("/today")
async def today(request: Request, variant: str = "plo4", hands: int = 4):
    if bad := _bad_table(variant, hands):
        return bad
    day = daily.puzzle_day()
    out = {"day": day, "variant": variant, "hands": hands, "deals": showdown.DAILY_DEALS,
           "penalty_ms": showdown.PENALTY_MS, "played": False, "signed_in": False}
    uid = _uid(request)
    if uid:
        out["signed_in"] = not ph.is_guest(uid)
        if ph.is_guest(uid):
            out["guest"] = uid.split(":", 2)[2]
        run = await queries.get_showdown_run(uid, day, variant, hands)
        if run and run["finished_at"]:
            out["played"] = True
            out["your_result"] = _result(run)
    return out


class StartBody(BaseModel):
    variant: str
    hands: int


@router.post("/start")
async def start(request: Request, body: StartBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in (or pick a guest name) to play the daily"}, status_code=401)
    if bad := _bad_table(body.variant, body.hands):
        return bad
    day = daily.puzzle_day()
    run = await queries.get_or_create_showdown_start(uid, day, body.variant, body.hands)
    if run["finished_at"]:
        return JSONResponse({"error": "you already ran today's table", "result": _result(run)}, status_code=409)
    deals = _deals(day, body.variant, body.hands)
    token = _signer.dumps({"day": day, "variant": body.variant, "hands": body.hands, "uid": uid})
    return {"day": day, "deals": deals, "token": token, "elapsed_ms": _elapsed_ms(run["started_at"]),
            "penalty_ms": showdown.PENALTY_MS}


class SubmitBody(BaseModel):
    token: str
    picks: list[list[str]]   # per deal, one pick per step; the time is measured server-side


@router.post("/submit")
async def submit(request: Request, body: SubmitBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play the daily"}, status_code=401)
    try:
        data = _signer.loads(body.token, max_age=_TTL)
    except (BadSignature, SignatureExpired, ValueError):
        return JSONResponse({"error": "your run expired — press Start again"}, status_code=400)
    day = daily.puzzle_day()
    if data.get("uid") != uid or data.get("day") != day:
        return JSONResponse({"error": "stale run — the day rolled over"}, status_code=400)
    variant, hands = data["variant"], data["hands"]
    run = await queries.get_showdown_run(uid, day, variant, hands)
    if run is None:
        return JSONResponse({"error": "press Start first"}, status_code=400)
    if run["finished_at"]:
        return JSONResponse({"error": "you already ran today's table", "result": _result(run)}, status_code=409)
    deals = _deals(day, variant, hands)
    right = showdown.grade(variant, deals, body.picks)
    elapsed = min(_elapsed_ms(run["started_at"]), _TTL * 1000)
    score = showdown.score_ms(elapsed, right)
    wrong = sum(1 for r in right if not r)
    if not await queries.finish_showdown_run(uid, day, variant, hands, elapsed, wrong, score):
        return JSONResponse({"error": "you already ran today's table"}, status_code=409)
    board = await queries.get_showdown_board(day, variant, hands, 10_000)
    rank = next((i + 1 for i, r in enumerate(board) if r["discord_user"] == uid), len(board))
    return {"right": right, "answers": [showdown.answers(variant, d) for d in deals],
            "elapsed_ms": elapsed, "wrong": wrong, "score_ms": score, "rank": rank, "field": len(board)}


@router.get("/leaderboard")
async def leaderboard(request: Request, variant: str = "plo4", hands: int = 4):
    if bad := _bad_table(variant, hands):
        return bad
    day = daily.puzzle_day()
    rows = await queries.get_showdown_board(day, variant, hands, 50)
    names = await queries.get_display_names([r["discord_user"] for r in rows if not ph.is_guest(r["discord_user"])])
    me = _uid(request)

    def nm(u):
        return u.split(":", 2)[2] if ph.is_guest(u) else names.get(u) or f"user-{u[-4:]}"

    return {"day": day, "variant": variant, "hands": hands,
            "top": [{"rank": i + 1, "name": nm(r["discord_user"]), "guest": ph.is_guest(r["discord_user"]),
                     "score_ms": r["score_ms"], "elapsed_ms": r["elapsed_ms"], "wrong": r["wrong"],
                     "me": r["discord_user"] == me} for i, r in enumerate(rows)]}
