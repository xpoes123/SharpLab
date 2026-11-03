"""Election night: what's worth a look pushes itself onto the TV. A race call, a key race's first votes or a big Kalshi
move (MOVE_PTS within MOVE_S on the favourite) goes on top of
the spotlight queue (wall/spotlight.py) and a rotating data tile jumps to that office's map (Instance.show_screen).
The first look at the results is the baseline: a restart never replays the night's calls."""
from __future__ import annotations

MAX_PER_CHECK = 3   # a burst at poll close (a dozen calls at once) shows the first few; the board's ticker has the rest
MOVE_PTS, MOVE_S, MOVE_QUIET_S = 10.0, 900.0, 1200.0   # ≥10 pts inside 15 min; then that race stays quiet 20 min


class Events:
    def __init__(self) -> None:
        self.calls: set[str] | None = None      # call ids already seen
        self.counting: set[str] | None = None   # key races with votes already
        self.prices: dict[str, list[tuple[float, float, str]]] = {}   # race → [(t, prob, who)] inside MOVE_S
        self.moved: dict[str, float] = {}       # race → when its last move event went out

    def check_odds(self, odds: dict[str, dict], races: dict[str, dict], now: float) -> list[dict]:
        """Kalshi moves: the race's favourite (same person) moved ≥ MOVE_PTS within MOVE_S → one event, then quiet a while."""
        out = []
        for rid, o in odds.items():
            p = o.get("prob")
            if p is None or rid not in races:
                continue
            h = [x for x in self.prices.get(rid, []) if now - x[0] <= MOVE_S and x[2] == o.get("who")] + [(now, float(p), o.get("who"))]
            self.prices[rid] = h
            swing = max(abs(p - x[1]) for x in h)
            if swing >= MOVE_PTS and now - self.moved.get(rid, -1e9) >= MOVE_QUIET_S:
                self.moved[rid] = now
                self.prices[rid] = [h[-1]]
                out.append({"race": rid, "by": f"Kalshi move {round(swing)} pts", "state": races[rid]["state"], "office": races[rid]["office"]})
        return out[:MAX_PER_CHECK]

    def check(self, snap: dict) -> list[dict]:
        """New since the last look: [{state, race, office, by}], calls first."""
        races = {r["id"]: r for r in snap.get("races") or []}
        if not races:
            return []
        calls = {c["id"] for c in snap.get("calls") or [] if c.get("id")}
        counting = {i for i, r in races.items() if r.get("watch") and (r.get("votes") or 0) > 0}
        if self.calls is None:
            self.calls, self.counting = calls, counting
            return []
        out = [{"race": i, "by": "race call"} for i in sorted(calls - self.calls)] + \
              [{"race": i, "by": "first votes"} for i in sorted(counting - self.counting) if i not in calls]
        self.calls |= calls
        self.counting |= counting
        return [{**e, "state": races[e["race"]]["state"], "office": races[e["race"]]["office"]}
                for e in out if e["race"] in races][:MAX_PER_CHECK]
