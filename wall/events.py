"""Election night: what's worth a look pushes itself onto the TV. A race call or a key race's first votes goes on top of
the spotlight queue (wall/spotlight.py) and a rotating data tile jumps to that office's map (Instance.show_screen).
The first look at the results is the baseline: a restart never replays the night's calls."""
from __future__ import annotations

MAX_PER_CHECK = 3   # a burst at poll close (a dozen calls at once) shows the first few; the board's ticker has the rest


class Events:
    def __init__(self) -> None:
        self.calls: set[str] | None = None      # call ids already seen
        self.counting: set[str] | None = None   # key races with votes already

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
