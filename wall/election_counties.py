"""The state sheet's higher-fidelity view of one race: vote totals + margin, % of the expected vote in, the votes still
out, and the counties (closest, most vote still out, biggest swing vs the last presidential race). Pure shaping; the web
(web/wall_election_live.py) fetches and caches.

What each feed has, per race (checked 2026-11-03 against 2026's live files and 2024's final ones):
  * CNN  politics.api.cnn.io/results/county-races/<key>.json (key = 2026-SG-TX, 2026-SP-OH, 2026-GG-GA): every county's
         candidates + votes, `percentReporting` and `voteTimestamp` (no expected vote, no history). The same file for the
         last presidential race (2024-PG-TX) is the swing baseline. results/race/<key>.json = one race, ~700 bytes —
         the fast lane polls that for races someone has open.
  * NYT  static01.nyt.com/elections-assets/pages/data/2026-11-03/results-<state>-<us-senate|governor>[-special].json
         (AP): state + county `eevp` (% of the expected vote in), `total_expected_vote`, per county the 2024 presidential
         + 2020 Senate margins, and a vote-count timeseries. Richer, so its expected vote / history are used when it
         answers; CNN stays the source of the counts.
"""
from __future__ import annotations

NYT_BASE = "https://static01.nyt.com/elections-assets/pages/data/2026-11-03"
SLUG = {"S": "us-senate", "G": "governor"}
TOP = 6


def baseline_key(key: str) -> str:
    """The last presidential race before this one, same state: 2026-SG-TX → 2024-PG-TX, 2024-SG-PA → 2020-PG-PA."""
    y, _, st = key.split("-")[:3]
    y = int(y)
    return f"{y - 4 if y % 4 == 0 else y - 2}-PG-{st}"


def nyt_url(race: dict, state_name: str) -> str | None:
    if race["office"] not in SLUG:
        return None
    slug = state_name.lower().replace(".", "").replace(" ", "-")
    return f"{NYT_BASE}/results-{slug}-{SLUG[race['office']]}{'-special' if race.get('special') else ''}.json"


def dr(cands: list[dict]) -> tuple[int, int, int]:
    """CNN candidates → (D votes, R votes, all votes)."""
    d = sum(c.get("voteNum") or 0 for c in cands if (c.get("majorParty") or "")[:1] == "D")
    r = sum(c.get("voteNum") or 0 for c in cands if (c.get("majorParty") or "")[:1] == "R")
    return d, r, sum(c.get("voteNum") or 0 for c in cands)


def est_left(votes: int, pct: float | None) -> int | None:
    """Votes still out, from the share in: votes / pct − votes (None when nothing's in; 0 at 99 %+, CNN's "all in")."""
    if pct and pct >= 99:
        return 0
    if not votes or not pct or pct <= 0:
        return None
    return int(votes / (pct / 100) - votes)


def nyt_top(page: dict | None) -> tuple[dict, dict]:
    """The NYT race page → (its state-level unit, {county fips: county unit})."""
    if not page:
        return {}, {}
    rs = page.get("races") or []
    if not rs:
        return {}, {}
    r = rs[0]
    units = {f"{u.get('fips_state', '')}{u.get('fips_county', '')}": u for u in r.get("reporting_units") or [] if u.get("level") == "county"}
    return r.get("top_reporting_unit") or {}, units


def totals(race: dict, page: dict | None = None) -> dict:
    """Votes, the leader's margin (points + votes), % of the expected vote in and the votes still out."""
    cs = sorted(race.get("cands") or [], key=lambda c: -(c.get("votes") or 0))
    votes = int(race.get("votes") or 0)
    a, b = (cs + [None, None])[:2]
    mv = (a["votes"] - (b["votes"] if b else 0)) if a and votes else 0
    top, _ = nyt_top(page)
    exp = int(top.get("total_expected_vote") or 0) or None
    pct = float(top["eevp"]) if top.get("eevp") is not None and exp else float(race.get("pct") or 0)
    if exp:
        left, how = max(0, exp - votes), "AP's expected vote (NYT)"
    else:
        left, how = est_left(votes, race.get("pct")), "from % reporting"
    hist = {}
    for k, v in (top.get("historical_comparison_race_data") or {}).items():
        meta = (top.get("historical_comparison_race_meta") or {}).get(k) or {}
        hist = {"label": meta.get("label") or k, "margin": v.get("margin")}
        break
    return {"votes": votes, "leader": a["name"] if a and votes else "", "party": a["party"] if a and votes else "",
            "margin": round(100 * mv / votes, 2) if votes else None, "margin_votes": mv, "pct": round(pct, 1),
            "expected": exp, "left": left, "left_how": how if left is not None else "", "history": hist}


def shape(cnn: list[dict] | None, base: list[dict] | None = None, page: dict | None = None, base_label: str = "") -> dict | None:
    """County rows + the three short lists the sheet draws. cnn = CNN's county-races file for the race, base = the same
    for the last presidential race (swing), page = NYT's race page (expected vote, its own history when CNN has none)."""
    if not cnn:
        return None
    _, nyt = nyt_top(page)
    prev = {}
    for c in base or []:
        d, r, t = dr(c.get("candidates") or [])
        if t:
            prev[c.get("countyFipsCode")] = 100 * (d - r) / t
    label = base_label
    rows = []
    for c in cnn:
        fips = c.get("countyFipsCode") or ""
        cands = sorted(c.get("candidates") or [], key=lambda x: -(x.get("voteNum") or 0))
        votes = int(c.get("totalVote") or sum(x.get("voteNum") or 0 for x in cands))
        u = nyt.get(fips) or {}
        exp = int(u.get("total_expected_vote") or 0) or None
        pct = float(u["eevp"]) if exp and u.get("eevp") is not None else float(c.get("percentReporting") or 0)
        left = max(0, exp - votes) if exp else est_left(votes, c.get("percentReporting"))
        a, b = (cands + [None, None])[:2]
        m = round(100 * ((a.get("voteNum") or 0) - ((b or {}).get("voteNum") or 0)) / votes, 1) if votes and a else None
        d, r, t = dr(cands)
        swing = None
        if t:
            was = prev.get(fips)
            if was is None and u.get("historical_comparison_race_data"):
                h = next(iter(u["historical_comparison_race_data"].values()))
                was, label = h.get("margin"), label or "2024 presidential"
            if was is not None:
                swing = round(100 * (d - r) / t - was, 1)
        rows.append({"fips": fips, "name": c.get("countyName") or fips, "votes": votes, "pct": round(pct), "left": left,
                     "lead": (a.get("lastName") if a and votes else ""), "party": ((a.get("majorParty") or "")[:1] if a and votes else ""),
                     "m": m, "mv": ((a.get("voteNum") or 0) - ((b or {}).get("voteNum") or 0)) if a and votes else 0, "swing": swing,
                     "t": c.get("voteTimestamp") or ""})
    counted = [x for x in rows if x["votes"]]
    sw = [x for x in counted if x["swing"] is not None]
    wsum = sum(x["votes"] for x in sw)
    return {"n": len(rows), "reporting": len(counted), "swing_label": label,
            "swing_all": round(sum(x["swing"] * x["votes"] for x in sw) / wsum, 1) if wsum else None,
            "closest": sorted(counted, key=lambda x: abs(x["m"] or 0))[:TOP],
            "outstanding": sorted((x for x in rows if x["left"]), key=lambda x: -x["left"])[:TOP],
            "swing": sorted(sw, key=lambda x: -abs(x["swing"]))[:TOP],
            "biggest": sorted(rows, key=lambda x: -(x["votes"] + (x["left"] or 0)))[:TOP]}
