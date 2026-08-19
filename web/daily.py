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

from datetime import date, datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from db import queries
from shared import daily
from web import auth

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
    """Hand over today's board + a signed start token (which carries the server start time)."""
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play the daily"}, status_code=401)
    day = daily.puzzle_day()
    puz = await queries.get_or_create_daily_puzzle(day)
    token = _start_signer.dumps({"day": day, "game": puz["game_id"], "uid": uid})
    return {"board": puz["payload"], "difficulty": puz["difficulty"], "par": puz["par"],
            "number": daily.puzzle_number(day), "start_token": token}


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

    # Decode the start token → (payload, server start time). This is the authoritative clock.
    try:
        data, started_at = _start_signer.loads(
            body.start_token, max_age=_START_TTL, return_timestamp=True)
    except (BadSignature, SignatureExpired, ValueError):
        return JSONResponse({"error": "your run expired — press Start again"}, status_code=400)
    if data.get("day") != day or data.get("uid") != uid or data.get("game") != puz["game_id"]:
        return JSONResponse({"error": "stale run — press Start again"}, status_code=400)

    if await queries.get_daily_result(puz["game_id"], day, uid):
        return JSONResponse({"error": "you already played today's daily"}, status_code=409)

    result = game.validate(puz["payload"], body.solution)
    if result is None:
        return JSONResponse({"error": "that didn't trap the pig — keep going"}, status_code=400)
    # Server-authoritative elapsed: now − start, bounded.
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    elapsed_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    result["secondary"] = max(0, min(elapsed_ms, _START_TTL * 1000))

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
