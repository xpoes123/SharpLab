"""SharpLab Web API — leaderboards + game engine."""

import asyncio
import os
from contextlib import asynccontextmanager
from math import isqrt

import aiosqlite
from fastapi import FastAPI, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from db.schema import init_db
from web.sudoku import router as sudoku_router, sudoku_websocket, cleanup_stale_rooms
from web.figgie import router as figgie_router, figgie_websocket, cleanup_stale_figgie_rooms
from web.bingo import router as bingo_router, bingo_websocket, cleanup_stale_bingo_rooms
from web.trading_floor import router as tf_router, tf_websocket, cleanup_stale_tf_rooms

DB_PATH = os.environ.get("SHARPLAB_DB_PATH", "data/sharplab.db")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    cleanup_task = asyncio.create_task(cleanup_stale_rooms())
    figgie_cleanup_task = asyncio.create_task(cleanup_stale_figgie_rooms())
    bingo_cleanup_task = asyncio.create_task(cleanup_stale_bingo_rooms())
    tf_cleanup_task = asyncio.create_task(cleanup_stale_tf_rooms())
    yield
    cleanup_task.cancel()
    figgie_cleanup_task.cancel()
    bingo_cleanup_task.cancel()
    tf_cleanup_task.cancel()


app = FastAPI(title="SharpLab", docs_url=None, redoc_url=None, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://djiang.xyz", "http://localhost:8000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Mount game routers
app.include_router(sudoku_router)
app.include_router(figgie_router)
app.include_router(bingo_router)
app.include_router(tf_router)


# WebSocket endpoint (not on the API router — different path pattern)
@app.websocket("/ws/sudoku/{room_id}")
async def ws_sudoku(websocket: WebSocket, room_id: str):
    await sudoku_websocket(websocket, room_id)


@app.websocket("/ws/figgie/{room_id}")
async def ws_figgie(websocket: WebSocket, room_id: str):
    await figgie_websocket(websocket, room_id)


@app.websocket("/ws/bingo/{room_id}")
async def ws_bingo(websocket: WebSocket, room_id: str):
    await bingo_websocket(websocket, room_id)


@app.websocket("/ws/tradingfloor/{room_id}")
async def ws_tradingfloor(websocket: WebSocket, room_id: str):
    await tf_websocket(websocket, room_id)

# ── Static data (replicated from bot cogs to avoid importing bot deps) ───────

GAME_LABELS: dict[str, str] = {
    "blackjack": "Blackjack",
    "plinko": "Plinko",
    "craps": "Craps",
    "hilo": "Hi-Lo",
    "roulette": "Roulette",
    "crash": "Crash",
    "videopoker": "Video Poker",
    "uth": "Texas Hold'em",
    "baccarat": "Baccarat",
    "paigow": "Pai Gow",
    "bingo": "Bingo",
    "horserace": "Horse Race",
    "stockmarket": "Stock Market",
    "stockguess": "Stock Guess",
    "math24": "Math 24",
    "countdown": "Countdown",
    "mastermind": "Mastermind",
    "liarsdice": "Liar's Dice",
    "slots": "Slots",
    "nbasim": "NBA Sim",
    "nflsim": "NFL Sim",
    "mlbsim": "MLB Sim",
    "tennissim": "Tennis Sim",
    "soccersim": "Soccer Sim",
    "penalties": "Penalties",
    "tictactoe": "Tic Tac Toe",
    "rps": "Rock Paper Scissors",
    "geography": "Geography",
    "wordle": "Wordle",
    "nba-trivia": "NBA Trivia",
    "nfl-trivia": "NFL Trivia",
    "sudoku": "Sudoku Sprint",
    "duel": "Duels",
    "tournament": "Tournaments",
    "figgie": "Figgie",
    "mathsprint": "Math Sprint",
    "tradingfloor": "Trading Floor",
}

ALL_ACHIEVEMENTS = [
    {"id": "first_game", "name": "First Steps", "description": "Play your first casino game", "category": "Progression", "emoji": "\U0001f3ae"},
    {"id": "play_10", "name": "Regular", "description": "Play 10 casino games", "category": "Progression", "emoji": "\U0001f3af"},
    {"id": "play_100", "name": "Grinder", "description": "Play 100 casino games", "category": "Progression", "emoji": "\u2699\ufe0f"},
    {"id": "play_500", "name": "Veteran", "description": "Play 500 casino games", "category": "Progression", "emoji": "\U0001f396\ufe0f"},
    {"id": "first_win", "name": "Winner", "description": "Win your first casino game", "category": "Winning", "emoji": "\U0001f3c6"},
    {"id": "streak_3", "name": "Hot Streak", "description": "Win 3 games in a row", "category": "Winning", "emoji": "\U0001f525"},
    {"id": "streak_5", "name": "On Fire", "description": "Win 5 games in a row", "category": "Winning", "emoji": "\U0001f525"},
    {"id": "streak_10", "name": "Unstoppable", "description": "Win 10 games in a row", "category": "Winning", "emoji": "\U0001f4a5"},
    {"id": "big_win", "name": "Big Winner", "description": "Profit 1000+ coins in one game", "category": "Winning", "emoji": "\U0001f4b0"},
    {"id": "jackpot", "name": "Jackpot", "description": "Profit 5000+ coins in one game", "category": "Winning", "emoji": "\U0001f48e"},
    {"id": "explore_5", "name": "Explorer", "description": "Play 5 different games", "category": "Diversity", "emoji": "\U0001f5fa\ufe0f"},
    {"id": "explore_15", "name": "World Traveler", "description": "Play 15 different games", "category": "Diversity", "emoji": "\U0001f30d"},
    {"id": "explore_all", "name": "Completionist", "description": "Play every casino game at least once", "category": "Diversity", "emoji": "\u2b50"},
    {"id": "duel_win", "name": "Challenger", "description": "Win a duel", "category": "Social", "emoji": "\u2694\ufe0f"},
    {"id": "duel_10", "name": "Duelist", "description": "Win 10 duels", "category": "Social", "emoji": "\U0001f5e1\ufe0f"},
    {"id": "tourney_win", "name": "Tournament Victor", "description": "Win a tournament", "category": "Social", "emoji": "\U0001f3c5"},
    {"id": "tourney_5", "name": "Champion", "description": "Win 5 tournaments", "category": "Social", "emoji": "\U0001f451"},
    {"id": "daily_1", "name": "Task Master", "description": "Complete a daily challenge", "category": "Daily", "emoji": "\U0001f4cb"},
    {"id": "daily_all", "name": "Dedicated", "description": "Complete all 3 daily challenges in one day", "category": "Daily", "emoji": "\U0001f4cb"},
    {"id": "daily_7", "name": "Devoted", "description": "Complete all daily challenges 7 days in a row", "category": "Daily", "emoji": "\U0001f5d3\ufe0f"},
    {"id": "coins_5k", "name": "Thousandaire", "description": "Reach 5,000 coin balance", "category": "Wealth", "emoji": "\U0001f4b5"},
    {"id": "coins_25k", "name": "Wealthy", "description": "Reach 25,000 coin balance", "category": "Wealth", "emoji": "\U0001f4b0"},
    {"id": "coins_100k", "name": "Mogul", "description": "Reach 100,000 coin balance", "category": "Wealth", "emoji": "\U0001f911"},
    {"id": "wager_50k", "name": "High Roller", "description": "Wager 50,000+ total coins", "category": "Wealth", "emoji": "\U0001f3b2"},
    {"id": "wager_500k", "name": "Whale", "description": "Wager 500,000+ total coins", "category": "Wealth", "emoji": "\U0001f40b"},
]

ACHIEVEMENTS_BY_ID = {a["id"]: a for a in ALL_ACHIEVEMENTS}


def compute_level(xp: int) -> int:
    return isqrt(xp // 50) + 1


def xp_for_level(level: int) -> int:
    return 50 * (level - 1) ** 2


# ── DB helpers ───────────────────────────────────────────────────────────────


async def _db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA query_only = ON")
    return db


async def _fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    db = await _db()
    try:
        cursor = await db.execute(sql, params)
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def _fetch_one(sql: str, params: tuple = ()) -> dict | None:
    db = await _db()
    try:
        cursor = await db.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


# ── Username enrichment ──────────────────────────────────────────────────────


async def _enrich_users(rows: list[dict], user_key: str = "discord_user") -> list[dict]:
    """Add username and avatar_url from discord_users cache."""
    if not rows:
        return rows
    user_ids = list({r[user_key] for r in rows})
    placeholders = ",".join("?" for _ in user_ids)
    cache = await _fetch_all(
        f"SELECT discord_user, username, avatar_url FROM discord_users "
        f"WHERE discord_user IN ({placeholders})",
        tuple(user_ids),
    )
    lookup = {r["discord_user"]: r for r in cache}
    for row in rows:
        cached = lookup.get(row[user_key])
        row["username"] = cached["username"] if cached else f"User {row[user_key][:8]}"
        row["avatar_url"] = cached["avatar_url"] if cached else None
    return rows


# ── API Endpoints ────────────────────────────────────────────────────────────


@app.get("/api/v1/health")
async def health():
    try:
        row = await _fetch_one(
            "SELECT COUNT(DISTINCT discord_user) AS players FROM casino_wallets"
        )
        players = row["players"] if row else 0
        db_size = os.path.getsize(DB_PATH) / (1024 * 1024)
        return {"status": "ok", "players": players, "db_size_mb": round(db_size, 1)}
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)


@app.get("/api/v1/casino/leaderboard")
async def casino_leaderboard(limit: int = Query(25, ge=1, le=100)):
    rows = await _fetch_all(
        """SELECT
                w.discord_user,
                w.balance,
                COALESCE(SUM(h.wagered), 0) AS total_wagered,
                COALESCE(SUM(h.payout), 0)  AS total_payout,
                COALESCE(SUM(h.payout) - SUM(h.wagered), 0) AS net_profit,
                COUNT(h.id) AS rounds
            FROM casino_wallets w
            LEFT JOIN casino_history h ON w.discord_user = h.discord_user
            GROUP BY w.discord_user
            ORDER BY w.balance DESC
            LIMIT ?""",
        (limit,),
    )
    await _enrich_users(rows)
    return {"leaderboard": rows}


@app.get("/api/v1/casino/leaderboard/{game}")
async def casino_game_leaderboard(game: str, limit: int = Query(25, ge=1, le=100)):
    if game not in GAME_LABELS:
        return JSONResponse({"error": f"Unknown game: {game}"}, status_code=404)
    rows = await _fetch_all(
        """SELECT
                discord_user,
                COALESCE(SUM(wagered), 0) AS total_wagered,
                COALESCE(SUM(payout), 0)  AS total_payout,
                COALESCE(SUM(payout) - SUM(wagered), 0) AS net_profit,
                COUNT(*) AS rounds
            FROM casino_history
            WHERE game = ?
            GROUP BY discord_user
            ORDER BY net_profit DESC
            LIMIT ?""",
        (game, limit),
    )
    for row in rows:
        row["roi"] = round(
            (row["net_profit"] / row["total_wagered"] * 100) if row["total_wagered"] else 0, 1,
        )
    await _enrich_users(rows)
    return {"game": game, "game_label": GAME_LABELS[game], "leaderboard": rows}


@app.get("/api/v1/trading/leaderboard")
async def trading_leaderboard(limit: int = Query(25, ge=1, le=100)):
    rows = await _fetch_all(
        """SELECT
                discord_user,
                SUM(payout) - SUM(wager) AS net_profit,
                SUM(wager) AS total_wagered,
                COUNT(*) AS num_bets,
                SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) AS num_won,
                SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) AS num_lost,
                CASE WHEN SUM(wager) > 0
                    THEN (SUM(payout) - SUM(wager)) * 100.0 / SUM(wager)
                    ELSE 0
                END AS roi,
                AVG(clv) AS avg_clv
            FROM paper_bets
            WHERE status IN ('won', 'lost', 'push')
            GROUP BY discord_user
            ORDER BY net_profit DESC
            LIMIT ?""",
        (limit,),
    )
    for row in rows:
        row["roi"] = round(row["roi"] or 0, 1)
        row["avg_clv"] = round(row["avg_clv"], 2) if row["avg_clv"] else None
    await _enrich_users(rows)
    return {"leaderboard": rows}


@app.get("/api/v1/player/{user_id}")
async def player_profile(user_id: str):
    # Username
    cached = await _fetch_one(
        "SELECT username, avatar_url FROM discord_users WHERE discord_user = ?",
        (user_id,),
    )
    username = cached["username"] if cached else f"User {user_id[:8]}"
    avatar_url = cached["avatar_url"] if cached else None

    # XP / Level
    xp_row = await _fetch_one(
        "SELECT total_xp, level FROM user_xp WHERE discord_user = ?", (user_id,),
    )
    total_xp = xp_row["total_xp"] if xp_row else 0
    level = xp_row["level"] if xp_row else 1
    next_level_xp = xp_for_level(level + 1)

    # Casino balance
    bal_row = await _fetch_one(
        "SELECT balance FROM casino_wallets WHERE discord_user = ?", (user_id,),
    )
    balance = bal_row["balance"] if bal_row else 0

    # Casino stats
    casino = await _fetch_one(
        """SELECT
                COALESCE(SUM(wagered), 0) AS total_wagered,
                COALESCE(SUM(payout), 0)  AS total_payout,
                COALESCE(SUM(payout) - SUM(wagered), 0) AS net_profit,
                COUNT(*) AS rounds
            FROM casino_history WHERE discord_user = ?""",
        (user_id,),
    )

    # Per-game breakdown
    per_game = await _fetch_all(
        """SELECT
                game,
                COALESCE(SUM(wagered), 0) AS total_wagered,
                COALESCE(SUM(payout), 0)  AS total_payout,
                COALESCE(SUM(payout) - SUM(wagered), 0) AS net_profit,
                COUNT(*) AS rounds
            FROM casino_history WHERE discord_user = ?
            GROUP BY game ORDER BY rounds DESC""",
        (user_id,),
    )
    for row in per_game:
        row["game_label"] = GAME_LABELS.get(row["game"], row["game"])

    # Paper trading stats
    paper = await _fetch_one(
        """SELECT
                COALESCE(SUM(wager), 0) AS total_wagered,
                COALESCE(SUM(payout), 0) AS total_payout,
                COALESCE(SUM(payout) - SUM(wager), 0) AS net_profit,
                SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) AS num_won,
                SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) AS num_lost,
                SUM(CASE WHEN status = 'push' THEN 1 ELSE 0 END) AS num_push,
                AVG(clv) AS avg_clv
            FROM paper_bets
            WHERE discord_user = ? AND status IN ('won', 'lost', 'push')""",
        (user_id,),
    )

    # Achievements
    unlocked = await _fetch_all(
        "SELECT achievement_id, unlocked_at FROM user_achievements "
        "WHERE discord_user = ? ORDER BY unlocked_at ASC",
        (user_id,),
    )
    unlocked_ids = {a["achievement_id"] for a in unlocked}
    achievements = []
    for a in ALL_ACHIEVEMENTS:
        achievements.append({
            **a,
            "unlocked": a["id"] in unlocked_ids,
        })

    # Duels
    duel = await _fetch_one(
        """SELECT
               SUM(CASE WHEN winner_id = ? THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN winner_id IS NOT NULL AND winner_id != ? THEN 1 ELSE 0 END) AS losses
           FROM duels
           WHERE status = 'finished'
             AND (challenger_id = ? OR opponent_id = ?)""",
        (user_id, user_id, user_id, user_id),
    )

    # Tournaments
    tourney = await _fetch_one(
        """SELECT
               COUNT(*) AS entries,
               SUM(CASE WHEN final_place = 1 THEN 1 ELSE 0 END) AS wins,
               COALESCE(SUM(payout), 0) AS total_payout
           FROM tournament_entries WHERE discord_user = ?""",
        (user_id,),
    )

    return {
        "user_id": user_id,
        "username": username,
        "avatar_url": avatar_url,
        "level": level,
        "total_xp": total_xp,
        "next_level_xp": next_level_xp,
        "balance": balance,
        "casino": dict(casino) if casino else {"total_wagered": 0, "total_payout": 0, "net_profit": 0, "rounds": 0},
        "per_game": per_game,
        "paper_trading": dict(paper) if paper else None,
        "achievements": achievements,
        "duels": dict(duel) if duel and duel["wins"] is not None else {"wins": 0, "losses": 0},
        "tournaments": dict(tourney) if tourney and tourney["entries"] is not None else {"entries": 0, "wins": 0, "total_payout": 0},
    }


@app.get("/api/v1/games")
async def list_games():
    return {"games": [{"key": k, "label": v} for k, v in GAME_LABELS.items()]}
