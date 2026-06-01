"""Approximate CLV for player-prop bets, including ALT lines.

We poll the standard (main) prop line per player/stat. To value a bet — at the
main line OR an alt line — we:
  1. de-vig the closing main line → fair P(over main_line),
  2. number-adjust to the bet's line using a per-unit slope for the stat,
  3. compare the fair prob of the bet's side to the price you took.

CLV (pp) = fair_prob_of_your_side_at_close − your_bet_implied_prob.
Positive = you beat the closing (no-vig) line. It's an estimate, not exact —
exact alt-line CLV would require polling the alternate-line markets.
"""
from __future__ import annotations

from collections import Counter

from shared.odds_utils import american_to_prob, devig_two_way

# Rough fair-prob change per 1 unit of line, near the number, by market.
STAT_SLOPE = {
    "player_points": 0.035,
    "player_rebounds": 0.060,
    "player_assists": 0.070,
    "player_threes": 0.130,
    "player_points_rebounds_assists": 0.025,
}

# stat label (as stored in a prop bet's notes) → Odds API market key
_STAT_TO_MARKET = {
    "Points": "player_points",
    "Rebounds": "player_rebounds",
    "Assists": "player_assists",
    "Threes": "player_threes",
    "PRA (pts+reb+ast)": "player_points_rebounds_assists",
    "PRA": "player_points_rebounds_assists",
}


def parse_prop_note(notes: str | None) -> tuple[str | None, str | None]:
    """A prop bet's notes are '<player> <StatLabel>[ — user note]'. Return
    (player, market_key)."""
    if not notes:
        return None, None
    base = notes.split(" — ")[0].strip()
    for label in sorted(_STAT_TO_MARKET, key=len, reverse=True):
        if base.endswith(label):
            return base[: -len(label)].strip(), _STAT_TO_MARKET[label]
    return None, None


def closing_fair_over(rows: list[dict]) -> tuple[float | None, float | None]:
    """From a player/market's rows across books, return (consensus line, average
    de-vigged fair P(over) at that line)."""
    lines = Counter(r["line"] for r in rows if r["line"] is not None)
    if not lines:
        return None, None
    line = lines.most_common(1)[0][0]
    fairs = []
    for r in rows:
        if r["line"] != line or r["over_odds"] is None or r["under_odds"] is None:
            continue
        over, _ = devig_two_way(r["over_odds"], r["under_odds"])
        fairs.append(over)
    if not fairs:
        return line, None
    return line, sum(fairs) / len(fairs)


def prop_clv(side: str, bet_line: float, bet_odds: int, market: str,
             close_line: float, close_fair_over: float) -> dict | None:
    """CLV (pp) for a prop bet vs the closing line, number-adjusted for line diff."""
    if close_fair_over is None or close_line is None:
        return None
    slope = STAT_SLOPE.get(market, 0.04)
    fair_over = min(max(close_fair_over - slope * (bet_line - close_line), 0.02), 0.98)
    fair_side = fair_over if side == "over" else 1 - fair_over
    bet_implied = american_to_prob(bet_odds)
    return {
        "clv": (fair_side - bet_implied) * 100,
        "fair_side_prob": fair_side,
        "bet_implied": bet_implied,
        "close_line": close_line,
        "alt": abs(bet_line - close_line) > 0.01,
    }
