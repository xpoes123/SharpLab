"""Election night, higher fidelity: the state sheet's per-race detail (totals, margin in votes, % of the expected vote in,
votes still out, counties, the race's vote-updates timeline with the Kalshi price at each update) and the watch page's
"Live updates" feed. The logic is wall/election_feed.py (diffing + ranking + history) and wall/election_counties.py
(county shaping); this is the fetching, caching and HTTP.

* The national poll stays every 30 s (wall/election.py). A race someone has open in the sheet (its /race/{id} asked in
  the last FOCUS_S) is also polled on its own every FAST_S from CNN's one-race file (~700 bytes) — the fast lane.
* County files (CNN; NYT/AP for the expected vote and history when it answers) only while a sheet asks, cached and
  single-flight like the Kalshi data (web/wall_election.Cache).
* The Kalshi price saved with each update comes from the markets cache only (filled while that state's sheet is open):
  never an extra Kalshi call.
* Public data, same audience as the map: members and guests.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from shared import election_markets as em
from wall import election as we
from wall import election_counties as ec
from wall import election_feed as ef
from web import wall_election as wel

log = logging.getLogger(__name__)
FAST_S = 10
FOCUS_S = 60
MAX_FOCUS = 8
COUNTY_TTL = 30
BASE_TTL = 6 * 3600
NYT_TTL = 60
PRICE_AGE = 600
_RID = re.compile(r"^[SGH]-[A-Z]{2}(-\d{1,2})?(-special)?$")

cache = wel.Cache()
focus: dict[str, float] = {}
_lane: dict = {"t": None}


def results() -> we.Results:
    r = wel.results()
    if r.price is None:
        r.price = cached_price
    return r


def cnn_source(r: we.Results):
    return next((s for s in r.sources if isinstance(s, we.Cnn)), None)


def cnn_key(race: dict, year: int = 2026) -> str:
    """CNN's key of a race from any source (CNN's own races carry it)."""
    if race.get("key"):
        return race["key"]
    k = f"{year}-{race['office']}{'P' if race.get('special') else 'G'}-{race['state']}"
    return k + (f"-{race['district']}" if race["office"] == "H" and race.get("district") else "")


# ---- the Kalshi price at an update: cached only --------------------------------------------------------------------------
def cached_price(race: dict, now: float | None = None) -> dict | None:
    data = wel.markets.cache.data
    clock = wel.markets.cache.clock
    now = clock() if now is None else now
    ser = data.get("series")
    if not ser:
        return None
    raw = []
    for name in ser[1].get(race["state"], []) + ([em.HOUSERACE] if race["office"] == "H" else []):
        hit = data.get(("markets", name))
        if hit and now - hit[0] < PRICE_AGE:
            raw += hit[1]
    rows = em.race_markets(raw, {race["id"]: race}, race["state"]).get(race["id"]) or []
    if not rows:
        return None
    m = next((x for x in rows if x["party"] == "D"), rows[0])   # one side throughout, so the moves compare
    return {"who": m["who"], "party": m["party"], "prob": m["prob"]} if m["prob"] is not None else None


# ---- the fast lane -----------------------------------------------------------------------------------------------------
def want(rid: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    focus[rid] = now + FOCUS_S
    for k in [k for k, t in focus.items() if t < now]:
        focus.pop(k, None)
    while len(focus) > MAX_FOCUS:
        focus.pop(min(focus, key=focus.get), None)
    t = _lane["t"]
    if not (t and t.is_alive()):
        _lane["t"] = threading.Thread(target=_run_lane, name="election-fast", daemon=True)
        _lane["t"].start()


def fast_poll(r: we.Results, now: float | None = None) -> int:
    """One round of the fast lane: each focused race's own CNN file → swapped into the results + diffed."""
    now = time.time() if now is None else now
    src = cnn_source(r)
    if src is None:
        return 0
    by = {x["id"]: x for x in r.races}
    got = []
    for rid in [k for k, t in focus.items() if t >= now]:
        race = by.get(rid)
        if not race:
            continue
        try:
            raw = src.fetch(f"{src.base}/race/{cnn_key(race, src.year)}.json")
            got.append(src.race(raw))
        except Exception as e:  # noqa: BLE001 — the national poll still covers it
            log.info("fast lane %s: %s", rid, type(e).__name__)
    r.merge(got)
    return len(got)


def _run_lane() -> None:
    while any(t >= time.time() for t in focus.values()):
        try:
            fast_poll(results())
        except Exception as e:  # noqa: BLE001
            log.warning("fast lane: %s", e)
        time.sleep(FAST_S)


# ---- counties ----------------------------------------------------------------------------------------------------------
async def counties(race: dict, r: we.Results) -> tuple[dict | None, dict | None]:
    """(county view, NYT race page or None) for a Senate / Governor race."""
    src = cnn_source(r)
    if src is None or race["office"] not in ("S", "G"):
        return None, None
    key = cnn_key(race, src.year)
    bkey = ec.baseline_key(key)

    async def cnn():
        return await asyncio.to_thread(src.fetch, f"{src.base}/county-races/{key}.json")

    async def base():
        return await asyncio.to_thread(src.fetch, f"{src.base}/county-races/{bkey}.json")

    async def nyt():
        url = ec.nyt_url(race, we.STATES.get(race["state"], race["state"]))
        return await asyncio.to_thread(we.get_json, url) if url else None
    live_nyt = type(src) is we.Cnn and src.year == 2026 and src.fetch is we.get_json   # not in a replay
    got = await asyncio.gather(cache.get(("county", key), COUNTY_TTL, cnn), cache.get(("county", bkey), BASE_TTL, base),
                               *([cache.get(("nyt", race["id"]), NYT_TTL, nyt)] if live_nyt else []), return_exceptions=True)
    c, b = (None if isinstance(got[0], Exception) else got[0]), (None if isinstance(got[1], Exception) else got[1])
    page = got[2] if len(got) > 2 and not isinstance(got[2], Exception) else None
    for g in got:
        if isinstance(g, Exception):
            log.info("counties %s: %s", race["id"], type(g).__name__)
    label = f"{bkey[:4]} presidential" if b else ""
    return ec.shape(c, b, page, label), page


def race_brief(r: dict) -> dict:
    return {k: r.get(k) for k in ("id", "office", "state", "district", "special", "label", "pct", "votes", "leader", "margin",
                                  "called", "caller", "watch", "closes")}


async def race_payload(rid: str) -> dict | None:
    r = results()
    snap = await wel.snapshot()
    race = next((x for x in snap.get("races") or [] if x["id"] == rid), None)
    if race is None:
        return None
    want(rid)
    cty, page = await counties(race, r)
    return {"race": race_brief(race), "totals": ec.totals(race, page), "counties": cty, "timeline": r.feed.timeline(rid),
            "price": cached_price(race), "source": snap.get("source", ""), "fast_s": FAST_S, "now": snap.get("now")}


def live_payload(snap: dict, r: we.Results, key_only: bool, n: int = 60) -> dict:
    items = r.feed.items(key_only=key_only)[:n]
    return {"items": items, "next": ef.upcoming(snap.get("races") or [], snap["now"]),
            "source": snap.get("source", ""), "updated": snap.get("updated", 0), "now": snap["now"], "error": snap.get("error", "")}


# ---- HTTP --------------------------------------------------------------------------------------------------------------
router = APIRouter(prefix="/api/v1/wall/election", tags=["wall"])


@router.get("/live")
async def get_live(request: Request, scope: str = "key"):
    if not wel.who(request)[1]:
        return wel._deny()
    r = results()
    snap = await wel.snapshot()
    return live_payload(snap, r, scope != "all")


@router.get("/race/{rid}")
async def get_race(rid: str, request: Request):
    if not wel.who(request)[1]:
        return wel._deny()
    if not _RID.match(rid):
        return JSONResponse({"error": "bad race id"}, status_code=404)
    out = await race_payload(rid)
    if out is None:
        return JSONResponse({"error": "no such race"}, status_code=404)
    return out
