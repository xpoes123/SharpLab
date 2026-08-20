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

# Start the web leaderboard API (dev mode with auto-reload)
web:
    uv run uvicorn web.api:app --host 127.0.0.1 --port 8000 --reload

# ── Tests ──────────────────────────────────────────────────────────────────────

# Run unit tests (fast, no Temporal server needed)
test:
    uv run pytest tests/test_activities.py -v

# Run all tests (workflow tests will download a Temporal test server on first run)
test-all:
    uv run pytest -v -s

# ── Deploy ────────────────────────────────────────────────────────────────────

VPS := "root@87.99.136.82"
DEPLOY_PATH := "/opt/sharplab"
SERVICES := "sharplab-bot sharplab-worker sharplab-web"

# Deploy to VPS: push, pull main, install deps, restart all services
deploy:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "▶ Pushing to GitHub..."
    git push
    echo "▶ Safe deploy on VPS (pre-flight import check → restart → /health gate → auto-rollback)..."
    # Fetch the latest deploy script first (self-updating), then run it. It does the pull, the
    # pre-flight, the health-gated web restart with rollback, then the worker/bot. In-flight bets
    # are refunded on the graceful shutdown (web/inflight.py), so no one loses an ante.
    ssh {{VPS}} 'cd {{DEPLOY_PATH}} && git fetch origin main -q && git checkout origin/main -- scripts/vps_web_deploy.sh && bash scripts/vps_web_deploy.sh'

# Deploy bot only (no worker/web restart)
deploy-bot:
    #!/usr/bin/env bash
    set -euo pipefail
    git push
    ssh {{VPS}} bash -s <<'REMOTE'
    set -euo pipefail
    cd {{DEPLOY_PATH}}
    git fetch origin main
    git checkout main --force
    git reset --hard origin/main
    source venv/bin/activate
    pip install -e . -q
    systemctl restart sharplab-bot
    echo "✓ Bot restarted on $(git log --oneline -1)"
    REMOTE

# Show VPS service status and recent logs
status:
    #!/usr/bin/env bash
    ssh {{VPS}} bash -s <<'REMOTE'
    echo "── Services ──"
    for svc in {{SERVICES}}; do
        status=$(systemctl is-active "$svc")
        echo "  $svc: $status"
    done
    echo ""
    echo "── Recent bot logs ──"
    journalctl -u sharplab-bot --no-pager -n 15 --since "10 min ago"
    echo ""
    echo "── Recent web logs ──"
    journalctl -u sharplab-web --no-pager -n 15 --since "10 min ago"
    REMOTE

# SSH into the VPS
ssh:
    ssh {{VPS}}

# ── Misc ───────────────────────────────────────────────────────────────────────

# Install dependencies
install:
    uv sync
