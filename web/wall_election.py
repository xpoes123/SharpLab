"""Election Night on the watch page: click a state → its races (CNN's live results), their Kalshi markets and price
charts; the chamber-control markets; and the TV's state spotlight card (the wall service asks for it with the API key).
docs/wall-service.md ("Election map + Kalshi") has the whole picture; the logic is shared/election_markets.py.

* Results: this app's own wall.election.Results (CNN, then NYT/AP, then Fox — the same sources and order as the wall
  service), polled only while someone looks. WALL_ELECTION_REPLAY works here too.
* Kalshi: public endpoints only (no keys), through the bets poller's token bucket (web/wall_bets.bets.limiter), only on
  demand — a state's markets when someone opens it (and the page's light refresh while it stays open), a chart's
  candles when it's drawn. Everything is cached here and shared by every viewer: prices MARKETS_TTL, today's candles
  TODAY_TTL, the older days OLDER_TTL, the series list SERIES_TTL; concurrent asks for one key make one upstream call.
* Who sees what: the map, races, prices and charts are public data — any viewer of the page (members and guests). Opted-in
  members' positions are for members only, by the same rule as the Bets panel; a guest's answer never has them.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response

from shared import election_markets as em
from shared import kalshi_account as ka
from shared import kalshi_election as ke
from web import wall, wall_bets
from wall import election as we

log = logging.getLogger(__name__)
SERIES_TTL = 6 * 3600
MARKETS_TTL = 45
TODAY_TTL = 45
OLDER_TTL = 15 * 60
CONCURRENCY = 3
SHAPES = Path(__file__).resolve().parent.parent / "wall" / "pages" / "us-states.json"
_ST = re.compile(r"^[A-Z]{2}$")


class Cache:
    """A TTL cache with single flight: one upstream call per key however many viewers ask at once. On an upstream error
    the last value (if any) is served stale rather than failing the page."""

    def __init__(self, clock=time.monotonic, max_items: int = 4000) -> None:
        self.clock, self.max_items = clock, max_items
        self.data: dict = {}
        self.inflight: dict = {}
        self.misses = 0

    async def get(self, key, ttl: float, fn):
        hit = self.data.get(key)
        if hit and self.clock() - hit[0] < ttl:
            return hit[1]
        fut = self.inflight.get(key)
        if fut is not None:
            return await asyncio.shield(fut)
        fut = asyncio.get_running_loop().create_future()
        self.inflight[key] = fut
        self.misses += 1
        try:
            val = await fn()
        except Exception as e:  # noqa: BLE001 — stale beats nothing on election night
            fut.set_exception(e)
            fut.exception()   # retrieved: no "never retrieved" warning when nobody else waited
            if hit:
                return hit[1]
            raise
        else:
            self.data[key] = (self.clock(), val)
            if len(self.data) > self.max_items:
                for k in sorted(self.data, key=lambda k: self.data[k][0])[: len(self.data) - self.max_items]:
                    self.data.pop(k, None)
            fut.set_result(val)
            return val
        finally:
            self.inflight.pop(key, None)


async def kalshi_get(path: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(ka.KALSHI_BASE + path, params=params)
        r.raise_for_status()
        return r.json()


class Markets:
    """Kalshi's public election data, cached + rate limited (the bets poller's bucket)."""

    def __init__(self, fetch=kalshi_get, clock=time.time, cache: Cache | None = None) -> None:
        self.fetch, self.clock = fetch, clock
        self.cache = cache or Cache()
        self.calls = 0
        self.sem = asyncio.Semaphore(CONCURRENCY)

    async def _get(self, path: str, params: dict) -> dict:
        """One Kalshi call through the shared bucket, at most CONCURRENCY at once; a 429 waits a second and tries once more."""
        b = wall_bets.bets
        async with self.sem:
            for attempt in (0, 1):
                wait = b.limiter.take()
                if wait:
                    await b.sleep(wait)
                self.calls += 1
                b.calls += 1
                try:
                    return await self.fetch(path, params)
                except httpx.HTTPStatusError as e:
                    if attempt or e.response.status_code != 429:
                        raise
                    await b.sleep(1.0)
        raise AssertionError("unreachable")

    async def series(self) -> dict[str, list[str]]:
        async def go():
            return em.state_series((await self._get("/series", {"category": "Elections"})).get("series") or [])
        return await self.cache.get("series", SERIES_TTL, go)

    async def series_markets(self, series: str) -> list[dict]:
        async def go():
            return (await self._get("/markets", {"series_ticker": series, "limit": 1000})).get("markets") or []
        return await self.cache.get(("markets", series), MARKETS_TTL, go)

    async def state_markets(self, state: str) -> list[dict]:
        names = (await self.series()).get(state, []) + [em.HOUSERACE]
        got = await asyncio.gather(*(self.series_markets(s) for s in names), return_exceptions=True)
        out = []
        for name, g in zip(names, got):
            if isinstance(g, Exception):
                log.warning("kalshi %s: %s", name, type(g).__name__)
                continue
            out += g
        return out

    async def candles(self, ticker: str, now: float) -> tuple[list[dict], list[dict], dict]:
        win = em.windows(now)
        series = em.series_of(ticker)
        path = f"/series/{series}/markets/{ticker}/candlesticks"

        async def older():
            a, b = win["older"]
            return (await self._get(path, {"start_ts": a, "end_ts": b, "period_interval": 60})).get("candlesticks") or []

        async def today():
            a, b = win["today"]
            return (await self._get(path, {"start_ts": a, "end_ts": b, "period_interval": 1})).get("candlesticks") or []
        o, t = await asyncio.gather(self.cache.get(("older", ticker, win["older"][0]), OLDER_TTL, older),
                                    self.cache.get(("today", ticker, win["split"]), TODAY_TTL, today), return_exceptions=True)
        return ([] if isinstance(o, Exception) else o), ([] if isinstance(t, Exception) else t), win


markets = Markets()
_results: dict = {"r": None}


def results() -> we.Results:
    if _results["r"] is None:
        spec = os.environ.get("WALL_ELECTION_REPLAY", "")
        _results["r"] = we.Results(we.replay(spec) if spec else None)
    return _results["r"]


def reset(fetch=None, res: we.Results | None = None, clock=time.time, cache_clock=time.monotonic) -> Markets:
    """Tests: fresh caches, a fake Kalshi, recorded results."""
    global markets
    markets = Markets(fetch or kalshi_get, clock=clock, cache=Cache(clock=cache_clock))
    _results["r"] = res
    return markets


async def snapshot(wait_s: float = 8.0) -> dict:
    """The results as of now; the first ask after an idle spell waits (briefly) for the first poll."""
    r = results()
    r.touch()
    t0 = time.monotonic()
    while not r.races and not r.error and time.monotonic() - t0 < wait_s:
        await asyncio.sleep(0.25)
    return r.snapshot()


# ---- the views ------------------------------------------------------------------------------------------------------------
def positions_for(user: dict | None, snap: dict) -> list[dict] | None:
    """Members' election rows (opt-in filtered), with race status from these results. None for a guest."""
    if not user:
        return None
    b = wall_bets.bets
    b.want()
    races = {r["id"]: r for r in snap.get("races") or []}
    return ke.enrich(ke.election_rows(b.book, b.clock(), b.sharers), races, snap.get("offices") or {})


async def state_payload(state: str, snap: dict, user: dict | None) -> dict:
    races = snap.get("races") or []
    by_id = {r["id"]: r for r in races}
    error = ""
    try:
        mk = em.race_markets(await markets.state_markets(state), by_id, state)
    except Exception as e:  # noqa: BLE001
        log.warning("kalshi state %s: %s", state, type(e).__name__)
        mk, error = {}, "Kalshi didn't answer — prices will be back shortly."
    out = em.state_view(state, races, mk, snap.get("calls") or [])
    out.update(name=we.STATES.get(state, state), source=snap.get("source", ""), updated=snap.get("updated", 0),
               error=error or snap.get("error", ""), now=markets.clock())
    rows = positions_for(user, snap)
    if rows is not None:
        out["positions"] = em.state_positions(rows, state)
    return out


async def control_payload(office: str, snap: dict, user: dict | None) -> dict:
    error = ""
    try:
        rows = em.control_markets(await markets.series_markets("CONTROL" + office), office)
    except Exception as e:  # noqa: BLE001
        log.warning("kalshi control %s: %s", office, type(e).__name__)
        rows, error = [], "Kalshi didn't answer — prices will be back shortly."
    out = {"office": office, "name": we.OFFICES[office]["name"], "balance": (snap.get("offices") or {}).get(office),
           "markets": rows, "chart": [r["ticker"] for r in rows][:2], "error": error, "now": markets.clock()}
    pos = positions_for(user, snap)
    if pos is not None:
        out["positions"] = em.control_positions(pos, office)
    return out


async def chart_payload(key: str, snap: dict, want: list[str] | None = None) -> dict:
    """key = a race id (S-GA, H-PA-7, S-OH-special) or CTRL-S / CTRL-H → {lines, marks, split, now}. The tickers drawn are
    that race's own markets only (want = a subset of them; default its chart picks)."""
    now = markets.clock()
    race, rows = None, []
    if key.startswith("CTRL-") and key[5:] in ("S", "H"):
        rows = em.control_markets(await markets.series_markets("CONTROL" + key[5:]), key[5:])
        default = [r["ticker"] for r in rows][:2]
    else:
        race = next((r for r in snap.get("races") or [] if r["id"] == key), None)
        if race is None:
            return {"lines": [], "marks": [], "error": "no such race"}
        rows = em.race_markets(await markets.state_markets(race["state"]), {r["id"]: r for r in snap["races"]}, race["state"]).get(key, [])
        default = em.chart_tickers(rows)
    by = {r["ticker"]: r for r in rows}
    pick = [t for t in (want or []) if t in by][:6] or default
    got = await asyncio.gather(*(markets.candles(t, now) for t in pick))
    lines = [em.chart_line(by[t], o, td, win, now) for t, (o, td, win) in zip(pick, got)]
    called_at = em.call_times(snap.get("calls") or []).get(key)
    return {"key": key, "lines": lines, "marks": em.marks(race, called_at), "split": em.windows(now)["split"], "now": now}


async def spot_payload(state: str, snap: dict) -> dict:
    """The TV spotlight card: the state's races that matter most (Senate, Governor, then the House races with markets,
    closest first), each with its top two picks, prices, 24 h change and a sparkline of the favourite."""
    full = await state_payload(state, snap, None)
    rs = full["races"]
    top = [r for r in rs if r["office"] in ("S", "G")]
    house = sorted((r for r in rs if r["office"] == "H" and r["markets"]),
                   key=lambda r: abs((r["markets"][0]["prob"] or 100) - 50))
    pick = (top + house)[:3]
    now = markets.clock()
    cards = []
    for r in pick:
        spark = []
        if r["chart"]:
            o, td, win = await markets.candles(r["chart"][0], now)
            spark = em.sparkline(em.chart_line(r["markets"][0], o, td, win, now))
        cards.append({"id": r["id"], "office": r["office"], "label": r["label"], "status": r["status"], "called": r["called"],
                      "pct": r["pct"], "cands": [{k: c[k] for k in ("name", "party", "pct", "winner")} for c in r["cands"][:2]],
                      "markets": [{k: m[k] for k in ("who", "party", "prob", "bid", "ask", "chg24")} for m in r["markets"][:2]],
                      "spark": spark, "spark_party": (r["markets"][0]["party"] if r["markets"] else "")})
    return {"state": state, "name": full["name"], "races": cards, "more": max(0, len(rs) - len(pick)), "error": full["error"]}


# ---- HTTP ------------------------------------------------------------------------------------------------------------
router = APIRouter(prefix="/api/v1/wall/election", tags=["wall"])


def who(request: Request) -> tuple[dict | None, bool]:
    """(the member or None, may view at all). Guests view; nobody logged in doesn't."""
    user = wall.session_user(wall.auth.read_session(request))
    return user, bool(user or wall.read_guest(request))


def _deny() -> JSONResponse:
    return JSONResponse({"error": "log in"}, status_code=401)


@router.get("/map")
async def get_map(request: Request):
    user, ok = who(request)
    if not ok:
        return _deny()
    snap = await snapshot()
    return {"races": em.map_races(snap.get("races") or []), "offices": snap.get("offices") or {}, "source": snap.get("source", ""),
            "updated": snap.get("updated", 0), "error": snap.get("error", ""), "calls": (snap.get("calls") or [])[:8],
            "states": we.STATES, "member": bool(user), "owner": bool(user and wall.hub.is_owner(user["id"])),
            "now": snap.get("now")}


@router.get("/shapes")
async def get_shapes(request: Request):
    if not who(request)[1]:
        return _deny()
    return Response(SHAPES.read_bytes(), media_type="application/json", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/state/{st}")
async def get_state(st: str, request: Request):
    user, ok = who(request)
    if not ok:
        return _deny()
    st = st.upper()
    if not _ST.match(st) or st not in we.STATES:
        return JSONResponse({"error": "no such state"}, status_code=404)
    return await state_payload(st, await snapshot(), user)


@router.get("/control/{office}")
async def get_control(office: str, request: Request):
    user, ok = who(request)
    if not ok:
        return _deny()
    if office not in ("S", "H"):
        return JSONResponse({"error": "office is S or H"}, status_code=404)
    return await control_payload(office, await snapshot(), user)


@router.get("/chart/{key}")
async def get_chart(key: str, request: Request, t: str = ""):
    if not who(request)[1]:
        return _deny()
    if not re.match(r"^(CTRL-[SH]|[SGH]-[A-Z]{2}(-\d{1,2})?(-special)?)$", key):
        return JSONResponse({"error": "bad chart key"}, status_code=404)
    want = [x for x in t.split(",") if x][:6] if t else None
    try:
        return await chart_payload(key, await snapshot(), want)
    except Exception as e:  # noqa: BLE001
        log.warning("chart %s: %s", key, type(e).__name__)
        return JSONResponse({"lines": [], "marks": [], "error": "Kalshi didn't answer — try again shortly."}, status_code=502)


@router.get("/spot/{st}")
async def get_spot(st: str, x_api_key: str = Header("")):
    """The wall service (homelab) asks for the TV spotlight card of a state. Never positions."""
    wall._need_key(x_api_key)
    st = st.upper()
    if st not in we.STATES:
        return JSONResponse({"error": "no such state"}, status_code=404)
    return await spot_payload(st, await snapshot())


@router.post("/spotlight")
async def post_spotlight(request: Request):
    """A member pushes a state to the TV's spotlight: a vote like any other change (the owner's applies at once).
    Guests can't (the same rule as starting the wall or putting up pages)."""
    user = wall.session_user(wall.auth.read_session(request))
    if not user:
        return JSONResponse({"error": "Members only."}, status_code=403 if wall.read_guest(request) else 401)
    try:
        body = await request.json()
    except ValueError:
        body = None
    st = body.get("state") if isinstance(body, dict) else None
    if not isinstance(st, str) or (st and st.upper() not in we.STATES):
        return JSONResponse({"error": "state is a two-letter code"}, status_code=422)
    p, err = await wall.hub.propose(user, {"op": "spotlight", "state": st.upper()})
    if not p:
        return JSONResponse({"error": err}, status_code=409)
    return {"id": p.id, "status": p.status, "text": wall.describe(p.action)}
