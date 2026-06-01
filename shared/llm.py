"""Shared Anthropic (Haiku) client helper.

Consolidates the copy-pasted httpx POST + JSON-extraction that the NLP cogs
(bet_nlp, rohan_nlp) and sportsnews each used to inline. Behaviour-preserving:
identical headers, body shape, timeout, and the same "grab the first {...}
object out of the response text" parsing. Callers pass their own api_key (the
cogs already hold ``self.api_key``) and their own ``max_tokens``.
"""
from __future__ import annotations

import json
import logging

import httpx

log = logging.getLogger(__name__)

HAIKU = "claude-haiku-4-5-20251001"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


async def haiku_text(prompt: str, *, api_key: str, max_tokens: int = 300) -> str | None:
    """POST a single-user-message prompt to Haiku and return the concatenated
    response text. Returns None on non-200 or network error."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                ANTHROPIC_URL,
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": HAIKU, "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=30.0,
            )
    except httpx.HTTPError:
        log.debug("haiku request failed", exc_info=True)
        return None
    if r.status_code != 200:
        return None
    return "".join(b.get("text", "") for b in r.json().get("content", []))


async def haiku_json(prompt: str, *, api_key: str, max_tokens: int = 300) -> dict | None:
    """POST a prompt to Haiku and parse the first ``{...}`` JSON object out of
    the response text. Returns None on non-200, network error, or unparseable
    JSON so callers keep their existing fallback paths."""
    text = await haiku_text(prompt, api_key=api_key, max_tokens=max_tokens)
    if text is None:
        return None
    try:
        return json.loads(text[text.find("{"): text.rfind("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return None
