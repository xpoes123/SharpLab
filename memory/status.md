# SharpLab — Current Status

Last updated: 2026-04-26

## Architecture

Monorepo. Two main pieces + web layer:
- **Temporal pipeline** (`temporal/`) — data producer. Polls odds, captures closes, resolves bets.
- **Discord bot** (`bot/`) — interface. Odds info, bet logging, CLV tracking, casino games, paper trading.
- **Web server** (`web/`) — FastAPI + WebSocket. Browser-based games, leaderboard API.

Shared DB (`data/sharplab.db` SQLite, WAL mode). All access through `db/queries.py`.

## What's Built

### Shared layer
- `shared/odds_utils.py` — prob/American/decimal/cents conversions + `parse_odds_input()` + `fmt_prob()`
- `shared/models.py` — Game, OddsSnapshot, OddsBatch, Bet, GameResult, InjuryAlert dataclasses
  - `TEAM_ABBR = {"nba": {...30 teams...}, "mlb": {...30 teams...}}`
  - `get_team_abbr(name, sport)` helper
- `shared/elo.py` — pure ELO math (expected score, update, K-factor by rating)
- `shared/sudoku_logic.py`, `shared/figgie_logic.py`, `shared/bingo_logic.py` — web game logic

### DB layer
- `db/schema.py` — all tables + `init_db()` with migrations
- `db/queries.py` — all DB access (games, odds_snapshots, bets, injuries, paper_bets, casino_history, user_xp, user_achievements, elo_ratings, elo_match_history, game_sessions, game_tokens, discord_users, etc.)
- Tables: games, odds_snapshots, bets, injuries, paper_bets, casino_history, user_xp, user_achievements, daily_challenges, daily_bonus_claimed, duels, tournaments, tournament_entries, elo_ratings, elo_match_history, game_sessions, game_tokens, discord_users

### Temporal pipeline
- **OddsPollingWorkflow** — every 30 min: fetch games → The Odds API → Kalshi → Polymarket → DB
- **CloseCaptureWorkflow** — at tip-off: capture DK + Kalshi + Polymarket closing lines
- **InjuryPollingWorkflow** — every 5 min: ESPN injuries → status change detection → odds re-fetch → Discord alert
- **BetResolutionWorkflow** — every 2 hours: balldontlie/ESPN scores → resolve open/graded bets → mark games final
- Activities: fetch_games_for_today, fetch_odds_batch, upsert_odds_snapshot, fetch_close_odds_snapshot, fetch_kalshi_odds_batch, fetch_kalshi_close_snapshot, fetch_polymarket_odds_batch, fetch_polymarket_close_snapshot, fetch_injuries, fetch_final_scores, resolve_bets_for_game

### Discord bot — Slash commands
**Odds & Lines:** /odds, /best-line, /line-move, /scores, /kalshi, /rosters
**Bet Tracking:** /log, /bets, /void, /record, /clv-summary
**Utilities:** /convert, /ev, /kelly, /parlay, /help
**History:** /db (paginated game browser)
**Paper Trading:** /trade, /mlb-trade, /portfolio, /leaderboard, /void-trade
**Casino:** 13+ games via cogs (poker, blackjack, pokemon, geography, quizbowl, etc.)
**Engagement:** /daily (challenges), /duel, /tournament, /achievements, /profile, /level
**Ratings:** /ratings, /standings (F1 championship), /game-ratings

### Discord bot — Other features
- CLV auto-post (`bot/cogs/clv.py`) — closing lines for every game, pings bettors
- Injury alerts (`bot/cogs/injuries.py`) — Out transitions only, with current ML
- Prefix API (`bot/cogs/prefix.py`) — `!` commands for bot-to-bot interface
- Paper trading auto-resolution (`bot/cogs/trading.py`) — every 5 min loop

### Web layer
- FastAPI backend (`web/api.py`) — leaderboard API + game WebSocket engine
- Static frontend (`web/static/`) — dark theme, vanilla HTML/CSS/JS
- Games: sudoku, figgie, bingo (all WebSocket-based, session-link auth)
- Live at `sharplab.djiang.xyz` (Caddy reverse proxy, auto-HTTPS)

### Engagement & ELO
- Daily challenges: 3 rotating objectives, 100c each + 200c all-3 bonus
- Duels: `/duel @user [amount]` — best-of-3 mini-games, optional coin wager
- Tournaments: `/tournament <4|8> [buy_in]` — single elimination brackets
- Achievements: 25 badges in 6 categories
- XP & Leveling: auto-awarded on game completion. `level = isqrt(xp/50)+1`
- ELO: per-game for 13 mini-games + Pokemon. Start 1000, K=32/24/16, floor 100
- F1 Championship: position points per game leaderboard. Min 5 games to qualify.

### Dev tooling
- `justfile` — `just dev` starts everything. Individual: `just temporal`, `just worker`, `just poll`, `just injuries`, `just resolve`, `just bot`, `just test`
- VPS: Hetzner (`87.99.136.82`). Services: temporal, sharplab-worker, sharplab-bot, sharplab-web
- Tests: `tests/test_activities.py` (unit tests)

## Key Decisions

- Game ID = The Odds API event UUID
- Poll interval = 30 min (fits free tier)
- American odds in DB, implied probability in Discord embeds
- CLV: Kalshi close for ML, DraftKings close for spread/total
- Guild sync (instant) via DISCORD_GUILD_ID
- Bet lifecycle: open → graded → won/lost/push/void
- Coin deduct on join, refund in finally block
- Web games: Discord for coordination, browser for gameplay
- `games.home_score`/`away_score` written by resolution activity (used by paper trading)
- Kalshi KXNBASPREAD series is illiquid binary ladder — DK is spread/total source of truth

## What Doesn't Exist Yet

1. Pick'em (daily free contest on real games)
2. Poker (web game)
3. `/record` improvements (time-period filter, per-book ROI)
4. Sharp move flag in `/line-move` (reverse line movement detection)
5. Line alerts (`/alert` — ping on threshold crossing)
