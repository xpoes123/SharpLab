"""Shared bet resolution logic used by both the Temporal pipeline and the Discord bot."""
from __future__ import annotations


def resolve_outcome(
    side: str,
    market: str,
    line: float | None,
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
) -> str:
    """Return won/lost/push/void for a bet given the final score.

    Parameters are all primitives so the function can be called from both
    the Temporal activity layer (Bet dataclass input) and the bot trading
    cog (dict input) without coupling either caller to this module's types.
    """
    side = side.lower()
    market = market.lower()
    home_l = home_team.lower()
    away_l = away_team.lower()

    def _is_home(s: str) -> bool:
        return s in home_l or home_l.split()[-1] in s

    def _is_away(s: str) -> bool:
        return s in away_l or away_l.split()[-1] in s

    if market in ("moneyline", "kalshi"):
        if side == "yes" or _is_home(side):
            return "won" if home_score > away_score else "lost"
        if side == "no" or _is_away(side):
            return "won" if away_score > home_score else "lost"

    elif market == "spread":
        if line is None:
            return "void"
        if _is_home(side):
            side_score, opp_score = home_score, away_score
        elif _is_away(side):
            side_score, opp_score = away_score, home_score
        else:
            return "void"
        margin = (side_score - opp_score) + line
        if abs(margin) < 0.01:
            return "push"
        return "won" if margin > 0 else "lost"

    elif market == "total":
        if line is None:
            return "void"
        total = home_score + away_score
        diff = total - line
        if abs(diff) < 0.01:
            return "push"
        if side == "over":
            return "won" if diff > 0 else "lost"
        if side == "under":
            return "won" if diff < 0 else "lost"

    return "void"
