# SharpLab — Current Status

Last updated: 2026-03-21

## Architecture Decided

Monorepo. Two main pieces:
- **Temporal pipeline** (`temporal/`) — data producer. Polls odds, captures closes, writes to DB.
- **Discord bot** (`bot/`) — interface. Serves info to the server, logs bets, CLV tracker.

Shared DB (`data/sharplab.db` SQLite). All access through `db/queries.py`.

## What's Built

### Shared layer
- `shared/odds_utils.py` — prob↔American↔decimal conversions (fully tested)
- `shared/models.py` — `Game`, `OddsSnapshot`, `OddsBatch` dataclasses

### DB layer
- `db/schema.py` — SQLite schema + `init_db()` (WAL mode, games/odds_snapshots/bets tables)
- `db/queries.py` — `upsert_game`, `upsert_odds_snapshot`, `get_latest_snapshots_for_game`,
  `get_snapshots_for_game_since`, `get_close_snapshot`, `find_games_by_team`

### Temporal pipeline (real, not stubs)
- `temporal/activities.py`:
  - `fetch_games_for_today` → The Odds API `/events` endpoint (lightweight, gets commence_time)
  - `fetch_odds_batch` → The Odds API `/odds` endpoint (per-game, per-bookmaker snapshots)
  - `upsert_odds_snapshot` → real SQLite via db/queries.py
  - `fetch_close_odds_snapshot` → The Odds API filtered by eventId, returns `list[OddsSnapshot]`
- `temporal/workflows.py` — `OddsPollingWorkflow` + `CloseCaptureWorkflow` (durable, correct)
- `temporal/worker.py` — calls `init_db()` on startup

### Discord bot
- `bot/main.py` — `SharpBot` entrypoint, loads cogs, syncs slash commands on startup
- `bot/cogs/utils.py` — `/convert`, `/ev`, `/kelly`, `/parlay` (pure math, no API/DB)
- `bot/cogs/odds.py` — `/odds`, `/best-line` (reads from DB, zero API quota)

### Tests
- `tests/test_activities.py` — 8 unit tests, all passing (payload extraction + odds utils)
- `tests/test_workflows.py` — workflow tests with stubs; need Temporal test server binary (slow first run)

## Key Decisions Made

- **Game ID = The Odds API event ID** (UUID-like string). No balldontlie for now.
- **Poll interval = 30 min** during game window → ~360 req/month, within free tier.
- **`fetch_close_odds_snapshot` returns `list[OddsSnapshot]`** not `Optional` — Temporal SDK can't deserialize union return types.
- **DraftKings = canonical close source** (falls back to first available bookmaker).
- **`/odds` and `/best-line` read from DB** (not live API) to protect quota. Staleness shown in output.

## What Doesn't Exist Yet

- `bot/cogs/bets.py` — `/log`, `/record`
- `bot/cogs/markets.py` — `/kalshi`
- CLV auto-post (background task in bot that fires when a close snapshot lands)
- `/line-move` command
- Kalshi and Polymarket activities (future)
- `data/sharplab.db` — created at runtime by `init_db()`

## API Keys in .env

- `ODDS_API_KEY` ✅
- `BALLDONTLIE_API_KEY` ✅
- `KALSHI_API_KEY` ✅
- `DISCORD_BOT_TOKEN` ✅

## Build Order (next steps)

1. `/log` and `/record` (bets cog — DB read/write)
2. CLV auto-post (background task: detect close snapshot → compute CLV → post to Discord)
3. `/line-move` (reads pipeline history from DB)
4. `/kalshi` (Kalshi API call)
