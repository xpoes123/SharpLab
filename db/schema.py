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

CREATE TABLE IF NOT EXISTS user_xp (
    discord_user  TEXT PRIMARY KEY,
    total_xp      INTEGER NOT NULL DEFAULT 0,
    level         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS user_achievements (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user   TEXT NOT NULL,
    achievement_id TEXT NOT NULL,
    unlocked_at    TEXT NOT NULL,
    UNIQUE(discord_user, achievement_id)
);
CREATE INDEX IF NOT EXISTS idx_user_achievements_user ON user_achievements(discord_user);

CREATE TABLE IF NOT EXISTS daily_challenges (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user   TEXT NOT NULL,
    challenge_date TEXT NOT NULL,
    slot           INTEGER NOT NULL,
    challenge_id   TEXT NOT NULL,
    completed      INTEGER DEFAULT 0,
    completed_at   TEXT,
    UNIQUE(discord_user, challenge_date, slot)
);
CREATE INDEX IF NOT EXISTS idx_daily_challenges_user_date
    ON daily_challenges(discord_user, challenge_date);

CREATE TABLE IF NOT EXISTS daily_bonus_claimed (
    discord_user   TEXT NOT NULL,
    challenge_date TEXT NOT NULL,
    claimed_at     TEXT NOT NULL,
    PRIMARY KEY (discord_user, challenge_date)
);

CREATE TABLE IF NOT EXISTS duels (
    duel_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    challenger_id    TEXT NOT NULL,
    opponent_id      TEXT NOT NULL,
    wager            INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    winner_id        TEXT,
    score_challenger INTEGER DEFAULT 0,
    score_opponent   INTEGER DEFAULT 0,
    games_played     TEXT,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    channel_id       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tournaments (
    tournament_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    game           TEXT NOT NULL,
    size           INTEGER NOT NULL,
    buy_in         INTEGER NOT NULL,
    prize_pool     INTEGER NOT NULL,
    status         TEXT NOT NULL DEFAULT 'registration',
    host_id        TEXT NOT NULL,
    channel_id     TEXT NOT NULL,
    bracket_json   TEXT,
    created_at     TEXT NOT NULL,
    finished_at    TEXT
);

CREATE TABLE IF NOT EXISTS tournament_entries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id  INTEGER NOT NULL,
    discord_user   TEXT NOT NULL,
    seed           INTEGER,
    eliminated     INTEGER DEFAULT 0,
    final_place    INTEGER,
    payout         INTEGER DEFAULT 0,
    UNIQUE(tournament_id, discord_user)
);

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

CREATE TABLE IF NOT EXISTS prediction_markets (
    market_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id         TEXT NOT NULL,
    question           TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'open',
    winning_outcome_id INTEGER,
    created_at         TEXT NOT NULL,
    resolved_at        TEXT
);

CREATE TABLE IF NOT EXISTS market_outcomes (
    outcome_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id      INTEGER NOT NULL REFERENCES prediction_markets(market_id),
    label          TEXT NOT NULL,
    UNIQUE(market_id, label)
);

CREATE TABLE IF NOT EXISTS market_orders (
    order_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id      INTEGER NOT NULL REFERENCES prediction_markets(market_id),
    outcome_id     INTEGER NOT NULL REFERENCES market_outcomes(outcome_id),
    discord_user   TEXT NOT NULL,
    side           TEXT NOT NULL,
    price          INTEGER NOT NULL,
    quantity       INTEGER NOT NULL,
    filled_qty     INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'open',
    placed_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_orders_market ON market_orders(market_id, outcome_id, status);
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
