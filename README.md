# SharpLab

A personal quantitative sports betting research lab. Durable automation, interpretable models, disciplined evaluation.

Not a gambling product. Not a SaaS. A long-term quant notebook with real infrastructure.

---

## What it is

SharpLab automates the unglamorous parts of sports modeling:

- **Odds ingestion** — snapshot lines across all major books every 30 minutes via [The Odds API](https://the-odds-api.com)
- **Close capture** — lock in the final line at tip-off for every tracked game
- **CLV tracking** — measure every bet against the closing line
- **Discord bot** — log bets, pull live lines, run quick math from your server

The goal is a system that runs quietly in the background and generates a disciplined paper trail. Model sophistication comes later. Automation and evaluation discipline come first.

---

## Why Temporal

All pipeline automation runs on [Temporal](https://temporal.io) — a durable workflow engine.

Temporal handles:

- Scheduled odds polling (survives crashes and restarts)
- Per-game close capture (sleeps until tip-off, then fires exactly once)
- Retry logic for flaky API calls
- Clean separation between orchestration and side effects

---

## Architecture

```
┌─────────────────────────────────────────┐
│            Temporal Worker              │
│                                         │
│  OddsPollingWorkflow  (every 30 min)    │
│    └─ fetch today's games               │
│    └─ snapshot odds (all books)         │
│    └─ persist per-game, per-book        │
│    └─ spawn CloseCaptureWorkflow        │
│                                         │
│  CloseCaptureWorkflow  (per-game)       │
│    └─ sleep until tip-off               │
│    └─ capture final closing line        │
│    └─ persist close snapshot            │
└─────────────────────────────────────────┘
               │
               ▼
   ┌───────────────────────┐
   │   SQLite (WAL mode)   │
   │                       │
   │  games                │
   │  odds_snapshots       │
   │  bets                 │
   └───────────────────────┘
               │
               ▼
   ┌───────────────────────┐
   │    Discord Bot        │
   │                       │
   │  /odds  /line-move    │
   │  /log   /record       │
   │  /ev    /kelly        │
   │  /convert  /kalshi    │
   └───────────────────────┘
```

---

## Stack

| Layer | Tech |
|---|---|
| Language | Python 3.14, async everywhere |
| Package manager | `uv` |
| Pipeline orchestration | [Temporal](https://temporal.io) (`temporalio`) |
| Discord | `discord.py` with slash commands |
| HTTP client | `httpx` (async) |
| Database | SQLite (`aiosqlite`), WAL mode |
| Odds | [The Odds API](https://the-odds-api.com) — all major US books in one call |
| NBA schedule | The Odds API events endpoint (exact tip-off times) |

---

## Data Sources

| Source | Used for | Auth |
|---|---|---|
| The Odds API | Game schedule + live odds (spread, ML, total) across all books | `ODDS_API_KEY` |
| Kalshi | NBA game contracts (yes/no prices) | `KALSHI_API_KEY` (coming soon) |
| Polymarket | Secondary market signal | None (public) |

**Quota management:** The Odds API free tier is 500 req/month. Smart polling — only on game days, 30-min intervals during game window — keeps usage around 360 req/month.

---

## Project Status

Pipeline complete. Bot partially built.

**Done:**
- ✅ Temporal workflows (`OddsPollingWorkflow`, `CloseCaptureWorkflow`)
- ✅ Real odds ingestion via The Odds API (per-game, per-book snapshots)
- ✅ SQLite persistence layer (`db/schema.py`, `db/queries.py`)
- ✅ Shared model + odds conversion utilities (`shared/`)
- ✅ Unit tests for payload extraction and odds math
- ✅ Discord bot skeleton (`bot/main.py`)
- ✅ Pure-math commands: `/convert`, `/ev`, `/kelly`, `/parlay`
- ✅ `/odds` — current lines across all books (reads from DB, 30 min freshness)
- ✅ `/best-line` — best spread, ML, and total across all books

**Up next:**
- `/log` and `/record` (bet tracking)
- `/line-move` (reads pipeline history from DB)
- CLV auto-post on game close

---

## Getting Started

```bash
# Install dependencies (requires uv)
uv sync

# Copy env template and fill in your keys
cp .env.example .env

# Start a local Temporal server
temporal server start-dev

# Start the worker (also initialises the DB)
python -m temporal.worker

# Start odds polling (30-min interval)
python -m temporal.start_odds_polling
```

### Environment variables

```
ODDS_API_KEY=        # https://the-odds-api.com
KALSHI_API_KEY=      # https://kalshi.com (optional for now)
DISCORD_BOT_TOKEN=   # https://discord.com/developers
```

### Running tests

```bash
# Unit tests (fast, no external deps)
uv run pytest tests/test_activities.py -v

# Workflow tests (downloads Temporal test server on first run — slow)
uv run pytest tests/test_workflows.py -v -s
```

---

## Design Principles

**Observability over magic.** Every bet must answer: what line did we get, what did the market close at, did we beat it.

**Automation before modeling.** A stable pipeline with no model beats a great model with manual processes.

**CLV over ROI at small sample sizes.** ROI is noise below ~500 bets. Closing line value is signal.

**Flat sizing until the sample is large enough.** No confidence-based multipliers until the edge is demonstrated.
