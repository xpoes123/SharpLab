#!/usr/bin/env python3
"""Post a player-facing update to Discord when a deploy ships announce-worthy PRs.

Runs as part of every SharpLab deploy. For each squash-merged commit since the
last announcement, Claude decides whether the change is worth telling players
about (new games, features, notable UX) versus internal noise (tests, refactors,
chores, invisible bugfixes). A commit-body trailer `Announce: yes|no` is a hard
override. If anything is worthy, Claude drafts a themed announcement in the
established style and it's posted to the update channel. The marker advances so
each change is considered exactly once.

    python scripts/announce_deploy.py                 # dry run — classify + draft, no post
    python scripts/announce_deploy.py --post          # post it and advance the marker
    python scripts/announce_deploy.py --since SHA      # override the start commit
    python scripts/announce_deploy.py --note "..."     # extra guidance for the draft
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
MARKER = REPO / ".last_announced_sha"
CHANNEL_DEFAULT = "1497129140153876570"

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DISCORD_LIMIT = 2000

# Record separators for safe git-log parsing.
FS, RS = "\x1f", "\x1e"


def _claude(api_key: str, model: str, prompt: str, max_tokens: int) -> str:
    r = httpx.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60.0,
    )
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", [])).strip()


def _commits(since: str) -> list[dict]:
    rng = f"{since}..HEAD" if since else "HEAD"
    out = subprocess.run(
        ["git", "-C", str(REPO), "log", "--no-merges",
         f"--pretty=format:%H{FS}%s{FS}%b{RS}", rng],
        capture_output=True, text=True, check=True,
    ).stdout
    commits = []
    for rec in out.split(RS):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        sha, subject, body = (rec.split(FS) + ["", ""])[:3]
        commits.append({"sha": sha, "subject": subject, "body": body})
    return commits


def _worthy(api_key: str, c: dict) -> dict | None:
    """Return {summary} if this commit should be announced, else None."""
    body_l = c["body"].lower()
    if "announce: no" in body_l:
        return None
    if "announce: yes" in body_l:
        return {"summary": c["subject"]}
    prompt = (
        "You decide whether a code change to a Discord casino + sports-betting bot "
        "should be announced to players. Announce user-facing things: new games, new "
        "modes, new commands, a new website/page, notable UX changes, or fixes players "
        "would clearly notice. Do NOT announce internal-only work: tests, refactors, CI, "
        "tooling, dependency bumps, or invisible plumbing.\n\n"
        f"Commit: {c['subject']}\n{c['body'][:800]}\n\n"
        'Respond with ONLY JSON: {"announce": true|false, "summary": "<short '
        'player-facing phrase, or empty>"}'
    )
    try:
        raw = _claude(api_key, HAIKU, prompt, 200)
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001 — classification is best-effort
        print(f"  ! classify failed for {c['sha'][:7]} ({e}); skipping", file=sys.stderr)
        return None
    return {"summary": data.get("summary") or c["subject"]} if data.get("announce") else None


def _draft(api_key: str, summaries: list[str], note: str) -> str:
    bullets = "\n".join(f"- {s}" for s in summaries)
    note_line = f"\nIMPORTANT extra context to weave in: {note}\n" if note else ""
    prompt = (
        "Write a fun, hype Discord announcement for players of 'Sharp Bot', a Discord "
        "casino + sports-betting bot. Group related items under emoji headers with short "
        "punchy lines. Use Discord markdown. Lead with a '📢 Sharp Bot — Update!' style "
        "title. Keep it tight and skimmable, UNDER 1800 characters. End with 'GLHF 🎲'. "
        "Do not invent features that aren't listed. Here are the shipped changes:\n\n"
        f"{bullets}\n{note_line}"
    )
    msg = _claude(api_key, SONNET, prompt, 1200)
    if len(msg) > DISCORD_LIMIT:
        msg = msg[: DISCORD_LIMIT - 1].rstrip() + "…"
    return msg


def _post_discord(token: str, channel: str, content: str) -> None:
    r = httpx.post(
        f"https://discord.com/api/v10/channels/{channel}/messages",
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
        json={"content": content},
        timeout=30.0,
    )
    r.raise_for_status()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--post", action="store_true", help="actually post + advance marker")
    ap.add_argument("--since", default=None, help="start commit (default: marker file, else HEAD~1)")
    ap.add_argument("--note", default="", help="extra guidance for the drafted message")
    args = ap.parse_args()

    load_dotenv(REPO / ".env")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    token = os.environ.get("DISCORD_BOT_TOKEN")
    channel = os.environ.get("UPDATE_ANNOUNCE_CHANNEL_ID", CHANNEL_DEFAULT)
    if not api_key or not token:
        print("Missing ANTHROPIC_API_KEY or DISCORD_BOT_TOKEN in .env", file=sys.stderr)
        return 1

    since = args.since or (MARKER.read_text().strip() if MARKER.exists() else "HEAD~1")
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()

    commits = _commits(since)
    print(f"Considering {len(commits)} commit(s) since {since[:12]}…")
    summaries: list[str] = []
    for c in commits:
        w = _worthy(api_key, c)
        mark = "✓" if w else "·"
        print(f"  {mark} {c['sha'][:7]} {c['subject']}")
        if w:
            summaries.append(w["summary"])

    if not summaries:
        print("Nothing announce-worthy.")
        if args.post:
            MARKER.write_text(head + "\n")
            print(f"Marker advanced to {head[:12]}.")
        return 0

    message = _draft(api_key, summaries, args.note)
    print("\n──────── drafted announcement ────────\n")
    print(message)
    print("\n──────────────────────────────────────")

    if not args.post:
        print("\nDry run — re-run with --post to publish.")
        return 0

    _post_discord(token, channel, message)
    MARKER.write_text(head + "\n")
    print(f"\nPosted to channel {channel}; marker advanced to {head[:12]}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
