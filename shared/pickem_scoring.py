"""Pure pick'em standings math — shared by the Discord bot and the web HQ.

Rows are scored picks: {discord_user, correct (0/1), pick ('home'|'away'),
stake (1-5), home_prob, away_prob}, ordered by user then game start time so
streaks compute correctly. Returns uid -> {correct, total, accuracy, points,
units}. Units are a stake-unit bet at the market's win prob (start at 0, can go
negative); points add an escalating streak bonus capped at STREAK_CAP.
"""

from __future__ import annotations

STREAK_CAP = 5


def compute_pickem_standings(rows: list[dict]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for r in rows:
        uid = r["discord_user"]
        s = stats.setdefault(
            uid, {"correct": 0, "total": 0, "points": 0, "units": 0.0, "_streak": 0},
        )
        s["total"] += 1
        stake = r.get("stake") or 1
        prob = (r.get("home_prob") if r.get("pick") == "home" else r.get("away_prob")) or 0.5
        if prob <= 0:
            prob = 0.5
        if r["correct"]:
            s["correct"] += 1
            s["_streak"] += 1
            s["points"] += min(s["_streak"], STREAK_CAP)
            s["units"] += stake * ((1.0 / prob) - 1.0)
        else:
            s["_streak"] = 0
            s["units"] -= stake
    for s in stats.values():
        s["accuracy"] = s["correct"] / s["total"] if s["total"] else 0.0
        s.pop("_streak", None)
    return stats
