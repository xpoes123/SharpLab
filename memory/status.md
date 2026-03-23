# SharpLab — Current Status

Last updated: 2026-03-23 (session 2)

## Architecture Decided

Monorepo. Two main pieces:
- **Temporal pipeline** (`temporal/`) — data producer. Polls odds, captures closes, writes to DB.
- **Discord bot** (`bot/`) — interface. Serves info to the server, logs bets, CLV tracker.

Shared DB (`data/sharplab.db` SQLite). All access through `db/queries.py`.

## What's Built

### Shared layer
- `shared/odds_utils.py` — prob↔American↔decimal↔cents conversions + `parse_odds_input()` (fully tested)
- `shared/models.py` — `Game`, `OddsSnapshot`, `OddsBatch`, `Bet` dataclasses
  - NOTE: NO `from __future__ import annotations` — breaks Temporal's get_type_hints() on Python 3.14

### DB layer
- `db/schema.py` — SQLite schema + `init_db()` (WAL mode, games/odds_snapshots/bets tables)
- `db/queries.py` — upsert_game, upsert_odds_snapshot, get_latest_snapshots_for_game,
  get_snapshots_for_game_since, get_close_snapshot, find_games_by_team,
  get_upcoming_games, get_game_by_id, get_games_in_window,
  insert_bet, get_bets_for_user, get_open_bets_for_game,
  get_games_with_close_and_open_bets, get_any_close_snapshot, update_bet_clv

### Temporal pipeline (real, not stubs)
- `temporal/activities.py` — fetch_games_for_today, fetch_odds_batch, upsert_odds_snapshot, fetch_close_odds_snapshot, fetch_kalshi_odds_batch, fetch_kalshi_close_snapshot
  - Kalshi matching: KXNBAGAME series, team abbr from last 6 chars of event ticker
  - `TEAM_ABBR` dict in `shared/models.py` maps full team names → 3-char abbreviations
- `temporal/workflows.py` — OddsPollingWorkflow + CloseCaptureWorkflow (durable, correct)
- `temporal/worker.py` — calls init_db() on startup

### Discord bot
- `bot/main.py` — SharpBot entrypoint, loads cogs, calls init_db(), guild-syncs slash commands (instant)
  - Uses `DISCORD_GUILD_ID` env var — guild sync is instant vs global sync (1 hour delay)
- `bot/cogs/utils.py` — /convert, /ev, /kelly, /parlay (pure math, no API/DB)
  - /convert accepts: American (-110), decimal (1.91), cents (52), probability (0.52/52%)
- `bot/cogs/odds.py` — /odds, /best-line, /line-move, /scores
  - /odds and /best-line: game autocomplete from DB, Kalshi + Polymarket live ML overlay
  - /line-move: Kalshi (+ Polymarket later) ML open vs current delta in probability points
    - Shows pp move with ↑/↓ arrow, snapshot count, "opened X ago"
    - `PREDICTION_MARKET_SOURCES = ["kalshi", "polymarket"]` — add polymarket by adding to list
  - /scores: balldontlie live scores with ET time formatting
    - Finals: spread result + ✅/❌/➖ cover emoji (home_score - away_score + spread > 0 = covered)
    - Upcoming: ML implied probs (away%/home%) — **Kalshi preferred over DK** (no vig)
    - Blank line separator between finals / live / upcoming sections
  - NBA day rollover at 11 AM UTC (7 AM ET) — fetches yesterday+today when post-midnight
  - `_preload_game_odds`: prefers Kalshi for ML (no vig), falls back to any book; spread from separate snap
- `bot/cogs/bets.py` — /log, /record
  - /log odds param accepts all formats (American, decimal, cents) — converts to American for storage
  - Books: DraftKings, FanDuel, BetMGM, Caesars, Bet365, PointsBet, Kalshi, Polymarket, Other
- `bot/cogs/clv.py` — CLV auto-post background task
  - `@tasks.loop(minutes=5)` — polls DB for games with close snapshot + open bets
  - Computes CLV in probability points (close_prob − bet_prob × 100)
  - Posts embed to CLV_CHANNEL_ID (default: 1485475287054418151) with per-user mention
  - Updates bets: clv=X, status='graded' (prevents re-posting)
  - Supports: moneyline (home+away), spread (home side), total (over/under)
  - **Kalshi close = source of truth for ML CLV; DraftKings fallback for spread/total**

### Dev tooling
- `justfile` — `just dev` starts all services at once (Temporal server + worker + odds poller + bot)
  - Individual recipes: `just temporal`, `just worker`, `just poll`, `just bot`, `just test`
  - `just` installed via winget; PATH added to `~/.bashrc`

### Tests
- `tests/test_activities.py` — 19 unit tests, all passing

## Key Decisions Made

- **Game ID = The Odds API event ID** (UUID-like string).
- **Poll interval = 30 min** → ~360 req/month, within free tier.
- **`fetch_close_odds_snapshot` returns `list[OddsSnapshot]`** not Optional — Temporal SDK limitation.
- **DraftKings = canonical close source** (falls back to first available).
- **All odds stored as American** in DB. Convert at input boundary in `shared/odds_utils.py`.
- **Guild sync** (`tree.sync(guild=guild)`) not global sync — commands appear instantly.
- **CLV auto-post channel** = `1485475287054418151` (env var `CLV_CHANNEL_ID` to override).
- **Bet status lifecycle**: `open` → `graded` (CLV computed at tip-off) → `won`/`lost`/`push`/`void` (manual).
- **tzdata** added as dependency for `zoneinfo` ET timezone support on Windows.

## What Doesn't Exist Yet

- `bot/cogs/markets.py` — `/kalshi` command
- Polymarket pipeline activity
- Polymarket in `/line-move` (slot reserved — just add "polymarket" to PREDICTION_MARKET_SOURCES)

## API Keys in .env

- `ODDS_API_KEY` ✅
- `BALLDONTLIE_API_KEY` ✅
- `KALSHI_API_KEY` ✅
- `DISCORD_BOT_TOKEN` ✅
- `DISCORD_GUILD_ID` ✅

## Build Order (next steps)

1. `/kalshi` — live Kalshi market explorer (bot/cogs/markets.py)
2. Polymarket pipeline activity → then add to `/line-move` via PREDICTION_MARKET_SOURCES
