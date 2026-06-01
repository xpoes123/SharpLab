"""Shared bot↔web API secret + a timing-safe verifier.

Every game router (sudoku, bingo, figgie, …) used to read `WEB_API_SECRET`
itself and compare with `!=`; this centralizes the secret, makes the comparison
constant-time, and warns once when the insecure default is in use.
"""
from __future__ import annotations

import logging
import os
import secrets

log = logging.getLogger(__name__)

WEB_API_SECRET = os.environ.get("WEB_API_SECRET", "dev-secret")
if WEB_API_SECRET == "dev-secret":
    log.warning("WEB_API_SECRET is the insecure default 'dev-secret' — set it in the "
                "environment; room/token endpoints are forgeable until you do.")


def check_api_key(provided: str | None) -> bool:
    """Timing-safe equality against the shared bot↔web secret."""
    return secrets.compare_digest(provided or "", WEB_API_SECRET)
