"""Pure logic for the Kalshi auto-logger — no I/O, fully unit-tested (test_kalshi_log.py).

Two jobs:
  1. parse_kalshi_ticker: turn a Kalshi contract ticker into (market, side, line) so a fill
     can be mirrored into the `bets` table. Never raises — an unrecognized ticker still logs.
  2. YES-space metric math: every metric is computed against the contract's YES mid price in
     cents, then sign-flipped by whether the position is effectively long-YES or long-NO. This
     lets one price trajectory yield drift (the live-edge signal), excursions, and realized P&L.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParsedTicker:
    series: str            # e.g. 'KXNFLGAME'
    market: str            # 'moneyline' | 'spread' | 'total' | 'kalshi' (fallback)
    side: str              # the YES-outcome subject: team code, or 'over'
    line: float | None     # spread/total line (Kalshi threshold N -> N-0.5)
    event: str             # date+teams segment, e.g. '26SEP21NYGLAR'


_SUFFIX_ALPHANUM = re.compile(r"^([A-Za-z]+)(\d+)$")


def parse_kalshi_ticker(ticker: str) -> ParsedTicker:
    """KXNFLGAME-26SEP21NYGLAR-NYG / KXNFLSPREAD-...-KC8 / KXNFLTOTAL-...-64.
    Unknown or malformed -> market='kalshi' with the raw suffix as side (never dropped)."""
    parts = ticker.split("-")
    if len(parts) < 3:
        return ParsedTicker(parts[0] if parts else ticker, "kalshi", ticker, None, "")
    series, event, suffix = parts[0], parts[1], parts[2]
    if series.endswith("GAME"):
        return ParsedTicker(series, "moneyline", suffix, None, event)
    if series.endswith("SPREAD"):
        m = _SUFFIX_ALPHANUM.match(suffix)
        team = m.group(1) if m else suffix
        line = (int(m.group(2)) - 0.5) if m else None
        return ParsedTicker(series, "spread", team, line, event)
    if series.endswith("TOTAL"):
        line = (int(suffix) - 0.5) if suffix.isdigit() else None
        return ParsedTicker(series, "total", "over", line, event)
    return ParsedTicker(series, "kalshi", suffix, None, event)


def long_yes(action: str, side: str) -> bool:
    """Am I effectively long the YES contract? buy+yes or sell+no -> long YES."""
    return (side == "yes") == (action == "buy")


def signed_move(entry_yes_c: float, now_yes_c: float, long_yes: bool) -> float:
    """Favorable price movement in cents (positive = in your favor). For a long-YES
    position the YES price rising is good; for long-NO, falling is good."""
    return (now_yes_c - entry_yes_c) if long_yes else (entry_yes_c - now_yes_c)


def realized_pnl_c(entry_yes_c: float, result_yes: bool, long_yes: bool) -> float:
    """Per-contract P&L in cents at settlement (YES settles to 100, NO to 0)."""
    settle = 100.0 if result_yes else 0.0
    return signed_move(entry_yes_c, settle, long_yes)


def excursions(entry_yes_c: float, mids_yes_c: list[float], long_yes: bool) -> tuple[float, float]:
    """(max favorable, max adverse) signed movement over the hold, in cents."""
    moves = [signed_move(entry_yes_c, m, long_yes) for m in mids_yes_c]
    return (max(moves, default=0.0), min(moves, default=0.0))


def drift_at(entry_yes_c: float, traj: list[tuple[float, float]], long_yes: bool,
             seconds: float) -> float | None:
    """Signed drift at the first snapshot at least `seconds` after entry.
    traj = list of (age_seconds, yes_mid_c), assumed in ascending age. None if not reached yet."""
    for age, mid in traj:
        if age >= seconds:
            return signed_move(entry_yes_c, mid, long_yes)
    return None
