"""SharpLab HQ — Discord-authed dashboard endpoints (auth + live data)."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import aiosqlite
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from db import queries
from shared.elo import championship_points
from shared.pickem_scoring import compute_pickem_standings
from web import auth

router = APIRouter(prefix="/api/v1")

DB_PATH = os.environ.get("SHARPLAB_DB_PATH", "data/sharplab.db")
WEB_BASE_URL = os.environ.get("WEB_BASE_URL", "https://sharplab.djiang.xyz")
CHESS_API = os.environ.get("CHESS_API_BASE", "https://games.djiang.xyz/chess/api")

_SERVER_CACHE: dict[str, tuple[float, dict]] = {}
_SERVER_TTL = 30.0


async def _names(user_ids: list[str]) -> dict[str, dict]:
    """Look up cached username/avatar for a set of discord ids."""
    if not user_ids:
        return {}
    placeholders = ",".join("?" for _ in user_ids)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT discord_user, username, avatar_url FROM discord_users "
            f"WHERE discord_user IN ({placeholders})",
            tuple(user_ids),
        )
        rows = await cur.fetchall()
    return {r["discord_user"]: dict(r) for r in rows}


# ── OAuth ─────────────────────────────────────────────────────────────────────


@router.get("/auth/discord/login")
async def discord_login():
    if not auth.oauth_configured():
        return JSONResponse({"error": "oauth_not_configured"}, status_code=503)
    return RedirectResponse(auth.login_url(auth.make_state()))


@router.get("/auth/discord/callback")
async def discord_callback(code: str = "", state: str = ""):
    if not code or not auth.check_state(state):
        return RedirectResponse(f"{WEB_BASE_URL}/hq?error=bad_request")
    try:
        info = await auth.exchange_code(code)
    except Exception:
        return RedirectResponse(f"{WEB_BASE_URL}/hq?error=oauth_failed")
    if not auth.is_member(info["guilds"]):
        return RedirectResponse(f"{WEB_BASE_URL}/hq?error=not_member")
    resp = RedirectResponse(f"{WEB_BASE_URL}/hq")
    resp.set_cookie(
        auth.COOKIE_NAME, auth.make_session_cookie(info["user"]),
        max_age=auth.SESSION_TTL, httponly=True, secure=True, samesite="lax",
    )
    return resp


@router.get("/auth/logout")
async def logout():
    resp = RedirectResponse(f"{WEB_BASE_URL}/hq")
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


# ── HQ data ───────────────────────────────────────────────────────────────────


@router.get("/hq/me")
async def hq_me(request: Request):
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"authenticated": False}, status_code=401)
    uid = sess["id"]

    elo = await queries.get_elo_ratings_for_user(uid)
    rows = await queries.get_pickem_resolved_picks()
    standings = compute_pickem_standings(rows)
    me = standings.get(uid, {"correct": 0, "total": 0, "accuracy": 0.0, "points": 0, "units": 0.0})

    try:
        balance = await queries.get_casino_balance(uid)
    except Exception:
        balance = None

    return {
        "authenticated": True,
        "user": {"id": uid, "username": sess.get("username"), "avatar": sess.get("avatar")},
        "balance": balance,
        "pickem": me,
        "elo": [
            {"game": r["game"], "rating": r["rating"], "games_played": r["games_played"],
             "wins": r["wins"], "losses": r["losses"]}
            for r in elo
        ],
    }


async def _fetch_one(sql: str, params: tuple = ()) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        row = await cur.fetchone()
    return dict(row) if row else None


async def _chess_leaderboard() -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{CHESS_API}/leaderboard", params={"min_games": 1, "limit": 10})
            r.raise_for_status()
            players = r.json().get("players", [])
    except Exception:
        return []
    return [
        {"handle": p.get("handle", "?"), "rating": p.get("rating", 1000),
         "wins": p.get("wins", 0), "losses": p.get("losses", 0), "draws": p.get("draws", 0),
         "games_played": p.get("games_played", 0)}
        for p in players
    ]


async def _elo_champions() -> list[dict]:
    """F1-style championship points across every ELO game."""
    boards = await queries.get_all_elo_leaderboards(min_games=5)
    points: dict[str, int] = {}
    for _game, lb in boards.items():
        for pos, entry in enumerate(lb, 1):
            pts = championship_points(pos)
            if pts == 0:
                break
            points[entry["discord_user"]] = points.get(entry["discord_user"], 0) + pts
    names = await _names(list(points))
    out = [
        {"user_id": uid, "username": (names.get(uid) or {}).get("username") or f"Player {uid[:6]}",
         "points": pts}
        for uid, pts in points.items()
    ]
    out.sort(key=lambda x: x["points"], reverse=True)
    return out[:10]


async def _stock_leaders() -> list[dict]:
    """Top traders by realized P&L (cheap — no live price fetch)."""
    try:
        users = await queries.get_all_portfolio_users()
    except Exception:
        return []
    rows = []
    for uid in users:
        try:
            positions = await queries.get_stock_positions_full(uid)
        except Exception:
            continue
        realized = sum(p.get("realized_pnl", 0) for p in positions)
        rows.append((uid, realized))
    names = await _names([uid for uid, _ in rows])
    out = [
        {"user_id": uid, "username": (names.get(uid) or {}).get("username") or f"Player {uid[:6]}",
         "realized_pnl": round(pnl, 2)}
        for uid, pnl in rows
    ]
    out.sort(key=lambda x: x["realized_pnl"], reverse=True)
    return out[:10]


@router.get("/hq/server")
async def hq_server():
    """Server-wide stats home: leaderboards across every system, cached briefly."""
    cached = _SERVER_CACHE.get("server")
    if cached and (time.monotonic() - cached[0]) < _SERVER_TTL:
        return cached[1]

    # Casino top
    casino = await queries.get_casino_leaderboard(limit=10)
    cnames = await _names([c["discord_user"] for c in casino])
    casino_top = [
        {"username": (cnames.get(c["discord_user"]) or {}).get("username") or f"Player {c['discord_user'][:6]}",
         "balance": c.get("balance", 0)}
        for c in casino
    ]

    # Pick'em top (Market P&L)
    standings = compute_pickem_standings(await queries.get_pickem_resolved_picks())
    pnames = await _names(list(standings))
    pickem_top = sorted(
        ({"username": (pnames.get(uid) or {}).get("username") or f"Player {uid[:6]}",
          "units": round(s["units"], 1), "correct": s["correct"], "total": s["total"]}
         for uid, s in standings.items()),
        key=lambda x: x["units"], reverse=True,
    )[:10]

    totals = {
        "coins": (await _fetch_one("SELECT COALESCE(SUM(balance),0) AS v FROM casino_wallets") or {}).get("v", 0),
        "stock_traders": (await _fetch_one("SELECT COUNT(DISTINCT discord_user) AS v FROM stock_holdings WHERE shares > 0") or {}).get("v", 0),
        "pickem_players": len(standings),
    }

    data = {
        "totals": totals,
        "casino": casino_top,
        "pickem": pickem_top,
        "elo_champions": await _elo_champions(),
        "stocks": await _stock_leaders(),
        "chess": await _chess_leaderboard(),
    }
    _SERVER_CACHE["server"] = (time.monotonic(), data)
    return data


async def _resolve_handle(handle: str) -> dict | None:
    """Map a /hq/{handle} segment to a discord user. Numeric → id; else username."""
    if handle.isdigit():
        row = await _fetch_one(
            "SELECT discord_user, username, avatar_url FROM discord_users WHERE discord_user = ?",
            (handle,),
        )
        if row:
            return row
        return {"discord_user": handle, "username": None, "avatar_url": None}
    return await _fetch_one(
        "SELECT discord_user, username, avatar_url FROM discord_users "
        "WHERE lower(username) = lower(?) LIMIT 1",
        (handle,),
    )


async def _chess_for_handle(handle: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{CHESS_API}/leaderboard", params={"min_games": 0, "limit": 500})
            r.raise_for_status()
            for p in r.json().get("players", []):
                if (p.get("handle") or "").lower() == handle.lower():
                    return {"handle": p["handle"], "rating": p.get("rating"),
                            "wins": p.get("wins", 0), "losses": p.get("losses", 0),
                            "draws": p.get("draws", 0), "games_played": p.get("games_played", 0)}
    except Exception:
        return None
    return None


@router.get("/hq/profile/{handle}")
async def hq_profile(handle: str):
    who = await _resolve_handle(handle)
    if not who:
        return JSONResponse({"error": "not_found"}, status_code=404)
    uid = who["discord_user"]

    standings = compute_pickem_standings(await queries.get_pickem_resolved_picks())
    pk = standings.get(uid, {"correct": 0, "total": 0, "accuracy": 0.0, "points": 0, "units": 0.0})

    elo = await queries.get_elo_ratings_for_user(uid)
    try:
        balance = await queries.get_casino_balance(uid)
    except Exception:
        balance = None
    try:
        positions = await queries.get_stock_positions_full(uid)
    except Exception:
        positions = []
    realized = round(sum(p.get("realized_pnl", 0) for p in positions), 2)
    open_positions = sum(1 for p in positions if p.get("shares", 0) > 0)

    return {
        "user": {"id": uid, "username": who.get("username") or f"Player {uid[:6]}",
                 "avatar_url": who.get("avatar_url")},
        "pickem": {"units": round(pk["units"], 1), "correct": pk["correct"],
                   "total": pk["total"], "accuracy": round(pk["accuracy"] * 100),
                   "points": pk["points"]},
        "elo": [{"game": r["game"], "rating": round(r["rating"]), "wins": r["wins"],
                 "losses": r["losses"], "games_played": r["games_played"]} for r in elo],
        "casino": {"balance": balance},
        "stocks": {"realized_pnl": realized, "open_positions": open_positions},
        "chess": await _chess_for_handle(who.get("username") or ""),
    }


@router.get("/hq/pickem/open")
async def hq_pickem_open(request: Request):
    """Today's still-open pick'em games, with the viewer's current bet if any."""
    sess = auth.read_session(request)
    uid = sess["id"] if sess else None
    now = datetime.now(timezone.utc).isoformat()
    games = [g for g in await queries.get_unlocked_pickem_games() if g["start_time"] > now]
    games.sort(key=lambda g: g["start_time"])

    mine: dict[str, dict] = {}
    if uid:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT message_id, pick, stake FROM pickem_picks WHERE discord_user = ?", (uid,),
            )
            mine = {r["message_id"]: dict(r) for r in await cur.fetchall()}

    out = []
    for g in games:
        my = mine.get(g["message_id"])
        out.append({
            "message_id": g["message_id"], "sport": g["sport"],
            "home_team": g["home_team"], "away_team": g["away_team"],
            "start_time": g["start_time"], "home_prob": g["home_prob"],
            "away_prob": g["away_prob"], "odds_source": g["odds_source"],
            "my_pick": my["pick"] if my else None, "my_stake": my["stake"] if my else None,
        })
    return {"authenticated": bool(uid), "games": out}


class BetIn(BaseModel):
    message_id: str
    team: str
    stake: int


@router.post("/hq/pickem/bet")
async def hq_pickem_bet(body: BetIn, request: Request):
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"error": "not_authenticated"}, status_code=401)
    if body.team not in ("home", "away") or not (1 <= body.stake <= 5):
        return JSONResponse({"error": "bad_input"}, status_code=400)
    game = await queries.get_pickem_game(body.message_id)
    now = datetime.now(timezone.utc).isoformat()
    if game is None or game["locked"] or game["start_time"] <= now:
        return JSONResponse({"error": "closed"}, status_code=409)
    locked = await queries.record_pickem_pick(body.message_id, sess["id"], body.team, body.stake)
    if not locked:
        return JSONResponse({"error": "already_bet"}, status_code=409)
    return {"ok": True, "team": body.team, "stake": body.stake}


@router.get("/hq/pickem/leaderboard")
async def hq_pickem_leaderboard():
    rows = await queries.get_pickem_resolved_picks()
    standings = compute_pickem_standings(rows)
    names = await _names(list(standings))
    out = []
    for uid, s in standings.items():
        n = names.get(uid, {})
        out.append({
            "user_id": uid,
            "username": n.get("username") or f"Player {uid[:6]}",
            "avatar_url": n.get("avatar_url"),
            "units": round(s["units"], 1),
            "correct": s["correct"],
            "total": s["total"],
            "accuracy": round(s["accuracy"] * 100),
            "points": s["points"],
        })
    out.sort(key=lambda x: x["units"], reverse=True)
    return {"leaderboard": out}
