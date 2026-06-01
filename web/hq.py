"""SharpLab HQ — Discord-authed dashboard endpoints (auth + live data)."""

from __future__ import annotations

import asyncio
import bisect
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import aiosqlite
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from db import queries
from shared.achievements import ALL_ACHIEVEMENTS
from shared.models import Bet
from shared.pickem_scoring import compute_pickem_standings
from web import auth


async def _progression_payload(uid: str) -> dict:
    """Level/XP + unlocked achievements for a user (HQ profile cards)."""
    xp = await queries.get_or_create_xp(uid)
    total_xp, level = xp["total_xp"], xp["level"]
    floor = queries.xp_for_level(level)
    nxt = queries.xp_for_level(level + 1)
    unlocked_ids = {r["achievement_id"] for r in await queries.get_user_achievements(uid)}
    unlocked = [
        {"id": a.id, "name": a.name, "emoji": a.emoji, "description": a.description,
         "category": a.category, "xp": a.xp_reward}
        for a in ALL_ACHIEVEMENTS if a.id in unlocked_ids
    ]
    return {
        "level": level,
        "total_xp": total_xp,
        "xp_into_level": total_xp - floor,
        "xp_for_next": max(1, nxt - floor),
        "achievements": {"unlocked": unlocked, "unlocked_count": len(unlocked),
                         "total": len(ALL_ACHIEVEMENTS)},
    }

router = APIRouter(prefix="/api/v1")

DB_PATH = os.environ.get("SHARPLAB_DB_PATH", "data/sharplab.db")
WEB_BASE_URL = os.environ.get("WEB_BASE_URL", "https://sharplab.djiang.xyz")
CHESS_API = os.environ.get("CHESS_API_BASE", "https://games.djiang.xyz/chess/api")

_SERVER_CACHE: dict[str, tuple[float, dict]] = {}
_SERVER_TTL = 30.0
_SPY_CACHE: dict[str, object] = {"t": 0.0, "data": []}  # [(date_iso, close)] sorted


async def _spy_history() -> list[tuple[str, float]]:
    """Daily SPY closes (2y), cached for an hour. [] on failure."""
    if _SPY_CACHE["data"] and time.monotonic() - float(_SPY_CACHE["t"]) < 3600:
        return _SPY_CACHE["data"]  # type: ignore[return-value]

    def _fetch() -> list[tuple[str, float]]:
        import yfinance as yf
        h = yf.Ticker("SPY").history(period="2y")
        return [(idx.date().isoformat(), float(row["Close"])) for idx, row in h.iterrows()]

    try:
        data = await asyncio.get_running_loop().run_in_executor(None, _fetch)
    except Exception:
        return _SPY_CACHE["data"]  # type: ignore[return-value]
    if data:
        _SPY_CACHE.update(t=time.monotonic(), data=data)
    return data


def _benchmark(equity: list[dict], spy: list[tuple[str, float]]) -> list[dict]:
    """SPY normalized to the portfolio's starting value, sampled at equity dates."""
    if len(equity) < 2 or not spy:
        return []
    dates = [d for d, _ in spy]

    def spy_at(date_iso: str) -> float | None:
        i = bisect.bisect_right(dates, date_iso) - 1
        return spy[i][1] if i >= 0 else None

    base_v = equity[0]["v"]
    spy0 = spy_at(equity[0]["t"][:10])
    if not spy0 or not base_v:
        return []
    out = []
    for p in equity:
        sp = spy_at(p["t"][:10])
        if sp:
            out.append({"t": p["t"], "v": base_v * (sp / spy0)})
    return out


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


async def _log_member(request: Request, uid: str, username: str | None, etype: str) -> None:
    """Record a signed-in member event (login / member_visit) for /hq/analytics."""
    import hashlib
    import json as _json
    import time as _time
    try:
        ua = (request.headers.get("user-agent") or "")[:240]
        fwd = request.headers.get("x-forwarded-for", "")
        ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")
        ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16] if ip else ""
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO web_events (ts, sid, type, page, ref, ua, ip_hash, data) VALUES (?,?,?,?,?,?,?,?)",
                (int(_time.time() * 1000), uid, etype, "/hq", "", ua, ip_hash,
                 _json.dumps({"id": uid, "username": username or uid})),
            )
            await db.commit()
    except Exception:
        pass  # analytics never breaks auth


@router.get("/auth/discord/callback")
async def discord_callback(request: Request, code: str = "", state: str = ""):
    if not code or not auth.check_state(state):
        return RedirectResponse(f"{WEB_BASE_URL}/hq?error=bad_request")
    try:
        info = await auth.exchange_code(code)
    except Exception:
        return RedirectResponse(f"{WEB_BASE_URL}/hq?error=oauth_failed")
    if not auth.is_member(info["guilds"]):
        return RedirectResponse(f"{WEB_BASE_URL}/hq?error=not_member")
    await _log_member(request, str(info["user"].get("id")), info["user"].get("username"), "login")
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
    await _log_member(request, uid, sess.get("username"), "member_visit")

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
        "is_owner": bool(os.environ.get("OWNER_DISCORD_ID")) and uid == os.environ.get("OWNER_DISCORD_ID"),
        "user": {"id": uid, "username": sess.get("username"), "avatar": sess.get("avatar")},
        "balance": balance,
        "progression": await _progression_payload(uid),
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


async def _elo_games() -> list[dict]:
    """One ELO leaderboard per game that has rankings, ordered by popularity."""
    pop = await queries.get_elo_game_popularity()  # most-played first
    boards = await queries.get_all_elo_leaderboards(min_games=1)
    all_uids = {e["discord_user"] for lb in boards.values() for e in lb}
    enames = await _names(list(all_uids))
    out = []
    for p in pop:
        g = p["game"]
        lb = boards.get(g, [])[:10]
        if not lb:
            continue
        out.append({
            "key": g, "plays": p["total_plays"],
            "rows": [
                {"username": (enames.get(e["discord_user"]) or {}).get("username") or f"Player {e['discord_user'][:6]}",
                 "rating": round(e["rating"]), "wins": e["wins"], "losses": e["losses"],
                 "games_played": e["games_played"]}
                for e in lb
            ],
        })
    return out


async def _stock_leaders() -> list[dict]:
    """Top traders by current portfolio value (latest hourly snapshot)."""
    try:
        users = await queries.get_all_portfolio_users()
    except Exception:
        return []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT p.discord_user, p.account_value
               FROM portfolio_snapshots p
               JOIN (SELECT discord_user, MAX(captured_at) mc FROM portfolio_snapshots
                     GROUP BY discord_user) m
                 ON p.discord_user = m.discord_user AND p.captured_at = m.mc""",
        )
        vals = {r["discord_user"]: r["account_value"] for r in await cur.fetchall()}
    names = await _names(users)
    out = [
        {"user_id": uid, "username": (names.get(uid) or {}).get("username") or f"Player {uid[:6]}",
         "account_value": vals.get(uid)}
        for uid in users if vals.get(uid) is not None
    ]
    out.sort(key=lambda x: x["account_value"], reverse=True)
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
        "stock_traders": (await _fetch_one("SELECT COUNT(DISTINCT discord_user) AS v FROM stock_trades") or {}).get("v", 0),
        "pickem_players": len(standings),
    }

    # Levels + achievements leaderboards
    xp_lb = await queries.get_xp_leaderboard(limit=10)
    levels_top = [
        {"username": r["username"] or f"Player {r['discord_user'][:6]}",
         "level": r["level"], "xp": r["total_xp"]}
        for r in xp_lb
    ]
    ach_lb = await queries.get_achievement_leaderboard(limit=10)
    ach_total = len(ALL_ACHIEVEMENTS)
    ach_top = [
        {"username": r["username"] or f"Player {r['discord_user'][:6]}",
         "unlocked": r["unlocked"], "total": ach_total}
        for r in ach_lb
    ]

    data = {
        "totals": totals,
        "casino": casino_top,
        "pickem": pickem_top,
        "levels": levels_top,
        "achievements": ach_top,
        "elo_games": await _elo_games(),
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
        "progression": await _progression_payload(uid),
        "chess": await _chess_for_handle(who.get("username") or ""),
    }


@router.get("/hq/stocks")
async def hq_stocks():
    """Every trader's portfolio — value (latest hourly snapshot), realized P&L,
    and open holdings. Public, cached briefly."""
    cached = _SERVER_CACHE.get("stocks")
    if cached and (time.monotonic() - cached[0]) < _SERVER_TTL:
        return cached[1]

    users = await queries.get_all_portfolio_users()
    # "Yesterday's close" baseline = last snapshot before ET midnight today.
    et_midnight = datetime.now(ZoneInfo("America/New_York")).replace(
        hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT p.discord_user, p.account_value, p.stock_value, p.options_value, p.cash
               FROM portfolio_snapshots p
               JOIN (SELECT discord_user, MAX(captured_at) mc FROM portfolio_snapshots
                     GROUP BY discord_user) m
                 ON p.discord_user = m.discord_user AND p.captured_at = m.mc""",
        )
        snaps = {r["discord_user"]: dict(r) for r in await cur.fetchall()}
        cur2 = await db.execute(
            """SELECT p.discord_user, p.account_value
               FROM portfolio_snapshots p
               JOIN (SELECT discord_user, MAX(captured_at) mc FROM portfolio_snapshots
                     WHERE captured_at < ? GROUP BY discord_user) m
                 ON p.discord_user = m.discord_user AND p.captured_at = m.mc""",
            (et_midnight,),
        )
        prior = {r["discord_user"]: r["account_value"] for r in await cur2.fetchall()}
    names = await _names(users)

    # Prefetch positions/options for everyone, then one batched live-quote call so we can
    # show each trader's open (unrealized) P&L and per-holding market value.
    user_positions = {uid: await queries.get_stock_positions_full(uid) for uid in users}
    user_options = {uid: await queries.get_option_positions_full(uid) for uid in users}
    all_tickers = sorted({p["ticker"] for ps in user_positions.values()
                          for p in ps if p.get("shares", 0) > 0})
    from bot.cogs.stock import fetch_quotes  # lazy import (heavy deps)
    quotes = await fetch_quotes(all_tickers) if all_tickers else {}
    qprice = {t: (quotes[t]["price"] if quotes.get(t) and quotes[t].get("price") else None)
              for t in all_tickers}

    # Recent equity (last 30d) per trader → downsampled sparkline in each card.
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur3 = await db.execute(
            "SELECT discord_user, account_value FROM portfolio_snapshots "
            "WHERE captured_at >= ? ORDER BY discord_user, captured_at", (since,))
        spark_rows = await cur3.fetchall()
    spark_raw: dict[str, list] = {}
    for du, av in spark_rows:
        spark_raw.setdefault(du, []).append(av)

    def _downsample(vals, n=28):
        if len(vals) <= n:
            return [round(v) for v in vals]
        step = (len(vals) - 1) / (n - 1)
        return [round(vals[round(i * step)]) for i in range(n)]

    traders = []
    for uid in users:
        positions = user_positions[uid]
        opos = user_options[uid]
        realized = round(
            sum(p.get("realized_pnl", 0) for p in positions)
            + sum(o.get("realized_pnl", 0) for o in opos), 2,
        )
        holdings, unreal, have_unreal = [], 0.0, False
        for p in positions:
            if p.get("shares", 0) <= 0:
                continue
            price = qprice.get(p["ticker"])
            value = round(p["shares"] * price, 2) if price is not None else None
            u = round(p["shares"] * (price - p["dca_price"]), 2) if price is not None else None
            if u is not None:
                unreal += u
                have_unreal = True
            holdings.append({
                "ticker": p["ticker"], "shares": round(p.get("shares", 0), 4),
                "dca": round(p.get("dca_price", 0), 2), "cost_basis": round(p.get("cost_basis", 0), 2),
                "price": round(price, 2) if price is not None else None,
                "value": value, "unrealized": u,
            })
        open_opts = sum(1 for o in opos if o.get("net_contracts", 0))
        snap = snaps.get(uid, {})
        av = snap.get("account_value")
        base = prior.get(uid)
        day_change = round(av - base, 2) if av is not None and base else None
        day_pct = round((day_change / base) * 100, 2) if day_change is not None and base else None
        traders.append({
            "user_id": uid,
            "username": (names.get(uid) or {}).get("username") or f"Player {uid[:6]}",
            "avatar_url": (names.get(uid) or {}).get("avatar_url"),
            "account_value": av,
            "stock_value": snap.get("stock_value"),
            "options_value": snap.get("options_value"),
            "cash": snap.get("cash"),
            "realized_pnl": realized,
            "unrealized_pnl": round(unreal, 2) if have_unreal else None,
            "day_change": day_change,
            "day_pct": day_pct,
            "positions": len(holdings) + open_opts,
            "spark": _downsample(spark_raw.get(uid, [])),
            "holdings": sorted(holdings, key=lambda h: (h["value"] or h["cost_basis"] or 0), reverse=True),
        })
    traders.sort(key=lambda t: (t["account_value"] is not None, t["account_value"] or 0), reverse=True)
    data = {"traders": traders}
    _SERVER_CACHE["stocks"] = (time.monotonic(), data)
    return data


def _period_pnl(trades, series, shares_now, price_now, cuts, close_on_or_before):
    """A position's *actual* P/L over each window, accounting for trades made inside it
    — so a stock bought partway through the period only counts gains since the buy
    (not the stock's full move). Per window:
        P/L = current_value − value_at_window_start − net_cash_invested_during_window
    `trades`: ascending [{executed_at, side, shares, price}] for ONE ticker.
    `series`: ascending [(date_iso, close)]. `cuts`: {label: date_iso | None(=inception)}."""
    out = {}
    cur_val = shares_now * price_now
    for label, cd in cuts.items():
        if cd is None:                                  # ALL → from inception (held 0 before)
            shares_at_t, start_val, window = 0.0, 0.0, trades
        else:
            shares_at_t = sum((t["shares"] if t["side"] == "buy" else -t["shares"])
                              for t in trades if t["executed_at"][:10] <= cd)
            if abs(shares_at_t) > 1e-9:
                price_at_t = close_on_or_before(series, cd)
                if not price_at_t:                      # no history that far back
                    out[label] = {"pnl": None, "pct": None}
                    continue
                start_val = shares_at_t * price_at_t
            else:
                start_val = 0.0
            window = [t for t in trades if t["executed_at"][:10] > cd]
        buys = sum(t["shares"] * t["price"] for t in window if t["side"] == "buy")
        sells = sum(t["shares"] * t["price"] for t in window if t["side"] == "sell")
        pnl = cur_val - start_val - (buys - sells)
        denom = start_val + buys                        # capital exposed during the window
        out[label] = {"pnl": round(pnl, 2),
                      "pct": round(pnl / denom * 100, 2) if denom > 1e-9 else None}
    return out


@router.get("/hq/stocks/{handle}")
async def hq_stock_trader(handle: str):
    """One trader's full portfolio: equity curve, stock + option positions, txns."""
    who = await _resolve_handle(handle)
    if not who:
        return JSONResponse({"error": "not_found"}, status_code=404)
    uid = who["discord_user"]

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT captured_at, account_value, stock_value, options_value, cash, kind "
            "FROM portfolio_snapshots WHERE discord_user = ? ORDER BY captured_at", (uid,),
        )
        snaps = [dict(r) for r in await cur.fetchall()]
    latest = snaps[-1] if snaps else {}
    equity = [{"t": s["captured_at"], "v": s["account_value"], "k": s["kind"]} for s in snaps]
    live_since = next((s["captured_at"] for s in snaps if s["kind"] == "live"), None)

    sp = await queries.get_stock_positions_full(uid)
    stock_realized = sum(p.get("realized_pnl", 0) for p in sp)
    open_pos = [p for p in sp if p.get("shares", 0) > 0]
    # lazy import (heavy deps); _ticker_history is cache-shared with fetch_period_changes
    from datetime import timedelta
    from bot.cogs.stock import fetch_quotes, _ticker_history, _close_on_or_before
    quotes = await fetch_quotes([p["ticker"] for p in open_pos]) if open_pos else {}
    # 1y daily-close series per holding (cached), so the browser can chart any ticker
    # instantly without a second round-trip.
    hist_list = await asyncio.gather(*[_ticker_history(p["ticker"]) for p in open_pos]) if open_pos else []
    hist_map = dict(zip((p["ticker"] for p in open_pos), hist_list))

    # Trade log grouped by ticker — drives holding-aware period P/L (so a stock bought
    # mid-period only counts gains since the buy, not the stock's full move).
    all_trades = await queries.get_stock_trades(uid)
    trades_by_tkr: dict[str, list] = {}
    for t in all_trades:
        trades_by_tkr.setdefault(t["ticker"], []).append(dict(t))
    for lst in trades_by_tkr.values():
        lst.sort(key=lambda t: t["executed_at"])
    _today = datetime.now(timezone.utc).date()
    _cut = lambda days: (_today - timedelta(days=days)).isoformat()
    period_cuts = {"1D": _cut(1), "1W": _cut(7), "1M": _cut(30), "3M": _cut(90),
                   "YTD": f"{_today.year}-01-01", "1Y": _cut(365), "ALL": None}
    today_iso = _today.isoformat()

    stock_holdings = []
    for p in open_pos:
        q = quotes.get(p["ticker"])
        price = q["price"] if q else None
        unreal = round(p["shares"] * (price - p["dca_price"]), 2) if price is not None else None
        series = hist_map.get(p["ticker"]) or []
        # Daily closes + a live "today" point so the line ends at the current price.
        hist = [[d, round(c, 2)] for d, c in series]
        if price is not None and (not hist or hist[-1][0] < today_iso):
            hist.append([today_iso, round(price, 2)])
        period = (_period_pnl(trades_by_tkr.get(p["ticker"], []), series,
                              p.get("shares", 0), price, period_cuts, _close_on_or_before)
                  if price is not None else {})
        stock_holdings.append({
            "ticker": p["ticker"], "shares": round(p.get("shares", 0), 4),
            "dca": round(p.get("dca_price", 0), 2), "cost_basis": round(p.get("cost_basis", 0), 2),
            "price": round(price, 2) if price is not None else None,
            "unrealized": unreal,
            "realized": round(p.get("realized_pnl", 0), 2),
            "period": period,        # {label: {pnl, pct}} — her ACTUAL P/L over each window
            "history": hist,         # [[date_iso, close], ...] ascending
        })
    stock_holdings.sort(key=lambda h: h["cost_basis"], reverse=True)

    op = await queries.get_option_positions_full(uid)
    opt_realized = sum(o.get("realized_pnl", 0) for o in op)
    option_positions = [
        {"underlying": o["underlying"], "opt_type": o["opt_type"], "strike": o["strike"],
         "expiry": o["expiry"], "contracts": o.get("net_contracts", 0),
         "avg_premium": round(o.get("avg_premium", 0), 2)}
        for o in op if o.get("net_contracts", 0)
    ]

    txns = []
    for t in all_trades:
        txns.append({"at": t["executed_at"], "kind": "stock",
                     "desc": f"{t['side'].upper()} {t['shares']:g} {t['ticker']} @ ${t['price']:,.2f}"})
    for t in await queries.get_option_trades(uid):
        otype = t["opt_type"][0].upper()
        txns.append({"at": t["executed_at"], "kind": "option",
                     "desc": f"{t['side'].upper()} {t['contracts']} {t['underlying']} "
                             f"${t['strike']:g}{otype} {t['expiry']} @ ${t['premium']:,.2f}"})
    txns.sort(key=lambda x: x["at"], reverse=True)

    return {
        "user": {"id": uid, "username": who.get("username") or f"Player {uid[:6]}",
                 "avatar_url": who.get("avatar_url")},
        "summary": {
            "account_value": latest.get("account_value"),
            "stock_value": latest.get("stock_value"),
            "options_value": latest.get("options_value"),
            "cash": latest.get("cash"),
            "realized_pnl": round(stock_realized + opt_realized, 2),
            # Open P&L on stocks = Σ per-position (live price − cost basis).
            "unrealized_pnl": (
                round(sum(h["unrealized"] for h in stock_holdings if h["unrealized"] is not None), 2)
                if any(h["unrealized"] is not None for h in stock_holdings) else None
            ),
        },
        "equity": equity,
        "live_since": live_since,
        "benchmark": _benchmark(equity, await _spy_history()),
        "stock_holdings": stock_holdings,
        "option_positions": option_positions,
        "transactions": txns[:150],
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

    # All bets on each game (for the expandable list) — one name lookup.
    picks_by_game = {g["message_id"]: await queries.get_pickem_picks_for_message(g["message_id"]) for g in games}
    names = await _names(list({p["discord_user"] for ps in picks_by_game.values() for p in ps}))

    out = []
    for g in games:
        my = mine.get(g["message_id"])
        bets = sorted(
            ({"username": (names.get(p["discord_user"]) or {}).get("username") or f"Player {p['discord_user'][:6]}",
              "pick": p["pick"], "stake": p["stake"]}
             for p in picks_by_game[g["message_id"]]),
            key=lambda b: b["stake"], reverse=True,
        )
        out.append({
            "message_id": g["message_id"], "sport": g["sport"],
            "home_team": g["home_team"], "away_team": g["away_team"],
            "start_time": g["start_time"], "home_prob": g["home_prob"],
            "away_prob": g["away_prob"], "odds_source": g["odds_source"],
            "my_pick": my["pick"] if my else None, "my_stake": my["stake"] if my else None,
            "bets": bets,
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


class StockTradeIn(BaseModel):
    ticker: str
    side: str       # buy | sell
    shares: float
    price: float


@router.post("/hq/stocks/trade")
async def hq_stock_trade(body: StockTradeIn, request: Request):
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"error": "not_authenticated"}, status_code=401)
    ticker = body.ticker.strip().upper()
    if body.side not in ("buy", "sell") or not ticker or body.shares <= 0 or body.price <= 0:
        return JSONResponse({"error": "bad_input"}, status_code=400)
    if body.side == "sell":
        holding = await queries.get_stock_holding(sess["id"], ticker)
        held = holding["shares"] if holding else 0.0
        if body.shares > held + 1e-9:
            return JSONResponse({"error": f"You only hold {held:g} sh of {ticker}."}, status_code=409)
    try:
        await queries.add_stock_trade(sess["id"], ticker, body.side, body.shares, body.price,
                                      datetime.now(timezone.utc).isoformat(), "via HQ")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True, "side": body.side, "ticker": ticker, "shares": body.shares, "price": body.price}


class OptionTradeIn(BaseModel):
    underlying: str
    opt_type: str   # call | put
    strike: float
    expiry: str     # YYYY-MM-DD
    side: str        # buy | sell
    contracts: int
    premium: float


@router.post("/hq/options/trade")
async def hq_option_trade(body: OptionTradeIn, request: Request):
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"error": "not_authenticated"}, status_code=401)
    if body.opt_type not in ("call", "put") or body.side not in ("buy", "sell"):
        return JSONResponse({"error": "bad_input"}, status_code=400)
    try:
        await queries.add_option_trade(sess["id"], body.underlying.strip().upper(), body.opt_type,
                                       body.strike, body.expiry.strip(), body.side, body.contracts,
                                       body.premium, datetime.now(timezone.utc).isoformat(), "via HQ")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True}


class BetLogIn(BaseModel):
    game_id: str
    market: str               # spread | moneyline | total
    side: str                 # team name, "over", or "under"
    odds: int
    units: float = 1.0
    line: float | None = None
    book: str = "other"


@router.post("/hq/bet/log")
async def hq_bet_log(body: BetLogIn, request: Request):
    """Log a bet to your record straight from the Lines page (mirrors /bet log)."""
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"error": "not_authenticated"}, status_code=401)
    if (body.market not in ("spread", "moneyline", "total") or not body.side
            or body.odds == 0 or not (0 < body.units <= 1000)):
        return JSONResponse({"error": "bad_input"}, status_code=400)
    game = await queries.get_game_by_id(body.game_id)
    if game is None:
        return JSONResponse({"error": "game_not_found"}, status_code=404)
    bet = Bet(
        game_id=body.game_id, placed_at=datetime.now(timezone.utc).isoformat(),
        discord_user=sess["id"], book=(body.book or "other"), market=body.market,
        side=body.side, odds=body.odds, units=body.units, line=body.line, notes="via HQ",
    )
    bet_id = await queries.insert_bet(bet)
    return {"ok": True, "bet_id": bet_id}


@router.get("/hq/bet/mine")
async def hq_bet_mine(request: Request):
    """The signed-in user's open + graded bets, for the Lines page 'My Bets' panel."""
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"authenticated": False}, status_code=401)
    bets = await queries.get_open_bets_for_user(sess["id"])
    out = []
    for b in bets[:25]:
        g = await queries.get_game_by_id(b.game_id)
        label = f"{g.away_team.split()[-1]} @ {g.home_team.split()[-1]}" if g else b.game_id[:8]
        out.append({
            "bet_id": b.bet_id, "game": label, "market": b.market, "side": b.side,
            "line": b.line, "odds": b.odds, "units": b.units, "status": b.status, "clv": b.clv,
        })
    return {"bets": out}


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
