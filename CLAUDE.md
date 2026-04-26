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

### Local Copies

This project has two local checkouts (same repo, same code):
- **`C:\Users\David\CS\SharpLab\`** — primary
- **`C:\Users\David\CS\hobbies\sports\betlab\`** — alternate working directory

Both point to the same GitHub repo (`xpoes123/sharplab`). Changes in one should be committed+pushed so the other stays in sync.

---

## Repository Layout

```
sharplab/
├── temporal/           # data pipeline — Temporal workflows + activities
│   ├── workflows.py    # orchestration only, no API calls
│   ├── activities.py   # all side effects: API calls, DB writes
│   └── worker.py       # worker entrypoint
├── bot/                # Discord bot
│   ├── main.py         # bot entrypoint
│   └── cogs/
│       ├── odds.py     # /odds, /line-move, /best-line
│       ├── bets.py     # /log, /record
│       ├── markets.py  # /kalshi
│       └── utils.py    # /ev, /kelly, /parlay, /convert
├── db/
│   ├── schema.py       # CREATE TABLE statements + init_db()
│   └── queries.py      # all DB access lives here, nowhere else
├── shared/
│   ├── models.py       # dataclasses shared between pipeline + bot
│   └── odds_utils.py   # American ↔ decimal ↔ implied prob conversions
├── data/
│   └── sharplab.db     # SQLite, source of truth
├── memory/
│   └── status.md       # current project state, updated each session
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

```bash
# Install deps
uv sync

# Start Temporal server (required for pipeline)
temporal server start-dev

# Start the worker
python -m temporal.worker

# Start odds polling workflow
python -m temporal.start_odds_polling

# Start the Discord bot
python -m bot.main

# Run tests
uv run pytest
```

---

## Discord Bot — Features

The bot serves two purposes: **info** (what are the lines, what's the market saying) and **tracking** (logging bets, computing CLV, quick math). No social features.

### Odds & Lines
| Command | What it does |
|---|---|
| `/odds [game]` | Live lines for a game across all major books (spread, ML, total) |
| `/line-move [game]` | How the line has moved since open — reads from `odds_snapshots` history |
| `/best-line [game]` | Surfaces the best number available across all tracked books |

### Bet Tracking
| Command | What it does |
|---|---|
| `/log [game] [book] [market] [side] [line] [odds] [units]` | Log a bet to the DB |
| `/record [@user]` | Pull up a user's W/L record and ROI |

### Kalshi / Prediction Markets
| Command | What it does |
|---|---|
| `/kalshi [market]` | Current yes/no price on a Kalshi contract |
| CLV (auto) | When a game closes, bot posts CLV for anyone who logged a bet on it |

### Utilities (pure math, no API needed)
| Command | What it does |
|---|---|
| `/ev [odds] [true-prob]` | Expected value calculator |
| `/kelly [bankroll] [odds] [edge]` | Kelly criterion stake sizing |
| `/parlay [leg1] [leg2] ...` | Parlay odds calculator |
| `/convert [odds]` | Odds format converter: American ↔ decimal ↔ implied % |

---

## How the Pipeline and Bot Connect

The Temporal pipeline is the **data producer**. The Discord bot is a **read-mostly consumer** on the same DB.

- `/line-move` reads `odds_snapshots` rows the pipeline writes every 15 min
- `/best-line` compares the most recent poll snapshot across sources
- CLV auto-post reads the `close` snapshot the `CloseCaptureWorkflow` writes at tip-off
- `/odds` can either query the DB (fast, slightly stale) or hit The Odds API live (fresh, costs quota)

Default behavior: `/odds` and `/best-line` hit the API live. `/line-move` reads the DB history.

---

## Database Schema

```sql
CREATE TABLE games (
    game_id     TEXT PRIMARY KEY,
    home_team   TEXT NOT NULL,
    away_team   TEXT NOT NULL,
    start_time  TEXT NOT NULL,   -- UTC ISO 8601
    season      TEXT,
    status      TEXT DEFAULT 'scheduled'  -- scheduled | live | final
);

CREATE TABLE odds_snapshots (
    snapshot_id  TEXT PRIMARY KEY,
    game_id      TEXT REFERENCES games(game_id),
    kind         TEXT NOT NULL,   -- 'poll' | 'close'
    source       TEXT NOT NULL,   -- 'draftkings' | 'fanduel' | 'kalshi' | 'polymarket' | ...
    captured_at  TEXT NOT NULL,   -- UTC ISO 8601
    payload      TEXT NOT NULL    -- JSON: {spread, spread_odds, ml_home, ml_away, total, total_odds}
);

CREATE TABLE bets (
    bet_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id       TEXT REFERENCES games(game_id),
    placed_at     TEXT NOT NULL,   -- UTC ISO 8601
    discord_user  TEXT NOT NULL,
    book          TEXT NOT NULL,   -- 'draftkings' | 'fanduel' | 'kalshi' | ...
    market        TEXT NOT NULL,   -- 'spread' | 'moneyline' | 'total' | 'kalshi'
    side          TEXT NOT NULL,   -- team name, 'over', 'under', 'yes', 'no'
    line          REAL,            -- spread or total number (null for ML/kalshi)
    odds          INTEGER NOT NULL, -- American odds (-110, +150, etc.)
    units         REAL NOT NULL,
    status        TEXT DEFAULT 'open',  -- open | won | lost | push | void
    clv           REAL,            -- filled after close, positive = beat the close
    notes         TEXT
);
```

**payload JSON shape** (standardized across all sources):
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
- **Services**: `temporal.service` → `sharplab-worker.service` + `sharplab-bot.service`
- **Deploy**: `git pull` → `pip install -e .` → restart services (temporal first, wait 3s, then bot+worker)
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
