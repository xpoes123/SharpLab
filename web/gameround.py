"""In-memory server-side round store for arcade guessers, so the secret answer stays on the
server. (An itsdangerous signed token is tamper-proof but its payload is base64-READABLE by the
client — putting the answer in the token lets a determined player decode it and cheat.) The token
is now just an opaque random id; the answer lives here and never leaves the server."""

from __future__ import annotations

import secrets

_STORE: dict[str, object] = {}
_MAX = 20_000  # bound memory; evict oldest (dicts keep insertion order) when over


def stash(data: object) -> str:
    """Store round data, return an opaque token id."""
    if len(_STORE) >= _MAX:
        _STORE.pop(next(iter(_STORE)), None)
    tok = secrets.token_urlsafe(16)
    _STORE[tok] = data
    return tok


def peek(token: str) -> object | None:
    """Read round data (round stays open for more guesses). None if unknown/evicted."""
    return _STORE.get(token)


def claim(token: str) -> object | None:
    """Atomically take-and-remove round data (single use)."""
    return _STORE.pop(token, None)
