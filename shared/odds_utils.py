"""Odds format conversion + vendor helpers.

Pure odds math (conversions, devig, CLV, kelly, parse/breakdown) is now the
canonical `djtoolkit.oddsmath` — re-exported here so existing
`from shared.odds_utils import ...` sites keep working unchanged. Only the
vendor/network-specific helpers (Kalshi exec price, Odds-API payload shaping,
Polymarket fetch, team-side matching) live here, since djtoolkit is pure math
with no I/O or vendor field knowledge.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

# Canonical pure-math (behavior verified identical to the previous local copies).
from djtoolkit.oddsmath import (  # noqa: F401  (re-exported for existing importers)
    _PP_PER_HALF_POINT_SPREAD,
    _PP_PER_HALF_POINT_TOTAL,
    american_to_decimal,
    american_to_prob,
    compute_clv,
    decimal_to_american,
    devig_two_way,
    fmt_odds,
    fmt_prob,
    odds_breakdown,
    parse_odds_input,
    prob_to_american,
)

if TYPE_CHECKING:
    import httpx

POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
ODDS_FORMATS = ("american", "decimal", "probability")

# Kalshi taker fee ≈ 0.07 * P * (1-P) per contract.
KALSHI_TAKER_FEE = 0.07


def kalshi_exec_price(market: dict) -> float | None:
    """Cost (0–1) to back a side on Kalshi: the yes ASK plus the taker fee — what
    you can ACTUALLY transact at. Falls back to last trade, then the bid/ask mid.

    The raw bid/ask mid is a fair-value midpoint you can't trade; using it made
    Kalshi look like the best price and created phantom arbs. Fields are Kalshi's
    *_dollars (0–1)."""
    ask = market.get("yes_ask_dollars")
    p = float(ask) if ask else 0.0
    if not (0 < p < 1):
        last = market.get("last_price_dollars")
        p = float(last) if last else 0.0
    if not (0 < p < 1):
        yes_bid = market.get("yes_bid_dollars") or 0
        p = (float(yes_bid) + float(ask or 0)) / 2 if (yes_bid or ask) else 0.0
    if not (0 < p < 1):
        return None
    return min(p + KALSHI_TAKER_FEE * p * (1 - p), 0.99)


def extract_book_payload(bookmaker: dict, home_team: str) -> dict:
    """Normalize one bookmaker's markets (The Odds API shape) into our standard
    payload: spread/spread_odds (home), ml_home/ml_away, total/over/under odds."""
    payload: dict = {}
    for market in bookmaker.get("markets", []):
        key = market["key"]
        outcomes = {o["name"]: o for o in market["outcomes"]}
        if key == "spreads":
            home = outcomes.get(home_team, {})
            away = [o for n, o in outcomes.items() if n != home_team]
            payload["spread"] = home.get("point")
            payload["spread_odds"] = home.get("price")
            if away:
                payload["spread_away"] = away[0].get("point")
                payload["spread_away_odds"] = away[0].get("price")
        elif key == "h2h":
            home = outcomes.get(home_team, {})
            away = [o for n, o in outcomes.items() if n != home_team]
            payload["ml_home"] = home.get("price")
            payload["ml_away"] = away[0].get("price") if away else None
        elif key == "totals":
            over = outcomes.get("Over", {})
            under = outcomes.get("Under", {})
            payload["total"] = over.get("point")
            payload["total_over_odds"] = over.get("price")
            payload["total_under_odds"] = under.get("price")
    return payload


def side_is_home(side: str, home_team: str, away_team: str) -> bool | None:
    """Determine if a bet side matches the home team, away team, or neither.

    Uses substring containment (side ⊆ team name) for matching.  When both
    teams match (e.g. side="sox" with Red Sox vs White Sox), returns None
    (ambiguous) instead of silently picking home.
    """
    s = side.lower()
    home = home_team.lower()
    away = away_team.lower()
    home_match = s in home
    away_match = s in away
    if home_match and away_match:
        return None  # ambiguous — shared substring
    if home_match:
        return True
    if away_match:
        return False
    return None


# ── Polymarket Gamma API ─────────────────────────────────────────────────────

async def fetch_polymarket_ml(
    client: "httpx.AsyncClient", home_team: str, away_team: str
) -> tuple[int, int] | None:
    """
    Search Polymarket Gamma API for a game winner market and return
    (ml_home_american, ml_away_american) or None if not found.

    The ``client`` is an ``httpx.AsyncClient`` supplied by the caller so that
    activities can share a session while the bot cog can create a short-lived one.
    """
    home_short = home_team.split()[-1].lower()
    away_short = away_team.split()[-1].lower()

    try:
        resp = await client.get(
            f"{POLYMARKET_GAMMA}/markets",
            params={"q": f"{home_short} {away_short}", "active": "true", "closed": "false", "limit": 20},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:
        return None

    markets = data if isinstance(data, list) else data.get("markets", data.get("data", []))
    target = next(
        (
            m for m in markets
            if home_short in (m.get("question") or m.get("title") or "").lower()
            and away_short in (m.get("question") or m.get("title") or "").lower()
        ),
        None,
    )
    if not target:
        return None

    tokens = target.get("tokens", [])
    if isinstance(tokens, str):
        try:
            tokens = json.loads(tokens)
        except Exception:
            return None

    if not tokens:
        return None

    home_prob: float | None = None
    away_prob: float | None = None
    for token in tokens:
        outcome = (token.get("outcome") or "").lower()
        price = token.get("price")
        if price is None:
            continue
        price = float(price)
        if not (0 < price < 1):
            continue
        if home_short in outcome:
            home_prob = price
        elif away_short in outcome:
            away_prob = price

    if home_prob is None or away_prob is None:
        return None
    try:
        return prob_to_american(home_prob), prob_to_american(away_prob)
    except (ValueError, ZeroDivisionError):
        return None
