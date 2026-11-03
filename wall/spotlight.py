"""Election night: the TV's spotlight queue. Someone picks a state or one race on the TV's phone remote, a watch-page vote
passes, the owner pushes one, or an event does (a race call, first votes, a big Kalshi move) → it goes on top of the L-bar
sidebar's queue (QMAX deep, the oldest evicted, each gone after SHOW_S) with a card: the state's main races (status, top two
candidates), their Kalshi prices + 24 h change and a sparkline of the favourite. Never over the video: the overlay draws it
in the sidebar (wall/qml/Spot.qml). The discord wall mirrors the TV, so the stream (and Discord) sees it too.

The card's data comes from sharplab-web (GET /api/v1/wall/election/spot/{ST}, X-Api-Key — the same path as the bets feed),
which owns the Kalshi rate limit and the caches; this side asks once when a state is picked and every REFRESH_S while it
shows. No positions on it, ever.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable

from wall.election import STATES

log = logging.getLogger("wall.spotlight")
SHOW_S = 600.0     # an item stays this long unless newer ones push it out first
REFRESH_S = 15.0
QMAX = 3           # the sidebar's queue: newest on top, the oldest evicted
RACE = re.compile(r"^(?P<o>[SGH])-(?P<st>[A-Z]{2})(-\d{1,2})?(-special)?$")


class Spotlight:
    """A queue of spotlight items (a state, or one race), newest first. set() pushes to the top (an item already in it
    moves up), the queue keeps QMAX, and each item leaves after show_s by itself."""

    def __init__(self, http: Callable[[str, str, dict | None], tuple[int, object]] | None = None, clock=time.time,
                 show_s: float = SHOW_S, spawn: Callable | None = None) -> None:
        self.http, self.clock, self.show_s = http, clock, show_s
        self.spawn = spawn or (lambda f: threading.Thread(target=f, name="spotlight", daemon=True).start())
        self.items: list[dict] = []   # {key, state, race, by, at, card, fetched, error}
        self.rev = 0
        self.lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return self.http is not None

    def set(self, state: str, by: str = "", race: str = "") -> dict | None:
        """Push a state (two-letter code) or one race of it ("S-GA", "H-WI-3") to the top; state "" closes them all.
        ValueError on anything else."""
        st, race = (state or "").strip().upper(), (race or "").strip()
        m = RACE.match(race) if race else None
        if race and not m:
            raise ValueError("race is an id like S-GA or H-WI-3")
        st = st or (m["st"] if m else "")
        if st and st not in STATES or m and m["st"] != st:
            raise ValueError("state is a two-letter state code")
        with self.lock:
            if not st:
                self.items = []
            else:
                key = race or st
                self.items = [{"key": key, "state": st, "race": race, "by": (by or "")[:40], "at": self.clock(), "card": None,
                               "fetched": 0.0, "error": ""}] + [i for i in self.items if i["key"] != key]
                del self.items[QMAX:]
            self.rev += 1
        if st:
            self.spawn(lambda: self.refresh(key))
        return self.view()

    def _live(self) -> list[dict]:
        now = self.clock()
        return [i for i in self.items if now - i["at"] < self.show_s]

    def active(self) -> bool:
        return bool(self._live())

    def due(self) -> bool:
        now = self.clock()
        return any(now - i["fetched"] >= REFRESH_S for i in self._live())

    def refresh(self, key: str | None = None) -> None:
        """Fetch the cards from the web (blocking: a thread / to_thread): the one item `key`, else every due one."""
        if not self.http:
            return
        now = self.clock()
        with self.lock:
            todo = [dict(i) for i in self._live() if i["key"] == key or key is None and now - i["fetched"] >= REFRESH_S]
        for it in todo:
            path = f"/api/v1/wall/election/spot/{it['state']}" + (f"?race={it['race']}" if it["race"] else "")
            try:
                status, data = self.http("GET", path, None)
            except (OSError, ValueError) as e:
                status, data = 0, {"error": f"web unreachable: {e}"[:120]}
            ok = status == 200 and isinstance(data, dict) and isinstance(data.get("races"), list)
            if ok and it["race"]:   # one race: just its card (the web puts it first when asked)
                data = {**data, "races": [r for r in data["races"] if r.get("id") == it["race"]][:1] or data["races"][:1], "more": 0}
            err = "" if ok else ((data or {}).get("error") if isinstance(data, dict) else "") or f"web answered {status}"
            with self.lock:
                for i in self.items:
                    if i["key"] == it["key"]:
                        i["fetched"] = self.clock()
                        if ok:
                            i["card"], i["error"] = data, ""
                        else:
                            i["error"] = err
                            log.warning("spotlight %s: %s", it["key"], err)
                self.rev += 1

    def view(self) -> dict | None:
        """What the overlay draws (None = nothing): {"items": [...newest first]} plus the newest item's fields at the top
        level (state, name, by, left, card, error) for the phone's picker."""
        with self.lock:
            live = self._live()
            if not live:
                return None
            now = self.clock()
            items = []
            for i in live:
                card = i["card"]   # a race item's office is on its race line: the title stays the state's name
                items.append({"key": i["key"], "state": i["state"], "race": i["race"], "name": STATES.get(i["state"], i["state"]), "by": i["by"],
                              "left": round(self.show_s - (now - i["at"]), 1), "show_s": self.show_s, "card": card,
                              "error": i["error"] if card is None else ""})
            return {**items[0], "items": items, "rev": self.rev}


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


ODDS_S = 10.0       # the races tile's Kalshi odds: asked for this often while the election pages are looked at (no delay)
ODDS_STALE_S = 120.0


class Odds:
    """The key races' Kalshi favourites for the races tile: {race id: {who, party, prob, chg24}} from sharplab-web
    (GET /api/v1/wall/election/odds, X-Api-Key; it owns the Kalshi rate limit + caches). Fetched every ODDS_S while an
    election page / board was looked at lately (Results.seen); odds older than ODDS_STALE_S are dropped, not shown."""

    def __init__(self, http: Callable[[str, str, dict | None], tuple[int, object]] | None = None, clock=time.time,
                 idle_s: float = 180.0) -> None:
        self.http, self.clock, self.idle_s = http, clock, idle_s
        self.data: dict[str, dict] = {}
        self.at = 0.0        # when data last came
        self.fetched = 0.0   # when it was last asked for (ok or not)

    def due(self, seen: float) -> bool:
        now = self.clock()
        return self.http is not None and now - seen < self.idle_s and now - self.fetched >= ODDS_S

    def refresh(self) -> None:
        if not self.http:
            return
        self.fetched = self.clock()
        try:
            status, data = self.http("GET", "/api/v1/wall/election/odds", None)
        except (OSError, ValueError) as e:
            log.warning("odds: web unreachable: %s", e)
            return
        if status != 200 or not isinstance(data, dict):
            log.warning("odds: web answered %s", status)
            return
        self.data = {k: {f: v.get(f) for f in ("who", "party", "prob", "chg24")} for k, v in data.items()
                     if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("prob"), (int, float))}
        self.at = self.clock()

    def view(self) -> dict[str, dict]:
        return self.data if self.clock() - self.at < ODDS_STALE_S else {}
