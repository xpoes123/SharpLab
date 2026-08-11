# SharpLab — Current Status

Last updated: 2026-06-01

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
- ~40 tables (see `db/schema.py`). Beyond the core odds/bets set: player_props + player_prop_alts, pickem_games/pickem_picks, stock_trades/option_trades/stock_cash/portfolio_snapshots/ticker_meta/stock_monitors, reaction_roles, error_logs, web_events, user_engagement, bot_settings. ⚠️ `stock_holdings` is DEAD — holdings computed from `stock_trades`.

### Temporal pipeline
- **OddsPollingWorkflow** — every 30 min: fetch games → The Odds API → Kalshi → Polymarket → DB
- **CloseCaptureWorkflow** — at tip-off: capture DK + Kalshi + Polymarket closing lines
- **InjuryPollingWorkflow** — every 5 min: ESPN injuries → status change detection → odds re-fetch → Discord alert
- **BetResolutionWorkflow** — every 2 hours: balldontlie/ESPN scores → resolve open/graded bets → mark games final
- Activities: fetch_games_for_today, fetch_odds_batch, upsert_odds_snapshot, fetch_close_odds_snapshot, fetch_kalshi_odds_batch, fetch_kalshi_close_snapshot, fetch_polymarket_odds_batch, fetch_polymarket_close_snapshot, fetch_injuries, fetch_final_scores, resolve_bets_for_game

### Discord bot — Slash commands (nested groups; sport is a sub-group, not an arg)
**Odds & Lines:** /odds nba|mlb (lines/best/move/props/scores), /kalshi, /rosters
**Bet Tracking:** /bet (log nba|mlb|prop, view, void, record, clv, leaderboard)
**Markets & Signals:** /signals (channel/scan — arb/middle/steam), /pickem (leaderboard/channel/post)
**Player Props:** NBA props via /odds nba props (props.py renders, pipeline ingests)
**Stock/Options Brokerage:** /stock (buy/sell/trades/edit/profile/graph/server/leaderboard/movers/cash/lookup), /option (buy/sell/positions), /monitor (add/list/remove/channel)
**Sports News:** sportsnews.py — auto NBA/NFL/MLB breaking-news posts that ping the league role (no slash cmd)
**Reaction Roles:** /reactionrole (create/bind/unbind/list)
**Utilities:** /calc (convert/ev/kelly/parlay), /help
**History:** /db (paginated game browser)
**Paper Trading:** /paper (trade nba|mlb, portfolio, profile, leaderboard, cashout)
**Casino:** ~80 game cogs, browse /games, launch /play (registry in casino.py, dispatch in game_menu.py)
**Engagement:** /daily (challenges), /duel, /tournament, /achievements, /profile, /level
**Ratings:** /ratings, /standings (F1 championship), /game-ratings

### Discord bot — Other features
- CLV auto-post (`bot/cogs/clv.py`) — closing lines for every game, pings bettors
- Injury alerts (`bot/cogs/injuries.py`) — Out transitions only, with current ML
- Prefix API (`bot/cogs/prefix.py`) — `!` commands for bot-to-bot interface
- Paper trading auto-resolution (`bot/cogs/trading.py`) — every 5 min loop

### Web layer
- FastAPI backend (`web/api.py`) — leaderboard API + game WebSocket engine
- **HQ dashboard** (`web/hq.py`) — portfolio/P&L + leaderboards; `_period_pnl` (holding-aware, trade-adjusted P/L per window), cached via `AsyncTTLCache`. OAuth/sessions in `web/auth.py`.
- Static frontend (`web/static/`) — dark theme, vanilla HTML/CSS/JS (incl. hq, stocks, player pages)
- Games: sudoku, figgie, bingo, blotto, minesweeper, solitaire-chess, trading floor (WebSocket, session-link auth)
- Live at `sharplab.djiang.xyz` (Caddy reverse proxy, auto-HTTPS) — `sharplab-web` systemd service

### Engagement & ELO
- Daily challenges: 3 rotating objectives, 100c each + 200c all-3 bonus
- Duels: `/duel @user [amount]` — best-of-3 mini-games, optional coin wager
- Tournaments: `/tournament <4|8> [buy_in]` — single elimination brackets
- Achievements: ~53 badges across ~11 categories (Progression, Winning, Diversity, Social, Daily, Wealth, Betting, Investing, Web, Voice, Chat) — see `shared/achievements.py`
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

1. Poker (web game)
2. `/bet record` improvements (time-period filter, per-book ROI)
3. Sharp move flag in `/odds nba move` (reverse line movement detection)
4. A predictive model to compare against the market (the original "find edge" goal)

(Pick'em, market signals, stock/options brokerage, and reaction roles now exist.)

## Sports Card Packs (shipped 2026-08-11)

Port of the nsba-markets pack system → NBA/NFL/MLB players. Engine `shared/cards.py`
(pure, 17 tests); tables `card_*` in `db/schema.py`; all access in `db/queries.py`.
Discord cog `bot/cogs/cards.py` (`/pack`, `/cards`, `/cardtrade`); web page at
`/cards` (`web/cards.py` + `web/static/cards.*`). Seed via `scripts/seed_cards.py`
+ `scripts/card_sources.py` (ESPN). 24 sets live (NBA/NFL/MLB × 8 seasons). Rarity =
career-fame rank × rookie premium; legendaries 1-of-1; holo/gems; vintage pack pricing;
quick-sell 75%; packs are a REAL coin spend (not the 1000 faucet). 7 card achievements.
Known limitation: card team labels track the *current* ESPN roster, not the card's
season (ESPN only returns currently-rostered players). Extend seasons: re-run seeder.
