"""Pure pick'em standings math — shared by the Discord bot and the web HQ.

Rows are scored picks: {discord_user, correct (0/1), pick ('home'|'away'),
stake (1-5), home_prob, away_prob}, ordered by user then game start time so
streaks compute correctly. Returns uid -> {correct, total, accuracy, points,
units}. Units are a stake-unit bet at the market's win prob (start at 0, can go
negative); points add an escalating streak bonus capped at STREAK_CAP.
"""

from __future__ import annotations

STREAK_CAP = 5


def _fair_prob(prob: float | None) -> float:
    """Clamp a market win prob to a usable value (default even money)."""
    p = prob or 0.5
    return p if p > 0 else 0.5


def potential_units(stake: int, prob: float | None) -> float:
    """Units gained if a `stake`-unit pick at `prob` wins (the upside only)."""
    return stake * ((1.0 / _fair_prob(prob)) - 1.0)


def pick_units(stake: int, prob: float | None, won: bool) -> float:
    """Settled units for one pick: +upside if it won, −stake if it lost.

    The single source of truth for pick'em unit P&L — used by the standings, the
    resolved-game recap, and the daily card so they can never disagree."""
    return potential_units(stake, prob) if won else -float(stake)


def pick_prob(pick: str | None, home_prob: float | None, away_prob: float | None) -> float | None:
    """The market win prob for whichever side was picked."""
    return home_prob if pick == "home" else away_prob


def compute_pickem_standings(rows: list[dict]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for r in rows:
        uid = r["discord_user"]
        s = stats.setdefault(
            uid, {"correct": 0, "total": 0, "points": 0, "units": 0.0, "_streak": 0},
        )
        s["total"] += 1
        stake = r.get("stake") or 1
        prob = pick_prob(r.get("pick"), r.get("home_prob"), r.get("away_prob"))
        if r["correct"]:
            s["correct"] += 1
            s["_streak"] += 1
            s["points"] += min(s["_streak"], STREAK_CAP)
            s["units"] += pick_units(stake, prob, True)
        else:
            s["_streak"] = 0
            s["units"] += pick_units(stake, prob, False)
    for s in stats.values():
        s["accuracy"] = s["correct"] / s["total"] if s["total"] else 0.0
        s.pop("_streak", None)
    return stats
