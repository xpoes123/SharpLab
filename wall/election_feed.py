"""Election night's vote updates: every poll of the results (wall/election.py Results) is diffed against the last one per
race, and each change becomes an update — a vote drop, a lead change, a call — saved with its time (and, on the web, the
Kalshi price at that moment when it's already in the cache). Poll closings come from the races' own closing times.

* A race's timeline (the state sheet's "vote updates": "+12,400 votes · 9:42 PM · Talarico +1.2 → +0.8") is every
  change of that race, newest first.
* The live feed (the watch page's "Live updates", the TV's "Latest") is the updates that matter across all races, ranked
  by significance: a lead change > a big drop in a close race > a call / polls closing > a routine drop.
* Everything survives restarts: $WALL_STATE_DIR/election-history.json (rolling, written at most every SAVE_S). The last
  snapshot of each race is saved too, so a restart diffs against it instead of reporting the whole count as one drop.
Pure apart from that file: the caller passes the time.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("wall.election")

SIG = {"lead": 4, "big": 3, "call": 2, "polls": 2, "drop": 1}
CLOSE_PTS = 5.0           # a race within this many points is close
BIG_VOTES = 25_000        # a drop this big (or BIG_SHARE of the race's votes so far) is big
BIG_SHARE = 0.05
MIN_DROP = 1_000          # smaller drops stay in the race's timeline but not in the all-races feed (unless a key race)
PER_RACE = {"S": 200, "G": 200, "H": 30}
FEED_MAX = 400
SAVE_S = 20.0
OFFICE = {"S": "Senate", "G": "Governor", "H": "House"}
SHORT = {"S": "SEN", "G": "GOV", "H": "HOUSE"}


def head(r: dict) -> str:
    """TX SEN / GA GOV / PA-7 HOUSE / OH SEN (sp.)"""
    if r["office"] == "H":
        return f"{r['state']}-{r.get('district') or 'AL'} HOUSE"
    return f"{r['state']} {SHORT[r['office']]}" + (" (sp.)" if r.get("special") else "")


def signed(x: float) -> str:
    return ("+" if x >= 0 else "−") + (f"{abs(x):.2f}" if 0 < abs(x) < 0.1 else f"{abs(x):.1f}")


def top2(r: dict) -> tuple[dict | None, dict | None]:
    cs = sorted(r.get("cands") or [], key=lambda c: -(c.get("votes") or 0))
    return (cs[0] if cs else None), (cs[1] if len(cs) > 1 else None)


def margin_of(votes: dict[str, int], a: str, b: str | None) -> tuple[float, int]:
    """(a's margin over b in points of all votes, in votes)."""
    tot = sum(votes.values()) or 1
    mv = votes.get(a, 0) - (votes.get(b, 0) if b else 0)
    return round(100 * mv / tot, 2), mv


def state_of(r: dict) -> dict:
    """What's kept of a race between polls (and across restarts)."""
    return {"votes": int(r.get("votes") or 0), "pct": float(r.get("pct") or 0), "called": r.get("called") or "",
            "cv": {c["name"]: int(c.get("votes") or 0) for c in r.get("cands") or []}}


def diff(r: dict, last: dict | None, t: float, price: dict | None = None) -> list[dict]:
    """One race now vs its last state → its updates (none on the first sight of it, or when the count went backwards —
    a stale copy of the feed, not news)."""
    if last is None:
        return []
    out = []
    base = {"race": r["id"], "state": r["state"], "office": r["office"], "label": r.get("label", ""), "head": head(r),
            "t": t, "key": bool(r.get("watch")), "votes": int(r.get("votes") or 0), "pct": float(r.get("pct") or 0),
            "pct0": last["pct"], "px": price}
    dv = int(r.get("votes") or 0) - last["votes"]
    a, b = top2(r)
    if dv > 0 and a and (a.get("votes") or 0) > 0:
        now_v = {c["name"]: int(c.get("votes") or 0) for c in r["cands"]}
        m1, mv1 = margin_of(now_v, a["name"], b["name"] if b else None)
        m0, mv0 = margin_of(last["cv"], a["name"], b["name"] if b else None) if last["votes"] else (0.0, 0)
        lead_change = last["votes"] > 0 and mv0 < 0 < mv1
        close = abs(m1) < CLOSE_PTS or abs(m0) < CLOSE_PTS
        big = close and (dv >= BIG_VOTES or (last["votes"] and dv >= BIG_SHARE * last["votes"]))
        kind = "lead" if lead_change else "big" if big else "drop"
        text = f"+{dv:,} votes · {a['name']} {signed(m0)} → {signed(m1)}" if last["votes"] else f"First votes · {a['name']} {signed(m1)}"
        if lead_change:
            text = f"{a['name']} takes the lead · " + text
        out.append({**base, "kind": kind, "sig": SIG[kind], "dv": dv, "lead": a["name"], "party": a.get("party") or "",
                    "m0": m0, "m1": m1, "mv": mv1, "text": text,
                    "feed": kind != "drop" or dv >= MIN_DROP or base["key"]})
    if r.get("called") and not last["called"]:
        w = next((c for c in r.get("cands") or [] if c.get("winner")), None)
        who = f"{w['name']} ({w['party']})" if w else r["called"]
        out.append({**base, "kind": "call", "sig": SIG["call"], "dv": 0, "lead": w["name"] if w else "", "party": r["called"],
                    "text": f"{who} wins · call: {r.get('caller') or '?'}" + (" · flip" if r.get("flip") else ""),
                    "flip": bool(r.get("flip")), "feed": True})
    return out


def closings(races: list[dict], after: float, upto: float) -> list[dict]:
    """Polls that closed in (after, upto]: one update per closing time, with its states."""
    by: dict[float, set] = {}
    for r in races:
        c = r.get("closes") or 0
        if after < c <= upto:
            by.setdefault(c, set()).add(r["state"])
    return [{"race": "", "state": "", "office": "", "label": "", "head": "POLLS", "t": t, "key": True, "kind": "polls",
             "sig": SIG["polls"], "states": sorted(s), "text": "Polls closed · " + " ".join(sorted(s)), "feed": True}
            for t, s in sorted(by.items())]


def upcoming(races: list[dict], now: float, n: int = 3) -> list[dict]:
    ts = sorted({r["closes"] for r in races if (r.get("closes") or 0) > now})[:n]
    return [{"t": t, "states": sorted({r["state"] for r in races if r.get("closes") == t})} for t in ts]


def rank(items: list[dict]) -> list[dict]:
    """Most significant first; ties: a key race / Senate / Governor before a House race, the bigger drop, the newer."""
    return sorted(items, key=lambda u: (-u["sig"], not (u["key"] or u["office"] in ("S", "G", "")), -(u.get("dv") or 0), -u["t"]))


class Feed:
    """The rolling history: each race's timeline + the poll closings, persisted."""

    def __init__(self, state_dir: str | Path | None = None) -> None:
        self.path = Path(state_dir) / "election-history.json" if state_dir else None
        self.lock = threading.Lock()
        self.races: dict[str, list[dict]] = {}      # race id → its updates, newest first
        self.polls: list[dict] = []
        self.last: dict[str, dict] = {}
        self.closed_upto = 0.0
        self.saved_at, self.dirty = 0.0, False
        self._load()

    def _load(self) -> None:
        try:
            d = json.loads(self.path.read_text()) if self.path and self.path.exists() else {}
        except (OSError, ValueError) as e:
            log.warning("election history: %s", e)
            d = {}
        self.races, self.polls = d.get("races") or {}, d.get("polls") or []
        self.last, self.closed_upto = d.get("last") or {}, float(d.get("closed_upto") or 0)

    def save(self, now: float, force: bool = False) -> None:
        if not self.path or not self.dirty or (not force and now - self.saved_at < SAVE_S):
            return
        with self.lock:
            blob = json.dumps({"races": self.races, "polls": self.polls, "last": self.last, "closed_upto": self.closed_upto},
                              separators=(",", ":"))
            self.dirty = False
        self.saved_at = now
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(blob)
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("election history: %s", e)

    def observe(self, races: list[dict], now: float, price=None, full: bool = True) -> list[dict]:
        """A poll's races (full = every race, from the national files; else a few, from the fast lane) → the new updates.
        price(race) → {"who", "party", "prob"} or None: the Kalshi price now, only when it's cheap (already cached)."""
        new = []
        with self.lock:
            for r in races:
                last = self.last.get(r["id"])
                if last is not None and int(r.get("votes") or 0) < last["votes"]:
                    continue   # an older copy than the one we have (fast lane vs national file): not news
                ups = diff(r, last, now)
                if ups and price:
                    try:
                        px = price(r)
                    except Exception:  # noqa: BLE001 — a price is a nice-to-have
                        px = None
                    for u in ups:
                        u["px"] = px
                if ups:
                    tl = self.races.setdefault(r["id"], [])
                    tl[:0] = ups[::-1]
                    del tl[PER_RACE.get(r["office"], 60):]
                    new += ups
                st = state_of(r)
                if st != last:
                    self.last[r["id"]] = st
                    self.dirty = True
            if full:
                if not self.closed_upto:
                    self.closed_upto = now - 12 * 3600   # first run: the closings of the last 12 h, at their own times
                cl = closings(races, self.closed_upto, now)
                if cl:
                    self.polls = (cl[::-1] + self.polls)[:40]
                    new += cl
                if now > self.closed_upto:
                    self.closed_upto, self.dirty = now, True
            if new:
                self.dirty = True
        self.save(now)
        return new

    def timeline(self, race_id: str, n: int = 60) -> list[dict]:
        with self.lock:
            return list(self.races.get(race_id) or [])[:n]

    def items(self, key_only: bool = False, since: float = 0) -> list[dict]:
        """Every feed-worthy update (all races + poll closings), newest first."""
        with self.lock:
            out = [u for tl in self.races.values() for u in tl if u.get("feed") and u["t"] > since and (u["key"] or not key_only)]
            out += [p for p in self.polls if p["t"] > since]
        return sorted(out, key=lambda u: -u["t"])

    def latest(self, now: float, window: float = 45 * 60, n: int = 8) -> list[dict]:
        """The TV's "Latest": the most significant feed items of the last `window`, shown newest first; the routine
        drops only fill in when nothing more significant happened."""
        recent = self.items(since=now - window)
        top = [u for u in rank(recent) if u["sig"] >= 2 or u["key"]][:n]
        return sorted(top, key=lambda u: -u["t"])
