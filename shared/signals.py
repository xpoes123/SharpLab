"""Market-signal detection over a game's current odds across books.

Pure functions (no I/O) so they're easy to test. The bot's signals cog feeds
these the latest per-source payloads and posts any hits to Discord.

A "payload" is the standardized dict stored in odds_snapshots:
  {spread, spread_odds, ml_home, ml_away, total, total_over_odds, total_under_odds}
`odds` is {source: payload}.
"""
from __future__ import annotations

from shared.odds_utils import american_to_prob

# Defaults — tuned to keep alerts rare and meaningful.
SPREAD_MIDDLE_MIN = 1.0   # points of gap to flag a spread middle
TOTAL_MIDDLE_MIN = 1.5    # points of gap to flag a total middle
STEAM_MIN_BOOKS = 3       # books that must move together to call it steam
STEAM_ML_CENTS = 12       # min |American| move per book to count
DIVERGENCE_MIN_PER_SIDE = 2  # books moving each way to call it divergence


def _best(odds: dict, key: str, *, highest: bool) -> tuple[str, float] | None:
    """Source offering the highest (best price / most points) or lowest value for `key`."""
    best = None
    for src, p in odds.items():
        v = p.get(key)
        if v is None:
            continue
        if best is None or (v > best[1] if highest else v < best[1]):
            best = (src, v)
    return best


def find_ml_arb(odds: dict, min_roi: float = 0.0) -> dict | None:
    """Two-way moneyline arbitrage: best home price + best away price across all
    books (incl. Kalshi). Arb exists when their implied probabilities sum < 1.
    min_roi filters marginal arbs (our per-book snapshots aren't simultaneous, so
    sub-~1% edges are unreliable)."""
    bh = _best(odds, "ml_home", highest=True)
    ba = _best(odds, "ml_away", highest=True)
    if not bh or not ba:
        return None
    ph, pa = american_to_prob(bh[1]), american_to_prob(ba[1])
    s = ph + pa
    roi = (1.0 / s - 1.0) * 100 if s > 0 else 0.0
    if s >= 1.0 or roi < min_roi:
        return None
    return {
        "kind": "arb",
        "home_book": bh[0], "home_odds": int(bh[1]),
        "away_book": ba[0], "away_odds": int(ba[1]),
        "roi_pct": (1.0 / s - 1.0) * 100,
        "home_stake_pct": ph / s * 100,
        "away_stake_pct": pa / s * 100,
    }


def find_total_middle(odds: dict, min_gap: float = TOTAL_MIDDLE_MIN) -> dict | None:
    """Bet OVER the lowest total and UNDER the highest total. If they differ you
    have a middle (both win if the score lands in the gap); if the same line and
    prices sum < 1 it's a pure arb."""
    cands = [(src, p["total"], p.get("total_over_odds") or -110, p.get("total_under_odds") or -110)
             for src, p in odds.items() if p.get("total") is not None]
    if len(cands) < 2:
        return None
    over = min(cands, key=lambda c: (c[1], -c[2]))   # lowest line, then best over price
    under = max(cands, key=lambda c: (c[1], c[3]))   # highest line, then best under price
    gap = under[1] - over[1]
    oo, uu = over[2], under[3]
    cost = american_to_prob(oo) + american_to_prob(uu)
    is_arb = cost < 1.0
    if not is_arb and gap < min_gap:
        return None
    return {
        "kind": "total_arb" if is_arb else "total_middle",
        "over_book": over[0], "over_line": over[1], "over_odds": int(oo),
        "under_book": under[0], "under_line": under[1], "under_odds": int(uu),
        "gap": gap, "cost": cost,
    }


def find_spread_middle(odds: dict, min_gap: float = SPREAD_MIDDLE_MIN) -> dict | None:
    """Best home spread (most points) at one book + best away spread at another,
    each priced at its own side's odds. A gap means a middle on the home margin."""
    home = away = None  # (book, line, odds)
    for src, p in odds.items():
        s = p.get("spread")
        if s is None:
            continue
        if home is None or s > home[1]:
            home = (src, s, p.get("spread_odds"))
        if away is None or s < away[1]:
            away = (src, s, p.get("spread_away_odds"))
    if not home or not away:
        return None
    gap = home[1] - away[1]
    if gap < min_gap:
        return None
    return {
        "kind": "spread_middle",
        "home_book": home[0], "home_line": home[1], "home_odds": home[2],
        "away_book": away[0], "away_line": -away[1], "away_odds": away[2],
        "gap": gap,
    }


# ── Middle profitability ──────────────────────────────────────────────────────
# Empirical base rates for how often a middle's window actually hits.
MLB_ONE_RUN_RATE = 0.286     # share of MLB games decided by exactly 1 run
PER_RUN_TOTAL = 0.115        # ~prob the total lands on a given run near the number
PER_POINT_NBA_SPREAD = 0.028  # ~prob an NBA margin equals a given number near the line
PER_POINT_NBA_TOTAL = 0.022


def _profit_mult(american: int | None) -> float | None:
    """Profit per $1 staked if the bet wins (e.g. +150 → 1.5, -200 → 0.5)."""
    if american is None or american == 0:
        return None
    return american / 100 if american > 0 else 100 / abs(american)


def _window_count(lo: float, hi: float) -> int:
    """Integer outcomes strictly between two lines (the margins/totals that hit)."""
    import math
    a, b = min(lo, hi), max(lo, hi)
    return max(0, math.floor(b - 1e-9) - math.ceil(a + 1e-9) + 1)


def estimate_middle_hit(sport: str, market: str, lo: float, hi: float) -> float:
    """Rough P(result lands in the middle window). Base rates only — not
    game-specific. Spread on MLB = the 1-run rate; everything else scales the
    integer-outcome count by a per-number probability."""
    if market == "spread" and sport == "mlb":
        return MLB_ONE_RUN_RATE
    n = _window_count(lo, hi)
    if market == "spread":
        return min(n * PER_POINT_NBA_SPREAD, 0.6)
    per = PER_RUN_TOTAL if sport == "mlb" else PER_POINT_NBA_TOTAL
    return min(n * per, 0.6)


def middle_profit(odds_a: int | None, odds_b: int | None, hit_p: float) -> dict | None:
    """Breakeven hit-rate and EV for a 2-leg middle at $1 per leg.
    Win both (hit) → pa+pb; exactly one wins (miss) → avg(pa-1, pb-1)."""
    pa, pb = _profit_mult(odds_a), _profit_mult(odds_b)
    if pa is None or pb is None:
        return None
    hit, miss = pa + pb, ((pa - 1) + (pb - 1)) / 2
    breakeven = -miss / (hit - miss) if (hit - miss) else None
    ev_roi = (hit_p * hit + (1 - hit_p) * miss) / 2 * 100  # % ROI on the $2 staked
    return {"breakeven": breakeven, "ev_roi": ev_roi, "hit_p": hit_p}


def detect_ml_moves(baseline: dict, current: dict,
                    min_books: int = STEAM_MIN_BOOKS,
                    cents: int = STEAM_ML_CENTS) -> dict | None:
    """Compare home moneyline now vs a baseline. ≥min_books moving the same way =
    steam; books moving opposite ways = divergence (books disagree on direction)."""
    moves: dict[str, int] = {}
    lagging: list[str] = []  # books that had a comparable line but barely moved
    for src, cur in current.items():
        base = baseline.get(src)
        if not base:
            continue
        a, b = base.get("ml_home"), cur.get("ml_home")
        if a is None or b is None:
            continue
        d = b - a
        if abs(d) >= cents:
            moves[src] = d
        else:
            lagging.append(src)
    if not moves:
        return None
    # ml_home more negative (d<0) = home steamed; more positive (d>0) = away steamed.
    home_side = [s for s, d in moves.items() if d < 0]
    away_side = [s for s, d in moves.items() if d > 0]
    if len(home_side) >= DIVERGENCE_MIN_PER_SIDE and len(away_side) >= DIVERGENCE_MIN_PER_SIDE:
        return {"kind": "divergence", "home_books": home_side, "away_books": away_side, "lagging": lagging}
    if len(moves) >= min_books and (not away_side or not home_side):
        return {"kind": "steam", "toward": "home" if home_side else "away",
                "books": list(moves.keys()), "lagging": lagging,
                "avg_cents": round(sum(abs(d) for d in moves.values()) / len(moves))}
    return None
