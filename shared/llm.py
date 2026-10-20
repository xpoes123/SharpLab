"""Shared LLM helper: Haiku (cloud) with an optional local-first Ollama path.

Callers pass their own api_key and ``max_tokens``. ``local=True`` tries the
homelab Ollama model first (only for call sites that passed the homelab
benchmark: bet_nlp, sportsnews, chessnews, announce_deploy's gate) and falls
back to Haiku on any local failure. Nothing here raises: every failure —
API errors, timeouts, connection errors, bad JSON — returns None so callers
keep their existing fallback paths.

Env (read per call so load_dotenv order doesn't matter):
    OLLAMA_URL       base URL, e.g. http://100.67.232.59:11434 — empty disables local
    OLLAMA_MODEL     default gemma4:12b
    OLLAMA_TIMEOUT   seconds, default 15
    OLLAMA_NUM_CTX   default 16384 — must match every other client of the shared
                     model (sage, nsba) or Ollama reloads it
"""
from __future__ import annotations

import json
import logging
import os

import anthropic
import httpx

log = logging.getLogger(__name__)

HAIKU = "claude-haiku-4-5"


def _parse_obj(text: str) -> dict | None:
    """First ``{...}`` object in the text, or None."""
    try:
        obj = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


async def local_text(prompt: str, *, max_tokens: int, fmt: str | None = None) -> str | None:
    """Single-user-message chat with the local Ollama model. Returns None when
    disabled or on any failure (caller then uses cloud). With ``fmt="json"`` the
    reply must parse as a JSON object."""
    url = os.getenv("OLLAMA_URL", "").strip()
    if not url:
        return None
    model = os.getenv("OLLAMA_MODEL") or "gemma4:12b"
    try:
        timeout = float(os.getenv("OLLAMA_TIMEOUT") or 15)
        num_ctx = int(os.getenv("OLLAMA_NUM_CTX") or 16384)
    except ValueError:
        log.warning("bad OLLAMA_TIMEOUT/OLLAMA_NUM_CTX; using cloud")
        return None

    # Ollama truncates silently past num_ctx — skip local rather than send a
    # clipped prompt. ~3 chars/token is a conservative estimate.
    if len(prompt) // 3 + max_tokens > num_ctx:
        log.info("local llm: prompt too long for num_ctx, using cloud")
        return None

    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "keep_alive": "24h",
        "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": max_tokens},
    }
    if fmt:
        body["format"] = fmt
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{url.rstrip('/')}/api/chat", json=body)
        if r.status_code != 200:
            log.warning("local llm: HTTP %d, using cloud", r.status_code)
            return None
        text = (r.json().get("message") or {}).get("content") or ""
    except Exception as exc:  # noqa: BLE001 — any local failure means "use cloud"
        log.warning("local llm failed (%s: %s), using cloud", type(exc).__name__, exc)
        return None
    if not text.strip():
        log.warning("local llm: empty reply, using cloud")
        return None
    if fmt == "json" and _parse_obj(text) is None:
        log.warning("local llm: invalid JSON reply, using cloud")
        return None
    return text


async def _cloud_text(prompt: str, *, api_key: str, max_tokens: int) -> str | None:
    if not api_key:
        return None
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key, timeout=60.0)
        msg = await client.messages.create(
            model=HAIKU,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if hasattr(b, "text"))
    except Exception as exc:  # noqa: BLE001 — APIError, timeouts, transport, SDK bugs
        log.warning("haiku request failed (%s: %s)", type(exc).__name__, exc)
        return None


async def haiku_text(prompt: str, *, api_key: str, max_tokens: int = 300,
                     local: bool = False) -> str | None:
    """Response text for a single-user-message prompt, or None on any failure.
    ``local=True`` tries Ollama first."""
    if local:
        text = await local_text(prompt, max_tokens=max_tokens)
        if text is not None:
            return text
    return await _cloud_text(prompt, api_key=api_key, max_tokens=max_tokens)


async def haiku_json(prompt: str, *, api_key: str, max_tokens: int = 300,
                     local: bool = False, meta: dict | None = None) -> dict | None:
    """First ``{...}`` JSON object out of the response, or None on any failure
    or unparseable JSON. ``local=True`` tries Ollama (format=json) first; an
    invalid local reply falls back to Haiku. Pass a ``meta`` dict to get
    ``meta["model"]`` set to whichever model answered."""
    if local:
        text = await local_text(prompt, max_tokens=max_tokens, fmt="json")
        if text is not None:
            if meta is not None:
                meta["model"] = "ollama:" + (os.getenv("OLLAMA_MODEL") or "gemma4:12b")
            return _parse_obj(text)  # local_text already validated it parses
    text = await _cloud_text(prompt, api_key=api_key, max_tokens=max_tokens)
    if text is not None and meta is not None:
        meta["model"] = HAIKU
    return None if text is None else _parse_obj(text)
