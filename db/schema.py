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
CREATE INDEX IF NOT EXISTS idx_games_sport ON games(sport);
CREATE INDEX IF NOT EXISTS idx_games_status ON games(status);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    snapshot_id  TEXT PRIMARY KEY,
    game_id      TEXT REFERENCES games(game_id),
    kind         TEXT NOT NULL,
    source       TEXT NOT NULL,
    captured_at  TEXT NOT NULL,
    payload      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_game ON odds_snapshots(game_id);
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_game_source_kind ON odds_snapshots(game_id, source, kind);

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
CREATE INDEX IF NOT EXISTS idx_bets_game ON bets(game_id);
CREATE INDEX IF NOT EXISTS idx_bets_user ON bets(discord_user);
CREATE INDEX IF NOT EXISTS idx_bets_user_status ON bets(discord_user, status);
CREATE INDEX IF NOT EXISTS idx_bets_game_status ON bets(game_id, status);

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
CREATE INDEX IF NOT EXISTS idx_injuries_team ON injuries(team);

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
CREATE INDEX IF NOT EXISTS idx_paper_bets_user ON paper_bets(discord_user);
CREATE INDEX IF NOT EXISTS idx_paper_bets_game ON paper_bets(game_id);
CREATE INDEX IF NOT EXISTS idx_paper_bets_user_status ON paper_bets(discord_user, status);

CREATE TABLE IF NOT EXISTS user_settings (
    discord_user          TEXT PRIMARY KEY,
    craps_default_bet     INTEGER,
    crapless_default_bet  INTEGER
);

CREATE TABLE IF NOT EXISTS discord_users (
    discord_user TEXT PRIMARY KEY,
    username     TEXT NOT NULL,
    avatar_url   TEXT,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS elo_ratings (
    discord_user  TEXT NOT NULL,
    game          TEXT NOT NULL,
    rating        REAL NOT NULL DEFAULT 1000.0,
    games_played  INTEGER NOT NULL DEFAULT 0,
    wins          INTEGER NOT NULL DEFAULT 0,
    losses        INTEGER NOT NULL DEFAULT 0,
    draws         INTEGER NOT NULL DEFAULT 0,
    peak_rating   REAL NOT NULL DEFAULT 1000.0,
    last_played   TEXT,
    PRIMARY KEY (discord_user, game)
);

CREATE TABLE IF NOT EXISTS elo_match_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user   TEXT NOT NULL,
    opponent_user  TEXT,
    game           TEXT NOT NULL,
    result         REAL NOT NULL,
    rating_before  REAL NOT NULL,
    rating_after   REAL NOT NULL,
    rating_change  REAL NOT NULL,
    context        TEXT,
    played_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_elo_match_user ON elo_match_history(discord_user, game);
CREATE INDEX IF NOT EXISTS idx_elo_match_time ON elo_match_history(played_at);

CREATE TABLE IF NOT EXISTS game_sessions (
    room_id         TEXT PRIMARY KEY,
    game_type       TEXT NOT NULL DEFAULT 'sudoku',
    host_discord_id TEXT NOT NULL,
    channel_id      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'waiting',
    prize_pool      INTEGER NOT NULL DEFAULT 0,
    result_json     TEXT,
    created_at      TEXT NOT NULL,
    finished_at     TEXT
);

CREATE TABLE IF NOT EXISTS game_tokens (
    token        TEXT PRIMARY KEY,
    room_id      TEXT NOT NULL REFERENCES game_sessions(room_id),
    discord_user TEXT NOT NULL,
    display_name TEXT NOT NULL,
    wager        INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    UNIQUE(room_id, discord_user)
);

CREATE TABLE IF NOT EXISTS geo_accuracy (
    discord_user  TEXT NOT NULL,
    country       TEXT NOT NULL,
    region        TEXT NOT NULL,
    category      TEXT NOT NULL,
    correct       INTEGER NOT NULL DEFAULT 0,
    total         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (discord_user, country, category)
);
CREATE INDEX IF NOT EXISTS idx_geo_accuracy_user ON geo_accuracy(discord_user);

CREATE TABLE IF NOT EXISTS active_discord_tables (
    channel_id   INTEGER PRIMARY KEY,
    message_id   INTEGER,
    game_type    TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS error_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      TEXT NOT NULL,
    error_type     TEXT NOT NULL,
    command        TEXT,
    user_id        TEXT,
    guild_id       TEXT,
    channel_id     TEXT,
    stack_trace    TEXT,
    severity       TEXT NOT NULL DEFAULT 'medium',
    resolved       INTEGER NOT NULL DEFAULT 0,
    resolved_at    TEXT,
    resolved_by    TEXT,
    resolution_note TEXT,
    ticket_id      TEXT,
    error_signature TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    last_occurred  TEXT NOT NULL,
    reopen_count   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_error_logs_severity ON error_logs(severity);
CREATE INDEX IF NOT EXISTS idx_error_logs_resolved ON error_logs(resolved);
CREATE INDEX IF NOT EXISTS idx_error_logs_signature ON error_logs(error_signature);
CREATE INDEX IF NOT EXISTS idx_error_logs_timestamp ON error_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_error_logs_command ON error_logs(command);

CREATE TABLE IF NOT EXISTS stock_holdings (
    discord_user TEXT NOT NULL,
    ticker       TEXT NOT NULL,
    shares       REAL NOT NULL,
    dca_price    REAL NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (discord_user, ticker)
);

CREATE TABLE IF NOT EXISTS stock_trades (
    trade_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user TEXT NOT NULL,
    ticker       TEXT NOT NULL,
    side         TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
    shares       REAL NOT NULL CHECK(shares > 0),
    price        REAL NOT NULL CHECK(price > 0),
    executed_at  TEXT NOT NULL,
    notes        TEXT
);
CREATE INDEX IF NOT EXISTS idx_stock_trades_user_ticker
    ON stock_trades(discord_user, ticker, executed_at);
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
        # Migration: add user_settings table (CREATE IF NOT EXISTS handles this idempotently,
        # but the executescript above only runs DDL at startup — explicit create ensures it
        # exists on databases created before this table was added)
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS user_settings "
                "(discord_user TEXT PRIMARY KEY, craps_default_bet INTEGER)"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: add crapless_default_bet column (missing from initial user_settings migration)
        try:
            await db.execute("ALTER TABLE user_settings ADD COLUMN crapless_default_bet INTEGER")
            await db.commit()
        except Exception:
            pass  # column already exists
        # Migration: add discord_users cache table for web leaderboard
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS discord_users "
                "(discord_user TEXT PRIMARY KEY, username TEXT NOT NULL, "
                "avatar_url TEXT, updated_at TEXT NOT NULL)"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: add active_discord_tables for cross-session table cleanup
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS active_discord_tables "
                "(channel_id INTEGER PRIMARY KEY, message_id INTEGER, "
                "game_type TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: add error_logs table for global error handler
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS error_logs "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, "
                "error_type TEXT NOT NULL, command TEXT, user_id TEXT, guild_id TEXT, "
                "channel_id TEXT, stack_trace TEXT, severity TEXT NOT NULL DEFAULT 'medium', "
                "resolved INTEGER NOT NULL DEFAULT 0, resolved_at TEXT, resolved_by TEXT, "
                "resolution_note TEXT, ticket_id TEXT, error_signature TEXT NOT NULL, "
                "occurrence_count INTEGER NOT NULL DEFAULT 1, last_occurred TEXT NOT NULL, "
                "reopen_count INTEGER NOT NULL DEFAULT 0)"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: add stock_holdings for /stock_portfolio
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS stock_holdings "
                "(discord_user TEXT NOT NULL, ticker TEXT NOT NULL, "
                "shares REAL NOT NULL, dca_price REAL NOT NULL, "
                "updated_at TEXT NOT NULL, PRIMARY KEY (discord_user, ticker))"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: add stock_trades log table
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS stock_trades ("
                "trade_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "discord_user TEXT NOT NULL, ticker TEXT NOT NULL, "
                "side TEXT NOT NULL CHECK(side IN ('buy', 'sell')), "
                "shares REAL NOT NULL CHECK(shares > 0), "
                "price REAL NOT NULL CHECK(price > 0), "
                "executed_at TEXT NOT NULL, notes TEXT)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_stock_trades_user_ticker "
                "ON stock_trades(discord_user, ticker, executed_at)"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: backfill stock_trades from existing stock_holdings as synthetic
        # buy trades. Only runs for rows that don't already have any trades — idempotent
        # and safe to re-run.
        try:
            await db.execute(
                "INSERT INTO stock_trades "
                "(discord_user, ticker, side, shares, price, executed_at, notes) "
                "SELECT h.discord_user, h.ticker, 'buy', h.shares, h.dca_price, "
                "       h.updated_at, 'migrated from DCA entry' "
                "FROM stock_holdings h "
                "WHERE NOT EXISTS ("
                "    SELECT 1 FROM stock_trades t "
                "    WHERE t.discord_user = h.discord_user AND t.ticker = h.ticker"
                ")"
            )
            await db.commit()
        except Exception:
            pass
