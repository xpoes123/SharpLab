"""CLV for player-prop bets from REAL prices at the bet's exact line.

We compare the bet's price to the closing price at the *same* line — pulled from
the standard prop board (player_props) for main lines and from the alternate
ladders (player_prop_alts) for alt lines. De-vig the two-way price where both
sides exist; otherwise fall back to the bet side's vigged implied.

CLV (pp) = closing_prob_of_your_side_at_your_line − your_bet_implied_prob.

If we have no real price at the bet's exact line, CLV is None (shown as "—") —
we do NOT fabricate it by extrapolating off the main line.
"""
from __future__ import annotations

from collections import Counter

from shared.odds_utils import american_to_prob, devig_two_way

# stat label (as stored in a prop bet's notes) → Odds API base market key
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


def consensus_main_line(rows: list[dict]) -> float | None:
    """Modal (consensus) main line across books, for tagging a bet as alt vs main."""
    lines = Counter(r["line"] for r in rows if r.get("line") is not None)
    return lines.most_common(1)[0][0] if lines else None


def prop_clv_at_line(side: str, bet_line: float, bet_odds: int, rows: list[dict]) -> dict | None:
    """CLV (pp) for a prop bet, using real closing prices at the bet's exact line.

    `rows`: every (line, over_odds, under_odds) row for this player+market across
    books AND sources (main board + alt ladders merged). Returns None if no book
    priced the bet's exact line."""
    side = side.lower()
    at = [r for r in rows if r.get("line") is not None and abs(r["line"] - bet_line) < 0.01]
    if not at:
        return None
    fair_sides: list[float] = []
    side_implieds: list[float] = []
    for r in at:
        o, u = r.get("over_odds"), r.get("under_odds")
        if o is not None and u is not None:
            fo, fu = devig_two_way(o, u)
            fair_sides.append(fo if side == "over" else fu)
        else:
            s = o if side == "over" else u
            if s is not None:
                side_implieds.append(american_to_prob(s))
    if fair_sides:
        close_p, devigged = sum(fair_sides) / len(fair_sides), True
    elif side_implieds:
        close_p, devigged = sum(side_implieds) / len(side_implieds), False
    else:
        return None
    bet_implied = american_to_prob(bet_odds)
    return {
        "clv": (close_p - bet_implied) * 100,
        "close_prob": close_p,
        "bet_implied": bet_implied,
        "devigged": devigged,
        "n_books": len(at),
    }
