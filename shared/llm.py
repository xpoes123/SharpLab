"""Shared Anthropic (Haiku) client helper.

Consolidates the copy-pasted httpx POST + JSON-extraction that the NLP cogs
(bet_nlp, rohan_nlp) and sportsnews each used to inline. Behaviour-preserving:
identical body shape, timeout, and the same "grab the first {...}
object out of the response text" parsing. Callers pass their own api_key (the
cogs already hold ``self.api_key``) and their own ``max_tokens``.
"""
from __future__ import annotations

import json
import logging

import anthropic

log = logging.getLogger(__name__)

HAIKU = "claude-haiku-4-5"


async def haiku_text(prompt: str, *, api_key: str, max_tokens: int = 300) -> str | None:
    """Send a single-user-message prompt to Haiku and return the concatenated
    response text. Returns None on API error."""
    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        msg = await client.messages.create(
            model=HAIKU,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError:
        log.debug("haiku request failed", exc_info=True)
        return None
    return "".join(b.text for b in msg.content if hasattr(b, "text"))


async def haiku_json(prompt: str, *, api_key: str, max_tokens: int = 300) -> dict | None:
    """Send a prompt to Haiku and parse the first ``{...}`` JSON object out of
    the response text. Returns None on API error, or unparseable
    JSON so callers keep their existing fallback paths."""
    text = await haiku_text(prompt, api_key=api_key, max_tokens=max_tokens)
    if text is None:
        return None
    try:
        return json.loads(text[text.find("{"): text.rfind("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return None
