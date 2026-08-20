#!/usr/bin/env bash
# Safe web deploy, run ON the VPS from /opt/sharplab.
#
#   1. PRE-FLIGHT: import-check the new code before touching the live service — a syntax/import
#      error aborts with ZERO downtime (the running service is never restarted).
#   2. Restart sharplab-web, then poll /api/v1/health.
#   3. If it doesn't come healthy, AUTO-ROLL-BACK to the previous commit and restart.
#   4. Only then restart the worker + bot.
#
# In-flight bets are refunded on the graceful shutdown (see web/inflight.py), so the restart in
# step 2 never eats a player's ante. Combined: a bad deploy can't take the site down or lose coins.
set -uo pipefail
cd /opt/sharplab || exit 2

OLD=$(git rev-parse HEAD)
echo "▶ current: $(git log --oneline -1)"
git fetch origin main -q
git reset --hard origin/main -q
NEW=$(git rev-parse HEAD)
echo "▶ target:  $(git log --oneline -1)"

# shellcheck disable=SC1091
source venv/bin/activate
pip install -e . -q 2>/dev/null || true

health() {
  for _ in $(seq 1 20); do
    curl -sf -m 3 http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

# 1) PRE-FLIGHT
if ! python -c "import web.api" 2>/tmp/preflight.err; then
  echo "✗ PRE-FLIGHT FAILED — new code won't import. Reverting; live service untouched:"
  sed -n '1,15p' /tmp/preflight.err
  git reset --hard "$OLD" -q
  exit 1
fi

# 2) restart + 3) health-gate / rollback
systemctl restart sharplab-web
if health; then
  echo "✓ sharplab-web healthy at $NEW"
else
  echo "✗ sharplab-web UNHEALTHY — rolling back to $OLD"
  git reset --hard "$OLD" -q
  pip install -e . -q 2>/dev/null || true
  systemctl restart sharplab-web
  if health; then echo "↩ rolled back to $OLD (healthy)"; else echo "‼ ROLLBACK ALSO UNHEALTHY — manual intervention needed"; fi
  exit 1
fi

# 4) non-user-facing services (safe to restart once web is confirmed good)
for svc in sharplab-worker sharplab-bot; do
  systemctl restart "$svc" && echo "  ✓ $svc" || echo "  ✗ $svc"
done
echo "✓ deploy complete: $NEW"
