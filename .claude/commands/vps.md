VPS operations — pull logs, deploy, check status, or troubleshoot SharpLab on the Hetzner VPS.

Read `docs/vps-hosting.md` first for full context.

## What you can do

The user will ask for one of these. Figure out which and do it:

### Pull logs
SSH into `root@87.99.136.82` and run journalctl for the relevant service(s):
- `sharplab-bot.service` — Discord bot
- `sharplab-worker.service` — Temporal worker
- `temporal.service` — Temporal server

Default: show last 50 lines of bot + worker. If the user says "errors", filter with `-p err`.
If the user says "follow" or "watch", use `-f` but warn them it'll stream indefinitely.

```bash
ssh root@87.99.136.82 "journalctl -u sharplab-bot.service -u sharplab-worker.service -n 50 --no-pager"
```

### Deploy
Deploy latest code from GitHub main branch to VPS:

```bash
ssh root@87.99.136.82 "cd /opt/sharplab && git pull origin main && /opt/sharplab/venv/bin/pip install -e . && systemctl restart temporal.service && sleep 3 && systemctl restart sharplab-worker.service sharplab-bot.service && echo '--- STATUS ---' && systemctl status sharplab-bot.service sharplab-worker.service temporal.service --no-pager"
```

After deploying:
1. Check the status output — all three services should be `active (running)`
2. Pull the last 10 lines of bot logs to confirm clean startup
3. Report success or failure clearly

### Status check
Quick health check of all SharpLab services:

```bash
ssh root@87.99.136.82 "systemctl status sharplab-bot.service sharplab-worker.service temporal.service --no-pager && echo '--- RAM ---' && free -h && echo '--- DISK ---' && df -h / && echo '--- DB SIZE ---' && ls -lh /opt/sharplab/data/sharplab.db 2>/dev/null || echo 'no db file'"
```

### Troubleshoot
If the user reports something is broken:
1. Check service status (are they running?)
2. Pull recent logs (last 100 lines, errors first)
3. Check if temporal is healthy (bot and worker depend on it)
4. Check disk space and RAM
5. Look for patterns: OOM kills, Python tracebacks, connection errors
6. Suggest a fix — usually a restart, but investigate first

## Rules
- **Never restart** sentinel, guardian, or stavid services — those aren't ours
- **Always restart temporal BEFORE** bot and worker (they depend on it)
- **Wait 3 seconds** between temporal restart and bot/worker restart
- **Always verify** after deploy/restart by checking status + recent logs
- **Don't touch `.env`** unless explicitly asked — it has secrets
