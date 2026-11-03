"""Election Pulse: live results × "% expected vote in" → projected finals, priced against Kalshi's count markets.

projected = votes counted / (% expected vote in) — the source's own estimate of how much is in (CNN's percentReporting,
NYT/AP's eevp). That's the whole model: it assumes the rest of the vote looks like what's in, which is exactly where it
breaks (mail counted late, a big county dumping at once, CA counting for weeks). SPREAD widens the band as less is in;
P(yes) is a normal around the projection. It's a scratchpad for a trader, not a forecast.

Markets (public, checked 2026-11-03):
  KXMIDTERMVOTETURN-{ST}{SEN|GOV|NN|AL}-{strike}   per-race total votes "above N" (certified)
  KXVOTEGENERAL-{race event}-{yy}{cand}-{N}        a candidate's vote share "at least N%"
  KXHOUSETURNOUT-26NOV03-*                         national House total votes (buckets, floor/cap_strike)
  KXHOUSEPOPVOTEMARGIN-27NOV03-*                   national House D-minus-R margin (buckets; B50 = R win)
Pure: no network, no clock — web/wall_election.py fetches.
"""
from __future__ import annotations

import math
import re
import unicodedata

from shared import kalshi_election as ke

TURNOUT, SHARE, HOUSE_TURNOUT, HOUSE_MARGIN = "KXMIDTERMVOTETURN", "KXVOTEGENERAL", "KXHOUSETURNOUT", "KXHOUSEPOPVOTEMARGIN"
SERIES = (TURNOUT, SHARE, HOUSE_TURNOUT, HOUSE_MARGIN)
MIN_IN = 0.03          # below 3% in, a projection is noise: none
SPREAD = 0.25          # ponytail: turnout sd = projection × SPREAD × (share still out); tune live if it's over/under-confident
SHARE_SD = (0.75, 8.0) # vote-share sd in points = a + b × (share still out)
EDGE = 12              # |model − market mid| in points worth a highlight
_TURN = re.compile(r"^(?P<st>[A-Z]{2})(?P<k>SEN|GOV|AL|\d{2})$")


def ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def p_between(mu: float, sd: float, lo: float | None, hi: float | None) -> float:
    """P(lo ≤ X ≤ hi) for X ~ N(mu, sd), in % (None = unbounded)."""
    sd = max(sd, 1e-9)
    a = ncdf((lo - mu) / sd) if lo is not None else 0.0
    b = ncdf((hi - mu) / sd) if hi is not None else 1.0
    return round(100 * max(0.0, b - a), 1)


def dollars(m: dict, k: str) -> float | None:
    v = m.get(k + "_dollars")
    try:
        return round(100 * float(v), 1) if v not in (None, "") else None
    except ValueError:
        return None


def price(m: dict) -> dict:
    bid, ask, last = dollars(m, "yes_bid"), dollars(m, "yes_ask"), dollars(m, "last_price")
    mid = (bid + ask) / 2 if bid is not None and ask and ask > 0 else last
    return {"ticker": m.get("ticker", ""), "bid": bid, "ask": ask, "last": last, "mid": round(mid, 1) if mid is not None else None,
            "vol": round(float(m.get("volume_fp") or m.get("volume") or 0))}


def live(m: dict) -> bool:
    """A book worth showing: someone bidding or offering between 1¢ and 99¢, or it has traded."""
    bid, ask = dollars(m, "yes_bid") or 0, dollars(m, "yes_ask") or 0
    return 0 < bid < 99 or 0 < ask < 99 or float(m.get("volume_fp") or m.get("volume") or 0) > 0


def priced(m: dict, model: float | None) -> dict:
    """A market row + the model's P(yes) + edge (model − mid; buy yes if > 0 and it beats the ask)."""
    p = price(m)
    p["model"] = None if model is None else max(0.1, min(99.9, model))
    p["edge"] = round(model - p["mid"], 1) if model is not None and p["mid"] is not None else None
    p["hot"] = p["edge"] is not None and abs(p["edge"]) >= EDGE
    return p


def bounds(m: dict) -> tuple[float | None, float | None]:
    st, lo, hi = m.get("strike_type"), m.get("floor_strike"), m.get("cap_strike")
    if st in ("greater", "greater_or_equal"):
        return lo, None
    if st in ("less", "less_or_equal"):
        return None, hi
    return lo, hi


# ---- one race ------------------------------------------------------------------------------------------------------------
def project(r: dict) -> dict | None:
    """{proj, sd, inn} total votes for a race, or None before MIN_IN is in."""
    inn = (r.get("pct") or 0) / 100
    if inn < MIN_IN or not r.get("votes"):
        return None
    inn = min(inn, 1.0)
    proj = r["votes"] / inn
    return {"proj": round(proj), "sd": round(max(proj * SPREAD * (1 - inn), r["votes"] * 0.005)), "inn": inn}


def short(v: float) -> str:
    return f"{v / 1e6:.2f}M" if v >= 1e6 else f"{round(v / 1e3)}K"


def turnout_race_id(code: str) -> list[str]:
    m = _TURN.match(code)
    if not m:
        return []
    st, k = m["st"], m["k"]
    if k in ("SEN", "GOV"):
        base = f"{'S' if k == 'SEN' else 'G'}-{st}"
        return [base, base + "-special"]
    return [f"H-{st}-{1 if k == 'AL' else int(k)}", f"H-{st}"]


def share_race(event: str) -> dict | None:
    """KXVOTEGENERAL-SENATEAK-26MPEL / -KXHOUSERACE-MI05-26CVUK / -SENATENE26DOSB → ke.classify's race."""
    parts = event.upper().split("-")[1:]
    if not parts:
        return None
    if parts[0] == "KXHOUSERACE" and len(parts) > 1:
        return ke.classify(f"KXHOUSERACE-{parts[1]}-26")
    series = re.sub(r"\d{2}[A-Z]*$", "", parts[0]) if len(parts) == 1 else parts[0]   # SENATENE26DOSB → SENATENE
    return ke.classify(f"{series}-26")


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def find_cand(race: dict, title: str) -> dict | None:
    t = _fold(title)
    hits = [c for c in race["cands"] if c.get("name") and re.search(rf"\b{re.escape(_fold(c['name']))}\b", t)]
    return max(hits, key=lambda c: len(c.get("full") or ""), default=None) if len(hits) != 1 else hits[0]


def _race(by: dict, ids: list[str]) -> dict | None:
    return next((by[i] for i in ids if i in by), None)


def race_rows(races: list[dict], markets: dict[str, list[dict]]) -> list[dict]:
    """Every race that has a turnout or vote-share market: its projection + those markets priced by the model."""
    by = {r["id"]: r for r in races}
    out: dict[str, dict] = {}

    def row(r: dict) -> dict:
        if r["id"] not in out:
            pj = project(r)
            out[r["id"]] = {"id": r["id"], "label": r["label"], "office": r["office"], "state": r["state"], "pct": r["pct"],
                            "votes": r["votes"], "called": r["called"], "closes": r.get("closes", 0),
                            "proj": pj["proj"] if pj else None, "sd": pj["sd"] if pj else None,
                            "cands": [{k: c[k] for k in ("name", "party", "votes", "pct", "winner")} for c in r["cands"][:3]],
                            "turnout": [], "shares": []}
        return out[r["id"]]

    for m in filter(live, markets.get(TURNOUT) or []):
        parts = (m.get("ticker") or "").split("-")
        r = _race(by, turnout_race_id(parts[1])) if len(parts) > 2 else None
        if not r:
            continue
        x = row(r)
        lo, hi = bounds(m)
        model = p_between(x["proj"], x["sd"], lo, hi) if x["proj"] else None
        x["turnout"].append({**priced(m, model), "strike": lo if lo is not None else hi})
    for m in filter(live, markets.get(SHARE) or []):
        c = share_race(m.get("event_ticker") or ke.event_of(m.get("ticker") or ""))
        r = _race(by, ke.race_ids(c)) if c and c.get("type") == "race" else None
        cand = find_cand(r, m.get("title") or "") if r else None
        if not cand:
            continue
        x = row(r)
        lo, hi = bounds(m)
        inn = min((r["pct"] or 0) / 100, 1.0)
        model = None
        if inn >= MIN_IN and r["votes"]:
            model = p_between(cand["pct"], SHARE_SD[0] + SHARE_SD[1] * (1 - inn), lo, hi)
        x["shares"].append({**priced(m, model), "name": cand["name"], "party": cand["party"], "now": cand["pct"] if r["votes"] else None,
                            "strike": lo if lo is not None else hi})
    for x in out.values():
        x["turnout"].sort(key=lambda t: t["strike"] or 0)
        x["shares"].sort(key=lambda t: (t["name"], t["strike"] or 0))
        x["hot"] = sum(1 for t in x["turnout"] + x["shares"] if t["hot"])
    return sorted(out.values(), key=lambda x: (-x["hot"], -(x["pct"] or 0), x["label"]))


# ---- national House + states -------------------------------------------------------------------------------------------
def national_house(races: list[dict], markets: dict[str, list[dict]]) -> dict:
    """House total votes and the D−R popular-vote margin, projected district by district. Districts with nothing in
    yet are filled with the mean projected district (sd 35% each) — rough until most of the map reports."""
    hs = [r for r in races if r["office"] == "H"]
    counted = sum(r["votes"] for r in hs)
    proj, var, d, rr, n_in, inn = 0.0, 0.0, 0.0, 0.0, 0, 0.0
    for r in hs:
        pj = project(r)
        if not pj:
            continue
        n_in += 1
        inn += pj["inn"]
        proj += pj["proj"]
        var += pj["sd"] ** 2
        scale = 1 / pj["inn"]
        d += scale * sum(c["votes"] for c in r["cands"] if c["party"] == "D")
        rr += scale * sum(c["votes"] for c in r["cands"] if c["party"] == "R")
    out = {"counted": counted, "districts": len(hs), "reporting": n_in, "proj": None, "sd": None, "margin": None, "margin_sd": None}
    if n_in:
        rest = len(hs) - n_in
        mean = proj / n_in
        total = proj + rest * mean
        # independent district noise + a shared error (every source's "% in" estimate can be off the same way)
        sd = math.sqrt(var + rest * (0.35 * mean) ** 2 + (total * 0.05 * (1 - inn / n_in + rest / len(hs))) ** 2)
        margin = 100 * (d - rr) / proj if proj else 0
        # ponytail: margin sd = 1 pt + up to 6 for districts not in + up to 3 for the vote still out in those that are
        msd = 1.0 + 6.0 * rest / len(hs) + 3.0 * (1 - inn / n_in)
        out.update(proj=round(total), sd=round(sd), margin=round(margin, 2), margin_sd=round(msd, 2))
    tms = sorted(markets.get(HOUSE_TURNOUT) or [], key=lambda m: bounds(m)[0] or 0)
    out["turnout"] = [{**priced(m, p_between(out["proj"], out["sd"], *bounds(m)) if out["proj"] else None),
                       "label": m.get("yes_sub_title") or m.get("ticker")} for m in tms]
    rows = []
    for m in markets.get(HOUSE_MARGIN) or []:
        lo, hi = bounds(m)
        if (m.get("yes_sub_title") or "").lower().startswith("republican"):
            lo, hi = None, 0   # B50: R wins = D margin < 0
        model = p_between(out["margin"], out["margin_sd"], lo, hi) if out["margin"] is not None else None
        rows.append({**priced(m, model), "label": m.get("yes_sub_title") or m.get("ticker"), "_k": lo if lo is not None else -100})
    out["margin_mkts"] = [{k: v for k, v in x.items() if k != "_k"} for x in sorted(rows, key=lambda x: x["_k"])]
    return out


def states(races: list[dict]) -> list[dict]:
    """Per state: the statewide race with the most votes (Senate / Governor), else the House districts summed."""
    out = []
    for st in sorted({r["state"] for r in races}):
        rs = [r for r in races if r["state"] == st]
        top = max((r for r in rs if r["office"] in ("S", "G")), key=lambda r: r["votes"], default=None)
        if top:
            pj = project(top)
            out.append({"state": st, "basis": top["label"] + (" Sen" if top["office"] == "S" else " Gov"), "votes": top["votes"],
                        "pct": top["pct"], "proj": pj["proj"] if pj else None, "sd": pj["sd"] if pj else None, "closes": top.get("closes", 0)})
            continue
        hs = [r for r in rs if r["office"] == "H"]
        pjs = [project(r) for r in hs]
        votes = sum(r["votes"] for r in hs)
        ok = all(pjs) and hs
        out.append({"state": st, "basis": "House (all districts)", "votes": votes,
                    "pct": round(100 * votes / sum(p["proj"] for p in pjs), 1) if ok else (hs[0]["pct"] if len(hs) == 1 else None),
                    "proj": sum(p["proj"] for p in pjs) if ok else None,
                    "sd": round(math.sqrt(sum(p["sd"] ** 2 for p in pjs))) if ok else None,
                    "closes": max((r.get("closes", 0) for r in hs), default=0)})
    return out


def pulse(snap: dict, markets: dict[str, list[dict]]) -> dict:
    races = snap.get("races") or []
    now = snap.get("now") or 0
    closes = sorted({r["closes"] for r in races if (r.get("closes") or 0) > now})[:3]
    return {"source": snap.get("source", ""), "updated": snap.get("updated", 0), "error": snap.get("error", ""), "now": now,
            "house": national_house(races, markets), "states": states(races), "races": race_rows(races, markets),
            "next": [{"t": t, "states": sorted({r["state"] for r in races if r.get("closes") == t})} for t in closes],
            "calls": (snap.get("calls") or [])[:10], "model": {"spread": SPREAD, "share_sd": SHARE_SD, "min_in": MIN_IN, "edge": EDGE}}
