"""Daily Games web API — today's puzzle, start, submit, leaderboards.

Server-authoritative on two axes:
  1. Outcome — a submission is scored only by replaying it through the plugin's `validate`.
  2. Time — the board is WITHHELD until POST /start, which stamps a signed server-side start time;
     /submit computes elapsed as (server now − start), so you can't study the puzzle before the
     clock runs and you can't fake your time client-side.
One submit per user per day (DB PK). A first solve grants participation coins + bumps the streak;
placement coins are paid later by the rollover job.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from datetime import date, datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from db import queries
from shared import daily
from web import auth, gameround

router = APIRouter(prefix="/api/v1/daily")

_START_TTL = 6 * 3600        # a start token is good for 6h (plenty for one sitting)
_start_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="daily-start")


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _meta(game_id: str) -> dict:
    g = daily.DAILY_GAMES[game_id]
    return {"id": g.ID, "name": g.NAME, "icon": g.ICON, "howto": getattr(g, "HOWTO", "")}


@router.get("/today")
async def today(request: Request):
    """Meta only — NO board. The board is handed out by /start once the clock is running."""
    day = daily.puzzle_day()
    puz = await queries.get_or_create_daily_puzzle(day)
    out = {
        "day": day, "number": daily.puzzle_number(day), "game": _meta(puz["game_id"]),
        "difficulty": puz["difficulty"], "par": puz["par"], "par_approx": puz["par_approx"],
        "played": False, "signed_in": False,
    }
    uid = _uid(request)
    if uid:
        out["signed_in"] = True
        out["streak"] = (await queries.get_daily_streak(uid, "__overall__"))["current"]
        out["balance"] = await queries.get_casino_balance(uid) or 0
        prior = await queries.get_daily_result(puz["game_id"], day, uid)
        if prior:
            out["played"] = True
            out["your_result"] = {"solved": bool(prior["solved"]),
                                  "primary": prior["primary_score"],
                                  "secondary": prior["secondary_score"]}
    return out


@router.post("/start")
async def start(request: Request):
    """Hand over today's board. The clock is anchored to the user's FIRST Start of the day and
    runs continuously — retries reuse that anchor (see get_or_create_daily_start), so the returned
    `elapsed_ms` already includes time spent on earlier attempts. Grinding costs time."""
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play the daily"}, status_code=401)
    day = daily.puzzle_day()
    puz = await queries.get_or_create_daily_puzzle(day)
    started_at = await queries.get_or_create_daily_start(uid, puz["game_id"], day)
    elapsed_ms = _elapsed_ms(started_at)
    token = _start_signer.dumps({"day": day, "game": puz["game_id"], "uid": uid})
    return {"board": puz["payload"], "difficulty": puz["difficulty"], "par": puz["par"],
            "number": daily.puzzle_number(day), "start_token": token, "elapsed_ms": elapsed_ms}


def _elapsed_ms(started_at_iso: str) -> int:
    started = datetime.fromisoformat(started_at_iso)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - started).total_seconds() * 1000))


class SubmitBody(BaseModel):
    start_token: str
    solution: dict   # {"moves": [[r,c],...]} — elapsed is measured SERVER-side, not trusted here


@router.post("/submit")
async def submit(request: Request, body: SubmitBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play the daily"}, status_code=401)
    day = daily.puzzle_day()
    puz = await queries.get_or_create_daily_puzzle(day)
    game = daily.DAILY_GAMES[puz["game_id"]]

    # Auth the token (this run belongs to this user + today's game). The CLOCK, though, comes from
    # the persisted first-Start anchor, not the token — so retries accumulate time.
    try:
        data = _start_signer.loads(body.start_token, max_age=_START_TTL)
    except (BadSignature, SignatureExpired, ValueError):
        return JSONResponse({"error": "your run expired — press Start again"}, status_code=400)
    if data.get("day") != day or data.get("uid") != uid or data.get("game") != puz["game_id"]:
        return JSONResponse({"error": "stale run — press Start again"}, status_code=400)

    started_at = await queries.get_daily_start(uid, puz["game_id"], day)
    if started_at is None:
        return JSONResponse({"error": "press Start first"}, status_code=400)

    if await queries.get_daily_result(puz["game_id"], day, uid):
        return JSONResponse({"error": "you already played today's daily"}, status_code=409)

    result = game.validate(puz["payload"], body.solution)
    if result is None:
        return JSONResponse({"error": "that didn't trap the pig — keep going"}, status_code=400)
    # Continuous clock from the first Start across all retries.
    result["secondary"] = min(_elapsed_ms(started_at), _START_TTL * 1000)

    recorded = await queries.record_daily_result(
        puz["game_id"], day, uid, solved=result["solved"],
        primary=result["primary"], secondary=result["secondary"])
    if not recorded:
        return JSONResponse({"error": "you already played today's daily"}, status_code=409)

    coins = await queries.grant_activity_reward(uid, "daily_play", day)
    await queries.update_daily_streak(uid, puz["game_id"], day)
    streak = await queries.update_daily_streak(uid, "__overall__", day)

    ranked = daily.rank_results(await queries.get_daily_results(puz["game_id"], day), puz["game_id"])
    rank = next((r["rank"] for r in ranked if r["discord_user"] == uid), len(ranked))
    balance = await queries.get_casino_balance(uid) or 0
    return {
        "result": result, "par": puz["par"], "rank": rank, "field": len(ranked),
        "coins": coins, "streak": streak, "balance": balance,
        "share": game.share_grid(result, {"difficulty": puz["difficulty"], "par": puz["par"],
                                           "number": daily.puzzle_number(day)}),
    }


# Board generation (rushhour build_solvable, etc.) is CPU-heavy BFS search — a single "hard"
# board can take 20+s. This endpoint is unauthenticated free-play, so a client hammering it once
# hung the ENTIRE async app (gambling included) because generation ran on the event loop.
# Fix, in layers:
#   1. Run generation OFF the loop (asyncio.to_thread) so it never blocks other requests.
#   2. Cache a recent board per (game, difficulty) for a short TTL — repeated/flooded previews
#      serve instantly from cache instead of regenerating, so a flood can't saturate the CPU.
#   3. A semaphore bounds concurrent generation to 1 (leaves a core free on the small VPS).
# ponytail: build_solvable's own slowness for "hard" is a separate perf bug (too-narrow accept
# range → thousands of BFS retries); the cache makes it operationally harmless.
_PREVIEW_SEM = asyncio.Semaphore(1)
_PREVIEW_TTL = 30.0  # seconds a generated free-play board is reused before regenerating
_preview_cache: dict[tuple[str, str], tuple[dict, int, float]] = {}


@router.get("/preview/{game_id}")
async def preview(game_id: str, difficulty: str = "easy"):
    """A fresh, randomly-seeded solvable board for FREE PLAY — no auth, no ranking, no submit.
    Powers the standalone practice pages (e.g. /rushhour). Board is cached per difficulty for a
    short TTL and generated off the event loop so this endpoint can't stall the app."""
    game = daily.DAILY_GAMES.get(game_id)
    if game is None:
        return JSONResponse({"error": "unknown game"}, status_code=404)
    diff = difficulty if difficulty in game.DIFFICULTIES else game.DIFFICULTIES[0]
    key = (game_id, diff)

    def _fresh(entry):
        return entry and (time.monotonic() - entry[2]) < _PREVIEW_TTL

    entry = _preview_cache.get(key)
    if not _fresh(entry):
        async with _PREVIEW_SEM:
            entry = _preview_cache.get(key)  # another request may have refreshed while we waited
            if not _fresh(entry):
                seed = secrets.randbelow(2 ** 32)
                gen = game.build_solvable if hasattr(game, "build_solvable") else game.generate
                board = await asyncio.to_thread(gen, seed, diff)
                par_v, _ = await asyncio.to_thread(game.par, board)
                entry = (board, par_v, time.monotonic())
                _preview_cache[key] = entry
    board, par_v = entry[0], entry[1]
    # Stash the (cached) board so a later solve can be validated server-side for a capped coin
    # reward — a fresh single-use token per request even when the board is reused from cache.
    token = gameround.stash({"game": game_id, "board": board})
    return {"game": _meta(game_id), "board": board, "par": par_v, "difficulty": diff,
            "board_token": token}


class PracticeBody(BaseModel):
    board_token: str
    solution: dict


@router.post("/practice-solve")
async def practice_solve(request: Request, body: PracticeBody):
    """Validate a free-play solve and grant a small, per-day-capped coin reward. Server-authoritative
    (replays the solution against the stashed board) so coins can't be faked; capped so free play
    isn't a coin farm. No auth → still validates, just no coins."""
    data = gameround.peek(body.board_token)
    if not isinstance(data, dict):
        return JSONResponse({"error": "board expired — start a new one"}, status_code=400)
    game = daily.DAILY_GAMES.get(data["game"])
    result = game.validate(data["board"], body.solution) if game else None
    if result is None:
        return {"solved": False}
    out = {"solved": True, "moves": result["primary"], "coins": 0}
    uid = _uid(request)
    if uid:
        out["coins"] = await queries.grant_activity_reward(uid, "practice_solve", daily.puzzle_day())
        out["balance"] = await queries.get_casino_balance(uid) or 0
    gameround.claim(body.board_token)   # single-use: one reward per board
    return out


@router.get("/leaderboard")
async def leaderboard(request: Request):
    day = daily.puzzle_day()
    puz = await queries.get_or_create_daily_puzzle(day)
    game_id = puz["game_id"]

    ranked = daily.rank_results(await queries.get_daily_results(game_id, day), game_id)
    # season = calendar month of `day`
    d = date.fromisoformat(day)
    start = d.replace(day=1).isoformat()
    end = day
    month_rows = await queries.get_daily_results_range(start, end)
    season = _season_standings(month_rows)

    names = await queries.get_display_names(
        [r["discord_user"] for r in ranked] + [s["discord_user"] for s in season])

    def nm(uid):
        return names.get(uid) or f"user-{uid[-4:]}"

    return {
        "day": day, "number": daily.puzzle_number(day), "game": _meta(game_id),
        "difficulty": puz["difficulty"], "par": puz["par"],
        "today": [{"rank": r["rank"], "name": nm(r["discord_user"]), "solved": bool(r["solved"]),
                   "primary": r["primary_score"], "secondary": r["secondary_score"],
                   "points": r["points"]} for r in ranked[:50]],
        "season": [{"rank": i + 1, "name": nm(s["discord_user"]), "points": s["points"],
                    "days": s["days"]} for i, s in enumerate(season[:50])],
    }


def _season_standings(month_rows: list[dict]) -> list[dict]:
    """Rank each day's field, award placement points, sum per user across the month."""
    by_day: dict[tuple[str, str], list[dict]] = {}
    for r in month_rows:
        by_day.setdefault((r["game_id"], r["puzzle_date"]), []).append(r)
    totals: dict[str, dict] = {}
    for (game_id, _day), rows in by_day.items():
        for r in daily.rank_results(rows, game_id):
            t = totals.setdefault(r["discord_user"], {"discord_user": r["discord_user"],
                                                       "points": 0, "days": 0})
            t["points"] += r["points"]
            t["days"] += 1
    return sorted(totals.values(), key=lambda t: -t["points"])
