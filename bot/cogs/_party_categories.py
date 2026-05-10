"""Shared answer-bank categories for party guessing games (cluemaster, imposter).

Each category exposes a list of items as `(name, [aliases])`. Add new
categories by populating CATEGORIES; both /cluemaster and /imposter pick
them up automatically.
"""

from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher

from bot.cogs.nbaguess import NBA_PLAYERS_DATA


# ── Category data ────────────────────────────────────────────────────────────

# (name, [aliases])
CategoryItem = tuple[str, list[str]]


def _nba_items() -> list[CategoryItem]:
    out: list[CategoryItem] = []
    for entry in NBA_PLAYERS_DATA:
        # entry shape: (id, name, [alts], [stints], stats)
        _, name, alts, *_ = entry
        out.append((name, list(alts)))
    return out


CATEGORIES: dict[str, tuple[str, str, list[CategoryItem]]] = {
    # key -> (display_name, emoji, items)
    "nba": ("NBA Players", "\U0001f3c0", _nba_items()),
}

DEFAULT_CATEGORY = "nba"


# ── Answer matching ──────────────────────────────────────────────────────────


def _normalize(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return "".join(c.lower() for c in stripped if c.isalnum()).strip()


def _fuzzy(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def check_answer(guess: str, item: CategoryItem) -> bool:
    """Return True if guess matches the item name or any alias.

    Mirrors the matching used by nbaguess: exact normalized match, fuzzy
    match (>=85% on >=5 chars), or last-name-only match (>=4 chars).
    """
    norm_g = _normalize(guess)
    if not norm_g or len(norm_g) < 3:
        return False
    name, alts = item
    for ans in [name, *alts]:
        norm_a = _normalize(ans)
        if not norm_a:
            continue
        if norm_g == norm_a:
            return True
        if len(norm_g) >= 5 and _fuzzy(norm_g, norm_a) >= 0.85:
            return True
        parts = ans.split()
        if len(parts) > 1:
            last = _normalize(parts[-1])
            if norm_g == last and len(last) >= 4:
                return True
    return False


def category_options(default_key: str = DEFAULT_CATEGORY) -> list[tuple[str, str, str, bool]]:
    """SelectOption-friendly tuples: (label, value, emoji, is_default)."""
    out: list[tuple[str, str, str, bool]] = []
    for key, (label, emoji, _items) in CATEGORIES.items():
        out.append((label, key, emoji, key == default_key))
    return out


def get_category(key: str) -> tuple[str, str, list[CategoryItem]]:
    return CATEGORIES.get(key, CATEGORIES[DEFAULT_CATEGORY])
