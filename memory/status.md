# SharpLab — Current Status

Last updated: 2026-03-26 (session 10)

## Architecture Decided

Monorepo. Two main pieces:
- **Temporal pipeline** (`temporal/`) — data producer. Polls odds, captures closes, writes to DB.
- **Discord bot** (`bot/`) — interface. Serves info to the server, logs bets, CLV tracker.

Shared DB (`data/sharplab.db` SQLite). All access through `db/queries.py`.

## What's Built

### Shared layer
- `shared/odds_utils.py` — prob↔American↔decimal↔cents conversions + `parse_odds_input()` (fully tested)
- `shared/models.py` — `Game`, `OddsSnapshot`, `OddsBatch`, `Bet`, `GameResult`, `InjuryAlert` dataclasses
  - NOTE: NO `from __future__ import annotations` — breaks Temporal's get_type_hints() on Python 3.14

### DB layer
- `db/schema.py` — SQLite schema + `init_db()` (WAL mode, games/odds_snapshots/bets/injuries tables)
- `db/queries.py` — upsert_game, upsert_odds_snapshot, get_latest_snapshots_for_game,
  get_snapshots_for_game_since, get_close_snapshot, find_games_by_team,
  get_upcoming_games, get_game_by_id, get_games_in_window,
  insert_bet, get_bets_for_user, get_open_bets_for_game,
  get_games_with_close_and_open_bets, get_any_close_snapshot, update_bet_clv,
  get_games_with_close_not_posted, mark_game_clv_posted,
  upsert_injury_status, get_unnotified_injuries, mark_injury_notified, get_todays_game_for_team,
  get_past_game_count, get_history_page, get_first_poll_snapshot,
  get_recent_games, get_games_by_id_prefix, get_all_games_filtered,
  get_resolvable_bets_for_game, get_game_by_team_suffixes, update_bet_result, update_game_status,
  **get_graded_bets_for_user** (clv IS NOT NULL), **get_open_bets_for_user** (status IN open/graded)

### Temporal pipeline (real, not stubs)
- `temporal/activities.py` — fetch_games_for_today, fetch_odds_batch, upsert_odds_snapshot,
  fetch_close_odds_snapshot, fetch_kalshi_odds_batch, fetch_kalshi_close_snapshot, fetch_injuries,
  **fetch_polymarket_odds_batch**, **fetch_polymarket_close_snapshot**,
  **fetch_final_scores**, **resolve_bets_for_game**
  - Kalshi matching: KXNBAGAME series, team abbr from last 6 chars of event ticker
  - `TEAM_ABBR` dict in `shared/models.py` maps full team names → 3-char abbreviations
  - ESPN injuries: polls `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries`
  - Polymarket: `_polymarket_ml_for_game(client, home, away)` helper → `fetch_polymarket_odds_batch(games)`
    + `fetch_polymarket_close_snapshot(inp)`. Searches `gamma-api.polymarket.com/markets?q={home_short} {away_short}`.
    No auth. Flexible response shape (list / {"markets":[...]} / {"data":[...]}). Tokens may be JSON-encoded string.
  - `fetch_final_scores(dates)` — hits balldontlie, returns `list[GameResult]` for final games only
  - `resolve_bets_for_game(GameResult)` — matches by team suffix, resolves open/graded bets, marks game 'final'
    - Resolution: moneyline/kalshi (team name or yes/no), spread (margin = side_score - opp + line), total (O/U)
    - Idempotent: `games.status = 'final'` gates re-processing
- `temporal/workflows.py` — OddsPollingWorkflow + CloseCaptureWorkflow + InjuryPollingWorkflow + **BetResolutionWorkflow**
  - OddsPollingWorkflow: steps 7+8 fetch+persist Polymarket after Kalshi every 30 min
  - CloseCaptureWorkflow: captures Polymarket close (ML only) alongside DK + Kalshi at tip-off
  - InjuryPollingWorkflow: re-fetches Polymarket on injury changes (same as Kalshi)
  - BetResolutionWorkflow: every 2 hours, fetches yesterday+today scores, resolves bets for each final game
- `temporal/worker.py` — calls init_db() on startup, all 4 workflows + all activities registered
- `temporal/start_injury_polling.py` — entry point for InjuryPollingWorkflow (`just injuries`)
- `temporal/start_bet_resolution.py` — entry point for BetResolutionWorkflow (`just resolve`)

### Discord bot
- `bot/main.py` — SharpBot entrypoint, loads cogs, calls init_db(), guild-syncs slash commands (instant)
  - Uses `DISCORD_GUILD_ID` env var — guild sync is instant vs global sync (1 hour delay)
- `bot/cogs/utils.py` — /convert, /ev, /kelly, /parlay, **/help** (pure math, no API/DB)
  - /convert accepts: American (-110), decimal (1.91), cents (52), probability (0.52/52%)
- `bot/cogs/odds.py` — /odds, /best-line, /line-move, /scores
  - /odds and /best-line: game autocomplete from DB, Kalshi + Polymarket prefer DB (live fallback only if pipeline hasn't run)
  - /line-move: Kalshi + Polymarket ML + DraftKings spread movement
    - `PREDICTION_MARKET_SOURCES = ["kalshi", "polymarket"]` — Polymarket now fully wired (pipeline writes to DB)
    - ML section (per prediction market source): open vs current with ↑/↓ delta
    - Spread section (DraftKings): home/away spread open vs current
    - Shows snapshot count, "opened X ago"
    - Autocomplete: yesterday+today+upcoming only; ID-prefix search (from /db) resolves all games
  - /scores: balldontlie live scores with ET time formatting
    - Finals: spread result + ✅/❌/➖ cover emoji (home_score - away_score + spread > 0 = covered)
    - Upcoming: ML implied probs (away%/home%) — **Kalshi preferred over DK** (no vig)
    - Blank line separator between finals / live / upcoming sections
  - NBA day rollover at 11 AM UTC (7 AM ET) — fetches yesterday+today when post-midnight
  - `_preload_game_odds`: prefers Kalshi for ML (no vig), falls back to any book; spread from separate snap
- `bot/cogs/bets.py` — /log, /open, /clv-summary, /record, **/void**
  - /void: ephemeral, `bet_id: int` param, guards ownership + status (open/graded only), calls `update_bet_result('void')`. `get_bet_by_id` added to `db/queries.py`.
  - /log game param uses same `game_autocomplete` as /odds (game_id selected directly)
  - /log odds param accepts all formats (American, decimal, cents) — converts to American for storage
  - Books: DraftKings, FanDuel, BetMGM, Caesars, Bet365, PointsBet, Kalshi, Polymarket, Other
  - /open: ephemeral list of user's open (⏳) + graded (📊) bets with CLV where available
  - /clv-summary: aggregate CLV analytics — avg CLV pp, total EV gained (Σ units × CLV/100),
    breakdown by market and by book. Optional `user` param. Color-coded by EV sign.
    EV formula: `units × (clv_pp / 100)` = theoretical edge captured vs. closing line
- `bot/cogs/markets.py` — /kalshi
  - Live fetch from Kalshi KXNBAGAME series — not cached in DB
  - Shows bid/ask/mid (as implied prob + American) and volume for each side (away/home)
  - Same game autocomplete as /odds. Matches by team abbr in event ticker.
- `bot/cogs/clv.py` — CLV auto-post background task
  - `@tasks.loop(minutes=5)` — polls DB for games where close snapshot exists + `clv_posted=0`
  - **Always posts closing lines** (Kalshi ML + DK spread/total) regardless of whether bets exist
  - If bets exist: computes CLV per bet, adds fields to embed, pings bettors via `content=`
  - Computes CLV in probability points (close_prob − bet_prob × 100)
  - Updates bets: clv=X, status='graded'; sets games.clv_posted=1 (prevents re-posting)
  - Supports: moneyline (home+away), spread (home side), total (over/under)
  - **Kalshi close = source of truth for ML CLV; DraftKings fallback for spread/total**
  - **Spread shows open→close movement** (e.g. `-4.5 → -3.0`) using earliest DK poll snapshot
  - `clv_posted` column in games table (migration-safe ALTER TABLE in init_db())
- `bot/cogs/history.py` — **/db command** (NEW)
  - Paginated embed: 5 games/page, Prev/Next buttons (120s timeout)
  - Shows matchup, date, status, pregame spread (from close snapshot), 8-char short ID
  - Short ID can be pasted into `/line-move` autocomplete to look up any historical game
- `bot/cogs/injuries.py` — **Injury alert auto-post background task**
  - `@tasks.loop(minutes=1)` — polls DB for `notified=0` injury rows
  - Looks up today's game for the team, fetches latest odds snapshots
  - Posts embed to same channel as CLV (CLV_CHANNEL_ID)
    - Red for Out/Doubtful, yellow for Questionable/Day-To-Day
    - Shows status change arrow ("Probable → Questionable") or "(new listing)"
    - Shows current ML per book (refreshed by workflow post-news)
  - Notification logic: Probable-only first inserts are silently suppressed (notified=1)
    all other first inserts and any status change trigger a post

### Dev tooling
- `justfile` — `just dev` starts all services (Temporal server + worker + odds poller + injury poller + bet resolver + bot)
  - Individual recipes: `just temporal`, `just worker`, `just poll`, `just injuries`, `just resolve`, `just bot`, `just test`
  - `just` installed via winget; PATH added to `~/.bashrc`

### Tests
- `tests/test_activities.py` — 26 unit tests, all passing

## Key Decisions Made

- **Game ID = The Odds API event ID** (UUID-like string).
- **Poll interval = 30 min** → ~360 req/month, within free tier.
- **`fetch_close_odds_snapshot` returns `list[OddsSnapshot]`** not Optional — Temporal SDK limitation.
- **DraftKings = canonical close source** (falls back to first available).
- **All odds stored as American** in DB. Convert at input boundary in `shared/odds_utils.py`.
- **Guild sync** (`tree.sync(guild=guild)`) not global sync — commands appear instantly.
- **CLV + injury auto-post channel** = `1485475287054418151` (env var `CLV_CHANNEL_ID` to override).
- **Bet status lifecycle**: `open` → `graded` (CLV computed at tip-off) → `won`/`lost`/`push`/`void` (auto-resolved by BetResolutionWorkflow).
- **CLV post gating**: `games.clv_posted` column (0/1). Set to 1 after posting; never re-posts on bot restart.
- **tzdata** added as dependency for `zoneinfo` ET timezone support on Windows.
- **ESPN injuries**: record_id = ESPN athlete ID.
  Notification rules (tightened): only Discord-post for `Out` status, and only when player was previously healthy (not already Questionable/Doubtful/D2D/Out).
  Odds re-fetch only triggers on transitions to Out. All other status changes are stored silently.

## What Doesn't Exist Yet

### Bot backlog (prioritized)

1. **`/record` improvements** — Time-period filter (last 30 days / season / all time) + per-book ROI breakdown.
3. **Sharp move flag in `/line-move`** — Detect reverse line movement (line moved against the public side) and flag it in the embed.
4. **Line alerts** — `/alert` command: ping user when a line crosses a threshold. Needs an `alerts` table + check on each pipeline poll. Bigger lift.

## API Keys in .env

- `ODDS_API_KEY` ✅
- `BALLDONTLIE_API_KEY` ✅
- `KALSHI_API_KEY` ✅
- `DISCORD_BOT_TOKEN` ✅
- `DISCORD_GUILD_ID` ✅

## Build Order (next steps)

1. `/record` improvements — time-period filter + per-book ROI breakdown
2. Sharp move flag in `/line-move` — detect reverse line movement
3. `/alert` — ping on line threshold crossing (bigger lift)
