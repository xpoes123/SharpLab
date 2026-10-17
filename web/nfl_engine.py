"""Down-by-down NFL game simulator for the web Sports Sim "simcast".

Given two team-parameter dicts (schema in web/nfl_teams_2026.py), play out a full
game snap by snap: downs, distance, field position, run/pass by team tendency, play
outcomes, 4th-down logic, clock, quarters and OT. The final score EMERGES from the
drives, and every play is recorded for the animated play-by-play, so the log and the
score can never disagree.

Pure and deterministic given a seed (seeded from os.urandom per game by the caller),
so it is fast and testable. No I/O, no module-level randomness.

Clock model: each half is one continuous 1800s clock; the quarter (1-4) and the
quarter clock shown to the user are derived from it. A drive simply burns the half
clock and ends when it hits 0.

Output: GameResult(home, away, home_score, away_score, plays=[dict], drives=[dict]).
Field position `fieldpos` is ABSOLUTE 0..100 (0 = home end zone, 100 = away end zone);
the home team scores by reaching 100, the away team by reaching 0.
"""

from __future__ import annotations

import random
import secrets
from dataclasses import dataclass, field

HALF_SECONDS = 2 * 15 * 60  # 1800
OT_SECONDS = 10 * 60

# Clock burned per play (seconds). Incompletions / out-of-bounds stop the clock.
_CLOCK = {"run": 38, "pass_cmp": 34, "pass_inc": 6, "sack": 36,
          "punt": 12, "fg": 6, "pat": 4}

_LEAGUE = {  # fallback params; real values come from web/nfl_teams_2026.py
    "name": "Team", "abbr": "???", "elo": 1500,
    "pass_rate": 0.58, "pace": 63, "ypa": 6.8, "ypc": 4.3, "cmp_rate": 0.65,
    "sack_rate": 0.065, "int_rate": 0.024, "fum_lost_rate": 0.009,
    "def_ypa": 6.8, "def_ypc": 4.3, "def_takeaway_rate": 0.11, "rz_td_rate": 0.56,
}


def _p(team: "_Team", key: str) -> float:
    return team.params.get(key, _LEAGUE[key])


@dataclass
class _Team:
    abbr: str
    params: dict
    score: int = 0


@dataclass
class GameResult:
    home: str
    away: str
    home_score: int
    away_score: int
    plays: list = field(default_factory=list)
    drives: list = field(default_factory=list)

    @property
    def winner(self) -> str:  # "home" | "away" | "tie"
        if self.home_score > self.away_score:
            return "home"
        if self.away_score > self.home_score:
            return "away"
        return "tie"


class _Sim:
    def __init__(self, home: dict, away: dict, rng: random.Random):
        self.rng = rng
        self.home = _Team(home.get("abbr", "HOME"), home)
        self.away = _Team(away.get("abbr", "AWAY"), away)
        self.plays: list = []
        self.drives: list = []
        self.half = 1
        self.clock = HALF_SECONDS
        self.ot = False

    # ── clock / display ─────────────────────────────────────────────────────
    def _q(self) -> int:
        if self.ot:
            return 5
        return (0 if self.half == 1 else 2) + (1 if self.clock > 900 else 2)

    def _clock_str(self) -> str:
        qc = self.clock - 900 if (not self.ot and self.clock > 900) else self.clock
        qc = max(0, qc)
        return f"{qc // 60}:{qc % 60:02d}"

    def _two_min(self) -> bool:
        return self.clock <= 120

    def _burn(self, kind: str, two_min: bool) -> None:
        t = _CLOCK[kind]
        if two_min and kind in ("run", "pass_cmp", "sack"):
            t = int(t * 0.55)  # hurry-up
        self.clock -= t

    # ── field position ──────────────────────────────────────────────────────
    def _fieldpos(self, off: _Team, ownyl: float) -> int:
        return round(ownyl if off is self.home else 100 - ownyl)

    def _opp(self, t: _Team) -> _Team:
        return self.away if t is self.home else self.home

    def _emit(self, off, deff, ptype, yards, result, desc,
              down=None, togo=None, ownyl=None):
        self.plays.append({
            "q": self._q(), "clock": self._clock_str(),
            "off": off.abbr, "def": deff.abbr,
            "down": down, "togo": (None if togo is None else max(0, round(togo))),
            "fieldpos": None if ownyl is None else self._fieldpos(off, ownyl),
            "type": ptype, "yards": round(yards), "result": result, "desc": desc,
            "home": self.home.score, "away": self.away.score,
        })

    # ── outcome sampling ────────────────────────────────────────────────────
    def _rush_yards(self, off, deff) -> float:
        mean = (_p(off, "ypc") + _p(deff, "def_ypc")) / 2
        if self.rng.random() < 0.04:
            return self.rng.uniform(15, 55)
        return max(-5, self.rng.gauss(mean, 3.4))

    def _pass_yards(self, off, deff) -> float:
        cmp_rate = _p(off, "cmp_rate")
        ypa = (_p(off, "ypa") + _p(deff, "def_ypa")) / 2
        ypc = ypa / max(0.45, cmp_rate)  # yards per completion
        if self.rng.random() < 0.06:
            return self.rng.uniform(20, 65)
        return max(0, self.rng.gauss(ypc, 5.5))

    def _pass_prob(self, off, down, togo, lead) -> float:
        p = _p(off, "pass_rate")
        if down >= 3 and togo >= 7:
            p += 0.25
        elif down >= 3 and togo <= 2:
            p -= 0.18
        if self._two_min():
            p += 0.2
        if self._q() >= 4:
            if lead <= -9:
                p += 0.2
            elif lead >= 9:
                p -= 0.25
        return min(0.95, max(0.08, p))

    # ── one drive → returns (next_off, next_ownyl) or None if the half ended ──
    def _drive(self, off, deff, start_ownyl):
        ownyl = max(1.0, min(99.0, start_ownyl))
        down, togo = 1, min(10.0, 100 - ownyl)
        pid = 0
        start_fp = self._fieldpos(off, ownyl)

        def record(result, points):
            self.drives.append({
                "off": off.abbr, "start": start_fp, "end": self._fieldpos(off, ownyl),
                "plays": pid, "result": result, "points": points, "q": self._q(),
            })

        while True:
            if self.clock <= 0:
                record("end of half", 0)
                return None
            two_min = self._two_min()
            lead = off.score - deff.score
            togo = min(togo, 100 - ownyl)

            # ── 4th-down decision ──
            if down == 4:
                fg_dist = (100 - ownyl) + 17
                go = (togo <= 2 and ownyl >= 50) or \
                     (lead <= -8 and self._q() >= 4 and ownyl >= 45) or \
                     (two_min and lead < 0)
                if not go:
                    if ownyl >= 62 and fg_dist <= 56:
                        return self._kick_fg(off, deff, ownyl, fg_dist, record)
                    return self._do_punt(off, deff, ownyl, record)
                # else fall through and go for it

            pid += 1
            is_pass = self.rng.random() < self._pass_prob(off, down, togo, lead)

            if is_pass:
                r = self.rng.random()
                if r < _p(off, "sack_rate"):
                    loss = min(ownyl - 1, self.rng.uniform(4, 9))
                    ownyl -= loss
                    down += 1
                    togo += loss
                    self._emit(off, deff, "sack", -loss, "sack",
                               f"{off.abbr} QB sacked -{round(loss)}", down - 1, togo - loss, ownyl)
                    self._burn("sack", two_min)
                elif r < _p(off, "sack_rate") + _p(off, "int_rate") / max(0.4, _p(off, "cmp_rate")):
                    self._emit(off, deff, "pass", 0, "interception",
                               f"{off.abbr} pass INTERCEPTED!", down, togo, ownyl)
                    self._burn("pass_inc", two_min)
                    record("interception", 0)
                    return self._flip(off, deff, ownyl, 10)
                elif self.rng.random() < _p(off, "cmp_rate"):
                    y = self._pass_yards(off, deff)
                    ownyl += y
                    if ownyl >= 100:
                        return self._touchdown(off, deff, "pass", y,
                                               f"{off.abbr} pass complete", record, pid, start_fp)
                    down, togo = self._advance(off, deff, ownyl, y, down, togo, "pass",
                                               f"{off.abbr} pass complete +{round(y)}")
                    self._burn("pass_cmp", two_min)
                else:
                    down += 1
                    self._emit(off, deff, "pass", 0, "incomplete",
                               f"{off.abbr} pass incomplete", down - 1, togo, ownyl)
                    self._burn("pass_inc", two_min)
            else:
                if self.rng.random() < _p(off, "fum_lost_rate"):
                    y = self._rush_yards(off, deff) * 0.5
                    ownyl = max(1.0, ownyl + y)
                    self._emit(off, deff, "run", y, "fumble",
                               f"{off.abbr} FUMBLE lost!", down, togo, ownyl)
                    self._burn("run", two_min)
                    record("fumble", 0)
                    return self._flip(off, deff, ownyl, 5)
                y = self._rush_yards(off, deff)
                ownyl += y
                if ownyl >= 100:
                    return self._touchdown(off, deff, "run", y,
                                           f"{off.abbr} run", record, pid, start_fp)
                down, togo = self._advance(off, deff, ownyl, y, down, togo, "run",
                                           f"{off.abbr} run +{round(y)}")
                self._burn("run", two_min)

            # safety
            if ownyl <= 0:
                deff.score += 2
                self._emit(off, deff, "run", 0, "safety", f"SAFETY — {deff.abbr} +2",
                           down, togo, max(0.0, ownyl))
                record("safety", 0)
                return (deff, 30.0)  # free kick → ~own 30

            if down > 4:
                self._emit(off, deff, "run", 0, "downs",
                           f"{off.abbr} turnover on downs", down, togo, ownyl)
                record("turnover on downs", 0)
                return self._flip(off, deff, ownyl, 0)

    # ── drive-ending helpers (each returns next possession or None) ──────────
    def _advance(self, off, deff, ownyl, y, down, togo, ptype, desc):
        if y >= togo:
            self._emit(off, deff, ptype, y, "first_down", desc + " (1st down)",
                       down, togo, ownyl)
            return 1, min(10.0, 100 - ownyl)
        self._emit(off, deff, ptype, y, "gain", desc, down, togo, ownyl)
        return down + 1, togo - y

    def _flip(self, off, deff, ownyl, cushion):
        """Opponent takes over; their own-yardline mirrors the spot."""
        if self.clock <= 0:
            return None
        new_own = max(1.0, min(99.0, 100 - ownyl + self.rng.uniform(-cushion, cushion)))
        return (deff, new_own)

    def _touchdown(self, off, deff, ptype, y, desc, record, pid, start_fp):
        off.score += 6
        self._emit(off, deff, ptype, y, "touchdown", f"{desc} — TOUCHDOWN!", None, None, 100)
        self._try(off, deff)
        self.drives.append({
            "off": off.abbr, "start": start_fp, "end": self._fieldpos(off, 100),
            "plays": pid, "result": "touchdown", "points": 6, "q": self._q(),
        })
        self._burn("pass_cmp" if ptype == "pass" else "run", self._two_min())
        if self.clock <= 0:
            return None
        return (deff, 25.0)  # kickoff → touchback

    def _try(self, off, deff):
        if self.rng.random() < 0.06:
            good = self.rng.random() < 0.48
            off.score += 2 if good else 0
            self._emit(off, deff, "2pt", 0, "two_point_good" if good else "two_point_fail",
                       f"{off.abbr} 2-pt {'GOOD' if good else 'failed'}")
        else:
            good = self.rng.random() < 0.94
            off.score += 1 if good else 0
            self._emit(off, deff, "xp", 0, "extra_point",
                       f"{off.abbr} XP {'good' if good else 'NO GOOD'}")

    def _kick_fg(self, off, deff, ownyl, fg_dist, record):
        prob = 0.97 - max(0, fg_dist - 30) * 0.016
        self._burn("fg", self._two_min())
        if self.rng.random() < max(0.3, prob):
            off.score += 3
            self._emit(off, deff, "fg", 0, "field_goal",
                       f"{off.abbr} {round(fg_dist)}-yd FG is GOOD", 4, None, ownyl)
            record("field goal", 3)
            return None if self.clock <= 0 else (deff, 25.0)
        self._emit(off, deff, "fg", 0, "missed_fg",
                   f"{off.abbr} {round(fg_dist)}-yd FG is NO GOOD", 4, None, ownyl)
        record("missed field goal", 0)
        return self._flip(off, deff, ownyl, 0)

    def _do_punt(self, off, deff, ownyl, record):
        self._burn("punt", self._two_min())
        net = self.rng.uniform(38, 48)
        self._emit(off, deff, "punt", 0, "punt", f"{off.abbr} punts", 4, None, ownyl)
        record("punt", 0)
        if self.clock <= 0:
            return None
        return (deff, max(1.0, min(99.0, 100 - (ownyl + net))))

    # ── halves / game ───────────────────────────────────────────────────────
    def _play_half(self, first_off):
        off = first_off
        deff = self._opp(off)
        ownyl = 25.0
        while self.clock > 0:
            nxt = self._drive(off, deff, ownyl)
            if nxt is None:
                return
            off, ownyl = nxt
            deff = self._opp(off)

    def play(self) -> GameResult:
        receives_2h = self.home if self.rng.random() < 0.5 else self.away
        first = self._opp(receives_2h)

        self.half, self.clock = 1, HALF_SECONDS
        self._play_half(first)
        self.half, self.clock = 2, HALF_SECONDS
        self._play_half(receives_2h)

        if self.home.score == self.away.score:
            self._overtime()

        return GameResult(self.home.abbr, self.away.abbr,
                          self.home.score, self.away.score, self.plays, self.drives)

    def _overtime(self):
        self.ot, self.clock = True, OT_SECONDS
        first = self.home if self.rng.random() < 0.5 else self.away
        nxt = self._drive(first, self._opp(first), 25.0)
        # if first team didn't score a TD, opponent gets a possession
        if self.home.score == self.away.score and self.clock > 0 and nxt is not None:
            off, ownyl = nxt
            self._drive(off, self._opp(off), ownyl)
        # still tied → a tie (rare, allowed)


def simulate(home: dict, away: dict, seed: int | None = None) -> GameResult:
    rng = random.Random(seed if seed is not None else secrets.randbits(128))
    return _Sim(home, away, rng).play()


# ── self-check ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import statistics
    strong = {**_LEAGUE, "abbr": "KC", "elo": 1680, "ypa": 7.6, "ypc": 4.8,
              "def_ypa": 6.0, "def_ypc": 3.9, "pass_rate": 0.58}
    weak = {**_LEAGUE, "abbr": "CAR", "elo": 1320, "ypa": 5.8, "ypc": 3.8,
            "def_ypa": 7.4, "def_ypc": 4.7, "pass_rate": 0.60}
    totals, margins, plays_ct, strong_wins, ties = [], [], [], 0, 0
    N = 4000
    for i in range(N):
        g = simulate(strong, weak, seed=i)
        assert g.plays and g.plays[-1]["home"] == g.home_score and g.plays[-1]["away"] == g.away_score
        # every scoring-type play must reconcile; scores only increase
        ph = pa = 0
        for pl in g.plays:
            assert pl["home"] >= ph and pl["away"] >= pa
            ph, pa = pl["home"], pl["away"]
        totals.append(g.home_score + g.away_score)
        margins.append(g.home_score - g.away_score)
        plays_ct.append(len(g.plays))
        if g.winner == "home":
            strong_wins += 1
        elif g.winner == "tie":
            ties += 1
    print(f"N={N}")
    print(f"mean total={statistics.mean(totals):.1f}  per-team={statistics.mean(totals)/2:.1f}  (real ~44, ~22)")
    print(f"strong margin={statistics.mean(margins):+.1f}  win%={strong_wins/N*100:.0f}  ties={ties}")
    print(f"mean plays/game={statistics.mean(plays_ct):.0f}  total range {min(totals)}-{max(totals)}")
    assert 34 <= statistics.mean(totals) <= 54, "unrealistic scoring"
    assert 0.60 < strong_wins / N < 0.92, "favorite win% off"
    assert 120 <= statistics.mean(plays_ct) <= 190, "play count off"
    print("nfl_engine self-check OK")
