Ship a change end-to-end: feature branch → PR → merge → VPS deploy → announce.

This is the **current, actual deploy flow**. Sentinel is decommissioned, so there is
**no auto-deploy on merge** — you must pull + restart on the VPS yourself. Never push
straight to `main`; one PR per logical feature.

Read `docs/vps-hosting.md` for VPS specifics.

---

## Step 1 — Branch & commit

If you're on `main`, branch first. Make one logical change per branch.

```bash
git checkout -b feat/<short-name>
git add -A
git commit
```

Commit message must end with the trailer:

```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

Optionally add an `Announce: yes|no` trailer in the commit body to tell the deploy
announcer whether this PR is worth posting about.

## Step 2 — PR & merge

```bash
git push -u origin HEAD
gh pr create --fill          # or write a proper title/body
gh pr merge --squash --delete-branch
```

(PR body should end with the standard `🤖 Generated with [Claude Code]` line.)

## Step 3 — Sync local main

After the squash-merge, reset local `main` to the merged commit:

```bash
git checkout main
git reset --hard origin/main
```

## Step 4 — Deploy to the VPS

Easiest is `just deploy` (push + remote pull + install + restart all services). The
manual equivalent:

```bash
ssh root@87.99.136.82 "cd /opt/sharplab && \
  git fetch origin main && git checkout main --force && git reset --hard origin/main && \
  source venv/bin/activate && pip install -e . -q && \
  systemctl restart temporal && sleep 3 && \
  systemctl restart sharplab-worker sharplab-bot sharplab-web"
```

- Restart `temporal` **first**, wait ~3s, then worker + bot + web.
- Services: `sharplab-bot`, `sharplab-worker`, `sharplab-web` (+ shared `temporal`).

## Step 5 — Verify

```bash
just status        # service status + recent logs
# or:
ssh root@87.99.136.82 "systemctl is-active sharplab-bot sharplab-worker sharplab-web temporal"
```

All should be `active`. Pull the last ~10 bot log lines to confirm a clean startup.
If you added/changed slash commands, confirm they synced.

## Step 6 — Announce (optional)

```bash
ssh root@87.99.136.82 "cd /opt/sharplab && source venv/bin/activate && python scripts/announce_deploy.py --post"
```

Posts a Claude-written update to the Discord deploy channel. Skip it for trivial PRs
(or set `Announce: no` in the commit body).

---

## Rules

- **Never push to `main`.** Always branch → PR → squash-merge.
- **One PR per logical feature** so each announce reads cleanly.
- **Never restart** `sentinel`, `guardian`, or `stavid` on the VPS.
- **Always restart `temporal` before** the other services, and **verify** after.
- Run `/pre-deploy` first if you want the full pre-flight checklist.
