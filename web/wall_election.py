"""Election Night on the watch page: click a state → its races (CNN's live results), their Kalshi markets and price
charts; the chamber-control markets; and the TV's state spotlight card (the wall service asks for it with the API key).
docs/wall-service.md ("Election map + Kalshi") has the whole picture; the logic is shared/election_markets.py.

* Results: this app's own wall.election.Results (CNN, then NYT/AP, then Fox — the same sources and order as the wall
  service), polled only while someone looks. WALL_ELECTION_REPLAY works here too.
* Kalshi: public endpoints only (no keys), through the bets poller's token bucket (web/wall_bets.bets.limiter), only on
  demand — a state's markets when someone opens it (and the page's light refresh while it stays open), a chart's
  candles when it's drawn. Everything is cached here and shared by every viewer: prices MARKETS_TTL, today's candles
  TODAY_TTL, the older days OLDER_TTL, the series list SERIES_TTL; concurrent asks for one key make one upstream call.
* Who sees what: the map, races, prices, charts and opted-in members' positions are for every viewer of the page —
  members, friends (the keyed link's guests) and, in public mode, anyone on the plain page (read-only: a public viewer
  can't push the spotlight).
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response

from db import queries
from shared import election_markets as em
from shared import election_pulse as ep
from shared import kalshi_account as ka
from shared import kalshi_election as ke
from web import wall, wall_bets
from wall import election as we

log = logging.getLogger(__name__)
SERIES_TTL = 6 * 3600
MARKETS_TTL = 45
TODAY_TTL = 45
OLDER_TTL = 15 * 60
ODDS_TTL = 5          # the races tile's prices: as fresh as Kalshi's book (the wall asks every 10 s)
ODDS_MAP_TTL = 300    # which markets those are (the series walk) changes slowly
ODDS_PICKS = 2        # per race: its busiest event's top picks (the favourite + the runner-up)
ODDS_BATCH = 100      # tickers per /markets call (as the bets poller does)
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

    async def all_open(self, series: str) -> list[dict]:
        """Every open market in a series, all pages (the turnout series has 1000+)."""
        async def go():
            out, cursor = [], ""
            for _ in range(10):
                got = await self._get("/markets", {"series_ticker": series, "status": "open", "limit": 1000, **({"cursor": cursor} if cursor else {})})
                out += got.get("markets") or []
                cursor = got.get("cursor") or ""
                if not cursor:
                    break
            return out
        return await self.cache.get(("open", series), MARKETS_TTL, go)

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
        # calls + the vote-updates history survive restarts ($WALL_STATE_DIR, else data/; a replay keeps none)
        _results["r"] = we.Results(we.replay(spec) if spec else None, None if spec else os.environ.get("WALL_STATE_DIR") or "data")
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


async def spot_payload(state: str, snap: dict, race: str = "") -> dict:
    """The TV spotlight card: the state's races that matter most (Senate, Governor, then the House races with markets,
    closest first), each with its top two picks, prices, 24 h change and a sparkline of the favourite."""
    full = await state_payload(state, snap, None)
    rs = full["races"]
    top = [r for r in rs if r["office"] in ("S", "G")]
    house = sorted((r for r in rs if r["office"] == "H" and r["markets"]),
                   key=lambda r: abs((r["markets"][0]["prob"] or 100) - 50))
    pick = (top + house)[:3]
    if race:   # one race asked for (the spotlight queue): that one first, whatever its rank
        pick = [r for r in rs if r["id"] == race][:1] or pick
    now = markets.clock()
    cards = []
    for r in pick:
        spark = []
        if r["chart"]:
            o, td, win = await markets.candles(r["chart"][0], now)
            spark = em.sparkline(em.chart_line(r["markets"][0], o, td, win, now))
        short = f"✓ {r['called']} · {r['caller']}" if r["called"] else f"{round(r['pct'] or 0)}% in" if r["votes"] else "no votes yet"
        cards.append({"id": r["id"], "office": r["office"], "label": r["label"], "status": r["status"], "short": short, "called": r["called"],
                      "pct": r["pct"], "cands": [{k: c[k] for k in ("name", "party", "pct", "winner")} for c in r["cands"][:2]],
                      "markets": [{k: m[k] for k in ("who", "party", "prob", "bid", "ask", "chg24")} for m in r["markets"][:2]],
                      "spark": spark, "spark_party": (r["markets"][0]["party"] if r["markets"] else "")})
    return {"state": state, "name": full["name"], "races": cards, "more": max(0, len(rs) - len(pick)), "error": full["error"]}


def favourite(rows: list[dict]) -> dict | None:
    """A race's Kalshi favourite: the likeliest priced pick of its busiest event (race_markets' order)."""
    ev = [r for r in rows if r["event"] == rows[0]["event"] and r["prob"] is not None] if rows else []
    if not ev:
        return None
    f = max(ev, key=lambda r: r["prob"])
    return {"who": f["who"], "party": f["party"], "prob": f["prob"], "chg24": f["chg24"]}


async def odds_map(snap: dict) -> dict[str, list[str]]:
    """{race id: the tickers of its busiest event's top ODDS_PICKS picks} for every race to watch — which markets the
    odds read, found through the series (slow: ODDS_MAP_TTL). The House ones all come from KXHOUSERACE; a state's own
    series are fetched only when it has a Senate / Governor race to watch."""
    races = snap.get("races") or []
    want = {r["id"]: r for r in races if r.get("watch")}

    async def go():
        by_id = {r["id"]: r for r in races}
        sg = sorted({r["state"] for r in want.values() if r["office"] in ("S", "G")})
        houses = sorted({r["state"] for r in want.values() if r["office"] == "H"})
        try:
            per_state = await markets.series()
        except Exception as e:  # noqa: BLE001 — the House ones still work without the list
            log.warning("odds series: %s", type(e).__name__)
            per_state = {}
        names = sorted({s for st in sg for s in per_state.get(st, [])}) + ([em.HOUSERACE] if houses or sg else [])
        got = await asyncio.gather(*(markets.series_markets(s) for s in names), return_exceptions=True)
        mk = {}
        for name, g in zip(names, got):
            if isinstance(g, Exception):
                log.warning("odds kalshi %s: %s", name, type(g).__name__)
            else:
                mk[name] = g
        out = {}
        for st in sorted(set(sg) | set(houses)):
            own = per_state.get(st, []) if st in sg else []
            ms = [m for s in own for m in mk.get(s, [])] + mk.get(em.HOUSERACE, [])
            for rid, rows in em.race_markets(ms, by_id, st).items():
                if rid in want and rows:
                    out[rid] = [r["ticker"] for r in rows if r["event"] == rows[0]["event"]][:ODDS_PICKS]
        return out
    return await markets.cache.get(("odds-map", frozenset(want)), ODDS_MAP_TTL, go)


async def odds_payload(snap: dict) -> dict[str, dict]:
    """The races tile's odds, as fresh as Kalshi's book (no spoiler delay): {race id: favourite} for every race to watch.
    The prices are one batched public GET /markets?tickers=… (ODDS_BATCH tickers a call), cached ODDS_TTL for every caller."""
    m = await odds_map(snap)
    tickers = sorted({t for ts in m.values() for t in ts})

    async def go():
        got = []
        for i in range(0, len(tickers), ODDS_BATCH):
            chunk = tickers[i:i + ODDS_BATCH]
            got += (await markets._get("/markets", {"tickers": ",".join(chunk), "limit": 1000})).get("markets") or []
        return {x["ticker"]: em.market_row(x) for x in got if x.get("ticker")}
    rows = await markets.cache.get(("odds", tuple(tickers)), ODDS_TTL, go) if tickers else {}
    out = {}
    for rid, ts in m.items():
        f = favourite([rows[t] for t in ts if t in rows])
        if f:
            out[rid] = f
    return out


# ---- HTTP ------------------------------------------------------------------------------------------------------------
router = APIRouter(prefix="/api/v1/wall/election", tags=["wall"], dependencies=wall.guarded)


def who(request: Request) -> tuple[dict | None, bool]:
    """(the member or friend, may view at all). None + True = a public viewer (public mode): sees the data, acts on nothing."""
    user = wall.viewer(request)
    return user, bool(user) or wall.public_mode()


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
            "public": not user, "now": snap.get("now")}


_pulse_cache: dict = {"at": 0.0, "data": None}
_pulse_lock = asyncio.Lock()


async def pulse_data() -> dict:
    """Pulse for everyone, computed at most every 15 s (its Kalshi series are ~1.7 MB): the state sheets share it."""
    async with _pulse_lock:
        if _pulse_cache["data"] is None or time.monotonic() - _pulse_cache["at"] > 15:
            _pulse_cache.update(data=await _pulse_compute(), at=time.monotonic())
        return _pulse_cache["data"]


@router.get("/pulse/state/{st}")
async def get_pulse_state(st: str, request: Request):
    """One state's Pulse for the map's state sheet: its turnout projection + its races' turnout / vote-share markets."""
    if not who(request)[1]:
        return _deny()
    st = st.upper()
    if st not in we.STATES:
        return JSONResponse({"error": "no such state"}, status_code=404)
    d = await pulse_data()
    return {"state": next((s for s in d["states"] if s["state"] == st), None),
            "races": [r for r in d["races"] if r["state"] == st], "error": d.get("error", ""), "updated": d.get("updated", 0)}


async def _pulse_compute() -> dict:
    snap = await snapshot()
    got = await asyncio.gather(*(markets.all_open(s) for s in ep.SERIES), return_exceptions=True)
    mk, bad = {}, []
    for s, g in zip(ep.SERIES, got):
        if isinstance(g, Exception):
            log.warning("pulse kalshi %s: %s", s, type(g).__name__)
            bad.append(s)
        else:
            mk[s] = g
    out = ep.pulse(snap, mk)
    if bad:
        out["error"] = (out["error"] + " · " if out["error"] else "") + "Kalshi didn't answer for " + ", ".join(bad)
    return out


@router.get("/pulse")
async def get_pulse(request: Request):
    """Election Pulse (web/static/pulse.html): results x % in -> projected finals, against Kalshi's count markets."""
    if not who(request)[1]:
        return _deny()
    out = await pulse_data()
    body = json.dumps(out, separators=(",", ":")).encode()   # ~1.7 MB of markets; ~200 KB gzipped (Caddy doesn't encode)
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(gzip.compress(body, 5), media_type="application/json", headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(body, media_type="application/json")


@router.get("/pulse/mine")
async def get_pulse_mine(request: Request):
    """The viewer's own open positions in Pulse's markets (their linked Kalshi key, read-only; only ever their own).
    {ticker: {side, qty, cost}}; linked=False when they have no key."""
    user = wall.session_user(wall.auth.read_session(request))
    if not user:
        return JSONResponse({"error": "log in"}, status_code=401)
    row = await queries.get_kalshi_account(user["id"])
    if not row:
        return {"linked": False, "positions": {}}
    try:
        key = ka.load_key(ka.decrypt(row[1]))
        async with httpx.AsyncClient(timeout=15) as c:
            mps = await ka._all(c, row[0], key, "/portfolio/positions?count_filter=position", "market_positions")
    except Exception as e:  # noqa: BLE001 — never log the key, only that Kalshi said no
        log.warning("pulse mine %s: %s", user["id"], type(e).__name__)
        return JSONResponse({"linked": True, "positions": {}, "error": "Kalshi didn't answer for your key."}, status_code=502)
    out = {}
    for p in mps:
        q, t = ka._f(p.get("position_fp") or p.get("position")), p.get("ticker") or ""
        if q and t.startswith(ep.SERIES):
            out[t] = {"side": "YES" if q > 0 else "NO", "qty": abs(q), "cost": ka._f(p.get("market_exposure_dollars"))}
    return {"linked": True, "positions": out}


# ---- Pulse feedback: members write, the Claude Code session watching it replies (X-API-Key) ------------------------------
# ponytail: one JSON file + the single uvicorn worker's lock; a table if this outlives election night
FEEDBACK = Path(os.environ.get("WALL_STATE_DIR") or "data") / "pulse-feedback.json"
_fb_lock = asyncio.Lock()
_fb_last: dict[str, float] = {}


def _fb_load() -> list[dict]:
    try:
        return json.loads(FEEDBACK.read_text())
    except (OSError, ValueError):
        return []


def _fb_save(items: list[dict]) -> None:
    tmp = FEEDBACK.with_suffix(".tmp")
    tmp.write_text(json.dumps(items[-300:]))
    os.replace(tmp, FEEDBACK)


@router.get("/pulse/feedback")
async def get_feedback(request: Request):
    if not who(request)[1]:
        return _deny()
    return {"items": [{k: x.get(k) for k in ("id", "t", "who", "text", "reply", "reply_t")} for x in _fb_load()[-60:]]}


@router.post("/pulse/feedback")
async def post_feedback(request: Request):
    user = wall.session_user(wall.auth.read_session(request))
    if not user:
        return JSONResponse({"error": "Log in with Discord to send feedback."}, status_code=401)
    try:
        body = await request.json()
    except ValueError:
        body = None
    text = (body.get("text") if isinstance(body, dict) else "") or ""
    text = text.strip()[:2000] if isinstance(text, str) else ""
    if not text:
        return JSONResponse({"error": "Write something first."}, status_code=422)
    now = time.time()
    if now - _fb_last.get(user["id"], 0) < 10:
        return JSONResponse({"error": "One message every 10 s."}, status_code=429)
    _fb_last[user["id"]] = now
    async with _fb_lock:
        items = _fb_load()
        item = {"id": f"{int(now * 1000):x}", "t": now, "uid": user["id"], "who": user["name"], "text": text, "reply": None, "reply_t": None}
        _fb_save(items + [item])
    return {"id": item["id"]}


@router.post("/pulse/feedback/{fid}/reply")
async def reply_feedback(fid: str, request: Request, x_api_key: str = Header("")):
    wall._need_key(x_api_key)
    body = await request.json()
    async with _fb_lock:
        items = _fb_load()
        hit = next((x for x in items if x["id"] == fid), None)
        if not hit:
            return JSONResponse({"error": "no such feedback"}, status_code=404)
        hit["reply"], hit["reply_t"] = str(body.get("text") or "")[:4000], time.time()
        _fb_save(items)
    return {"ok": True}


@router.get("/shapes")
async def get_shapes(request: Request):
    if not who(request)[1]:
        return _deny()
    return Response(SHAPES.read_bytes(), media_type="application/json", headers={"Cache-Control": "private, max-age=86400"})


_turnout: dict = {"at": 0.0, "data": None}


@router.get("/turnout")
async def get_turnout(request: Request):
    """Official turnout by state from the wall service (wall/turnout.py), cached a minute: the state sheet's turnout block."""
    _, ok = who(request)
    if not ok:
        return _deny()
    if time.monotonic() - _turnout["at"] > 60 or _turnout["data"] is None:
        c = wall.cfg()
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(c["service"] + "/p/election/turnout.json", headers={"Authorization": f"Bearer {c['token']}"})
            _turnout.update(at=time.monotonic(), data=r.json())
        except (httpx.HTTPError, ValueError):
            _turnout["at"] = time.monotonic()   # try again in a minute; keep the last numbers
    return _turnout["data"] or {"states": {}}


@router.get("/state/{st}")
async def get_state(st: str, request: Request):
    user, ok = who(request)
    if not ok:
        return _deny()
    st = st.upper()
    if not _ST.match(st) or st not in we.STATES:
        return JSONResponse({"error": "no such state"}, status_code=404)
    return await state_payload(st, await snapshot(), user or wall.PUBLIC)   # positions for everyone who may view


@router.get("/control/{office}")
async def get_control(office: str, request: Request):
    user, ok = who(request)
    if not ok:
        return _deny()
    if office not in ("S", "H"):
        return JSONResponse({"error": "office is S or H"}, status_code=404)
    return await control_payload(office, await snapshot(), user or wall.PUBLIC)


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
async def get_spot(st: str, x_api_key: str = Header(""), race: str = ""):
    """The wall service (homelab) asks for the TV spotlight card of a state. Never positions."""
    wall._need_key(x_api_key)
    st = st.upper()
    if st not in we.STATES:
        return JSONResponse({"error": "no such state"}, status_code=404)
    return await spot_payload(st, await snapshot(), race if re.match(r"^[SGH]-[A-Z]{2}(-\d{1,2})?(-special)?$", race) else "")


@router.get("/odds")
async def get_odds(x_api_key: str = Header("")):
    """The wall service (homelab) asks for the key races' Kalshi favourites: {race id: {who, party, prob, chg24}}. Never
    positions."""
    wall._need_key(x_api_key)
    try:
        return await odds_payload(await snapshot())
    except Exception as e:  # noqa: BLE001
        log.warning("odds: %s", type(e).__name__)
        return JSONResponse({"error": "Kalshi didn't answer"}, status_code=502)


@router.post("/spotlight")
async def post_spotlight(request: Request):
    """A member or friend pushes a state to the TV's spotlight: a vote like any other change (the owner's applies at once).
    A public viewer can't."""
    user = wall.viewer(request)
    if not user:
        return JSONResponse({"error": "The watch party is invite-only."}, status_code=403 if wall.public_mode() else 401)
    try:
        body = await request.json()
    except ValueError:
        body = None
    st = body.get("state") if isinstance(body, dict) else None
    race = body.get("race", "") if isinstance(body, dict) else ""
    if not isinstance(st, str) or (st and st.upper() not in we.STATES) or not isinstance(race, str):
        return JSONResponse({"error": "state is a two-letter code"}, status_code=422)
    try:
        label = body.get("label") if isinstance(body.get("label"), str) else ""
        action = wall.check_action({"op": "spotlight", "state": st.upper(), **({"race": race, "label": label} if race else {})})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    p, err = await wall.hub.propose(user, action)
    if not p:
        return JSONResponse({"error": err}, status_code=409)
    return {"id": p.id, "status": p.status, "text": wall.describe(p.action)}


# ---- ad-skip: the homelab's wall/adwatch.py asks whether a news stream's last ~45 s of transcript is coverage or a
# commercial (wall/onair.py acts on it). The Anthropic key lives here, so the LLM call does too. ----
CLASSIFY_SYSTEM = (
    "You label a ~45-second transcript snippet from a live US TV news stream on election night. Answer only JSON "
    '{"label": "coverage"|"ad"|"break"|"offtopic", "confidence": 0..1}. coverage = news anchors, reporters or guests on '
    "the election, politics or the news; ad = a commercial (products, drugs, insurance, cars, phone numbers, disclaimers); "
    "break = bumpers, \"we'll be right back\", station IDs, promos for the channel's own shows, music or silence; "
    "offtopic = regular programming unrelated to the news (lifestyle, sports, entertainment). Also \"soon\": 0..1, how "
    "likely the stream cuts to a commercial break within the next minute, judging by the end of the snippet (anchors "
    "teasing what's next, \"stay with us\", \"after the break\"). Speech-to-text may be garbled; judge the gist.")
CLASSIFY_EVERY_S = 15.0
CLASSIFY_TIMEOUT_S = 8.0
_cls: dict = {"cache": {}, "last": {}}   # cache: text hash → (t, answer); last: stream → (t, answer)


JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MIN = 0.75   # Jev's answer stands at this confidence; below it (or no answer) Haiku decides — accept when sure, escalate when not
JEV_CRITERIA = {"coverage": "news anchors, reporters or guests on the election, politics or the news",
                "ad": "a paid commercial for a product or service (drugs, insurance, cars, phone numbers, disclaimers)",
                "break": "a station break: bumpers, \"we'll be right back\", station IDs, promos for the channel's own shows, music",
                "offtopic": "regular programming unrelated to the news: weather, lifestyle, sports, entertainment"}


# the preemptive sound move (wall/onair.py): is a break coming within a minute, and — at the moment it would move — is
# something on that can't be missed. Both are yes/no choices; the probability of the first label is what's returned.
JEV_SOON = {"soon": "the anchors are wrapping up and heading to a commercial: teasing what's coming next, \"stay with us\", "
                    "\"after the break\", \"when we come back\", \"don't go anywhere\", a toss to a break",
            "not": "coverage carries on: reporting, analysis, interviews or results, with no sign of a cut to a commercial"}
JEV_IMPORTANT = {"yes": "something viewers shouldn't miss is happening now: breaking news, a race call or projection being "
                        "announced, a candidate speaking, results coming in",
                 "no": "routine coverage: panel talk, recaps, analysis, teases, small talk"}
IMPORTANT_TIMEOUT_S = 1.2   # the wall waits 1.5 s at most; no answer = don't preempt


def _jev_p(a: object, label: str) -> float | None:
    """P(label) from a Jev choice answer: its probabilities, else its confidence (two-label questions)."""
    if not isinstance(a, dict):
        return None
    probs = a.get("probabilities")
    p = probs.get(label) if isinstance(probs, dict) else None
    if not isinstance(p, (int, float)) or isinstance(p, bool):
        conf = a.get("confidence")
        if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not isinstance(a.get("choice"), str):
            return None
        p = conf if a["choice"] == label else 1 - conf
    return round(max(0.0, min(1.0, float(p))), 2)


async def _jev(state: str, questions: dict, timeout: float = 4) -> dict | None:
    key = os.environ.get("JEV_API_KEY", "")
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(JEV_URL, json={"model": "jev-latest", "state": state, "questions": questions},
                             headers={"Authorization": f"Bearer {key}"})
        a = r.json()["answers"] if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning("jev: %s", type(e).__name__)
        return None
    return a if isinstance(a, dict) else None


async def _classify_jev(text: str, recent: str = "") -> dict | None:
    """TypeSafe's Jev decision model: what the stream airs (a typed choice + its confidence, ~0.1 s) and, from the last
    ~20 s, P(a break within the next minute) as "soon". None without a key or on any failure."""
    recent = recent.strip() or text[-400:]
    a = await _jev(f"Transcript of the last ~45 s of a live US TV news stream on election night (speech-to-text, may be "
                   f"garbled):\n{text}\n\nThe last ~20 s of it:\n{recent}",
                   {"segment": {"type": "choice", "question": "What is this stream airing right now?", "criteria": JEV_CRITERIA},
                    "soon": {"type": "choice", "question": "Judging by the last ~20 s, is this stream about to cut to a "
                             "commercial break within the next minute?", "criteria": JEV_SOON},
                    "important": {"type": "choice", "question": "Is something important happening right now that viewers "
                                  "shouldn't miss (breaking news, a race call/projection, a candidate speaking, major results)?",
                                  "criteria": JEV_IMPORTANT}})
    seg = a.get("segment") if a else None
    if not isinstance(seg, dict) or seg.get("choice") not in JEV_CRITERIA:
        return None
    out = {"label": seg["choice"], "confidence": seg.get("confidence")}
    soon = _jev_p(a.get("soon"), "soon")
    if soon is not None:
        out["soon"] = soon
    imp = _jev_p(a.get("important"), "yes")   # the stream director's "jump to it now" signal (wall/streamdir.py)
    if imp is not None:
        out["important"] = imp
    return out


async def important(text: str) -> dict:
    """{important: P(something can't-miss is on now)} from Jev on the last ~20 s; {} without a key, on a failure or a timeout."""
    async def ask():
        a = await _jev(f"The last ~20 s of a live US TV news stream on election night (speech-to-text, may be garbled):\n"
                       f"{text}", {"now": {"type": "choice", "question": "Is something important happening right now "
                                           "that viewers shouldn't miss?", "criteria": JEV_IMPORTANT}},
                       timeout=IMPORTANT_TIMEOUT_S)
        return _jev_p(a.get("now"), "yes") if a else None
    try:
        p = await asyncio.wait_for(ask(), IMPORTANT_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 — timeouts included
        log.warning("important: %s", type(e).__name__)
        p = None
    return {} if p is None else {"important": p}


async def _classify_llm(text: str) -> dict | None:
    from shared import llm
    return await llm.haiku_json(f"Transcript:\n{text}", api_key=os.environ.get("ANTHROPIC_API_KEY", ""), max_tokens=400,
                                model=llm.HAIKU_FAST, system=CLASSIFY_SYSTEM, effort="low", timeout=CLASSIFY_TIMEOUT_S)


async def classify(stream: str, text: str, now: float | None = None, recent: str = "") -> dict:
    """{label, confidence, src[, soon]}: cached by text, at most one LLM call per stream per CLASSIFY_EVERY_S (else that stream's
    last answer), "unknown" on a timeout or a bad answer."""
    import hashlib
    now = time.monotonic() if now is None else now
    key = hashlib.sha1(text.encode()).hexdigest()
    hit = _cls["cache"].get(key)
    if hit and now - hit[0] < 600:
        return {**hit[1], "src": "cache"}
    last = _cls["last"].get(stream)
    if last and now - last[0] < CLASSIFY_EVERY_S:
        return {**last[1], "src": "limited"}
    _cls["last"][stream] = (now, {"label": "unknown", "confidence": 0.0})   # holds the slot while the call runs
    src = "jev"
    got = await _classify_jev(text, recent)
    jev_soon = got.get("soon") if isinstance(got, dict) else None
    jev_imp = got.get("important") if isinstance(got, dict) else None
    if not got or not isinstance(got.get("confidence"), (int, float)) or got["confidence"] < JEV_MIN:
        src = "llm"
        try:
            got = await asyncio.wait_for(_classify_llm(text), CLASSIFY_TIMEOUT_S + 2)
        except Exception as e:  # noqa: BLE001 — timeouts included: never fail the caller
            log.warning("classify: %s", type(e).__name__)
            got = None
    label = got.get("label") if isinstance(got, dict) else None
    if label not in ("coverage", "ad", "break", "offtopic"):
        return {"label": "unknown", "confidence": 0.0, "src": src}
    conf = got.get("confidence")
    ans = {"label": label, "confidence": round(max(0.0, min(1.0, float(conf))), 2)
           if isinstance(conf, (int, float)) and not isinstance(conf, bool) else 0.6}
    soon = jev_soon if jev_soon is not None else got.get("soon")   # Jev's read of "a break is coming" first, then Haiku's
    if isinstance(soon, (int, float)) and not isinstance(soon, bool):
        ans["soon"] = round(max(0.0, min(1.0, float(soon))), 2)
    if isinstance(jev_imp, (int, float)) and not isinstance(jev_imp, bool):
        ans["important"] = jev_imp
    _cls["last"][stream] = (now, ans)
    if len(_cls["cache"]) > 2000:
        _cls["cache"].clear()
    _cls["cache"][key] = (now, ans)
    return {**ans, "src": src}


@router.post("/direct")
async def post_direct(request: Request, x_api_key: str = Header("")):
    """The wall service's director (wall/director.py): {state, options: {screen: when to show it}} → Jev's
    {choice, probabilities} over those screens. {} without a Jev key or on any failure (the wall then rotates in order)."""
    wall._need_key(x_api_key)
    try:
        body = await request.json()
    except ValueError:
        body = None
    state, options = (body.get("state"), body.get("options")) if isinstance(body, dict) else (None, None)
    question = body.get("question") if isinstance(body, dict) else None   # the stream director's (wall/streamdir.py)
    if not isinstance(state, str) or not isinstance(options, dict) or not 2 <= len(options) <= 12 or \
            not all(isinstance(k, str) and isinstance(v, str) for k, v in options.items()) or \
            not (question is None or isinstance(question, str) and question.strip()):
        return JSONResponse({"error": "state is a string, options {screen: description}, question a string"}, status_code=422)
    key = os.environ.get("JEV_API_KEY", "")
    if not key:
        return {}
    req = {"model": "jev-latest", "state": "An election-night TV wall has two dashboard tiles next to live news streams. "
           "What is happening right now:\n" + state[:4000],
           "questions": {"screen": {"type": "choice",
                                    "question": (question or "Which dashboard is the most useful to show right now?")[:300],
                                    "criteria": {k[:60]: v[:300] for k, v in options.items()}}}}
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.post(JEV_URL, json=req, headers={"Authorization": f"Bearer {key}"})
        a = r.json()["answers"]["screen"] if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning("direct: %s", type(e).__name__)
        return {}
    return {"choice": a.get("choice"), "probabilities": a.get("probabilities") or {}} if isinstance(a, dict) else {}


@router.post("/classify")
async def post_classify(request: Request, x_api_key: str = Header("")):
    """{stream, text[, recent]} → {label: coverage|ad|break|offtopic|unknown, confidence, src[, soon]};
    {check: "important", stream, text} → {important: 0..1} or {} (the preemptive sound move's last look). The wall
    service's key only."""
    wall._need_key(x_api_key)
    try:
        body = await request.json()
    except ValueError:
        body = None
    stream, text = (body.get("stream"), body.get("text")) if isinstance(body, dict) else (None, None)
    if not isinstance(stream, str) or not isinstance(text, str) or not text.strip():
        return JSONResponse({"error": "stream and text are strings"}, status_code=422)
    if body.get("check") == "important":
        return await important(text.strip()[-1200:])
    recent = body.get("recent") if isinstance(body.get("recent"), str) else ""
    return await classify(stream[:300], text.strip()[-2000:], recent=recent.strip()[-1200:])
