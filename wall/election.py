"""Election night results for the wall's data tiles and its "Election" board (2026 US midterms, 2026-11-03).

Sources, tried in order every poll (the first that answers with races wins; all are the public JSON behind news sites'
own results pages — no key, no login, no paywall; checked 2026-11-03 02:00 ET, all three returning the 2026 races with
zero votes):

  1. CNN   politics.api.cnn.io/results/national-races/2026-{SG,GG,HG}.json + bop/2026-{SG,GG,HG}-US.json
           (~70 KB gzipped per round; their own Decision Desk calls; `raceToWatch` = CNN's key races; a balance-of-power
           feed with each party's net gain). 2024's files are still up, final — the test fixtures are those.
  2. NYT   static01.nyt.com/elections-assets/pages/data/2026-11-03/results-{senate,governor,house}.json (AP votes and
           AP calls, `outcome.called_by`; House is 6.8 MB raw, so only as a fallback).
  3. Fox   feeds-elections.foxnews.com/…/2026_Generals/{Senate,Governor,House}/national_level_summary/file.json (Fox
           News Decision Desk calls; no balance feed — seats not up come from the table below).

Everything is unofficial until certified; a race is "called" only when its source's desk called it, and every view
says whose call it is. Polls run only while someone looks (a page or the board asked in the last 3 min): every POLL_S,
gzip. The latest calls are remembered across restarts ($WALL_STATE_DIR/election-calls.json).
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("wall.election")
POLL_S = 30
IDLE_S = 180
UA = {"User-Agent": "Mozilla/5.0 (homelab tv election wall)", "Accept-Encoding": "gzip", "Accept": "application/json"}

OFFICES = {   # seats in all, how many to control the chamber, seats not up this year (2026) by party
    "S": {"name": "Senate", "total": 100, "control": {"D": 51, "R": 50}, "not_up": {"D": 34, "R": 31}},   # D 34 = 32 + 2 I caucusing; VP is R
    "H": {"name": "House", "total": 435, "control": {"D": 218, "R": 218}, "not_up": {}},
    "G": {"name": "Governors", "total": 50, "control": {}, "not_up": {"D": 6, "R": 8}},
}
STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
          "CT": "Connecticut", "DE": "Delaware", "DC": "D.C.", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
          "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
          "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
          "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
          "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
          "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
          "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
          "WI": "Wisconsin", "WY": "Wyoming"}


def get_json(url: str, timeout: float = 20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
    return json.loads(raw)


def party(code: str | None) -> str:
    """Any feed's party code → D / R / I (independents, incl. ones caucusing with a party) / O."""
    c = (code or "").upper().rstrip(".")
    if c in ("D", "DEM", "DEMOCRAT", "DEMOCRATIC"):
        return "D"
    if c in ("R", "REP", "GOP", "REPUBLICAN"):
        return "R"
    if c in ("I", "IND", "INDEPENDENT"):
        return "I"
    return "O"


def race(office: str, state: str, district: int | None, special: bool, cands: list[dict], *, pct: float, votes: int,
         called: str, caller: str, flip: bool, watch: bool, rating: str = "", closes: float = 0, uncontested: bool = False) -> dict:
    """The one race shape every source becomes (and the pages + the board read)."""
    # most votes first; before any: the winner, the major parties, incumbents, then the feed's own order (sorted is stable)
    cands = sorted(cands, key=lambda c: (-(c["votes"] or 0), not c["winner"], c["party"] not in ("D", "R"), not c["inc"]))
    if votes > 0:
        tot = sum(c["votes"] for c in cands) or 1
        for c in cands:
            c["pct"] = round(100 * c["votes"] / tot, 1)
    leader = cands[0]["party"] if cands and votes > 0 else ""
    margin = round(cands[0]["pct"] - cands[1]["pct"], 1) if len(cands) > 1 and votes > 0 else None
    rid = f"{office}-{state}" + (f"-{district}" if district else "") + ("-special" if special else "")
    label = f"{state}-{district or 'AL'}" if office == "H" else STATES.get(state, state) + (" (special)" if special else "")
    return {"id": rid, "office": office, "state": state, "district": district, "special": special, "label": label,
            "cands": cands, "pct": round(float(pct or 0), 1), "votes": int(votes or 0), "leader": leader, "margin": margin,
            "called": called, "caller": caller if called else "", "flip": bool(flip and called), "watch": bool(watch),
            "rating": rating, "closes": closes, "uncontested": uncontested}


def cand(name: str, full: str, p: str, votes: int, pct: float, winner: bool, inc: bool) -> dict:
    return {"name": name, "full": full, "party": party(p), "votes": int(votes or 0), "pct": float(pct or 0), "winner": bool(winner),
            "inc": bool(inc)}


# ---- sources ---------------------------------------------------------------------------------------------------------------
class Cnn:
    name, desk = "CNN", "CNN"
    base = "https://politics.api.cnn.io/results"

    def __init__(self, year: int = 2026, fetch=get_json) -> None:
        self.year, self.fetch = year, fetch

    def poll(self) -> dict:
        races, bop = [], {}
        for o in OFFICES:
            for r in self.fetch(f"{self.base}/national-races/{self.year}-{o}G.json"):
                if r.get("contestType", f"{o}G")[1:] in ("G", "P"):   # not HQ: TX-18's special for the rest of this term
                    races.append(self.race(r))
            try:
                bop[o] = self.bop(self.fetch(f"{self.base}/bop/{self.year}-{o}G-US.json"))
            except Exception as e:  # noqa: BLE001 — the seat counts come from the races anyway
                log.info("cnn bop %s: %s", o, e)
        return {"races": races, "net": bop}

    def race(self, r: dict) -> dict:
        key = r.get("ecKey", "")          # 2026-SG-TX, 2026-SP-FL (special), 2026-HG-PA-1
        parts = key.split("-")
        office = (r.get("raceTypeCode") or r.get("raceType") or parts[1][:1])[:1]
        dist = int(parts[3]) if office == "H" and len(parts) > 3 and parts[3].isdigit() else None
        cs = [cand(c.get("lastName") or c.get("fullName", ""), c.get("fullName", ""), c.get("majorParty") or c.get("candidatePartyCode"),
                   c.get("voteNum"), c.get("votePercentNum"), c.get("winner"), c.get("isIncumbent")) for c in r.get("candidates") or []]
        called = next((c["party"] for c in cs if c["winner"]), "") if r.get("editorialStatus") == "called" or r.get("status") == "W" else ""
        emb = r.get("raceEmbargo") or {}
        return race(office, r.get("stateAbbreviation", ""), dist, len(parts) > 1 and parts[1].startswith(("SP", "HP")), cs,
                    pct=r.get("percentReporting") or 0, votes=r.get("totalVote") or 0, called=called, caller=self.desk,
                    flip=r.get("isSwitch"), watch=r.get("raceToWatch"), closes=(emb.get("assignedPollsClose") or 0) / 1000,
                    uncontested=bool(r.get("uncontested")))

    @staticmethod
    def bop(b: dict) -> dict:
        ps = (b.get("meta") or {}).get("parties") or {}
        return {"net": {party(k): v.get("netGain") or 0 for k, v in ps.items() if k in ("DEM", "REP")},
                "not_up": {party(k): v.get("notUpForElection") or 0 for k, v in ps.items() if k in ("DEM", "REP")}}


class Nyt:
    name, desk = "NYT (AP)", "AP"
    base = "https://static01.nyt.com/elections-assets/pages/data/2026-11-03"
    FILES = {"S": "senate", "G": "governor", "H": "house"}

    def __init__(self, fetch=get_json) -> None:
        self.fetch = fetch

    def poll(self) -> dict:
        races = []
        for o, f in self.FILES.items():
            for r in self.fetch(f"{self.base}/results-{f}.json").get("races") or []:
                if r.get("office_id") == o and r.get("type") in ("General", "Special General", "Nonpartisan Election") \
                        and r.get("coverage") != "national":
                    races.append(self.race(o, r))
        return {"races": races, "net": {}}

    def race(self, o: str, r: dict) -> dict:
        meta = r.get("candidate_metadata") or {}
        top = r.get("top_reporting_unit") or {}
        won = set((r.get("outcome") or {}).get("won") or [])
        cs = []
        for c in top.get("candidates") or []:
            m = meta.get(c.get("nyt_id"), {})
            full = " ".join(x for x in (m.get("first_name"), m.get("last_name")) if x)
            cs.append(cand(m.get("last_name") or c.get("nyt_id", ""), full, (m.get("party") or {}).get("nyt_id"),
                           (c.get("votes") or {}).get("total"), 0, c.get("nyt_id") in won, m.get("incumbent")))
        by = [str(x.get("name") or x.get("id") or "" if isinstance(x, dict) else x).upper()
              for x in (r.get("outcome") or {}).get("called_by") or []]
        called = next((c["party"] for c in cs if c["winner"]), "")
        seat = r.get("seat") or ""
        dist = int(seat.split()[-1]) if o == "H" and seat.split() and seat.split()[-1].isdigit() else None
        return race(o, top.get("state_postal", ""), dist, "special" in str(r.get("type", "")).lower(), cs,
                    pct=top.get("eevp") or 0, votes=top.get("total_votes") or 0, called=called, caller=" · ".join(by) or self.desk,
                    flip=bool((r.get("outcome") or {}).get("gained")), watch=r.get("rating") in ("tossup", "lean-dem", "lean-rep"),
                    rating=r.get("rating") or "", uncontested=bool(r.get("uncontested")))


class Fox:
    name, desk = "Fox News", "Fox News"
    base = "https://feeds-elections.foxnews.com/archive/politics/elections/2026/3/2026_Generals"
    FILES = {"S": "Senate", "G": "Governor", "H": "House"}

    def __init__(self, fetch=get_json) -> None:
        self.fetch = fetch

    def poll(self) -> dict:
        races = []
        for o, f in self.FILES.items():
            for r in self.fetch(f"{self.base}/{f}/national_level_summary/file.json"):
                if r.get("isRunoff"):
                    continue
                cs = [cand(x["candidate"].get("lastName", ""), f"{x['candidate'].get('firstName', '')} {x['candidate'].get('lastName', '')}".strip(),
                           x.get("partyCode"), (x.get("votes") or {}).get("count"), (x.get("votes") or {}).get("percentage"),
                           x.get("isWinner"), x.get("isIncumbent")) for x in r.get("results") or []]
                called = next((c["party"] for c in cs if c["winner"]), "") if r.get("called") else ""
                races.append(race(o, r.get("stateCode", ""), r.get("districtNumber") if o == "H" else None,
                                  "special" in str(r.get("raceType", "")).lower(), cs, pct=r.get("expectedPercentage") or 0,
                                  votes=sum(c["votes"] for c in cs), called=called, caller=self.desk, flip=r.get("isFlipped"),
                                  watch=bool(r.get("keyWin"))))
        return {"races": races, "net": {}}


# ---- the summary every view draws ----------------------------------------------------------------------------------------
def balance(races: list[dict], office: str, bop: dict | None = None) -> dict:
    """Seats by party for one office: not up + won (called), leading (uncalled, votes in), still out; who controls, net.
    bop = the source's own balance feed ({"net", "not_up"}), when it has one."""
    o = OFFICES[office]
    bop = bop or {}
    rs = [r for r in races if r["office"] == office]
    not_up = bop.get("not_up") or o["not_up"]
    seats = {p: {"held": not_up.get(p, 0), "won": 0, "lead": 0} for p in ("D", "R", "I")}
    for r in rs:
        p = r["called"] or r["leader"]
        if p in ("O",):
            p = "I"
        if p and r["called"]:
            seats[p]["won"] += 1
        elif p:
            seats[p]["lead"] += 1
    flips = {"D": 0, "R": 0}
    for r in rs:
        if r["flip"] and r["called"] in flips:
            flips[r["called"]] += 1
            flips["R" if r["called"] == "D" else "D"] -= 1
    net = {p: (bop.get("net") or {}).get(p, flips[p]) for p in ("D", "R")}
    total = {p: seats[p]["held"] + seats[p]["won"] for p in seats}
    winner = next((p for p, need in o["control"].items() if total[p] >= need), "")
    return {"office": office, "name": o["name"], "total": o["total"], "up": len(rs), "control": o["control"], "seats": seats,
            "counted": total, "out": len(rs) - sum(s["won"] + s["lead"] for s in seats.values()),
            "called": sum(1 for r in rs if r["called"]), "net": net, "winner": winner}


def call_text(r: dict) -> str:
    w = next((c for c in r["cands"] if c["winner"]), None)
    what = {"S": "Senate", "G": "Governor", "H": "House"}[r["office"]]
    who = f"{w['name']} ({w['party']})" if w else r["called"]
    return f"{r['label']} {what}: {who} wins" + (" · flip" if r["flip"] else "")


class Results:
    """The cached results + the calls log. touch() = someone is looking (starts / keeps the poller); snapshot() never
    blocks on the network."""

    def __init__(self, sources=None, state_dir: str | Path | None = None, clock=time.time, poll_s: float = POLL_S) -> None:
        self.sources = sources if sources is not None else [Cnn(), Nyt(), Fox()]
        self.clock, self.poll_s = clock, poll_s
        self.path = Path(state_dir) / "election-calls.json" if state_dir else None
        self.races: list[dict] = []
        self.net: dict = {}
        self.source, self.updated, self.error = "", 0.0, ""
        self.tried: list[dict] = []
        self.calls: list[dict] = self._load()
        self.seen = 0.0
        self.lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def _load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text()) if self.path and self.path.exists() else []
        except (OSError, ValueError):
            return []

    def touch(self) -> None:
        self.seen = self.clock()
        with self.lock:
            if not (self._thread and self._thread.is_alive()):
                self._thread = threading.Thread(target=self._run, name="election", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while self.clock() - self.seen < IDLE_S:
            self.poll()
            time.sleep(self.poll_s)

    def poll(self) -> bool:
        tried = []
        for s in self.sources:
            t0 = time.monotonic()
            try:
                got = s.poll()
                if not got["races"]:
                    raise ValueError("no races")
            except Exception as e:  # noqa: BLE001 — next source
                tried.append({"source": s.name, "ok": False, "error": str(e)[:120]})
                log.warning("election source %s: %s", s.name, e)
                continue
            tried.append({"source": s.name, "ok": True, "ms": round(1000 * (time.monotonic() - t0))})
            self._take(s, got)
            self.tried = tried
            return True
        self.tried = tried
        self.error = "no results feed answered" + (f" (kept {self.source}'s from {time.strftime('%-I:%M %p', time.localtime(self.updated))})" if self.updated else "")
        return False

    def _take(self, s, got: dict) -> None:
        now = self.clock()
        before = {r["id"]: r["called"] for r in self.races}
        known = {c["id"] for c in self.calls}
        new = []
        for r in got["races"]:
            if r["called"] and r["id"] not in known and (not before or before.get(r["id"]) != r["called"]):
                new.append({"id": r["id"], "t": now if before else 0, "office": r["office"], "party": r["called"],
                            "flip": r["flip"], "text": call_text(r), "by": r["caller"]})
        with self.lock:
            self.races, self.net = got["races"], got.get("net") or {}
            self.source, self.updated, self.error = s.name, now, ""
            if new:
                self.calls = sorted(self.calls + new, key=lambda c: -c["t"])[:60]
                if self.path:
                    try:
                        tmp = self.path.with_suffix(".tmp")
                        tmp.write_text(json.dumps(self.calls))
                        os.replace(tmp, self.path)
                    except OSError as e:
                        log.warning("election calls: %s", e)

    def snapshot(self) -> dict:
        with self.lock:
            races, net = list(self.races), dict(self.net)
            out = {"source": self.source, "updated": self.updated, "error": self.error, "tried": self.tried,
                   "official": False, "calls": self.calls[:30]}
        out["offices"] = {o: balance(races, o, net.get(o)) for o in OFFICES}
        out["races"] = races
        out["now"] = self.clock()
        return out


def board(snap: dict) -> dict:
    """The overlay's "election" board: balance per office + the latest calls + the next poll closings."""
    now = snap.get("now") or time.time()
    closes = sorted({r["closes"] for r in snap.get("races") or [] if r.get("closes", 0) > now})
    nxt = []
    for t in closes[:2]:
        sts = sorted({r["state"] for r in snap["races"] if r.get("closes") == t})
        nxt.append({"t": t, "states": sts})
    counting = sum(1 for r in snap.get("races") or [] if r["votes"] > 0)
    return {"kind": "election", "title": "Election Night", "source": snap.get("source", ""), "official": False,
            "offices": [snap["offices"][o] for o in ("S", "H", "G")] if snap.get("offices") else [],
            "calls": snap.get("calls") or [], "next": nxt, "counting": counting,
            "watch": [r for r in sorted(snap.get("races") or [], key=lambda r: (r["called"] != "", r["margin"] if r["margin"] is not None else 99))
                      if r["watch"] and r["office"] in ("S", "G") and r["votes"] > 0][:6],
            "note": "" if snap.get("races") else (snap.get("error") or "Loading results…")}


# ---- recorded data (tests, page renders, a dry run before polls close) ------------------------------------------------------
def replay(spec: str) -> list:
    """WALL_ELECTION_REPLAY = "<dir>[:<share>]": CNN's files as recorded in dir (national-races/<year>-SG.json … or
    flat cnn-<year>-SG.json, bop too; .json or .json.gz) instead of the network. With a share (0–1), the night is
    replayed as if only that share were in: votes scaled, and only the races with that much counted stay called."""
    d, _, share = spec.partition(":")
    root = Path(d)
    year = next((int(p.name.split("-")[1]) for p in sorted(root.glob("cnn-*-SG.json*"))), 2024)

    def fetch(url: str):
        kind, name = ("bop" if "/bop/" in url else "races"), url.rsplit("/", 1)[1].removesuffix(".json")
        for f in (root / f"cnn-{'bop-' if kind == 'bop' else ''}{name}.json", root / f"cnn-{'bop-' if kind == 'bop' else ''}{name}.json.gz"):
            if f.exists():
                data = json.loads(gzip.decompress(f.read_bytes()) if f.suffix == ".gz" else f.read_text())
                return partial(data, float(share)) if share and kind == "races" else data
        raise FileNotFoundError(name)
    return [Cnn(year, fetch)]


def partial(races: list[dict], share: float) -> list[dict]:
    """A recorded final night as if `share` of it were counted (deterministic per race: a stable spread of shares)."""
    import zlib
    out = []
    for r in races:
        r = json.loads(json.dumps(r))
        k = (zlib.crc32(r.get("ecKey", "").encode()) % 1000) / 1000           # this race's own progress, 0–1
        f = max(0.0, min(1.0, share * (0.4 + 1.2 * k)))
        r["percentReporting"] = round(99 * f)
        r["totalVote"] = int((r.get("totalVote") or 0) * f)
        for c in r.get("candidates") or []:
            c["voteNum"] = int((c.get("voteNum") or 0) * f * (1 + 0.08 * (k - 0.5) * (1 if c.get("winner") else -1)))
            if f < 0.8:
                c["winner"] = False
        if f < 0.8:
            r["editorialStatus"], r["status"], r["isSwitch"] = "scheduled", "U", False
        out.append(r)
    return out
