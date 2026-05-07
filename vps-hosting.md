# SharpLab VPS Hosting Guide

> How SharpLab is deployed and operated on the Hetzner VPS.

---

## VPS Overview

| Detail | Value |
|--------|-------|
| **Provider** | Hetzner |
| **OS** | Ubuntu 24.04 LTS |
| **IP** | `87.99.136.82` |
| **SSH** | `ssh root@87.99.136.82` (key-based auth) |
| **RAM** | 2GB total (~750MB used by all services) |
| **Disk** | 38GB total (~31GB free) |
| **No swap** | Consider adding 1-2GB if RAM gets tight |
| **No firewall** | UFW inactive — all ports exposed |

---

## Directory Layout

```
/opt/sharplab/
├── bot/            # Discord bot code
├── temporal/       # Temporal workflows + worker
├── shared/         # Shared utilities
├── db/             # Database layer
├── data/           # Runtime data (sharplab.db, temporal.db)
├── tests/          # Test suite
├── venv/           # Python virtualenv
├── pyproject.toml  # Project config
└── .env            # Environment variables (secrets)
```

---

## Services

Three systemd services run SharpLab:

### `sharplab-bot.service`
- **Runs**: `python -u -m bot.main`
- **WorkingDirectory**: `/opt/sharplab`
- **Depends on**: `temporal.service`
- **Restart**: always (5s delay)

### `sharplab-worker.service`
- **Runs**: `python -m temporal.worker`
- **WorkingDirectory**: `/opt/sharplab`
- **Depends on**: `temporal.service`
- **Restart**: always (5s delay)

### `temporal.service`
- **Runs**: `temporal server start-dev`
- **Ports**: 7233 (gRPC), 8233 (UI) — localhost only
- **DB**: SQLite at `/opt/sharplab/data/temporal.db`

### Service unit file example

```ini
# /etc/systemd/system/sharplab-bot.service
[Unit]
Description=SharpLab Discord Bot
After=temporal.service
Requires=temporal.service

[Service]
Type=simple
WorkingDirectory=/opt/sharplab
ExecStart=/opt/sharplab/venv/bin/python -u -m bot.main
Restart=always
RestartSec=5
EnvironmentFile=/opt/sharplab/.env

[Install]
WantedBy=multi-user.target
```

---

## Other Services on the VPS

Do NOT disrupt these — they share the server:

| Service | Owner | Notes |
|---------|-------|-------|
| `sentinel.service` | Sentinel | AI engineering bot |
| `guardian.service` | Sentinel | Watchdog/auto-restart |
| `stavid.service` | Stavid | Couple's Discord bot (uses PostgreSQL) |
| Vencord bridge (port 7777) | Sentinel | Message ingestion |
| PostgreSQL (port 5432) | Stavid | localhost only |

---

## Deploy Flow

Standard deploy from local machine or CI:

```bash
# 1. SSH into VPS
ssh root@87.99.136.82

# 2. Pull latest code
cd /opt/sharplab
git pull origin main

# 3. Install dependencies
/opt/sharplab/venv/bin/pip install -e .

# 4. Restart services (order matters — temporal first)
systemctl restart temporal.service
sleep 3
systemctl restart sharplab-worker.service sharplab-bot.service

# 5. Verify
systemctl status sharplab-bot.service sharplab-worker.service temporal.service
journalctl -u sharplab-bot.service --no-pager -n 20
```

---

## Useful Commands

### Logs
```bash
# Bot logs (last 50 lines, follow)
journalctl -u sharplab-bot.service -n 50 -f

# Worker logs
journalctl -u sharplab-worker.service -n 50 -f

# Temporal server logs
journalctl -u temporal.service -n 50 -f

# All SharpLab logs combined
journalctl -u sharplab-bot.service -u sharplab-worker.service -u temporal.service -n 100 --no-pager

# Logs since a specific time
journalctl -u sharplab-bot.service --since "1 hour ago" --no-pager

# Errors only
journalctl -u sharplab-bot.service -p err --no-pager -n 50
```

### Service Management
```bash
# Status check
systemctl status sharplab-bot.service sharplab-worker.service temporal.service

# Restart all SharpLab services
systemctl restart temporal.service && sleep 3 && systemctl restart sharplab-worker.service sharplab-bot.service

# Stop all SharpLab services
systemctl stop sharplab-bot.service sharplab-worker.service temporal.service

# Start all SharpLab services
systemctl start temporal.service && sleep 3 && systemctl start sharplab-worker.service sharplab-bot.service
```

### Database
```bash
# Check DB size
ls -lh /opt/sharplab/data/sharplab.db

# Quick query (if sqlite3 is installed)
sqlite3 /opt/sharplab/data/sharplab.db "SELECT COUNT(*) FROM games;"
sqlite3 /opt/sharplab/data/sharplab.db "SELECT COUNT(*) FROM odds_snapshots;"
```

### System Health
```bash
# RAM usage
free -h

# Disk usage
df -h /

# All running services
systemctl list-units --type=service --state=running
```

---

## Network & Ports

| Port | Service | Scope |
|------|---------|-------|
| 22 | SSH | Public |
| 7233 | Temporal gRPC | localhost only |
| 8233 | Temporal UI | localhost only |
| 80/443 | *Free* | Not in use |

---

## Domain

- **Domain**: `djiang.xyz`
- **DNS**: GoDaddy
- **Status**: A record needs pointing to `87.99.136.82`
- **Subdomain plan**: `sharplab.djiang.xyz` for web UI (if/when built)
- See DNS setup details in the original hosting guide if needed

---

## Gotchas

- **RAM is tight** (2GB shared across 4+ bots). Don't install heavy services.
- **No Docker** — everything runs via systemd + venvs. Keep it that way.
- **Don't restart** sentinel/guardian/stavid services unless absolutely necessary.
- **Temporal must start before** bot and worker (dependency chain in systemd).
- **`.env` on VPS** is at `/opt/sharplab/.env` — never commit this file.
- **venv is at `/opt/sharplab/venv/`** — use its pip/python, not system Python.
- **Git remote** on VPS should point to the GitHub repo for `git pull` deploys.
