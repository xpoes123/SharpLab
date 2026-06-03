# VPS Hosting

SharpLab runs on a shared Hetzner VPS alongside other projects (Sage, Stavid, etc.).
This doc covers everything needed to deploy, inspect, and troubleshoot SharpLab in
production. The `justfile` is the source of truth for the deploy mechanics — when in
doubt, read it.

## Host

- **Address**: `root@87.99.136.82`
- **SSH**: `ssh root@87.99.136.82` (key at `~/.ssh/id_ed25519`; use the `/vps` skill)
- **Domain**: `sharplab.djiang.xyz` (Caddy reverse proxy, auto-HTTPS, fronts the web service)
- **Install dir**: `/opt/sharplab/` — contains the git checkout, `venv/`, `.env`, and `data/`
- **Database**: SQLite at `/opt/sharplab/data/sharplab.db` (WAL mode)

## Services (systemd)

Four units back SharpLab. `temporal` is the shared dependency; the worker and bot
both connect to it, so it must be up first.

| Unit | What it runs |
|---|---|
| `temporal.service` | Temporal dev server (shared dependency) |
| `sharplab-worker.service` | Temporal worker — runs pipeline activities/workflows |
| `sharplab-bot.service` | Discord bot |
| `sharplab-web.service` | FastAPI + WebSocket web server (`web.api:app`) |

The deploy/status recipes use `SERVICES := "sharplab-bot sharplab-worker sharplab-web"`.

## Deploy

> **Sentinel is decommissioned** — there is no auto-deploy on merge, no PR announcer,
> and no error forwarder. **Deploys are manual.** Use the `/deploy` skill or `just deploy`.

### Easiest: `just deploy`

From the local repo (after the PR is merged to `main` and your local `main` is current):

```bash
just deploy        # push, then on the VPS: fetch main → reset --hard → pip install -e . → restart all services
just deploy-bot    # same, but only restarts sharplab-bot
just status        # service status + recent bot/web logs
```

### Manual (matches `just deploy`)

```bash
ssh root@87.99.136.82 "cd /opt/sharplab && \
  git fetch origin main && git checkout main --force && git reset --hard origin/main && \
  source venv/bin/activate && pip install -e . -q && \
  systemctl restart temporal && sleep 3 && \
  systemctl restart sharplab-worker sharplab-bot sharplab-web"
```

**Rules:**
- Always restart `temporal` **before** the worker/bot, and wait ~3s in between (they depend on it).
- Deploy from `main` only — the recipe force-resets to `origin/main`.
- After deploying, verify: every service should be `active (running)`, and bot logs should show a clean startup.

### After a deploy — announce (optional)

Post a Claude-written update to the Discord deploy channel:

```bash
ssh root@87.99.136.82 "cd /opt/sharplab && source venv/bin/activate && python scripts/announce_deploy.py --post"
```

Judge announce-worthiness per PR via an `Announce: yes|no` trailer in the commit body.

**Quiet hours:** `announce_deploy.py --post` run between **10pm and 8am ET** does NOT
post — it holds the announcement (leaves the marker unadvanced) so the server isn't
pinged overnight. A cron flushes it in the morning by re-running `--post`, which
sweeps up everything accumulated overnight. Pass `--force` to post immediately
regardless of the hour.

```cron
# /etc/cron.d/sharplab-announce — post held overnight announcements in the morning.
# Two entries cover EST/EDT; the script's ET quiet-hours guard posts only once 8am ET passes.
0 12 * * * root cd /opt/sharplab && venv/bin/python scripts/announce_deploy.py --post >> /var/log/sharplab-announce.log 2>&1
0 13 * * * root cd /opt/sharplab && venv/bin/python scripts/announce_deploy.py --post >> /var/log/sharplab-announce.log 2>&1
```

## Logs

```bash
# Bot + worker, last 50 lines
ssh root@87.99.136.82 "journalctl -u sharplab-bot.service -u sharplab-worker.service -n 50 --no-pager"

# Web server
ssh root@87.99.136.82 "journalctl -u sharplab-web.service -n 50 --no-pager"

# Errors only
ssh root@87.99.136.82 "journalctl -u sharplab-bot.service -p err -n 50 --no-pager"

# Follow live (streams indefinitely — Ctrl+C to stop)
ssh root@87.99.136.82 "journalctl -u sharplab-bot.service -f"
```

## Health check

```bash
ssh root@87.99.136.82 "systemctl status sharplab-bot sharplab-worker sharplab-web temporal --no-pager && \
  echo '--- RAM ---' && free -h && \
  echo '--- DISK ---' && df -h / && \
  echo '--- DB ---' && ls -lh /opt/sharplab/data/sharplab.db"
```

## Troubleshooting

1. **Are the services running?** `systemctl is-active` each one.
2. **Is temporal healthy?** Bot and worker depend on it — if it's down, restart it first, wait 3s, then restart the others.
3. **Recent logs**, errors first (`-p err`). Look for OOM kills, Python tracebacks, connection errors.
4. **Disk / RAM** — a full disk or OOM is a common silent killer on a shared box.
5. Usual fix is a restart, but investigate first so you don't paper over a real bug.

## Hard rules

- **Never restart `sentinel`, `guardian`, or `stavid`** services — those aren't ours.
- **Always restart `temporal` before** the worker/bot/web, and wait ~3s.
- **Always verify** after a deploy/restart (status + recent logs).
- **Don't touch `/opt/sharplab/.env`** unless explicitly asked — it holds secrets.
- **Back up the DB** before risky schema work: `scripts/backup_db.sh` (writes to `backups/`).
