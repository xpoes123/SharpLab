"""Play-by-play generator for the unified Sports Sim.

Given a sport and the already-decided winner ("home"/"away"), produce a plausible final score
and an ordered list of scoring events the client animates through. This is PURELY COSMETIC —
the winner and payout are decided in web/sim.py before this runs; nothing here touches coins or
changes who won. Randomness is CSPRNG only for flavor variety.

Event schema (sport-agnostic so one client animator handles all five):
    {"clock": "Q2 4:31", "team": "home"|"away", "home": <cum home>, "away": <cum away>,
     "desc": "3-pointer"}
The final score is the last event's cumulative (home, away); for a 0-0 game the timeline is empty
and the caller shows the scoreboard flat.
"""

from __future__ import annotations

import secrets


def _rand(n: int) -> int:
    return secrets.randbelow(n)


def _pick(seq):
    return seq[_rand(len(seq))]


# ── per-sport final-score generators (winner strictly ahead) ──────────────────

def _final_nba(winner: str) -> tuple[int, int]:
    base = 100 + _rand(25)
    margin = 1 + _rand(18)
    return (base + margin, base) if winner == "home" else (base, base + margin)


def _final_nfl(winner: str) -> tuple[int, int]:
    base = 13 + _rand(18)
    margin = 1 + _rand(17)
    return (base + margin, base) if winner == "home" else (base, base + margin)


def _final_mlb(winner: str) -> tuple[int, int]:
    lo = _rand(6)
    margin = 1 + _rand(6)
    return (lo + margin, lo) if winner == "home" else (lo, lo + margin)


def _final_soccer(winner: str) -> tuple[int, int]:
    lo = _rand(3)
    margin = 1 + _rand(3)
    return (lo + margin, lo) if winner == "home" else (lo, lo + margin)


# ── timelines ─────────────────────────────────────────────────────────────────

def _timeline_scoring(sport: str, home_final: int, away_final: int, winner: str) -> list[dict]:
    """Point/run/goal sports: interleave scoring plays across periods to reach the final."""
    cfg = _SCORING[sport]
    increments, descs, periods, plabel = cfg["inc"], cfg["desc"], cfg["periods"], cfg["plabel"]

    # Build the raw scoring "chunks" for each team, then place them on a clock.
    def chunks(total: int) -> list[int]:
        out: list[int] = []
        while total > 0:
            fits = [i for i in increments if i <= total]
            step = _pick(fits) if fits else total  # no increment fits → take the remainder as-is
            out.append(step)
            total -= step
        return out

    home_chunks = [("home", c) for c in chunks(home_final)]
    away_chunks = [("away", c) for c in chunks(away_final)]
    plays = home_chunks + away_chunks
    # shuffle order (CSPRNG) so scoring alternates naturally
    for i in range(len(plays) - 1, 0, -1):
        j = _rand(i + 1)
        plays[i], plays[j] = plays[j], plays[i]

    events: list[dict] = []
    h = a = 0
    n = len(plays)
    for idx, (team, pts) in enumerate(plays):
        if team == "home":
            h += pts
        else:
            a += pts
        # spread plays across the periods by progress
        period_idx = min(len(periods) - 1, idx * len(periods) // max(1, n))
        events.append({
            "clock": f"{periods[period_idx]}",
            "team": team,
            "home": h,
            "away": a,
            "desc": _pick(descs.get(pts, descs["*"])),
        })
    return events


# MLB: runs land in half-innings (top = away bats, bottom = home). Keeps it baseball-shaped.
def _timeline_mlb(home_final: int, away_final: int) -> list[dict]:
    descs = ["RBI single", "sac fly scores a run", "2-run double", "solo homer",
             "bases-loaded walk", "run scores on an error", "three-run shot"]
    events: list[dict] = []
    h = a = 0
    remaining_home, remaining_away = home_final, away_final
    for inning in range(1, 10):
        # away bats top, home bats bottom; scatter remaining runs across innings
        for team in ("away", "home"):
            rem = remaining_away if team == "away" else remaining_home
            innings_left = 9 - inning  # innings after this one
            if rem <= 0:
                continue
            # score 0..min(4, rem) this half, biased toward 0; dump any remainder in the 9th
            got = min(rem, _rand(4)) if innings_left > 0 else rem
            if got <= 0:
                continue
            if team == "away":
                a += got
                remaining_away -= got
                half = f"Top {inning}"
            else:
                h += got
                remaining_home -= got
                half = f"Bot {inning}"
            events.append({"clock": half, "team": team, "home": h, "away": a,
                           "desc": (descs[_rand(len(descs))] if got == 1 else f"{got}-run inning")})
    return events


# Tennis: score IS sets. Emit one event per set with a games line; winner takes the last set.
def _timeline_tennis(winner: str) -> tuple[int, int, list[dict]]:
    best_of = 3
    need = best_of // 2 + 1  # 2
    h_sets = a_sets = 0
    events: list[dict] = []
    set_num = 0
    while h_sets < need and a_sets < need:
        set_num += 1
        # decide the set winner: the match winner takes enough sets; sprinkle one to the loser
        if winner == "home":
            set_to = "home" if (h_sets < need - 1 or a_sets >= 1 or set_num >= 2) else _pick(["home", "away"])
        else:
            set_to = "away" if (a_sets < need - 1 or h_sets >= 1 or set_num >= 2) else _pick(["home", "away"])
        # games line, e.g. 6-4 / 7-6 / 6-3
        loser_games = _pick([1, 2, 3, 4, 4, 5, 6])
        win_games = 7 if loser_games == 6 else 6
        if set_to == "home":
            h_sets += 1
            line = f"{win_games}-{loser_games}"
        else:
            a_sets += 1
            line = f"{loser_games}-{win_games}"
        events.append({"clock": f"Set {set_num}", "team": set_to,
                       "home": h_sets, "away": a_sets, "desc": f"wins the set {line}"})
    return h_sets, a_sets, events


_SCORING = {
    "nba": {
        "inc": [2, 2, 2, 3, 3, 1],
        "periods": ["Q1", "Q1", "Q2", "Q2", "Q3", "Q3", "Q4", "Q4"],
        "plabel": "Q",
        "desc": {2: ["layup", "mid-range jumper", "and-one finish", "putback"],
                 3: ["three-pointer", "deep three", "corner three"],
                 1: ["free throw"], "*": ["bucket"]},
    },
    "nfl": {
        "inc": [7, 3, 7, 3, 6, 2, 8],
        "periods": ["Q1", "Q1", "Q2", "Q2", "Q3", "Q3", "Q4", "Q4"],
        "plabel": "Q",
        "desc": {7: ["touchdown + XP", "TD, extra point good"],
                 3: ["field goal"], 6: ["touchdown, XP no good"],
                 2: ["safety", "two-point conversion"], 8: ["TD + 2-pt"], "*": ["score"]},
    },
}


def build(sport: str, winner: str) -> tuple[int, int, list[dict]]:
    """Return (home_final, away_final, timeline) for a sport given the decided winner."""
    if sport == "tennis":
        return _timeline_tennis(winner)
    if sport == "mlb":
        h, a = _final_mlb(winner)
        return h, a, _timeline_mlb(h, a)
    if sport == "soccer":
        h, a = _final_soccer(winner)
        # goals scatter across 90'
        events, ch, ca = [], 0, 0
        plays = [("home", 1)] * h + [("away", 1)] * a
        for i in range(len(plays) - 1, 0, -1):
            j = _rand(i + 1)
            plays[i], plays[j] = plays[j], plays[i]
        minute = 0
        for team, _ in plays:
            minute = min(90, minute + 5 + _rand(20))
            if team == "home":
                ch += 1
            else:
                ca += 1
            events.append({"clock": f"{minute}'", "team": team, "home": ch, "away": ca,
                           "desc": "GOAL!"})
        return h, a, events
    # nba / nfl
    final = _final_nba(winner) if sport == "nba" else _final_nfl(winner)
    h, a = final
    return h, a, _timeline_scoring(sport, h, a, winner)


if __name__ == "__main__":
    # self-check: final matches last event, winner ends ahead, timeline is monotone
    for sp in ("nba", "nfl", "mlb", "soccer", "tennis"):
        for w in ("home", "away"):
            for _ in range(200):
                h, a, tl = build(sp, w)
                if tl:
                    assert tl[-1]["home"] == h and tl[-1]["away"] == a, (sp, w, h, a, tl[-1])
                    ph = pa = 0
                    for e in tl:  # cumulative never decreases
                        assert e["home"] >= ph and e["away"] >= pa, (sp, e)
                        ph, pa = e["home"], e["away"]
                assert (h > a) == (w == "home") and h != a, (sp, w, h, a)
    print("simplay self-check OK")
