"""A recorded night replayed, sped up: WALL_ELECTION_REPLAY="<dir>:sim[=seconds]" plays 2024's election night (CNN's final
files in <dir>, the test fixtures) from 5 pm to 2 am ET in `seconds` of real time (default DUR_S), so the vote-updates
timeline, the live feed and the TV's "Latest" have something real-shaped to show before tonight's polls close.

Each race starts counting at its own poll close (CNN's assignedPollsClose, moved onto the sped-up clock so the closings
happen live too) and climbs to its final count at its own pace; the early vote leans one way per race and evens out, so
the close ones change leads the way real nights do. A race is called once ~85 % is in (a blowout at the close). County
files (cnn-county-<key>.json[.gz]) play along at the race's pace, each county at its own speed. Deterministic per race.
"""
from __future__ import annotations

import gzip
import json
import time
import zlib
from pathlib import Path

from wall.election import Cnn

NIGHT = 1730844000.0     # 2024-11-05 17:00 ET (an hour of nothing but upcoming poll closings)
SPAN = 9 * 3600.0        # … to 02:00 ET
DUR_S = 900.0


def _k(s: str) -> float:
    return (zlib.crc32(s.encode()) % 1000) / 1000


def _load(root: Path, name: str):
    for f in (root / f"{name}.json", root / f"{name}.json.gz"):
        if f.exists():
            return json.loads(gzip.decompress(f.read_bytes()) if f.suffix == ".gz" else f.read_text())
    raise FileNotFoundError(name)


class SimCnn(Cnn):
    name = "CNN (2024 replay)"

    def __init__(self, root: Path, dur: float = DUR_S, clock=time.time) -> None:
        self.root, self.dur, self.clock = Path(root), max(1.0, dur), clock
        self.t0 = clock()
        self.cache: dict = {}
        super().__init__(2024, self._fetch)

    # the sped-up clock
    def sim_now(self) -> float:
        return NIGHT + (self.clock() - self.t0) * SPAN / self.dur

    def real(self, sim_t: float) -> float:
        return self.t0 + (sim_t - NIGHT) * self.dur / SPAN

    def share(self, key: str, close: float) -> float:
        """How much of this race is in now: nothing before its polls close, then its own curve to 1."""
        k = _k(key)
        el = self.sim_now() - close
        if el <= 0:
            return 0.0
        return min(1.0, (el / (3600 * (2.0 + 3.0 * k))) ** 0.7)

    def _file(self, name: str):
        if name not in self.cache:
            self.cache[name] = _load(self.root, name)
        return self.cache[name]

    def _fetch(self, url: str):
        tail = url.split("/results/", 1)[-1].removesuffix(".json")
        kind, _, name = tail.partition("/")
        if kind == "bop":
            return self._file(f"cnn-bop-{name}")
        if kind == "national-races":
            return [self.race_now(r) for r in self._file(f"cnn-{name}")]
        if kind == "race":
            o = name.split("-")[1][:1]
            r = next((x for x in self._file(f"cnn-2024-{o}G") if x.get("ecKey") == name), None)
            if r is None:
                raise FileNotFoundError(name)
            return self.race_now(r)
        if kind == "county-races":
            key = name
            if "-PG-" in key:   # the baseline (last presidential): as it was, final
                return self._file(f"cnn-county-{key}")
            o = key.split("-")[1][:1]
            r = next((x for x in self._file(f"cnn-2024-{o}G") if x.get("ecKey") == key), None)
            close = ((r or {}).get("raceEmbargo") or {}).get("assignedPollsClose", 0) / 1000
            f = self.share(key, close)
            return [self._scale(c, key, f ** (0.6 + 0.8 * _k(c.get("countyFipsCode", "")))) for c in self._file(f"cnn-county-{key}")]
        raise FileNotFoundError(tail)

    def _scale(self, r: dict, key: str, f: float) -> dict:
        r = json.loads(json.dumps(r))
        lean = (1 if _k(key + "lean") < 0.5 else -1) * 0.12 * (1 - f)   # the early vote leans D (+) or R (−), then evens out
        tot = 0
        for c in r.get("candidates") or []:
            p = (c.get("majorParty") or "")[:1]
            w = 1 + (lean if p == "D" else -lean if p == "R" else 0)
            c["voteNum"] = int((c.get("voteNum") or 0) * f * w)
            tot += c["voteNum"]
        r["totalVote"] = tot
        r["percentReporting"] = round(99 * f) if f < 1 else 100
        r["voteTimestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(self.clock()))
        return r

    def race_now(self, r: dict) -> dict:
        key = r.get("ecKey", "")
        emb = dict(r.get("raceEmbargo") or {})
        close = (emb.get("assignedPollsClose") or 0) / 1000
        f = self.share(key, close)
        out = self._scale(r, key, f)
        out["raceEmbargo"] = {**emb, "assignedPollsClose": int(1000 * self.real(close)) if close else 0}
        cs = r.get("candidates") or []
        tot = sum(c.get("voteNum") or 0 for c in cs) or 1
        top = sorted((c.get("voteNum") or 0 for c in cs), reverse=True) + [0, 0]
        blowout = (top[0] - top[1]) / tot > 0.15
        if not (f >= 0.85 or (blowout and f > 0)):
            for c in out.get("candidates") or []:
                c["winner"] = False
            out["editorialStatus"], out["status"], out["isSwitch"] = "scheduled", "U", False
        return out
