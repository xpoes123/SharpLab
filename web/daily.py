"""Daily Games web API — today's puzzle, submit, leaderboards.

Server-authoritative: the board is generated + cached server-side (guaranteed solvable), and a
submission is scored only by replaying it through the plugin's `validate` — the client is never
trusted for the outcome. One submit per user per day (enforced in the DB). A first solve grants
participation coins and bumps the streak; placement coins are paid later by the rollover job.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import queries
from shared import daily
from web import auth

router = APIRouter(prefix="/api/v1/daily")


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _meta(game_id: str) -> dict:
    g = daily.DAILY_GAMES[game_id]
    return {"id": g.ID, "name": g.NAME, "icon": g.ICON}


@router.get("/today")
async def today(request: Request):
    day = daily.puzzle_day()
    puz = await queries.get_or_create_daily_puzzle(day)
    game = daily.DAILY_GAMES[puz["game_id"]]
    out = {
        "day": day, "game": _meta(puz["game_id"]), "difficulty": puz["difficulty"],
        "par": puz["par"], "par_approx": puz["par_approx"], "board": puz["payload"],
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


class SubmitBody(BaseModel):
    solution: dict   # {"moves": [[r,c],...], "elapsed_ms": int}


@router.post("/submit")
async def submit(request: Request, body: SubmitBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play the daily"}, status_code=401)
    day = daily.puzzle_day()
    puz = await queries.get_or_create_daily_puzzle(day)
    game = daily.DAILY_GAMES[puz["game_id"]]

    if await queries.get_daily_result(puz["game_id"], day, uid):
        return JSONResponse({"error": "you already played today's daily"}, status_code=409)

    result = game.validate(puz["payload"], body.solution)
    if result is None:
        return JSONResponse({"error": "that didn't trap the pig — keep going"}, status_code=400)
    # bound the client-reported time defensively
    result["secondary"] = max(0, min(result["secondary"], 3_600_000))

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
        "share": game.share_grid(result, {"difficulty": puz["difficulty"], "par": puz["par"]}),
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
        "day": day, "game": _meta(game_id), "difficulty": puz["difficulty"], "par": puz["par"],
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
