# SharpLab — task runner
# Install just: winget install Casey.Just

set shell := ["bash", "-c"]

# List available recipes
default:
    @just --list

# ── Dev ────────────────────────────────────────────────────────────────────────

# Start everything: Temporal server + worker + odds poller + injury poller + bet resolver + Discord bot
dev:
    #!/usr/bin/env bash
    trap 'echo "Shutting down..."; kill $(jobs -p) 2>/dev/null' INT TERM EXIT
    echo "▶ Starting Temporal server..."
    temporal server start-dev &
    echo "⏳ Waiting for Temporal to be ready..."
    sleep 5
    echo "▶ Starting Temporal worker..."
    uv run python -m temporal.worker &
    sleep 2
    echo "▶ Starting NBA odds polling workflow..."
    uv run python -m temporal.start_odds_polling nba
    echo "▶ Starting MLB odds polling workflow..."
    uv run python -m temporal.start_odds_polling mlb
    echo "▶ Starting injury polling workflow..."
    uv run python -m temporal.start_injury_polling
    echo "▶ Starting NBA bet resolution workflow..."
    uv run python -m temporal.start_bet_resolution nba
    echo "▶ Starting MLB bet resolution workflow..."
    uv run python -m temporal.start_bet_resolution mlb
    echo "▶ Starting Discord bot..."
    uv run python -m bot.main &
    echo "✓ All services running. Ctrl+C to stop."
    wait

# ── Individual services ────────────────────────────────────────────────────────

# Start the Temporal dev server
temporal:
    temporal server start-dev

# Start the Temporal worker
worker:
    uv run python -m temporal.worker

# Kick off the odds polling workflow (one-shot — Temporal keeps it running)
poll sport="nba":
    uv run python -m temporal.start_odds_polling {{sport}}

# Kick off the injury polling workflow (one-shot — Temporal keeps it running)
injuries:
    uv run python -m temporal.start_injury_polling

# Kick off the bet resolution workflow (one-shot — Temporal keeps it running)
resolve sport="nba":
    uv run python -m temporal.start_bet_resolution {{sport}}

# Start the Discord bot
bot:
    uv run python -m bot.main

# ── Tests ──────────────────────────────────────────────────────────────────────

# Run unit tests (fast, no Temporal server needed)
test:
    uv run pytest tests/test_activities.py -v

# Run all tests (workflow tests will download a Temporal test server on first run)
test-all:
    uv run pytest -v -s

# ── Misc ───────────────────────────────────────────────────────────────────────

# Install dependencies
install:
    uv sync
