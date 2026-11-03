"""Official turnout sources for wall/turnout.py, one function per state (public, documented pages and files only).

Each returns {"as_of", "eday", "advance", "counties", "note"} with counts keyed D / R / O (party registration) or N
(the state only publishes a total, no party). To add a state: write fetch_xx() and add it to SOURCES — nothing else.
None of these states publishes Election Day turnout BY PARTY (2026-11-03 sweep): E-day is a total where it exists at all.
"""
from __future__ import annotations

import html
import json
import re
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) sharplab-wall turnout"}


def get(url: str) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def tables(page: str) -> list[list[list[str]]]:
    """Every <table> as rows of cell texts."""
    cell = lambda c: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
    return [[[cell(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)] for tr in re.findall(r"<tr.*?</tr>", tb, re.S)]
            for tb in re.findall(r"<table.*?</table>", page, re.S)]


def num(s: str) -> int:
    return int(re.sub(r"[^\d]", "", s) or 0)


def et_minutes(at: str) -> int:
    """ "11:40 AM CT" → minutes past midnight Eastern (sorts county report times across the two zones)."""
    m = re.match(r"(\d+):(\d+) (AM|PM) (\w)", at)
    if not m:
        return -1
    h = int(m[1]) % 12 + (12 if m[3] == "PM" else 0) + (1 if m[4] == "C" else 0)
    return h * 60 + int(m[2])


# ---- Florida: E-day total by county (Election Watch, ~hourly) + early / mail voted by party (DOS stats, daily + noon/3/6 pm)
def fetch_fl() -> dict:
    rows = tables(get("https://floridaelectionwatch.gov/ElectionDayTurnout"))[0]
    counties = [{"name": r[0], "N": num(r[1]), "at": f"{r[2]} {r[3][:1]}T".strip()} for r in rows[1:] if r and r[0] != "TOTAL" and len(r) >= 4]
    total = next((num(r[1]) for r in rows if r and r[0] == "TOTAL"), sum(c["N"] for c in counties))
    adv, compiled = {"D": 0, "R": 0, "O": 0}, ""
    stats = tables(get("https://countyfilesvbm-ev.floridados.gov/VoteByMailEarlyVotingReports/PublicStats"))[0]
    for r in stats[1:]:
        if r and r[0] in ("Voted Vote-by-Mail", "Voted Early"):
            adv["R"] += num(r[1]); adv["D"] += num(r[2]); adv["O"] += num(r[3]) + num(r[4])
            compiled = r[6]
    latest = max((c["at"] for c in counties if c["at"]), default="", key=et_minutes)
    return {"as_of": latest, "eday": {"N": total}, "advance": adv, "counties": counties,
            "note": f"E-day: Election Watch totals (no party). Early + mail by party: DOS, compiled {compiled}."}


# ---- Delaware: votes by method and county, updated through the day (no party)
def fetch_de() -> dict:
    rows = tables(get("https://elections.delaware.gov/voter/registrationtotals/reports/GE2026_ElectionVoterCountsByVotingMethod.html"))[0]
    by = {r[0]: r for r in rows if r}
    head = rows[0][1:-1]
    eday = num(by["Polling Place"][-1]) if "Polling Place" in by else 0
    adv = sum(num(by[k][-1]) for k in ("Absentee", "Early Voting") if k in by)
    counties = [{"name": n.replace(" County", ""), "N": num(by["Polling Place"][i + 1])} for i, n in enumerate(head)] if "Polling Place" in by else None
    return {"as_of": "", "eday": {"N": eday}, "advance": {"N": adv}, "counties": counties,
            "note": "Polling place = E-day; early + absentee as advance. No party split."}


# ---- Pennsylvania: mail ballots returned, by party (data.pa.gov Socrata, daily)
def fetch_pa() -> dict:
    q = ("https://data.pa.gov/resource/nbwd-pfn4.json?$select=party,count(*)%20as%20n"
         "&$where=ballotreturneddate%20IS%20NOT%20NULL&$group=party")
    adv = {"D": 0, "R": 0, "O": 0}
    for r in json.loads(get(q)):
        adv["D" if r.get("party") == "DEM" else "R" if r.get("party") == "REP" else "O"] += int(r.get("n") or 0)
    return {"as_of": "", "eday": {}, "advance": adv, "counties": None,
            "note": "Mail ballots returned, by party (data.pa.gov, daily). No official E-day turnout."}


# ---- Idaho: absentee + early totals (no party)
def fetch_id() -> dict:
    d = {x["name"]: x["value"] for x in json.loads(get("https://archive.voteidaho.gov/data/absentee/absentee_totals_2026_general.json"))}
    return {"as_of": d.get("update_time", ""), "eday": {}, "advance": {"N": num(d.get("total_voted", "0"))}, "counties": None,
            "note": "Absentee returned + early votes (no party). No official E-day turnout."}


SOURCES = {
    "FL": ("Florida DOS", "https://floridaelectionwatch.gov/ElectionDayTurnout", fetch_fl),
    "DE": ("Delaware DOE", "https://elections.delaware.gov/", fetch_de),
    "PA": ("Pennsylvania DOS (data.pa.gov)", "https://data.pa.gov/d/nbwd-pfn4", fetch_pa),
    "ID": ("Idaho SOS", "https://voteidaho.gov/", fetch_id),
}
