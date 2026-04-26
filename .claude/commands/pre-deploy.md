# Pre-Deploy Checklist

Run this before deploying to the VPS. Catches the issues that have broken deploys repeatedly.

## 1. Check for breaking Temporal changes

Look at what changed since the last deploy:

```bash
git log --oneline origin/main..HEAD -- temporal/
```

If **any workflow or activity signatures changed** (added/removed params, renamed, changed return types):
- All running workflows MUST be terminated before deploying
- `ssh root@87.99.136.82 "temporal workflow terminate --workflow-id odds-polling-nba-v2 && temporal workflow terminate --workflow-id odds-polling-mlb-v2 && temporal workflow terminate --workflow-id bet-resolution-nba-v2 && temporal workflow terminate --workflow-id bet-resolution-mlb-v2 && temporal workflow terminate --workflow-id injury-polling-v2"`
- If unsure whether signatures changed: **terminate anyway**. It's always safe.

## 2. Check for new DB columns/tables

```bash
git diff origin/main..HEAD -- db/schema.py
```

- New `ALTER TABLE` or `CREATE TABLE` statements → the worker calls `init_db()` on startup, so migrations run automatically.
- **But**: if you renamed or dropped a column, you need a manual migration. SQLite doesn't support `ALTER TABLE DROP COLUMN` cleanly.

## 3. Check for new env vars

```bash
git diff origin/main..HEAD -- .env.example
```

- Any new vars → SSH in and add them to `/opt/sharplab/.env` before restarting.

## 4. Check for new dependencies

```bash
git diff origin/main..HEAD -- pyproject.toml
```

- New deps → need `pip install -e .` on VPS after `git pull`.

## 5. Check for new Caddy routes (web games)

```bash
git diff origin/main..HEAD -- docs/vps-hosting.md
```

- New web game or route → update `/etc/caddy/Caddyfile` on VPS and `systemctl reload caddy`.

## 6. Run tests locally first

```bash
uv run pytest tests/ -v
```

- Do NOT deploy if tests fail. Fix first.

## 7. Deploy

```bash
ssh root@87.99.136.82
cd /opt/sharplab && git pull
pip install -e .
systemctl restart temporal  # ALWAYS restart temporal first
sleep 3
systemctl restart sharplab-worker sharplab-bot sharplab-web
```

## 8. Verify

```bash
systemctl status sharplab-bot sharplab-worker sharplab-web
journalctl -u sharplab-bot -n 20 --no-pager
journalctl -u sharplab-worker -n 20 --no-pager
```

- Confirm no crash loops (check for rapid restart cycles)
- Confirm workflow starters re-registered (check worker logs for "started workflow")
