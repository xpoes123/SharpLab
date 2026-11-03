"""Election night turnout: who has voted, by registered party, from each state's official feed.

A state's source is a small function that fetches its official file and returns the running totals now:
    {"as_of": "8:48 AM PT" | "", "eday": {"D": n, "R": n, "O": n}, "advance": {...} | None,
     "counties": [{"name": str, "D": n, "R": n, "O": n}] | None, "note": str}
(party = voter registration, never vote choice; "O" = everyone outside the two majors). Adding a state = one function in
SOURCES (wall/turnout_sources.py). Turnout keeps every change of a state's E-day total as a batch (the community
trackers' view: a batch's size and party split), saved so a restart keeps the night's history.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("wall.turnout")
POLL_S, IDLE_S = 120.0, 900.0
PARTIES = ("D", "R", "O", "N")   # N = a count with no party split (the state publishes a total only)


def _sum(d: dict | None) -> int:
    return sum(int(d.get(p) or 0) for p in PARTIES) if d else 0


def margin(d: dict | None) -> float | None:
    """D minus R as a share of everyone in it (+ = Dem), or None when empty."""
    n = sum(int((d or {}).get(p) or 0) for p in ("D", "R", "O"))
    return None if not n else (int(d.get("D") or 0) - int(d.get("R") or 0)) / n


class Turnout:
    def __init__(self, sources: dict[str, tuple[str, str, Callable[[], dict]]] | None = None,
                 state_dir: str | Path | None = None, clock=time.time, poll_s: float = POLL_S) -> None:
        if sources is None:
            from wall.turnout_sources import SOURCES
            sources = SOURCES
        self.sources = sources            # ST → (publisher name, its public URL, fetch)
        self.clock, self.poll_s = clock, poll_s
        self.path = Path(state_dir) / "turnout.json" if state_dir else None
        self.states: dict[str, dict] = self._load()
        self.seen = 0.0
        self.lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text()) if self.path and self.path.exists() else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        if not self.path:
            return
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.states))
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("turnout save: %s", e)

    def touch(self) -> None:
        self.seen = self.clock()
        with self.lock:
            if not (self._thread and self._thread.is_alive()):
                self._thread = threading.Thread(target=self._run, name="turnout", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while self.clock() - self.seen < IDLE_S:
            self.poll()
            time.sleep(self.poll_s)

    def poll(self) -> None:
        for st, (name, url, fetch) in self.sources.items():
            try:
                got = fetch()
            except Exception as e:  # noqa: BLE001 — keep the last good numbers, say why
                log.warning("turnout %s: %s", st, e)
                with self.lock:
                    self.states.setdefault(st, {"st": st, "batches": []}).update(error=str(e)[:120], source=name, url=url)
                continue
            self.take(st, name, url, got)
        with self.lock:
            self._save()

    def take(self, st: str, name: str, url: str, got: dict) -> None:
        now = self.clock()
        with self.lock:
            s = self.states.setdefault(st, {"st": st, "batches": []})
            prev = s.get("eday") or {}
            eday = {p: int((got.get("eday") or {}).get(p) or 0) for p in PARTIES}
            if _sum(prev) and _sum(eday) > _sum(prev):   # a new report adds a batch (the first poll is the baseline; lower = a correction)
                add = {p: eday[p] - int(prev.get(p) or 0) for p in PARTIES}
                s["batches"] = ([{"t": now, "as_of": got.get("as_of") or "", **add}] + s["batches"])[:200]
            s.update(source=name, url=url, eday=eday, advance=got.get("advance"), counties=got.get("counties"),
                     as_of=got.get("as_of") or "", note=got.get("note") or "", error="", checked=now)

    def snapshot(self, st: str | None = None) -> dict:
        """{"states": {ST: view}} (or one state's view). A view adds totals and margins so pages only format."""
        with self.lock:
            states = json.loads(json.dumps(self.states if st is None else {st: self.states[st]} if st in self.states else {}))
        for s in states.values():
            adv = s.get("advance")
            s["total"] = {p: int((s.get("eday") or {}).get(p) or 0) + int((adv or {}).get(p) or 0) for p in PARTIES}
            s["m_eday"], s["m_adv"], s["m_total"] = margin(s.get("eday")), margin(adv), margin(s["total"])
            for b in s.get("batches") or []:
                b["m"] = margin(b)
        out = {"states": states, "now": self.clock(), "covered": sorted(self.sources)}
        return out if st is None else {**out, "state": states.get(st)}
