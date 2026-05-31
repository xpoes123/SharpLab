"""SharpLab HQ — Discord-authed dashboard endpoints (auth + live data)."""

from __future__ import annotations

import os

import aiosqlite
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from db import queries
from shared.pickem_scoring import compute_pickem_standings
from web import auth

router = APIRouter(prefix="/api/v1")

DB_PATH = os.environ.get("SHARPLAB_DB_PATH", "data/sharplab.db")
WEB_BASE_URL = os.environ.get("WEB_BASE_URL", "https://sharplab.djiang.xyz")


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
