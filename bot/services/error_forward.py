"""Forward errors to Sentinel's error-forward Discord channel.

Posts a JSON payload that Sentinel's `error_intake` cog parses, dedupes,
triages via Haiku, and either creates a ticket / dispatches a fix / dismisses.

Posting goes directly to the Discord REST API using SharpLab's bot token — no
bot-client connection is required, so this works from APScheduler jobs,
FastAPI middleware, or any non-async-bot context.

The function is best-effort: any failure is logged and swallowed so it never
disrupts the calling error path.
"""

from __future__ import annotations

import json
import logging
import os

import aiohttp

log = logging.getLogger(__name__)

_DISCORD_API = "https://discord.com/api/v10"
_REPO = "xpoes123/SharpLab"


async def forward_to_sentinel(
    error_id: int | str,
    exception_type: str,
    message: str,
    traceback_text: str | None,
    context: str | None = None,
) -> None:
    """Post an error to Sentinel's intake channel. Returns silently on any failure."""
    channel_id = int(os.getenv("SENTINEL_ERROR_FORWARD_CHANNEL_ID", "0") or "0")
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not channel_id or not token:
        return

    repo_owner, _, repo_name = _REPO.partition("/")
    payload = {
        "repo_owner": repo_owner,
        "repo_name": repo_name,
        "error_type": exception_type,
        "message": message,
        "traceback": traceback_text,
        "extra_context": (
            {"error_id": str(error_id), "context": context}
            if context else {"error_id": str(error_id)}
        ),
    }

    body = f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    # Discord caps message content at 2000 chars; truncate the JSON block if needed.
    if len(body) > 1990:
        if payload["traceback"]:
            payload["traceback"] = payload["traceback"][-1500:]
            body = f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
        if len(body) > 1990:
            body = body[:1985] + "\n```"

    url = f"{_DISCORD_API}/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "DiscordBot (sharplab-error-forwarder, 1.0)",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers=headers,
                json={"content": body},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status >= 400:
                    text = (await resp.text())[:200]
                    log.warning("error_forward: Discord %d: %s", resp.status, text)
    except Exception:
        log.debug("error_forward: post failed", exc_info=True)
