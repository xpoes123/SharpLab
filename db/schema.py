"""DB init and schema. Call init_db() once at startup."""
from __future__ import annotations
import aiosqlite

DB_PATH = "data/sharplab.db"

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS games (
    game_id     TEXT PRIMARY KEY,
    home_team   TEXT NOT NULL,
    away_team   TEXT NOT NULL,
    start_time  TEXT NOT NULL,
    sport       TEXT NOT NULL DEFAULT 'nba',
    season      TEXT,
    status      TEXT DEFAULT 'scheduled',
    clv_posted  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    snapshot_id  TEXT PRIMARY KEY,
    game_id      TEXT REFERENCES games(game_id),
    kind         TEXT NOT NULL,
    source       TEXT NOT NULL,
    captured_at  TEXT NOT NULL,
    payload      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bets (
    bet_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id       TEXT REFERENCES games(game_id),
    placed_at     TEXT NOT NULL,
    discord_user  TEXT NOT NULL,
    book          TEXT NOT NULL,
    market        TEXT NOT NULL,
    side          TEXT NOT NULL,
    line          REAL,
    odds          INTEGER NOT NULL,
    units         REAL NOT NULL,
    status        TEXT DEFAULT 'open',
    clv           REAL,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS injuries (
    record_id    TEXT PRIMARY KEY,
    player_name  TEXT NOT NULL,
    team         TEXT NOT NULL,
    status       TEXT NOT NULL,
    prev_status  TEXT,
    detail       TEXT,
    updated_at   TEXT NOT NULL,
    notified     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS wallets (
    discord_user  TEXT PRIMARY KEY,
    balance       INTEGER NOT NULL DEFAULT 0,
    last_daily    TEXT
);

CREATE TABLE IF NOT EXISTS casino_wallets (
    discord_user  TEXT PRIMARY KEY,
    balance       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS casino_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user  TEXT NOT NULL,
    game          TEXT NOT NULL,
    wagered       INTEGER NOT NULL,
    payout        INTEGER NOT NULL,
    played_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_casino_history_user ON casino_history(discord_user);

CREATE TABLE IF NOT EXISTS paper_bets (
    paper_bet_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id         TEXT NOT NULL REFERENCES games(game_id),
    discord_user    TEXT NOT NULL,
    placed_at       TEXT NOT NULL,
    market          TEXT NOT NULL,
    side            TEXT NOT NULL,
    line            REAL,
    odds            INTEGER NOT NULL,
    wager           INTEGER NOT NULL,
    potential_payout INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',
    resolved_at     TEXT,
    payout          INTEGER DEFAULT 0,
    clv             REAL
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        # Migration: add clv_posted if DB predates this column
        try:
            await db.execute("ALTER TABLE games ADD COLUMN clv_posted INTEGER DEFAULT 0")
            await db.commit()
        except Exception:
            pass  # column already exists
        # Migration: add sport column
        try:
            await db.execute("ALTER TABLE games ADD COLUMN sport TEXT NOT NULL DEFAULT 'nba'")
            await db.commit()
        except Exception:
            pass  # column already exists
        # Migration: add score columns for paper trading resolution
        for col in ("home_score INTEGER", "away_score INTEGER"):
            try:
                await db.execute(f"ALTER TABLE games ADD COLUMN {col}")
                await db.commit()
            except Exception:
                pass  # column already exists
        # Migration: add clv column to paper_bets
        try:
            await db.execute("ALTER TABLE paper_bets ADD COLUMN clv REAL")
            await db.commit()
        except Exception:
            pass  # column already exists
