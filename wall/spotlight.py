"""Election night: the TV's state spotlight. Someone picks a state on the TV's phone remote (or a watch-page vote passes,
or the owner pushes one) → for SHOW_S the L-bar sidebar shows a card for it: the state's main races (status, top two
candidates), their Kalshi prices + 24 h change and a sparkline of the favourite. Never over the video: the overlay draws it
in the sidebar (wall/qml/Spot.qml). The discord wall mirrors the TV, so the stream (and Discord) sees it too.

The card's data comes from sharplab-web (GET /api/v1/wall/election/spot/{ST}, X-Api-Key — the same path as the bets feed),
which owns the Kalshi rate limit and the caches; this side asks once when a state is picked and every REFRESH_S while it
shows. No positions on it, ever.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from wall.election import STATES

log = logging.getLogger("wall.spotlight")
SHOW_S = 30.0
REFRESH_S = 15.0


class Spotlight:
    def __init__(self, http: Callable[[str, str, dict | None], tuple[int, object]] | None = None, clock=time.time,
                 show_s: float = SHOW_S, spawn: Callable | None = None) -> None:
        self.http, self.clock, self.show_s = http, clock, show_s
        self.spawn = spawn or (lambda f: threading.Thread(target=f, name="spotlight", daemon=True).start())
        self.state = ""
        self.by = ""
        self.until = 0.0
        self.card: dict | None = None
        self.fetched = 0.0
        self.error = ""
        self.rev = 0
        self.lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return self.http is not None

    def set(self, state: str, by: str = "") -> dict:
        """Show a state (two-letter code) for show_s from now, or "" to dismiss. ValueError on anything else."""
        st = (state or "").strip().upper()
        if st and st not in STATES:
            raise ValueError("state is a two-letter state code")
        with self.lock:
            if st and st != self.state:
                self.card, self.fetched, self.error = None, 0.0, ""
            self.state, self.by = st, (by or "")[:40] if st else ""
            self.until = self.clock() + self.show_s if st else 0.0
            self.rev += 1
        if st:
            self.spawn(self.refresh)
        return self.view()

    def active(self) -> bool:
        return bool(self.state) and self.clock() < self.until

    def due(self) -> bool:
        return self.active() and self.clock() - self.fetched >= REFRESH_S

    def refresh(self) -> None:
        """Fetch the card from the web (blocking: a thread / to_thread)."""
        st = self.state
        if not st or not self.http:
            return
        try:
            status, data = self.http("GET", f"/api/v1/wall/election/spot/{st}", None)
        except (OSError, ValueError) as e:
            status, data = 0, {"error": f"web unreachable: {e}"[:120]}
        with self.lock:
            if st != self.state:
                return
            self.fetched = self.clock()
            if status == 200 and isinstance(data, dict) and isinstance(data.get("races"), list):
                self.card, self.error = data, ""
                self.rev += 1
            else:
                self.error = (data or {}).get("error", f"web answered {status}") if isinstance(data, dict) else f"web answered {status}"
                log.warning("spotlight %s: %s", st, self.error)

    def view(self) -> dict | None:
        """What the overlay draws (None = nothing): {state, name, by, left, card, error, rev}."""
        with self.lock:
            if not self.active():
                return None
            return {"state": self.state, "name": STATES.get(self.state, self.state), "by": self.by,
                    "left": round(self.until - self.clock(), 1), "show_s": self.show_s, "card": self.card,
                    "error": self.error if self.card is None else "", "rev": self.rev}


def state_list(snap: dict) -> list[dict]:
    """The phone's picker: every state with a race tonight — the ones with a Senate / Governor race to watch first, then
    the other Senate / Governor states, then House-only ones; alphabetical within each."""
    by: dict[str, dict] = {}
    for r in snap.get("races") or []:
        s = by.setdefault(r["state"], {"st": r["state"], "name": STATES.get(r["state"], r["state"]), "rank": 2, "called": 0, "n": 0})
        s["n"] += 1
        s["called"] += bool(r.get("called"))
        if r["office"] in ("S", "G"):
            s["rank"] = min(s["rank"], 0 if r.get("watch") else 1)
    return [{k: v for k, v in s.items() if k != "rank"} | {"key": s["rank"] == 0}
            for s in sorted(by.values(), key=lambda s: (s["rank"], s["name"]))]
