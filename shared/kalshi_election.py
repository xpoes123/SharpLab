"""Kalshi's 2026 US midterm markets ↔ the wall's election results (wall/election.py's race shape), and how a member's
election position is drawn: side, pick, entry → now, ▲/▼ %, and the race's live status (% in, leader, called ✓).

Kalshi's naming (checked live 2026-11-03; fixtures in tests/fixtures/wall/election/kalshi-*.json.gz):
  Senate      SENATE{ST}-26 (-D / -R / a candidate code), SENATE{ST}S-26 = the special (FL, OH), KXSENATE{ST}-26NOV,
              KX{ST}SENATE-26… (one market per person: KXAKSENATE, KXIASENATE, KXMISENATE)
  Governor    GOVPARTY{ST}-26, KXGOVPARTY{ST}-26, KXGOV{ST}-26 (per person)
  House       HOUSE{ST}{N|AL}-26, KXHOUSE{ST}{NN}-26, KXHOUSERACE-{ST}{NN|AL}-26
  Control     CONTROLS-2026, CONTROLH-2026 (-D / -R)
  Seat counts KX{R|D}{HOUSE|SENATE}WON-26NOV03 (seats won tonight, media calls: "At least N"),
              {KX}{R|D}{HOUSE|SENATE}SEATS[DIR|H]-27 and KX{HOUSE|SENATE}SEATS{R|D}-27 (seats held in the new Congress)
Kalshi's year codes aren't reliable (GOVPARTYNH-28 is the 2026 race, GOVPARTYKS-27 too): a race market counts when its
year is 26–28 *and*, if it names a person, that person is on the 2026 ballot in our results; a party-only market
needs the year 26. Control / seat events need the year that belongs to this election.

No delay: election results aren't a picture (nothing on screen runs behind real life), so these rows are built as of now.
"""
from __future__ import annotations

import re
import unicodedata

from shared import kalshi_wall as kw

STATES = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
          "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
          "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY"}
OFFICE_NAME = {"S": "Senate", "H": "House", "G": "Governor"}
PARTY_NAME = {"D": "Dem", "R": "Rep", "I": "Ind", "O": "Other"}

_RACE = [   # (series regex, office) — group "st" = state, "d" = district, "sp" = special
    (re.compile(r"^(?:KX)?SENATE(?P<st>[A-Z]{2})(?P<sp>S?)$"), "S"),
    (re.compile(r"^KX(?P<st>[A-Z]{2})SENATE$"), "S"),
    (re.compile(r"^(?:KX)?GOVPARTY(?P<st>[A-Z]{2})$"), "G"),
    (re.compile(r"^KXGOV(?P<st>[A-Z]{2})$"), "G"),
    (re.compile(r"^(?:KX)?HOUSE(?P<st>[A-Z]{2})(?P<d>\d{1,2}|AL)$"), "H"),
]
_HOUSERACE = re.compile(r"^(?P<st>[A-Z]{2})(?P<d>\d{1,2}|AL)$")
_CONTROL = re.compile(r"^CONTROL(?P<o>[SH])$")
_SEATS = [re.compile(r"^(?:KX)?(?P<p>[RD])(?P<o>HOUSE|SENATE)(?P<b>WON|SEATS)(?:DIR|H)?$"),
          re.compile(r"^KX(?P<o>HOUSE|SENATE)(?P<b>SEATS)(?P<p>[RD])$")]


def _year(seg: str) -> int | None:
    m = re.match(r"^(20)?(\d\d)(?:[A-Z]{3}\d{0,2})?$", seg or "")
    return 2000 + int(m.group(2)) if m else None


def event_of(ticker: str) -> str:
    """A market ticker → its event ticker (everything before the last dash; KXHOUSERACE events have three parts)."""
    return (ticker or "").rsplit("-", 1)[0] if (ticker or "").count("-") >= 2 else (ticker or "")


def classify(event_ticker: str) -> dict | None:
    """What an event is, from its ticker alone: {type: race, office, state, district, special, year} | {type: control,
    office} | {type: seats, office, party, basis: won|held} | None (not a midterm market we can place)."""
    parts = (event_ticker or "").upper().split("-")
    if len(parts) < 2:
        return None
    series = parts[0]
    if series == "KXGENERALCOUNTY" and len(parts) >= 3:   # one county of a statewide race (KXGENERALCOUNTY-GOVPARTYCA-26)
        c = classify("-".join(parts[1:]))
        return {**c, "type": "county"} if c and c["type"] == "race" else None
    if series == "KXHOUSERACE" and len(parts) >= 3:
        m, y = _HOUSERACE.match(parts[1]), _year(parts[2])
        if m and m["st"] in STATES and y in (2026, 2027, 2028):
            return {"type": "race", "office": "H", "state": m["st"], "district": _dist(m["d"]), "special": False, "year": y}
        return None
    y = _year(parts[1])
    for rx, office in _RACE:
        m = rx.match(series)
        if m and m["st"] in STATES and y in (2026, 2027, 2028):
            g = m.groupdict()
            return {"type": "race", "office": office, "state": m["st"], "district": _dist(g.get("d")) if office == "H" else None,
                    "special": bool(g.get("sp")), "year": y}
    m = _CONTROL.match(series)
    if m and y == 2026:
        return {"type": "control", "office": m["o"]}
    for rx in _SEATS:
        m = rx.match(series)
        if m:
            basis = "won" if m["b"] == "WON" else "held"
            if (basis == "won" and y == 2026) or (basis == "held" and y == 2027):
                return {"type": "seats", "office": m["o"][0], "party": m["p"], "basis": basis}
    return None


def _dist(d: str | None) -> int | None:
    if not d:
        return None
    return 1 if d == "AL" else int(d)   # at-large = district 1 in the results feeds (AK-1, DE-1, VT-1…)


def is_election(ticker: str) -> bool:
    return classify(event_of(ticker)) is not None


def race_ids(c: dict) -> list[str]:
    """Our race ids this Kalshi race may be (the special first if Kalshi says special, else the regular one first)."""
    base = f"{c['office']}-{c['state']}" + (f"-{c['district']}" if c["office"] == "H" and c["district"] else "")
    return [base + "-special", base] if c.get("special") else [base, base + "-special"]


# ---- what a market picks ---------------------------------------------------------------------------------------------
_GENERIC = re.compile(r"^(the )?(democratic|republican|independent)( party)?$", re.I)


def pick(m: dict) -> dict:
    """{party: D|R|I|'' , name: the person or ''} for one market (suffix -D/-R, the title's party, yes_sub_title)."""
    suffix = (m.get("ticker") or "").rsplit("-", 1)[-1].upper()
    title = (m.get("title") or "").lower()
    sub = (m.get("yes_sub_title") or "").strip()
    party = suffix if suffix in ("D", "R", "I") else ""
    if not party and "independent" not in title:
        party = "D" if re.search(r"\bdemocrat", title) else "R" if re.search(r"\brepublican", title) else ""
    name = "" if _GENERIC.match(sub) or not sub else sub
    return {"party": party, "name": name}


def _norm(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    toks = re.findall(r"[a-z]+", s.replace("-", ""))
    return [t for t in toks if t not in ("jr", "sr", "ii", "iii", "iv", "writein")]


def find_cand(race: dict, p: dict) -> dict | None:
    """The race's candidate this pick is: by name when the market names one (last name, then first name to break a tie
    — Alaska has two Sullivans), else the one candidate of the pick's party."""
    cands = race.get("cands") or []
    if p.get("name"):
        want = _norm(p["name"])
        best, score = None, 0
        for c in cands:
            last = _norm(c.get("name") or "")
            if not last or not all(t in want for t in last):
                continue
            first = [t for t in _norm(c.get("full") or "") if t not in last]
            s = 2 + (1 if first and first[0] in want else 0) + (1 if p.get("party") and c["party"] == p["party"] else 0)
            if s > score:
                best, score = c, s
        return best
    if p.get("party"):
        same = [c for c in cands if c["party"] == p["party"]]
        return same[0] if same else None
    return None


def resolve(c: dict | None, p: dict, races: dict[str, dict]) -> dict | None:
    """The results' race this market belongs to (or None: 2028's, another office, not on tonight's ballot)."""
    if not c or c["type"] != "race":
        return None
    for rid in race_ids(c):
        r = races.get(rid)
        if not r:
            continue
        if p.get("name"):
            if find_cand(r, p):
                return r
        elif c.get("year") == 2026:
            return r
    if p.get("name") and c.get("year") == 2026:   # Kalshi filed it under the wrong state (SENATELA-26 lists Kentucky's Barr /
        hits = [r for r in races.values()          # Booker): the one race of that office with this person, first + last name
                if r["office"] == c["office"] and (k := find_cand(r, p)) and _first_matches(k, p)]
        if len(hits) == 1:
            return hits[0]
    return None


def _first_matches(cand: dict, p: dict) -> bool:
    first = [t for t in _norm(cand.get("full") or "") if t not in _norm(cand.get("name") or "")]
    return bool(first) and first[0] in _norm(p["name"])


def market_label(c: dict, p: dict, m: dict) -> str:
    """TV-sized: 'GA Senate · Ossoff', 'PA-7 House · Dem', 'Senate control · Dem', 'Rep House seats · At least 194'."""
    if c["type"] == "race":
        where = f"{c['state']}-{c['district']}" if c["office"] == "H" else c["state"]
        who = (p["name"].split()[-1] if p["name"] else "") or PARTY_NAME.get(p["party"], "")
        return f"{where} {OFFICE_NAME[c['office']]}" + (" (sp.)" if c.get("special") else "") + (f" · {who}" if who else "")
    if c["type"] == "control":
        return f"{OFFICE_NAME[c['office']]} control · {PARTY_NAME.get(p['party'], p['party'])}"
    if c["type"] == "county":   # "Will Steve Hilton win Riverside County?", yes_sub_title "Riverside"
        county = (m.get("yes_sub_title") or "").strip() or (m.get("ticker") or "").rsplit("-", 1)[-1].title()
        who = p["name"].split()[-1] if p["name"] else PARTY_NAME.get(p["party"], "")
        return f"{c['state']} {OFFICE_NAME[c['office']]} · {county} Co." + (f" · {who}" if who else "")
    sub = (m.get("yes_sub_title") or "").strip() or (m.get("ticker") or "").rsplit("-", 1)[-1]
    return f"{PARTY_NAME[c['party']]} {OFFICE_NAME[c['office']]} seats{' won' if c['basis'] == 'won' else ''} · {sub}"


def market_info(m: dict) -> dict | None:
    """What the poller keeps about one election market (no prices): {class, pick, label, strike}."""
    tk = m.get("ticker") or ""
    c = classify(m.get("event_ticker") or event_of(tk))
    if not c or "state senate" in (m.get("title") or "").lower():
        return None
    p = pick(m)
    if c["type"] == "county":   # the sub-title is the county; the person is in the title
        w = re.match(r"^Will (.+?) win\b", m.get("title") or "")
        p = {"party": p["party"] if p["name"] else "", "name": w[1] if w else ""}
    if c["type"] == "control" and not p["party"]:
        p["party"] = tk.rsplit("-", 1)[-1].upper()[:1]
    return {"class": c, "pick": p, "label": market_label(c, p, m),
            "strike": {"type": m.get("strike_type") or "", "floor": kw._num(m.get("floor_strike")), "cap": kw._num(m.get("cap_strike"))}}


def see(book: kw.Book, m: dict) -> None:
    """After book.see_market(m): mark an election market as one (its own label + what it picks)."""
    info = market_info(m)
    if info and m.get("ticker") in book.markets:
        book.markets[m["ticker"]].update(kind="election", label=info["label"], election=info)


# ---- the race's live status, for a row ----------------------------------------------------------------------------------
def _strike_holds(n: int, s: dict) -> bool:
    lo, hi, t = s.get("floor"), s.get("cap"), s.get("type")
    if t == "less":
        return n < (hi if hi is not None else lo or 0)
    if t == "between":
        return (lo is None or n >= lo) and (hi is None or n <= hi)
    return lo is not None and n >= lo   # greater / greater_or_equal


def status(info: dict | None, side: str, races: dict[str, dict], offices: dict[str, dict]) -> dict:
    """{race, text, pct_in, leader, called, won, chip}. won: True = this side has won, False = lost, None = not decided."""
    if not info:
        return {"race": "", "text": "", "called": "", "won": None, "chip": ""}
    c, p = info["class"], info["pick"]
    out = {"race": "", "text": "", "called": "", "won": None, "chip": "", "pct_in": None, "leader": ""}
    if c["type"] == "county":   # filed under the statewide race; never won / lost from statewide results (the county can differ)
        who = p["name"].split()[-1] if p["name"] else p["party"] or "?"
        out.update(race=race_ids(c)[0], chip=f"{side} {who} (county)", text="county market")
        return out
    if c["type"] == "race":
        r = resolve(c, p, races)
        if not r:
            return out
        cand = find_cand(r, p)
        party = (cand or {}).get("party") or p["party"]
        out.update(race=r["id"], chip=f"{side} {party or (p['name'].split()[-1] if p['name'] else '?')}", pct_in=r["pct"], leader=r["leader"])
        if r["called"]:
            winner = next((x for x in r["cands"] if x["winner"]), None)
            yes = (cand is not None and winner is not None and cand is winner) if p["name"] else r["called"] == party
            out["called"] = r["called"]
            out["won"] = yes if side == "YES" else not yes
            out["text"] = f"✓ {(winner or {}).get('name') or r['called']} ({r['called']}) · {r['caller']}"
        elif r["votes"] > 0:
            lead = r["cands"][0]
            out["text"] = f"{round(r['pct'])}% in · {lead['name']} +{r['margin'] or 0:g}"
        else:
            out["text"] = "no votes yet"
        return out
    o = offices.get(c["office"]) or {}
    if not o:
        return out
    out["race"] = ("CTRL-" if c["type"] == "control" else "SEATS-") + c["office"]
    out["chip"] = f"{side} {p['party']}"
    counted = o.get("counted") or {}
    if c["type"] == "control":
        out["text"] = f"D {counted.get('D', 0)} · R {counted.get('R', 0)}" + (f" · {o['out']} out" if o.get("out") else "")
        if o.get("winner"):
            out["called"] = o["winner"]
            out["won"] = (o["winner"] == p["party"]) == (side == "YES")
            out["text"] = f"✓ {PARTY_NAME[o['winner']]} control · " + out["text"]
        return out
    seats = (o.get("seats") or {}).get(c["party"]) or {}
    n = seats.get("won", 0) if c["basis"] == "won" else counted.get(c["party"], 0)
    out["text"] = f"{PARTY_NAME[c['party']]} {n} {'won' if c['basis'] == 'won' else 'so far'}"
    s = info.get("strike") or {}
    final = o.get("up") and o.get("called") == o.get("up")
    clinched = c["basis"] == "won" and s.get("type", "").startswith("greater") and _strike_holds(n, s)
    if final or clinched:
        yes = _strike_holds(n, s)
        out["called"] = c["party"]
        out["won"] = yes if side == "YES" else not yes
        out["text"] = "✓ " + out["text"]
    return out


def enrich(rows: list[dict], races: dict[str, dict], offices: dict[str, dict]) -> list[dict]:
    """Election rows + each one's race status and, once called, the highlight line ('✓ CALLED · Hansen wins +38%')."""
    out = []
    for r in rows:
        st = status(r.get("election"), r.get("side", ""), races, offices)
        r = dict(r)
        r.update(race=st["race"], status=st["text"], called=st["called"], won=st["won"], chip=st["chip"],
                 pct_in=st.get("pct_in"), leader=st.get("leader", ""))
        r["highlight"] = called_line(r) if st["won"] is not None else ""
        out.append(r)
    order = lambda r: (r["won"] is None, r["who"].lower(), r["label"])   # noqa: E731 — decided races first
    return sorted(out, key=order)


def called_line(r: dict) -> str:
    pct = r.get("pct")
    tail = "" if pct is None else f" {'+' if pct >= 0 else '−'}{abs(round(pct))}%"
    return f"✓ CALLED · {r.get('who', '')} {'wins' if r.get('won') else 'loses'}{tail}"


def election_rows(book: kw.Book, as_of: float, sharers: set[str]) -> list[dict]:
    """Opted-in members' positions on midterm markets as of now (no spoiler delay). Anyone not in `sharers` never shows.
    Each row carries its market's `election` info for enrich()."""
    out = []
    for uid, tape in book.positions.items():
        if uid not in sharers:
            continue
        for ticker, pos in (tape.at(as_of) or {}).items():
            mk = book.markets.get(ticker) or {}
            if mk.get("kind") != "election":
                continue
            r = kw.position_row(book, uid, ticker, pos, as_of)
            if r:
                r["kind"], r["election"] = "election", mk["election"]
                out.append(r)
    return sorted(out, key=lambda r: (r["who"].lower(), r["label"]))


def compact_races(races: list[dict], ids: set[str] | None = None) -> dict[str, dict]:
    """The race fields status() reads, for the races asked for (what the wall service sends the web)."""
    keep = ("id", "office", "state", "district", "special", "label", "pct", "votes", "leader", "margin", "called", "caller")
    return {r["id"]: {**{k: r.get(k) for k in keep},
                      "cands": [{k: c.get(k) for k in ("name", "full", "party", "pct", "winner")} for c in r.get("cands") or []][:6]}
            for r in races if ids is None or r["id"] in ids or r["id"].removesuffix("-special") in ids}
