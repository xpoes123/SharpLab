"""Election night "vs 2024": tonight's key races against the 2024 presidential vote (the swing.html data tile).

  * Race swing: each key race counting, its margin now (Dem minus Rep, points) against 2024's presidential margin in the
    same state (Senate / Governor) or district (House) → the swing (+ = toward the Dems). Before votes: the baselines.
  * County swing (the early signal): in each key Senate / Governor race, the counties >= MIN_PCT reporting, now vs the
    last presidential race in that county → the vote-weighted median and mean swing + the biggest movers. County files
    are CNN's (the same ones the state sheet uses, web/wall_election_live.py): county-races/<2026 key> now, and
    county-races/<20xx-PG-ST> (wall/election_counties.baseline_key) as the baseline — final, so fetched once.
  * Turnout vs 2024: votes counted so far per key state as a share of its 2024 presidential vote.

The statewide + district baselines are vendored (wall/baseline2024.json, its "about" says where they're from). A race
with no Democrat (NE Senate: Osborn, I) counts its top independent as the Dem side, flagged "I".
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from wall import election as we
from wall import election_counties as ec

log = logging.getLogger("wall.swing")
BASE = json.loads((Path(__file__).with_name("baseline2024.json")).read_text())
POLL_S, IDLE_S = 120.0, 300.0
MIN_PCT = 95
MOVERS = 3


def dside(pairs: list[tuple[str, int]]) -> tuple[int, int, int, str]:
    """[(party D/R/I/O, votes)] → (Dem-side votes, Rep votes, all votes, "D" | "I"): no Democrat on the ballot = the top
    independent stands in for the Dem side."""
    d = sum(v for p, v in pairs if p == "D")
    r = sum(v for p, v in pairs if p == "R")
    t = sum(v for _, v in pairs)
    if not any(p == "D" for p, _ in pairs):
        ind = [v for p, v in pairs if p in ("I", "O")]
        return (max(ind) if ind else 0), r, t, "I"
    return d, r, t, "D"


def margin(d: int, r: int, t: int) -> float | None:
    return round(100 * (d - r) / t, 2) if t else None


def baseline(race: dict) -> float | None:
    """2024 presidential margin (D−R points) where this race is: the state, or the district for a House race."""
    if race["office"] == "H":
        return BASE["house"].get(f"{race['state']}-{race.get('district') or 1}")
    s = BASE["states"].get(race["state"])
    return margin(*s) if s else None


def race_rows(races: list[dict]) -> tuple[list[dict], list[dict]]:
    """(key races counting with their swing, most votes first; every key race's 2024 baseline, for before the votes)."""
    counting, watch = [], []
    for r in races:
        if not r.get("watch"):
            continue
        was = baseline(r)
        row = {k: r.get(k) for k in ("id", "office", "state", "district", "label", "votes", "pct", "leader", "margin", "called")}
        row["was"] = was
        watch.append(row)
        if r.get("votes"):
            d, rr, t, side = dside([(c["party"], c["votes"]) for c in r.get("cands") or []])
            now = margin(d, rr, t)
            counting.append({**row, "now": now, "side": side,
                             "swing": round(now - was, 1) if now is not None and was is not None else None})
    counting.sort(key=lambda x: -(x["votes"] or 0))
    watch.sort(key=lambda x: ("SGH".index(x["office"]), abs(x["was"]) if x["was"] is not None else 99, x["label"]))
    return counting, watch


def wmedian(pairs: list[tuple[float, float]]) -> float | None:
    """Weighted median of [(value, weight)]: the value where the running weight first reaches half."""
    pairs = sorted((v, w) for v, w in pairs if w > 0)
    tot = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= tot / 2:
            return v
    return None


def cnn_pairs(cands: list[dict]) -> list[tuple[str, int]]:
    return [(we.party(c.get("majorParty") or c.get("candidatePartyCode")), int(c.get("voteNum") or 0)) for c in cands]


def county_swing(now: list[dict] | None, base: list[dict] | None, min_pct: float = MIN_PCT) -> dict | None:
    """CNN county files (now, last presidential) → the counties >= min_pct reporting: weighted (by votes now) median +
    mean swing and the biggest movers. None without both files."""
    if not now or not base:
        return None
    prev = {}
    for c in base:
        d, r, t, _ = dside(cnn_pairs(c.get("candidates") or []))
        if t:
            prev[c.get("countyFipsCode")] = margin(d, r, t)
    rows = []
    for c in now:
        votes = int(c.get("totalVote") or 0)
        was = prev.get(c.get("countyFipsCode"))
        if (c.get("percentReporting") or 0) < min_pct or not votes or was is None:
            continue
        d, r, t, _ = dside(cnn_pairs(c.get("candidates") or []))
        if not t:
            continue
        m = margin(d, r, t)
        rows.append({"name": c.get("countyName") or c.get("countyFipsCode"), "votes": t, "now": round(m, 1), "was": round(was, 1),
                     "swing": round(m - was, 1)})
    w = sum(x["votes"] for x in rows)
    return {"n": len(rows), "of": len(now), "votes": w,
            "median": wmedian([(x["swing"], x["votes"]) for x in rows]),
            "mean": round(sum(x["swing"] * x["votes"] for x in rows) / w, 1) if w else None,
            "movers": sorted(rows, key=lambda x: -abs(x["swing"]))[:MOVERS]}


def turnout(races: list[dict]) -> list[dict]:
    """Per key state: votes counted so far (its biggest statewide race, else its House races added up) / 2024's total."""
    sts = sorted({r["state"] for r in races if r.get("watch")})
    out = []
    for st in sts:
        mine = [r for r in races if r["state"] == st]
        wide = [r for r in mine if r["office"] in ("S", "G")]
        top = max(wide, key=lambda r: r.get("votes") or 0) if wide else None
        votes = (top.get("votes") or 0) if top and top.get("votes") else sum(r.get("votes") or 0 for r in mine if r["office"] == "H")
        b = BASE["states"].get(st)
        out.append({"state": st, "name": we.STATES.get(st, st), "votes": votes, "was": b[2] if b else None,
                    "share": round(100 * votes / b[2], 1) if b and b[2] else None, "pct": (top or {}).get("pct")})
    out.sort(key=lambda x: (-(x["votes"] or 0), x["state"]))
    return out


class Swing:
    """The county files for the key Senate / Governor races counting, fetched every POLL_S while swing.json is asked for
    (touch); the baselines once. snapshot() never waits on the network."""

    def __init__(self, results: we.Results, clock=time.time, poll_s: float = POLL_S) -> None:
        self.results, self.clock, self.poll_s = results, clock, poll_s
        self.now: dict[str, tuple[str, list]] = {}   # race id → (its baseline key, its county file now)
        self.base: dict[str, list] = {}      # baseline key → its county file (final)
        self.error, self.updated, self.seen = "", 0.0, 0.0
        self.lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def touch(self) -> None:
        self.seen = self.clock()
        with self.lock:
            if not (self._thread and self._thread.is_alive()):
                self._thread = threading.Thread(target=self._run, name="swing", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while self.clock() - self.seen < IDLE_S:
            try:
                self.poll()
            except Exception as e:  # noqa: BLE001
                log.warning("swing: %s", e)
            time.sleep(self.poll_s)

    def _src(self) -> we.Cnn:
        return next((s for s in self.results.sources if isinstance(s, we.Cnn)), None) or we.Cnn()

    def poll(self) -> int:
        src = self._src()
        races = [r for r in self.results.snapshot().get("races") or []
                 if r.get("watch") and r["office"] in ("S", "G") and r.get("votes")]
        got, errs = 0, []
        for r in races:
            key = r.get("key") or f"{src.year}-{r['office']}{'P' if r.get('special') else 'G'}-{r['state']}"
            bkey = ec.baseline_key(key)
            try:
                if bkey not in self.base:
                    self.base[bkey] = src.fetch(f"{src.base}/county-races/{bkey}.json")
                self.now[r["id"]] = (bkey, src.fetch(f"{src.base}/county-races/{key}.json"))
                got += 1
            except Exception as e:  # noqa: BLE001 — that race skips this round
                errs.append(f"{r['id']} {type(e).__name__}")
        self.updated = self.clock()
        self.error = f"county files missing: {', '.join(errs[:4])}" if errs and not got else ""
        return got

    def snapshot(self) -> dict:
        snap = self.results.snapshot()
        races = snap.get("races") or []
        counting, watch = race_rows(races)
        counties = []
        for r in races:
            if r["id"] not in self.now or not r.get("watch"):
                continue
            bkey, rows = self.now[r["id"]]
            cs = county_swing(rows, self.base.get(bkey))
            if cs and cs["n"]:
                counties.append({"id": r["id"], "label": r["label"], "office": r["office"], "state": r["state"],
                                 "base": f"{bkey[:4]} presidential", **cs})
        counties.sort(key=lambda x: -x["votes"])
        return {"races": counting, "watch": watch, "counties": counties, "turnout": turnout(races), "min_pct": MIN_PCT,
                "source": snap.get("source", ""), "updated": snap.get("updated", 0), "counties_at": self.updated,
                "error": snap.get("error") or self.error, "now": snap.get("now") or self.clock()}
