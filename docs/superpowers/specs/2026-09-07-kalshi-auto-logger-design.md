# Kalshi auto-logger + live-edge measurement — design

**Date:** 2026-09-07
**Status:** approved (design), pending implementation
**Author:** David + Claude

## Problem

Bet logging on SharpLab is manual and high-friction, so David stopped doing it — which
means there is no data to measure whether any strategy has edge. He is pivoting toward
**live (in-game) betting on Kalshi** to hunt for alpha, and needs logging that is
effectively zero-effort plus a metric that actually detects live edge.

## Scope

This spec covers **sub-project 1 only: the Kalshi auto-logger + per-position price
tracker + edge metrics.** It is fully self-contained — no cross-venue game matching, no
NFL odds-pipeline work.

**Follow-on (separate spec, not here):** screenshot→vision logging for BetMGM/DraftKings
promo bets, which requires enabling `americanfootball_nfl` in the the-odds-api pipeline so
those bets get a closing line for CLV.

**Non-goals:** historical backfill (start from deploy time forward), automated trading
(this only *records*), sportsbook API integration.

## Key insight: CLV breaks for live bets

CLV (closing-line value) compares entry price to the **closing** line — valid only
pre-game. A live bet has no "close" for the game-state it was placed into. The live analog
is **post-entry price drift**: after the fill, does the market move toward your side? A
consistently positive drift is real live edge and surfaces with far less data than
realized P&L (which is brutally high-variance). This is the primary metric for David's
goal.

One mechanism powers every metric: **track each bet contract's price trajectory from fill
to settlement.** From that single price series we derive CLV, drift, excursions, and P&L.

## Architecture

A long-lived Temporal workflow (same pattern as `start_odds_polling` /
`start_close_capture`), started via `temporal/start_kalshi_logging.py`:

1. **Fills poll** (every ~5 min): `GET /portfolio/fills?min_ts=<watermark>` → dedup by
   `trade_id` → insert into `kalshi_fills` + mirror a row into `bets` (book=`kalshi`).
   Advance watermark. Watermark initialized to deploy time ⇒ "from today forward".
2. **Price tracking** (per open contract): poll the contract orderbook and append to
   `price_trajectory`. Cadence: **every 30s for the first 5 min after each fill, then
   every 2 min** until the market settles/closes. Denser early window captures the fast
   live re-pricing that matters for drift.
3. **Settlement/metrics**: when a market resolves (or at game close), compute final
   metrics from the trajectory, write CLV onto the `bets` row, and mark the fill done.

Reusing Temporal means no new daemon and inherits existing retry/observability.

## Data model (new tables)

```sql
CREATE TABLE kalshi_fills (
    trade_id       TEXT PRIMARY KEY,        -- Kalshi fill id (dedup)
    order_id       TEXT,
    ticker         TEXT NOT NULL,
    action         TEXT NOT NULL,           -- buy | sell
    side           TEXT NOT NULL,           -- yes | no
    count          INTEGER NOT NULL,
    yes_price_c    INTEGER NOT NULL,        -- entry price in YES cents
    created_time   TEXT NOT NULL,           -- Kalshi fill timestamp (UTC iso)
    is_live        INTEGER NOT NULL,        -- 1 if game had started at fill time
    bet_id         INTEGER REFERENCES bets(bet_id),
    settled        INTEGER NOT NULL DEFAULT 0,
    -- cached final metrics (also derivable from price_trajectory):
    clv_c          REAL,                    -- pre-game only: entry vs kickoff price
    drift_5m_c     REAL,                    -- live: (mid@+5m) - entry, YES cents
    mfe_c          REAL, mae_c REAL,        -- max favorable / adverse excursion
    pnl_c          REAL                     -- realized at settle, per contract
);

CREATE TABLE price_trajectory (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL,
    trade_id     TEXT REFERENCES kalshi_fills(trade_id),
    captured_at  TEXT NOT NULL,             -- UTC iso
    yes_bid_c    INTEGER, yes_ask_c INTEGER,
    mid_c        REAL                       -- (bid+ask)/2, the drift reference
);
CREATE INDEX idx_trajectory_trade ON price_trajectory(trade_id, captured_at);
```

`bets` is unchanged; Kalshi fills mirror into it (`book='kalshi'`, `odds`=American from
`yes_price_c`, `units`=dollars risked = `count*yes_price_c/100`, `market`/`side`/`line`
parsed from ticker best-effort, `game_id` matched if trivial else null, `notes`=ticker +
title). A fill is **never dropped** — exotic tickers fall back to `market='kalshi'`,
`side=<ticker>`.

## Metrics (all derived from `price_trajectory`)

| Metric | Applies to | Definition |
|---|---|---|
| CLV | pre-game | `price@kickoff − entry` (YES cents, sign-adjusted for side) |
| **Post-entry drift** | **live** | `mid@(+30s/+2m/+5m) − entry` — the live edge signal |
| MFE / MAE | live | max favorable / adverse `mid − entry` over the hold |
| Realized P&L | all | `(100 or 0) − entry` at settle, per contract, × count |

`is_live` splits reporting: pre-game bets report CLV, live bets report drift. Both report
P&L. A `/bet edge` (or extension of `clv.py`) surfaces aggregates: mean drift, % positive
drift, n — the numbers that answer "is the live edge real."

## Components

- `shared/kalshi.py` (new or ported from live-trader): `KalshiClient` + `.fills(min_ts,
  cursor)` calling `GET /portfolio/fills`.
- `temporal/activities.py`: `poll_kalshi_fills`, `snapshot_ticker_price`,
  `finalize_kalshi_metrics` activities.
- `temporal/workflows.py`: `KalshiLoggingWorkflow` (fills loop + per-position trackers).
- `temporal/start_kalshi_logging.py`: launcher (mirrors `start_odds_polling.py`).
- `db/schema.py`: two new tables + idempotent migrations.
- `db/queries.py`: `insert_kalshi_fill` (dedup), `append_trajectory`,
  `get_open_kalshi_fills`, `finalize_fill_metrics`.
- Reporting: extend `bot/cogs/clv.py` with a live-drift view.

## Config

SharpLab VPS env gains Kalshi API creds (same as live-trader): `KALSHI_KEY_ID`,
`KALSHI_PRIVATE_KEY_PATH`, `KALSHI_BASE`. Private key file copied to the SharpLab VPS
env (never committed).

## Ticker parsing

Parse the common NFL Kalshi series (game winner / spread / total) into
market/side/line; anything unrecognized logs raw so no fill is lost. Parser is a small
pure function with unit tests over real ticker examples.

## Testing

- Pure `parse_kalshi_ticker` unit tests (winner/spread/total + fallback).
- `is_live` detection (fill time vs game start) unit test.
- Metric math (drift/CLV/MFE/MAE/PnL sign handling for YES vs NO side) unit tests on a
  synthetic trajectory.
- Dedup: re-inserting the same `trade_id` is a no-op.
- One end-to-end dry run against the real Kalshi fills endpoint (read-only) after a live
  test bet.

## Rollout

Ship the logger, place one small live Kalshi bet, confirm it auto-logs with a trajectory
and correct drift/P&L, then let data accumulate. Revisit at ~a few hundred live bets to
judge whether drift is significantly positive.
