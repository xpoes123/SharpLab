# SharpLab — Claude Instructions

## About This Project

A multi-sport (NBA + MLB) quant trading lab and Discord casino. Monorepo with three pieces:

1. **Temporal pipeline** — ingests odds from major books + Kalshi + Polymarket on a schedule, captures closing lines, resolves bets
2. **Discord bot** — the interface for the server. Odds info, bet logging, CLV tracking, casino games, paper trading
3. **Web server** — FastAPI + WebSocket at `sharplab.djiang.xyz`. Browser-based games, leaderboard API

Core loop:
1. Ingest odds from major books + Kalshi + Polymarket on a schedule
2. Capture the closing line for every tracked game
3. Log bets via Discord `/log`
4. Measure every bet against the closing line (CLV)
5. Eventually: build a model, compare to market, find edge

All trading/paper-trading happens on **Kalshi** and **sportsbooks**. Polymarket is a secondary market signal.

### Local Checkout

Single local checkout (Linux): **`/home/david/code/SharpLab/`**, tracking the GitHub repo `xpoes123/sharplab`.

---

## Repository Layout

```
sharplab/
├── temporal/           # data pipeline — Temporal workflows + activities
│   ├── workflows.py    # orchestration only, no API calls
│   ├── activities.py   # all side effects: API calls, DB writes
│   └── worker.py       # worker entrypoint
├── bot/                # Discord bot
│   ├── main.py         # bot entrypoint (COGS list)
│   └── cogs/           # ~80 cogs. Highlights:
│       ├── odds.py     # /odds nba|mlb (lines/best/move/props/scores)
│       ├── bets.py     # /bet (log/view/void/record/clv/leaderboard)
│       ├── trading.py  # /paper (trade/portfolio/profile/leaderboard/cashout)
│       ├── stock.py    # /stock + /option brokerage, /monitor price alerts
│       ├── markets.py  # /kalshi prediction-market lookups
│       ├── signals.py  # /signals — arb/middle/steam market alerts
│       ├── pickem.py   # /pickem daily NBA/MLB pick'em
│       ├── props.py    # NBA player-props embed (used by /odds nba props)
│       ├── sportsnews.py    # auto NBA/NFL/MLB breaking-news role pings (no slash cmd)
│       ├── reactionroles.py # /reactionrole panels
│       ├── casino.py        # game registry: GAME_LABELS, CASINO_GAMES, GAME_CATEGORIES
│       ├── game_menu.py      # /play launcher — GAME_DISPATCH, PARAMETERIZED_SHORTCUTS
│       ├── utils.py    # /calc (ev/kelly/parlay/convert)
│       └── …           # ~70 casino/party/mini-game cogs (blackjack, wordle, sims, …)
├── web/                # FastAPI + WebSocket server (sharplab.djiang.xyz)
│   ├── api.py          # app entrypoint: leaderboard API + game WebSocket engine
│   ├── hq.py           # HQ dashboard (portfolio/leaderboards); _period_pnl, AsyncTTLCache
│   ├── auth.py         # Discord OAuth / session cookies
│   ├── _apisec.py      # API auth helpers
│   ├── sudoku.py figgie.py bingo.py blotto.py minesweeper.py solitairechess.py  # game routers
│   ├── trading_floor.py
│   └── static/         # vanilla HTML/CSS/JS frontend (dark theme)
├── db/
│   ├── schema.py       # CREATE TABLE statements + init_db() (~40 tables)
│   └── queries.py      # all DB access lives here, nowhere else
├── shared/
│   ├── models.py       # dataclasses shared between pipeline + bot
│   ├── achievements.py # ~53 achievements across ~11 categories
│   └── odds_utils.py   # American ↔ decimal ↔ implied prob conversions
├── scripts/
│   ├── announce_deploy.py     # post a Claude-written deploy update to Discord
│   ├── backfill_achievements.py
│   └── backup_db.sh           # SQLite backups (→ backups/)
├── data/
│   └── sharplab.db     # SQLite, source of truth
├── memory/
│   └── status.md       # current project state, updated each session
├── justfile            # task runner — the real entrypoints (just dev/worker/bot/web/deploy)
└── tests/
```

---

## Stack

| Layer | Tech |
|---|---|
| Language | Python 3.14, async everywhere |
| Package manager | `uv` — always `uv add <pkg>`, never `pip install` |
| Pipeline orchestration | Temporal (`temporalio`) — pipeline only |
| Discord | `discord.py` with app_commands (slash commands) |
| HTTP client | `httpx` (async) |
| Database | SQLite (`aiosqlite`), Postgres later if needed |
| Odds | The Odds API, Kalshi REST API, Polymarket CLOB API |
| NBA schedule | balldontlie API (free, no key needed) |

---

## Running the Project

The real entrypoints are the **`justfile`** recipes — prefer these over raw commands:

```bash
just install   # uv sync
just dev       # Temporal server + worker + pollers + resolver + bot (everything)
just temporal  # Temporal dev server only
just worker    # Temporal worker
just bot       # Discord bot
just web       # FastAPI web server (uvicorn, auto-reload, :8000)
just poll nba  # kick off an odds-polling workflow (sport arg)
just test      # fast unit tests (tests/test_activities.py)
just deploy    # push + VPS deploy (restarts all services)
just status    # VPS service status + recent logs
```

Production runs as three systemd services on the VPS: **`sharplab-bot`**, **`sharplab-worker`**, **`sharplab-web`** (plus the shared `temporal` service). See `docs/vps-hosting.md`.

---

## Discord Bot — Features

The bot does **info** (lines, market signals), **tracking** (bets, CLV), a stock/options **brokerage**, and a large **casino** (~80 cogs).

Commands are organized into nested subcommand groups to stay under Discord's 100-command-per-guild cap. Sport is a nested GROUP, **not** an argument — it's `/odds nba lines`, not `/odds lines nba`.

### Odds & Lines — `/odds nba|mlb ...`
| Command | What it does |
|---|---|
| `/odds nba lines [game]` / `/odds mlb lines [game]` | Live lines across all major books (spread, ML, total) |
| `/odds nba move [game]` | How the line has moved since open — reads `odds_snapshots` history |
| `/odds nba best [game]` | Best number available across all tracked books |
| `/odds nba props [game] [player]` | NBA player props (best line across books) |
| `/odds nba scores` / `/odds mlb scores` | Live scores for today's slate |

### Bet Tracking — `/bet ...`
| Command | What it does |
|---|---|
| `/bet log nba|mlb|prop ...` | Log a bet to the DB (`/bet log` is itself a group; pick the sport/prop subcommand) |
| `/bet view` | Open + graded bets with live CLV |
| `/bet void <bet_id>` | Void a logged bet (cancelled game / entry error) |
| `/bet record [@user]` | A user's W/L record and ROI |
| `/bet clv [@user]` | CLV breakdown and EV gained from beating the close |
| `/bet leaderboard` | CLV / ROI / record leaderboard |

### Paper Trading — `/paper ...` (`bot/cogs/trading.py`)
| Command | What it does |
|---|---|
| `/paper trade nba|mlb ...` | Open a paper trade with coins (`/paper trade` is a group; pick the sport) |
| `/paper portfolio` | Open trades + at-risk |
| `/paper profile` | Stats and history |
| `/paper leaderboard` | Top paper traders |
| `/paper cashout <trade_id>` | Cash out an open trade at current odds |

### Stock & Options Brokerage — `/stock ...`, `/option ...`, `/monitor ...` (`bot/cogs/stock.py`)
| Command | What it does |
|---|---|
| `/stock buy|sell|trades|edit` | Record stock trades; holdings are derived from the trade log |
| `/stock profile|graph|server|leaderboard|movers|lookup` | Portfolio views, equity curve, server P/L, S&P 100 movers |
| `/stock cash` | Set/deposit/withdraw portfolio cash (manual — buys/sells don't touch cash) |
| `/option buy|sell|positions` | Record option trades; show open option positions |
| `/monitor add|list|remove|channel` | Price-cross alerts; swing-alert channel |
| HQ web pages | `/hq` dashboard renders portfolios, P/L (`_period_pnl`), leaderboards |

### Markets & Signals
| Command | What it does |
|---|---|
| `/kalshi [market]` / `/mlb-kalshi [market]` | Yes/no price + depth on a Kalshi contract |
| `/signals channel|scan` | Arbitrage / middle / steam-move market alerts |
| `/pickem leaderboard|channel|post` | Daily NBA/MLB pick'em contest |
| `/reactionrole create|bind|unbind|list` | Reaction-role (auto-role) panels |
| Sports news (auto) | `sportsnews.py` posts NBA/NFL/MLB breaking news + pings the league role |
| CLV (auto) | When a game closes, bot posts CLV for anyone who logged a bet on it |

### Math — `/calc ...`
| Command | What it does |
|---|---|
| `/calc ev [odds] [true_prob]` | Expected value calculator |
| `/calc kelly [bankroll] [odds] [edge]` | Kelly criterion stake sizing |
| `/calc parlay [legs]` | Parlay odds calculator |
| `/calc convert [odds]` | Odds format converter: American ↔ decimal ↔ implied % |

### Casino & Games
~80 game cogs (blackjack, roulette, wordle, sports sims, party games, …). Browse with `/games`, launch with `/play` (registry in `bot/cogs/casino.py`, dispatch in `bot/cogs/game_menu.py`). Use the `/new-game` skill to add one — see GAMES.md.

### Gotchas an AI should know
- **`stock_holdings` table is DEAD.** Do NOT read it. Current holdings are computed from `stock_trades` (the authoritative log) via `get_stock_positions_full` / `get_all_stock_holdings` in `db/queries.py`.
- **Buys/sells move `stock_cash`.** A buy debits cash, a sell credits it, and option trades move premium×100. Trade-driven debits are **floored at 0** (`adjust_stock_cash` default) — most users never deposit, so a buy bigger than the balance is treated as funded by money they already had; cash only matters once selling generates proceeds. Overdraft is NOT allowed on trades (a negative balance made account value negative and flipped return %). `/stock cash` still sets/deposits/withdraws manually. An account's value = positions (stocks + options) **+** cash. `/stock gains <amount>` injects a manual realized gain: it credits cash **and** logs to `realized_adjustments`, which is added to the realized total at display time.
- **`_period_pnl`** (holding-aware, trade-adjusted P/L per time window) lives in `web/hq.py` — not in `db/queries.py`. It accounts for trades made *inside* a period so a mid-period buy only counts gains since the buy.
- **HQ pages are cached** via `AsyncTTLCache` in `web/hq.py` (short TTL) — expect slightly stale numbers right after a trade.

---

## How the Pipeline and Bot Connect

The Temporal pipeline is the **data producer**. The Discord bot is a **read-mostly consumer** on the same DB.

- `/odds nba move` reads `odds_snapshots` rows the pipeline writes each poll
- `/odds nba best` compares the most recent poll snapshot across sources
- CLV auto-post reads the `close` snapshot the `CloseCaptureWorkflow` writes at tip-off
- `/odds nba lines` can either query the DB (fast, slightly stale) or hit The Odds API live (fresh, costs quota)

Default behavior: `/odds nba lines` and `/odds nba best` hit the API live; `/odds nba move` reads the DB history.

---

## Database Schema

The schema lives in **`db/schema.py`** — `_SCHEMA` plus a long list of idempotent
`ALTER`/`CREATE` migrations in `init_db()`. There are **~40 tables** (57 `CREATE TABLE`
statements once you count migration restatements). Read `db/schema.py` for the truth; the
major groups:

| Group | Tables |
|---|---|
| **Odds & games** | `games`, `odds_snapshots`, `injuries` |
| **Bets & CLV** | `bets`, `paper_bets` |
| **Player props** | `player_props`, `player_prop_alts` (alternate ladders for exact alt-line CLV) |
| **Pick'em** | `pickem_games`, `pickem_picks` |
| **Casino economy** | `wallets`, `casino_wallets`, `casino_history`, `user_settings`, `discord_users`, `active_discord_tables` |
| **Progression / achievements** | `user_xp`, `user_achievements`, `daily_challenges`, `daily_bonus_claimed`, `elo_ratings`, `elo_match_history`, `user_engagement` |
| **Competition** | `duels`, `tournaments`, `tournament_entries`, `game_sessions`, `game_tokens`, `geo_accuracy`, `qb_answers` |
| **Stock / options brokerage** | `stock_trades`, `option_trades`, `stock_cash`, `portfolio_snapshots`, `ticker_meta`, `stock_monitors`, `bot_settings`. ⚠️ `stock_holdings` exists but is **dead** — compute holdings from `stock_trades`. |
| **Reaction roles** | `reaction_roles` |
| **Ops / web** | `error_logs`, `web_events` |

**`odds_snapshots.payload` JSON shape** (standardized across all sources):
```json
{
  "spread": -4.5,
  "spread_odds": -110,
  "ml_home": -180,
  "ml_away": +155,
  "total": 224.5,
  "total_over_odds": -110,
  "total_under_odds": -110
}
```

Core columns: `games(game_id, home_team, away_team, start_time, sport, season, status)`;
`bets(bet_id, game_id, placed_at, discord_user, book, market, side, line, odds, units, status, clv, notes)`.
American odds in the DB; convert to implied probability for Discord embeds.

---

## Odds Sources

### The Odds API
- Endpoint: `GET /v4/sports/basketball_nba/odds`
- Returns spread, ML, total for all major books in one call
- Env var: `ODDS_API_KEY`
- Free tier: 500 requests/month. Current poll interval: 30 min. Cache aggressively.

### Kalshi
- Base URL: `https://api.elections.kalshi.com/trade-api/v2`
- NBA game contracts: yes/no prices in probability (0–1). Convert to American for display.
- Env var: `KALSHI_API_KEY`
- Rate limit: generous, no issues at this scale

### Polymarket
- Base URL: `https://clob.polymarket.com`
- No auth needed for reads
- Prices in probability (0–1). Convert to American for display.

### balldontlie
- Base URL: `https://api.balldontlie.io/v1`
- Free, no key. Games, teams, scores.
- Use to replace the `fetch_games_for_today` stub in `temporal/activities.py`

---

## How to Add a New Odds Source

1. Add `@activity.defn` function in `temporal/activities.py` returning `OddsBatch`
2. Register it in `temporal/worker.py`
3. Call it from `OddsPollingWorkflow` in `temporal/workflows.py`
4. Add env var to `.env.example` with a comment
5. Add a test stub following the pattern in `tests/`

---

## VPS Deployment

SharpLab runs on a shared Hetzner VPS (`87.99.136.82`). Full details in [`docs/vps-hosting.md`](docs/vps-hosting.md).

**Quick reference:**
- **SSH**: `ssh root@87.99.136.82`
- **Install dir**: `/opt/sharplab/` (venv, .env, data/)
- **Services**: `temporal.service` → `sharplab-worker` + `sharplab-bot` + `sharplab-web`
- **Deploy**: `git pull` → `pip install -e .` → restart services (temporal first, wait 3s, then bot+worker+web). Sentinel is decommissioned, so deploys are manual — see `/deploy` skill.
- **Logs**: `journalctl -u sharplab-bot.service -n 50 --no-pager`
- **DB**: SQLite at `/opt/sharplab/data/sharplab.db`

**Rules**: Never restart sentinel/guardian/stavid. Always restart temporal before bot+worker. Verify with status + logs after deploy.

---

## Skills

Slash commands in `.claude/commands/`. Type to invoke.

- `/fresh-eyes` — re-orient at session start. Check git, status, stubs, what's next.
- `/new-source` — scaffold a new odds source end-to-end.
- `/new-game` — scaffold a new casino game cog. **Always run this when adding a game.** Reads `GAMES.md` checklist.
- `/new-web-game` — scaffold a browser-based casino game (WebSocket gameplay).
- `/clv-check` — compute CLV for recent bets against close snapshots.
- `/sanity-check` — adversarial data quality pass before trusting results.
- `/pre-deploy` — pre-deployment checklist. **Always run before deploying to VPS.**
- `/deploy` — the full ship flow: branch → PR → merge → VPS pull + restart → announce.
- `/debug-discord` — common Discord.py interaction bugs and fixes.
- `/vps` — VPS operations: pull logs, deploy, check status, troubleshoot.

---

## Development Philosophy

- **Incremental over one-shot.** Build one small piece, verify it works, then move on. Don't try to wire everything at once.
- **Test constantly.** Every new function gets a unit test. Run `uv run pytest` after every meaningful change.
- **Write the test first when debugging.** If something is broken, write a failing test that reproduces it before touching production code.
- **Delegate test-driven debugging to subagents.** When a bug is hard to pin down, write a test that reproduces it, then ask a subagent to fix it by running that test repeatedly until it passes.
- **Never trust untested code.** Stubs and scaffolding are placeholders — mark them clearly and don't build on top of them until they're verified.
- **Commit and push often.** Any time a unit of work is done, tests pass, or context is switching — commit and push. Good snapshots are cheap insurance. Prefer many small commits over one large one.

---

## Conventions

- **All times UTC**, ISO 8601. Never store local time. Never `datetime.now()` without `tz=timezone.utc`.
- **American odds in DB, implied probability in Discord embeds** (`fmt_prob()` in `shared/odds_utils.py`). Prefix API returns American. Convert Kalshi/Polymarket probabilities at the boundary.
- **Units not dollars** for sizing. 1 unit = whatever baseline is set in config.
- **No API keys in code.** `.env` + `python-dotenv`. `.env` is gitignored.
- **Async everywhere** in pipeline and bot.
- **All DB access through `db/queries.py`.** No raw SQL in `temporal/`, `bot/`, or anywhere else.
- **One activity per API call** in the pipeline.
- **`shared/odds_utils.py`** is the only place that does odds format conversion. Don't duplicate that logic.
- **Temporal workflows are deterministic.** No `datetime.now()`, no `random`, no HTTP inside `@workflow.defn`. Activities only.

---

## Preventing Mistakes

- **Never commit `.env`.** Verify `.gitignore` before touching any key file.
- **`workflow.now()` not `datetime.now()`** inside Temporal workflows.
- **The Odds API has a monthly request quota.** Don't poll every minute. Current interval is 30 min.
- **Kalshi and Polymarket return probabilities, not American odds.** Always convert in `shared/odds_utils.py` before storing or displaying.
- **SQLite + concurrent writes = WAL mode.** Enable with `PRAGMA journal_mode=WAL` on DB init. The pipeline and the bot both write.
- **Validate after wiring a new source.** Are prices in range? Are game IDs consistent with what's in the `games` table?
- **If CLV is consistently > 10%, something is broken**, not your edge.
- **Discord slash commands must be synced** after adding new ones: `await bot.tree.sync()`. Don't forget this or the commands won't appear.

---

## Documentation

- [`docs/vps-hosting.md`](docs/vps-hosting.md) — VPS deployment: services, deploy flow, logs, troubleshooting
- [`SPEC.md`](SPEC.md) — Technical specification for the trading pipeline
- [`TRADING_FLOOR.md`](TRADING_FLOOR.md) — Sports betting concepts and strategies
- [`GAMES.md`](GAMES.md) — Checklist for adding new casino games
- [`FUTURE_GAMES.md`](FUTURE_GAMES.md) — Backlog of games to implement

---

## Memory & Session Continuity

- `memory/status.md` — what's built, what's stubbed, what's next. Update at end of each session.
- `/fresh-eyes` at the start of every session.
- Check git log for commits since last session before touching anything.

---

## Testing Notes

- **Unit tests** (`test_activities.py`) run fast with no dependencies. Always run these first.
- **Workflow tests** (`test_workflows.py`) use `WorkflowEnvironment.start_time_skipping()` which downloads a Temporal test server binary on first run. This can hang for several minutes. Run them separately and be patient.
- **Temporal can't deserialize `Optional[X]` / `X | None` return types from activities.** Use `list[X]` instead — empty list = no result, one-item list = result. This is a known SDK limitation.
- Run unit tests with: `uv run pytest tests/test_activities.py -v`
- Run workflow tests with: `uv run pytest tests/test_workflows.py -v -s` (the `-s` shows Temporal logs)
