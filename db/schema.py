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
CREATE INDEX IF NOT EXISTS idx_games_sport_start ON games(sport, start_time);

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
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_game_source_kind_time ON odds_snapshots(game_id, source, kind, captured_at);

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

-- "Where did my coins come from" — a log of coin GAINS (never spends).
CREATE TABLE IF NOT EXISTS coin_ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user  TEXT NOT NULL,
    amount        INTEGER NOT NULL,      -- always > 0 (gains only)
    reason        TEXT NOT NULL,         -- human label, e.g. "Reached level 5"
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coin_ledger_user ON coin_ledger(discord_user, id);

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
    crapless_default_bet  INTEGER,
    odds_format           TEXT NOT NULL DEFAULT 'american',
    books                 TEXT,
    livebet_alerts        INTEGER NOT NULL DEFAULT 0, -- opt-in to live in-game bet swing pings
    stock_alerts          INTEGER NOT NULL DEFAULT 0  -- opt-in to portfolio swing alerts (≥10% daily moves)
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

-- DEPRECATED / DEAD TABLE — do NOT read or write this. No longer maintained.
-- Current holdings are computed from the stock_trades log (the authoritative source);
-- see get_stock_positions_full / get_all_stock_holdings in db/queries.py. Kept only so
-- the one-time backfill into stock_trades stays idempotent on old DBs.
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

CREATE TABLE IF NOT EXISTS stock_cash (
    discord_user TEXT PRIMARY KEY,
    balance      REAL NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL
);

-- Manually-injected realized gains (e.g. seeding pre-existing or real-brokerage
-- P/L). Added to the computed realized total at display time; the cash credit is
-- applied separately to stock_cash when the entry is created.
CREATE TABLE IF NOT EXISTS realized_adjustments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user TEXT NOT NULL,
    amount       REAL NOT NULL,
    note         TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_realized_adjustments_user
    ON realized_adjustments(discord_user);

CREATE TABLE IF NOT EXISTS option_trades (
    trade_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user TEXT NOT NULL,
    underlying   TEXT NOT NULL,
    opt_type     TEXT NOT NULL CHECK(opt_type IN ('call', 'put')),
    strike       REAL NOT NULL CHECK(strike > 0),
    expiry       TEXT NOT NULL,            -- YYYY-MM-DD
    side         TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
    contracts    INTEGER NOT NULL CHECK(contracts > 0),
    premium      REAL NOT NULL CHECK(premium > 0),  -- per-share; 1 contract = 100 shares
    executed_at  TEXT NOT NULL,
    notes        TEXT
);
CREATE INDEX IF NOT EXISTS idx_option_trades_user
    ON option_trades(discord_user, underlying, expiry);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user  TEXT NOT NULL,
    captured_at   TEXT NOT NULL,                  -- UTC ISO 8601
    account_value REAL NOT NULL,                  -- stock+options+cash (live); stock-only (backfill)
    stock_value   REAL NOT NULL DEFAULT 0,
    options_value REAL NOT NULL DEFAULT 0,
    cash          REAL NOT NULL DEFAULT 0,
    kind          TEXT NOT NULL DEFAULT 'live'    -- 'live' | 'backfill'
);
CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_user
    ON portfolio_snapshots(discord_user, captured_at);

CREATE TABLE IF NOT EXISTS ticker_meta (
    ticker      TEXT PRIMARY KEY,
    quote_type  TEXT,          -- EQUITY | ETF | MUTUALFUND | ...
    sector      TEXT,          -- equities only (NULL for ETFs)
    category    TEXT,          -- ETFs only (e.g. 'Large Blend')
    beta        REAL,          -- equities (NULL for many ETFs)
    name        TEXT,
    updated_at  TEXT NOT NULL  -- UTC ISO 8601; used for cache TTL
);

CREATE TABLE IF NOT EXISTS ticker_quotes (
    ticker      TEXT PRIMARY KEY,
    price       REAL,          -- regular-session last price
    prev_close  REAL,
    currency    TEXT,
    extended    TEXT,          -- JSON {session, price, pct} when pre/post-market, else NULL
    updated_at  TEXT NOT NULL  -- UTC ISO 8601; how stale the quote is
);

CREATE TABLE IF NOT EXISTS reaction_roles (
    message_id  TEXT NOT NULL,
    emoji       TEXT NOT NULL,   -- str(emoji): unicode char or '<:name:id>'
    role_id     TEXT NOT NULL,
    guild_id    TEXT NOT NULL,
    channel_id  TEXT NOT NULL,
    PRIMARY KEY (message_id, emoji)
);

CREATE TABLE IF NOT EXISTS qb_answers (
    answer      TEXT PRIMARY KEY,  -- primary answer / display name
    category    TEXT NOT NULL,     -- 'science' (room for other QBReader categories)
    aliases     TEXT,              -- JSON list of accepted alternates
    created_at  TEXT NOT NULL      -- UTC ISO 8601, first time we saw it
);

CREATE TABLE IF NOT EXISTS stock_monitors (
    monitor_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user  TEXT NOT NULL,
    channel_id    TEXT NOT NULL,   -- where to ping when it fires
    ticker        TEXT NOT NULL,   -- normalized (AAPL, BTC-USD)
    direction     TEXT NOT NULL CHECK(direction IN ('above', 'below')),
    target_price  REAL NOT NULL,
    created_at    TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_stock_monitors_active ON stock_monitors(active);

CREATE TABLE IF NOT EXISTS bot_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pickem_games (
    message_id   TEXT PRIMARY KEY,   -- the vote message
    game_id      TEXT NOT NULL,
    sport        TEXT NOT NULL,
    home_team    TEXT NOT NULL,
    away_team    TEXT NOT NULL,
    start_time   TEXT NOT NULL,      -- UTC ISO 8601
    posted_date  TEXT NOT NULL,      -- ET date YYYY-MM-DD (one post per game/day)
    locked       INTEGER NOT NULL DEFAULT 0,
    resolved     INTEGER NOT NULL DEFAULT 0,
    winner       TEXT,               -- 'home' | 'away' once resolved
    home_prob    REAL,               -- fair win prob (devigged) at post time
    away_prob    REAL,
    odds_source  TEXT                -- 'kalshi' | bookmaker key
);
CREATE INDEX IF NOT EXISTS idx_pickem_games_state ON pickem_games(locked, resolved);
CREATE INDEX IF NOT EXISTS idx_pickem_games_date ON pickem_games(posted_date);

CREATE TABLE IF NOT EXISTS pickem_picks (
    message_id   TEXT NOT NULL,
    discord_user TEXT NOT NULL,
    pick         TEXT NOT NULL CHECK(pick IN ('home', 'away')),
    stake        INTEGER NOT NULL DEFAULT 1,   -- 1-5 units wagered
    picked_at    TEXT NOT NULL,
    correct      INTEGER,            -- NULL until resolved, then 0/1
    PRIMARY KEY (message_id, discord_user)
);
CREATE INDEX IF NOT EXISTS idx_pickem_picks_user ON pickem_picks(discord_user);

-- Pick'em favorite teams: auto-pick any unlocked game your favorite team plays in.
-- `team` is a lowercased substring matched against the full team name.
CREATE TABLE IF NOT EXISTS pickem_favorites (
    discord_user TEXT NOT NULL,
    team         TEXT NOT NULL,
    PRIMARY KEY (discord_user, team)
);

-- Sports trading cards: sets (one per sport-season), designs (a player card),
-- instances (a minted, serial-numbered copy owned by a user). See docs spec 2026-08-11.
CREATE TABLE IF NOT EXISTS card_sets (
    set_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    sport        TEXT NOT NULL,          -- nba/nfl/mlb
    season       INTEGER NOT NULL,       -- e.g. 2024 (NBA 2024-25 => 2024)
    name         TEXT NOT NULL,
    total_packs  INTEGER NOT NULL,
    packs_opened INTEGER NOT NULL DEFAULT 0,
    base_cost    INTEGER NOT NULL,       -- coins per pack (vintage-priced at seed time)
    closed       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    UNIQUE(sport, season)
);

CREATE TABLE IF NOT EXISTS card_designs (
    design_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id       INTEGER NOT NULL REFERENCES card_sets(set_id),
    subject_key  TEXT NOT NULL,          -- stable id, e.g. nba|2024|lebron-james-1966
    subject_name TEXT NOT NULL,
    team         TEXT,
    rarity       TEXT NOT NULL,          -- common/uncommon/rare/epic/legendary
    is_rookie    INTEGER NOT NULL DEFAULT 0,
    career_fame  REAL,
    total_copies INTEGER NOT NULL,
    minted       INTEGER NOT NULL DEFAULT 0,
    stats        TEXT,                   -- JSON display stats
    headshot_url TEXT,
    book_value   REAL NOT NULL,
    UNIQUE(set_id, subject_key)
);
CREATE INDEX IF NOT EXISTS idx_card_designs_set ON card_designs(set_id);

CREATE TABLE IF NOT EXISTS card_instances (
    instance_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id    INTEGER NOT NULL REFERENCES card_designs(design_id),
    owner_id     TEXT NOT NULL,
    serial       INTEGER NOT NULL,
    is_holo      INTEGER NOT NULL DEFAULT 0,
    gem          TEXT,
    book_value   REAL NOT NULL,
    acquired_cost REAL NOT NULL DEFAULT 0,
    source       TEXT NOT NULL DEFAULT 'pack',  -- pack/daily/trade
    acquired_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_card_instances_owner ON card_instances(owner_id);
CREATE INDEX IF NOT EXISTS idx_card_instances_design ON card_instances(design_id);

CREATE TABLE IF NOT EXISTS card_wants (
    discord_user TEXT NOT NULL,
    design_id    INTEGER NOT NULL REFERENCES card_designs(design_id),
    PRIMARY KEY (discord_user, design_id)
);

CREATE TABLE IF NOT EXISTS card_pack_claims (
    discord_user TEXT NOT NULL,
    day          TEXT NOT NULL,
    PRIMARY KEY (discord_user, day)
);

CREATE TABLE IF NOT EXISTS daily_message_reward (
    discord_user TEXT NOT NULL,
    day          TEXT NOT NULL,
    PRIMARY KEY (discord_user, day)
);

CREATE TABLE IF NOT EXISTS daily_coin_earn (
    discord_user TEXT NOT NULL,
    day          TEXT NOT NULL,
    source       TEXT NOT NULL,   -- message / bet_log / trade_log / paper_trade / pickem_pick
    earned       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (discord_user, day, source)
);

CREATE TABLE IF NOT EXISTS card_trades (
    trade_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user    TEXT NOT NULL,
    to_user      TEXT NOT NULL,
    offer_ids    TEXT NOT NULL,          -- JSON list of instance_ids offered
    want_ids     TEXT NOT NULL,          -- JSON list of instance_ids requested
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending/accepted/declined/cancelled
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_card_trades_to ON card_trades(to_user, status);
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
        await db.execute("CREATE INDEX IF NOT EXISTS idx_games_sport_start ON games(sport, start_time)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_odds_snapshots_game_source_kind_time ON odds_snapshots(game_id, source, kind, captured_at)")
        await db.commit()
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
        # Migration: create dead stock_holdings table (required so the backfill to stock_trades below is idempotent on old DBs — do NOT write to this table)
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
        # Migration: add stock_cash table for portfolio cash positions
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS stock_cash "
                "(discord_user TEXT PRIMARY KEY, balance REAL NOT NULL DEFAULT 0, "
                "updated_at TEXT NOT NULL)"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: add option_trades log table
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS option_trades ("
                "trade_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "discord_user TEXT NOT NULL, underlying TEXT NOT NULL, "
                "opt_type TEXT NOT NULL CHECK(opt_type IN ('call', 'put')), "
                "strike REAL NOT NULL CHECK(strike > 0), expiry TEXT NOT NULL, "
                "side TEXT NOT NULL CHECK(side IN ('buy', 'sell')), "
                "contracts INTEGER NOT NULL CHECK(contracts > 0), "
                "premium REAL NOT NULL CHECK(premium > 0), "
                "executed_at TEXT NOT NULL, notes TEXT)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_option_trades_user "
                "ON option_trades(discord_user, underlying, expiry)"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: add portfolio_snapshots for the /stock graph equity curve
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS portfolio_snapshots ("
                "snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "discord_user TEXT NOT NULL, captured_at TEXT NOT NULL, "
                "account_value REAL NOT NULL, stock_value REAL NOT NULL DEFAULT 0, "
                "options_value REAL NOT NULL DEFAULT 0, cash REAL NOT NULL DEFAULT 0, "
                "kind TEXT NOT NULL DEFAULT 'live')"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_user "
                "ON portfolio_snapshots(discord_user, captured_at)"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: add ticker_meta cache (sector / quote type / beta) for analytics
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS ticker_meta ("
                "ticker TEXT PRIMARY KEY, quote_type TEXT, sector TEXT, "
                "category TEXT, beta REAL, name TEXT, updated_at TEXT NOT NULL)"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: add reaction_roles for the auto-role panels
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS reaction_roles ("
                "message_id TEXT NOT NULL, emoji TEXT NOT NULL, role_id TEXT NOT NULL, "
                "guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, "
                "PRIMARY KEY (message_id, emoji))"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: add qb_answers cache for party-game QBReader science pool
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS qb_answers ("
                "answer TEXT PRIMARY KEY, category TEXT NOT NULL, "
                "aliases TEXT, created_at TEXT NOT NULL)"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: add stock_monitors + bot_settings for price alerts
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS stock_monitors ("
                "monitor_id INTEGER PRIMARY KEY AUTOINCREMENT, discord_user TEXT NOT NULL, "
                "channel_id TEXT NOT NULL, ticker TEXT NOT NULL, "
                "direction TEXT NOT NULL CHECK(direction IN ('above','below')), "
                "target_price REAL NOT NULL, created_at TEXT NOT NULL, "
                "active INTEGER NOT NULL DEFAULT 1)"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS bot_settings ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: add pickem tables for the daily NBA/MLB pick'em
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS pickem_games ("
                "message_id TEXT PRIMARY KEY, game_id TEXT NOT NULL, sport TEXT NOT NULL, "
                "home_team TEXT NOT NULL, away_team TEXT NOT NULL, start_time TEXT NOT NULL, "
                "posted_date TEXT NOT NULL, locked INTEGER NOT NULL DEFAULT 0, "
                "resolved INTEGER NOT NULL DEFAULT 0, winner TEXT, "
                "home_prob REAL, away_prob REAL, odds_source TEXT)"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS pickem_picks ("
                "message_id TEXT NOT NULL, discord_user TEXT NOT NULL, "
                "pick TEXT NOT NULL CHECK(pick IN ('home','away')), "
                "stake INTEGER NOT NULL DEFAULT 1, picked_at TEXT NOT NULL, "
                "correct INTEGER, PRIMARY KEY (message_id, discord_user))"
            )
            await db.commit()
        except Exception:
            pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pickem_games_date ON pickem_games(posted_date)")
        await db.commit()
        # Migration: add win-probability columns to pickem_games
        for col in ("home_prob REAL", "away_prob REAL", "odds_source TEXT"):
            try:
                await db.execute(f"ALTER TABLE pickem_games ADD COLUMN {col}")
                await db.commit()
            except Exception:
                pass
        # Migration: add stake to pickem_picks (1-5 units)
        try:
            await db.execute("ALTER TABLE pickem_picks ADD COLUMN stake INTEGER NOT NULL DEFAULT 1")
            await db.commit()
        except Exception:
            pass
        # Migration: dividends paid (one credit per holder per ticker per ex-date)
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS dividends_paid ("
                "discord_user TEXT NOT NULL, ticker TEXT NOT NULL, ex_date TEXT NOT NULL, "
                "amount REAL NOT NULL, paid_at TEXT NOT NULL, "
                "PRIMARY KEY (discord_user, ticker, ex_date))"
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_dividends_user ON dividends_paid(discord_user)")
            await db.commit()
        except Exception:
            pass
        # Migration: card set-completion claims (one reward per user per set)
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS card_set_completed ("
                "discord_user TEXT NOT NULL, set_id INTEGER NOT NULL, claimed_at TEXT NOT NULL, "
                "PRIMARY KEY (discord_user, set_id))"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: earnings-results post dedupe (one summary per ticker per report date)
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS earnings_posted "
                "(ticker TEXT NOT NULL, report_date TEXT NOT NULL, PRIMARY KEY (ticker, report_date))"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: pickem_favorites table + per-user auto-pick stake
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS pickem_favorites "
                "(discord_user TEXT NOT NULL, team TEXT NOT NULL, PRIMARY KEY (discord_user, team))"
            )
            await db.commit()
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE user_settings ADD COLUMN pickem_fav_stake INTEGER NOT NULL DEFAULT 1")
            await db.commit()
        except Exception:
            pass
        # Migration: site analytics events (cookieless pageviews/duration for /hq/analytics)
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS web_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, sid TEXT, "
                "type TEXT NOT NULL, page TEXT, ref TEXT, ua TEXT, ip_hash TEXT, data TEXT)"
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_web_events_ts ON web_events(ts)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_web_events_type ON web_events(type)")
            await db.commit()
        except Exception:
            pass
        # Migration: NBA player props (latest line per game/book/player/market)
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS player_props ("
                "game_id TEXT NOT NULL, source TEXT NOT NULL, player TEXT NOT NULL, "
                "market TEXT NOT NULL, line REAL, over_odds INTEGER, under_odds INTEGER, "
                "captured_at TEXT NOT NULL, "
                "PRIMARY KEY (game_id, source, player, market))"
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_player_props_game ON player_props(game_id)")
            await db.commit()
        except Exception:
            pass
        # Migration: NBA player-prop ALTERNATE ladders (many lines per player/market).
        # market is the base key (player_assists), line is part of the PK so the full
        # ladder is retained. Used for exact alt-line CLV.
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS player_prop_alts ("
                "game_id TEXT NOT NULL, source TEXT NOT NULL, player TEXT NOT NULL, "
                "market TEXT NOT NULL, line REAL NOT NULL, over_odds INTEGER, under_odds INTEGER, "
                "captured_at TEXT NOT NULL, "
                "PRIMARY KEY (game_id, source, player, market, line))"
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_player_prop_alts_game ON player_prop_alts(game_id)")
            await db.commit()
        except Exception:
            pass
        # Migration: cumulative engagement counters (voice minutes, chat messages)
        # for Voice/Chat achievements.
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS user_engagement ("
                "discord_user TEXT PRIMARY KEY, voice_minutes INTEGER NOT NULL DEFAULT 0, "
                "messages INTEGER NOT NULL DEFAULT 0)"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: per-user display preferences (e.g. preferred odds format, books held).
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS user_settings ("
                "discord_user TEXT PRIMARY KEY, odds_format TEXT NOT NULL DEFAULT 'american', "
                "books TEXT NOT NULL DEFAULT '')"
            )
            await db.commit()
        except Exception:
            pass
        try:  # add `odds_format` to an already-existing user_settings table
            await db.execute("ALTER TABLE user_settings ADD COLUMN odds_format TEXT NOT NULL DEFAULT 'american'")
            await db.commit()
        except Exception:
            pass
        try:  # add `books` to an already-existing user_settings table
            await db.execute("ALTER TABLE user_settings ADD COLUMN books TEXT")
            await db.commit()
        except Exception:
            pass
        try:  # add `livebet_alerts` opt-in flag to an already-existing user_settings table
            await db.execute("ALTER TABLE user_settings ADD COLUMN livebet_alerts INTEGER NOT NULL DEFAULT 0")
            await db.commit()
        except Exception:
            pass
        try:  # add `stock_alerts` opt-in flag to an already-existing user_settings table
            await db.execute("ALTER TABLE user_settings ADD COLUMN stock_alerts INTEGER NOT NULL DEFAULT 0")
            await db.commit()
        except Exception:
            pass
        try:  # opt-in: always skip the card-pack reveal animation and show the whole haul at once
            await db.execute("ALTER TABLE user_settings ADD COLUMN cards_fast_open INTEGER NOT NULL DEFAULT 0")
            await db.commit()
        except Exception:
            pass
        # Migration: warm quote store so HQ stock pages never block on yfinance
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS ticker_quotes ("
                "ticker TEXT PRIMARY KEY, price REAL, prev_close REAL, currency TEXT, "
                "extended TEXT, updated_at TEXT NOT NULL)"
            )
            await db.commit()
        except Exception:
            pass
        # Migration: Daily Games platform (competitive daily puzzles). One puzzle per game/day,
        # cached with its par; one result per user/day (the PK is the one-submit rule); streaks.
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS daily_puzzles ("
                "game_id TEXT NOT NULL, puzzle_date TEXT NOT NULL, difficulty TEXT NOT NULL, "
                "seed INTEGER NOT NULL, payload TEXT NOT NULL, par INTEGER, "
                "par_approx INTEGER NOT NULL DEFAULT 0, awarded INTEGER NOT NULL DEFAULT 0, "
                "PRIMARY KEY (game_id, puzzle_date))"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS daily_results ("
                "game_id TEXT NOT NULL, puzzle_date TEXT NOT NULL, discord_user TEXT NOT NULL, "
                "solved INTEGER NOT NULL, primary_score INTEGER NOT NULL, "
                "secondary_score INTEGER NOT NULL, submitted_at TEXT NOT NULL, "
                "PRIMARY KEY (game_id, puzzle_date, discord_user))"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_results_date "
                "ON daily_results(puzzle_date)"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS daily_streaks ("
                "discord_user TEXT NOT NULL, game_id TEXT NOT NULL, "
                "current INTEGER NOT NULL DEFAULT 0, longest INTEGER NOT NULL DEFAULT 0, "
                "last_date TEXT, PRIMARY KEY (discord_user, game_id))"
            )
            await db.commit()
        except Exception:
            pass
        try:  # daily results: track which have been announced to the Discord thread
            await db.execute("ALTER TABLE daily_results ADD COLUMN posted INTEGER NOT NULL DEFAULT 0")
            await db.commit()
        except Exception:
            pass
        try:  # daily: the FIRST Start per user/day — the clock runs continuously from here
              # across retries, so grinding attempts costs time instead of resetting it.
            await db.execute(
                "CREATE TABLE IF NOT EXISTS daily_starts ("
                "discord_user TEXT NOT NULL, game_id TEXT NOT NULL, puzzle_date TEXT NOT NULL, "
                "started_at TEXT NOT NULL, PRIMARY KEY (discord_user, game_id, puzzle_date))"
            )
            await db.commit()
        except Exception:
            pass
