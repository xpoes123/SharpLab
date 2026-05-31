"""
Odds format conversion. This is the ONLY place that converts between formats.
All sources (Kalshi, Polymarket) return probabilities — convert here at the boundary.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"


def prob_to_american(prob: float) -> int:
    """Convert implied probability (0–1) to American odds."""
    if prob <= 0 or prob >= 1:
        raise ValueError(f"Probability must be between 0 and 1, got {prob}")
    if prob >= 0.5:
        return round(-prob / (1 - prob) * 100)
    else:
        return round((1 - prob) / prob * 100)


def american_to_prob(odds: int) -> float:
    """Convert American odds to implied probability."""
    if odds == 0:
        raise ValueError("American odds cannot be zero")
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)


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


def devig_two_way(ml_home: int, ml_away: int) -> tuple[float, float]:
    """Normalize a two-sided American moneyline into fair (home, away) win
    probabilities summing to 1 (removes the bookmaker's vig)."""
    hp = american_to_prob(ml_home)
    ap = american_to_prob(ml_away)
    total = hp + ap
    if total <= 0:
        return 0.5, 0.5
    return hp / total, ap / total


def american_to_decimal(odds: int) -> float:
    """Convert American odds to decimal odds."""
    if odds == 0:
        raise ValueError("American odds cannot be zero")
    if odds > 0:
        return (odds / 100) + 1
    else:
        return (100 / abs(odds)) + 1


def decimal_to_american(decimal: float) -> int:
    """Convert decimal odds to American odds."""
    if decimal <= 1.0:
        raise ValueError(f"Decimal odds must be > 1.0, got {decimal}")
    if decimal >= 2.0:
        return round((decimal - 1) * 100)
    else:
        return round(-100 / (decimal - 1))


def fmt_prob(odds: int) -> str:
    """Format American odds as implied probability percentage (e.g. -110 → '52.4%')."""
    return f"{american_to_prob(odds) * 100:.1f}%"


# ── Number-adjusted CLV ──────────────────────────────────────────────────────
# Approximate win-probability change per half-point of line movement.
# Standard industry approximation for NBA.
_PP_PER_HALF_POINT_SPREAD = 2.0
_PP_PER_HALF_POINT_TOTAL = 1.0


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


def compute_clv(
    bet_odds: int,
    close_odds: int,
    *,
    market: str = "",
    bet_line: float | None = None,
    close_line: float | None = None,
    is_home: bool | None = None,
    is_over: bool | None = None,
) -> float:
    """Compute CLV in percentage points, accounting for line number movement.

    For moneyline/kalshi: juice-based CLV only (close_prob - bet_prob).
    For spread/total: adds a per-half-point adjustment when the number moved.

    Parameters:
        bet_odds:   American odds at which the bet was placed
        close_odds: American odds at close (or current snapshot)
        market:     'spread', 'total', 'moneyline', or 'kalshi'
        bet_line:   Bettor's spread/total number (bettor's perspective for spreads)
        close_line: Closing spread (HOME perspective) or total number
        is_home:    True if bettor took the home side (spread only)
        is_over:    True if bettor took the over (total only)
    """
    bet_prob = american_to_prob(bet_odds)
    close_prob = american_to_prob(close_odds)
    juice_clv = (close_prob - bet_prob) * 100

    if bet_line is None or close_line is None:
        return juice_clv

    if market == "spread" and is_home is not None:
        # close_line is home-perspective; convert to bettor's perspective
        close_for_bettor = close_line if is_home else -close_line
        diff = bet_line - close_for_bettor  # positive = bettor got better number
        return juice_clv + (diff / 0.5) * _PP_PER_HALF_POINT_SPREAD

    if market == "total" and is_over is not None:
        # Over wants close to go up (easier to clear lower threshold);
        # under wants close to go down (easier to stay under higher threshold).
        diff = (close_line - bet_line) if is_over else (bet_line - close_line)
        return juice_clv + (diff / 0.5) * _PP_PER_HALF_POINT_TOTAL

    return juice_clv


def parse_odds_input(raw: str) -> tuple[int, str]:
    """
    Parse odds in any supported format and return (american_odds, format_label).

    Supported formats:
    - American:  -110, +150, 150  (negative, explicit +, or integer >= 100)
    - Decimal:   1.91, 2.50       (float with decimal point, value >= 1.01)
    - Cents:     52, 65           (integer 1–99, Kalshi/Polymarket style)
    - Prob:      0.52             (float with decimal point, value < 1.0)
    - Percent:   52%              (explicit % suffix)
    """
    raw = raw.strip()

    if raw.endswith("%"):
        prob = float(raw[:-1]) / 100
        return prob_to_american(prob), "percent"

    if "." in raw:
        val = float(raw)
        if val < 1.0:
            return prob_to_american(val), "prob"
        else:
            return decimal_to_american(val), "decimal"

    val = int(raw.lstrip("+"))

    # Cents: unsigned integer 1–99 (Kalshi / Polymarket price)
    if not raw.startswith("-") and not raw.startswith("+") and 1 <= val <= 99:
        return prob_to_american(val / 100), "cents"

    return val, "american"


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
