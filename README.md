# SharpLab

A personal quantitative sports betting research lab. Durable automation, interpretable models, disciplined evaluation.

Not a gambling product. Not a SaaS. A long-term quant notebook with real infrastructure.

---

## What it is

SharpLab automates the unglamorous parts of sports modeling:

- **Odds ingestion** — snapshot lines across books every 15–30 minutes
- **Close capture** — lock in the final line at game time for every tracked game
- **CLV tracking** — measure every prediction against the closing line
- **Evaluation** — weekly reports on calibration, margin error, and beat-close rate

The goal is a system that runs quietly in the background and generates a disciplined paper trail. Model sophistication comes later. Automation and evaluation discipline come first.

---

## Why Temporal

All pipeline automation runs on [Temporal](https://temporal.io) — a durable workflow engine.

Temporal handles:

- Scheduled odds polling (survives crashes and restarts)
- Per-game close capture (sleeps until tip-off, then fires exactly once)
- Retry logic for flaky API calls
- Clean separation between orchestration and side effects

This also serves as a hands-on learning project for distributed workflow design — relevant professional context for working with Temporal at scale.

---

## Architecture

```
┌─────────────────────────────────────┐
│           Temporal Worker           │
│                                     │
│  OddsPollingWorkflow                │
│    └─ fetch games                   │
│    └─ snapshot odds  (every N min)  │
│    └─ persist snapshot              │
│                                     │
│  CloseCaptureWorkflow  (per-game)   │
│    └─ sleep until tip-off           │
│    └─ capture final close           │
│    └─ persist snapshot              │
└─────────────────────────────────────┘
          │
          ▼
┌──────────────────┐    ┌──────────────────────┐
│   Data Layer     │    │   Modeling Layer      │
│                  │    │                       │
│  games           │    │  Runs once daily      │
│  odds_snapshots  │    │  Logs features +      │
│  bets            │    │  version + prediction │
│  predictions     │    │  Walk-forward eval    │
│  results         │    │                       │
└──────────────────┘    └──────────────────────┘
```

**Key constraint:** No modeling logic inside workflows. No orchestration logic inside models. Clean separation throughout.

---

## Evaluation

Primary metric: **Closing Line Value (CLV)**

> Did we beat the market before it closed?

Secondary metrics:
- Margin MAE
- Calibration (Brier score / log loss)
- Beat-close rate distribution

ROI alone is not a valid performance signal at low sample sizes. CLV consistency is.

---

## Stack

| Layer | Tech |
|---|---|
| Language | Python 3.14 |
| Workflow orchestration | [Temporal](https://temporal.io) (`temporalio`) |
| Initial model | Baseline Elo / regression margin |
| Database | TBD (Postgres likely) |

---

## Project Status

Early infrastructure phase. Stubs in place, real data sources not yet wired.

**Current priorities:**
1. Temporal fundamentals — durable loops, crash recovery, schedule management
2. Real odds ingestion (replacing stubs)
3. Close capture automation
4. Baseline margin model
5. CLV logging
6. Weekly evaluation reports

**Explicitly deferred:**
- UI / dashboards
- Live betting
- Advanced ML (GBMs, neural nets)
- Multi-league expansion

Scope increases only after automation is stable and 100+ bets are logged.

---

## Getting Started

```bash
# Install dependencies (requires uv)
uv sync

# Start a local Temporal server
temporal server start-dev

# Start the worker
python -m temporal.worker

# Start odds polling
python -m temporal.start_odds_polling

# Start close capture for a game
python -m temporal.start_close_capture
```

---

## Design Principles

**Observability over magic.** Every prediction must answer: what features were used, what data timestamp, what changed since last week, did we beat closing line.

**Interpretable before complex.** Linear models before gradient boosting. Correct evaluation before model sophistication.

**Automation before modeling.** A stable pipeline with a weak model beats a great model with manual processes.

**Flat sizing until sample is large enough.** No confidence-based multipliers. No emotional scaling.
