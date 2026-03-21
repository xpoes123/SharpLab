# SharpLab — Current Status

Last updated: 2026-03-21

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
  insert_bet, get_bets_for_user, get_open_bets_for_game

### Temporal pipeline (real, not stubs)
- `temporal/activities.py` — fetch_games_for_today, fetch_odds_batch, upsert_odds_snapshot, fetch_close_odds_snapshot
- `temporal/workflows.py` — OddsPollingWorkflow + CloseCaptureWorkflow (durable, correct)
- `temporal/worker.py` — calls init_db() on startup

### Discord bot
- `bot/main.py` — SharpBot entrypoint, loads cogs, calls init_db(), syncs slash commands
- `bot/cogs/utils.py` — /convert, /ev, /kelly, /parlay (pure math, no API/DB)
  - /convert accepts: American (-110), decimal (1.91), cents (52), probability (0.52/52%)
- `bot/cogs/odds.py` — /odds, /best-line (reads from DB, zero API quota)
- `bot/cogs/bets.py` — /log, /record
  - /log odds param accepts all formats (American, decimal, cents) — converts to American for storage
  - Books: DraftKings, FanDuel, BetMGM, Caesars, Bet365, PointsBet, Kalshi, Polymarket, Other

### Tests
- `tests/test_activities.py` — 8 unit tests, all passing

## Key Decisions Made

- **Game ID = The Odds API event ID** (UUID-like string).
- **Poll interval = 30 min** → ~360 req/month, within free tier.
- **`fetch_close_odds_snapshot` returns `list[OddsSnapshot]`** not Optional — Temporal SDK limitation.
- **DraftKings = canonical close source** (falls back to first available).
- **`/odds` and `/best-line` read from DB** to protect quota. Staleness shown in output.
- **All odds stored as American** in DB. Convert at input boundary in `shared/odds_utils.py`.

## What Doesn't Exist Yet

- CLV auto-post (background task: detect close snapshot → compute CLV → post to Discord)
- `/line-move` command (reads pipeline history from DB)
- `bot/cogs/markets.py` — `/kalshi`
- Kalshi and Polymarket pipeline activities

## API Keys in .env

- `ODDS_API_KEY` ✅
- `BALLDONTLIE_API_KEY` ✅
- `KALSHI_API_KEY` ✅
- `DISCORD_BOT_TOKEN` ✅

## Build Order (next steps)

1. CLV auto-post — background task in bot, polls for new close snapshots, posts CLV for logged bets
2. `/line-move` — reads odds_snapshots history from DB
3. `/kalshi` — live Kalshi API call
