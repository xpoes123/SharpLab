"""Members' Kalshi positions on the watch-party wall: the watch page's Bets panel and the TV overlay's per-tile badges.
docs/wall-service.md ("Bets on the wall") has the whole picture.

* Who shows: only members who turned sharing on (/kalshi wall, or the toggle on the watch page) and have a linked
  key. Guests (demo-mode nicknames) never get the panel. Sizes are bucketed and P&L is % only unless the member also
  chose exact numbers. Balances are never read.
* Kalshi: one poller for everyone, only while someone is looking (the page or the TV feed asked in the last 2 min):
  every REFRESH_S each sharer's open positions (one signed GET) + one public /markets batch for their tickers, through
  a token bucket. Keys are decrypted per call and never logged.
* Spoilers: every sample keeps the time it was seen (shared/kalshi_wall.Tape) and a view is built as of
  (the picture's time − that tile's delay D). The TV's delay is the wall service's (auto-calibrated from the tiles'
  scorebugs, or set by hand); a web viewer is a further WALL_SYNC_DELAY_S behind the TV, and the page says exactly
  where its picture is (the stream's program-date-time in server time), so its prices match its own picture.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from db import queries
from shared import kalshi_account as ka
from shared import kalshi_election as ke
from shared import kalshi_wall as kw
from web import wall

log = logging.getLogger(__name__)
REFRESH_S = 20
IDLE_S = 120
DEFAULT_DELAY = 45.0
SPORTS = tuple(kw.LEAGUE_PREFIX.values())


async def kalshi_positions(uid: str) -> dict[str, tuple[str, float, float]] | None:
    """{ticker: (side, qty, cost $)} of this member's open NBA/NFL + midterm positions; None = no key / Kalshi refused it."""
    row = await queries.get_kalshi_account(uid)
    if not row:
        return None
    key = ka.load_key(ka.decrypt(row[1]))
    async with httpx.AsyncClient(timeout=15) as c:
        mps = await ka._all(c, row[0], key, "/portfolio/positions?count_filter=position", "market_positions")
    out = {}
    for p in mps:
        q = ka._f(p.get("position_fp") or p.get("position"))
        t = p.get("ticker") or ""
        if q and (t.startswith(SPORTS) or ke.is_election(t)):
            out[t] = ("YES" if q > 0 else "NO", abs(q), ka._f(p.get("market_exposure_dollars")))
    return out


async def kalshi_markets(tickers: list[str]) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{ka.KALSHI_BASE}/markets", params={"tickers": ",".join(tickers), "limit": 200})
        r.raise_for_status()
        return r.json().get("markets") or []


class Bets:
    def __init__(self, clock=time.time, positions=kalshi_positions, markets=kalshi_markets, sharers=None,
                 limiter: kw.RateLimit | None = None, sleep=asyncio.sleep) -> None:
        self.clock, self.positions, self.markets, self.sleep = clock, positions, markets, sleep
        self.list_sharers = sharers or queries.list_kalshi_wall_sharers
        self.limiter = limiter or kw.RateLimit(rate=5, burst=10)
        self.book = kw.Book()
        self.sharers: set[str] = set()
        self.demand = 0.0
        self.polled = 0.0
        self.calls = 0
        self.error = ""
        # the election results the wall service last sent with its feed call (POST /feed "election"): the races our
        # sharers hold + the balance per office, for the rows' status line. Empty until a wall asks for election rows.
        self.races: dict[str, dict] = {}
        self.offices: dict[str, dict] = {}
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def _call(self, fn, *a):
        wait = self.limiter.take()
        if wait:
            await self.sleep(wait)
        self.calls += 1
        return await fn(*a)

    async def poll(self) -> None:
        """One round: who shares, their positions, the prices of what they hold."""
        async with self._lock:
            members = await self.list_sharers()
            self.sharers = {m["uid"] for m in members}
            for uid in [u for u in self.book.members if u not in self.sharers]:
                self.book.forget(uid)
            held: set[str] = set()
            for m in members:
                self.book.members[m["uid"]] = {"name": m["name"], "exact": m["exact"]}
                try:
                    rows = await self._call(self.positions, m["uid"])
                except ka.KalshiAuthError:
                    rows = None
                except Exception as e:  # noqa: BLE001 — one member's Kalshi hiccup keeps the others (never the key in logs)
                    log.warning("wall bets: positions for %s: %s", m["uid"], type(e).__name__)
                    snap = (self.book.positions.get(m["uid"]) or kw.Tape()).at(self.clock())
                    held |= set(snap or {})
                    continue
                if rows is None:
                    self.book.forget(m["uid"])
                    continue
                self.book.see_positions(self.clock(), m["uid"], rows)
                held |= set(rows)
            tickers = sorted(held)
            self.error = ""
            for i in range(0, len(tickers), 100):
                try:
                    got = await self._call(self.markets, tickers[i:i + 100])
                except Exception as e:  # noqa: BLE001
                    self.error = f"Kalshi prices: {type(e).__name__}"
                    log.warning("wall bets: %s", self.error)
                    continue
                now = self.clock()
                for mk in got:
                    self.book.see_market(now, mk)
                    ke.see(self.book, mk)
            self.polled = self.clock()

    def want(self) -> None:
        """Someone is looking: keep the poller going (and start it)."""
        self.demand = self.clock()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while self.clock() - self.demand < IDLE_S:
            try:
                await self.poll()
            except Exception as e:  # noqa: BLE001 — never let the poller die mid-party
                log.warning("wall bets poll: %s", type(e).__name__)
            await self.sleep(REFRESH_S)

    def rows(self, game: dict, as_of: float) -> list[dict]:
        return kw.game_rows(self.book, game, as_of, self.sharers)

    def election_rows(self) -> list[dict]:
        # midterm positions as of now: results aren't a picture, so no spoiler delay
        return ke.enrich(ke.election_rows(self.book, self.clock(), self.sharers), self.races, self.offices)

    def election_need(self) -> list[str]:
        # the race ids our sharers' election markets may be (what the wall service should send results for)
        out = set()
        for mk in self.book.markets.values():
            c = (mk.get("election") or {}).get("class")
            if c and c["type"] == "race":
                out.update(ke.race_ids(c))
        return sorted(out)

    def opt_out(self, uid: str) -> None:
        self.sharers.discard(uid)
        self.book.forget(uid)


bets = Bets()


def reset_bets(**kw_) -> Bets:
    global bets
    bets = Bets(**kw_)
    return bets


# ---- which games: the tiles (from the wall service's state) + every other live game ----------------------------------
_live: dict = {"at": 0.0, "games": []}


async def other_live_games() -> list[dict]:
    """Every live / soon NBA + NFL game the wall service knows (with teams + start for the Kalshi match), cached 20 s."""
    if time.monotonic() - _live["at"] < 20:
        return _live["games"]
    c = wall.cfg()
    out = []
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            for lg in ("nba", "nfl"):
                r = await client.get(f"{c['service']}/live/{lg}?n=16", headers={"Authorization": f"Bearer {c['token']}"})
                if r.status_code == 200:
                    out += [{"league": lg, "id": str(g["id"]), "label": str(g.get("label", ""))[:60], "away": g.get("away") or "",
                             "home": g.get("home") or "", "start": g.get("start") or "", "state": g.get("state") or ""}
                            for g in r.json() if isinstance(g, dict) and g.get("id") and g.get("away")]
    except (httpx.HTTPError, ValueError):
        return _live["games"]
    _live.update(at=time.monotonic(), games=out)
    return out


def sync_info(state: dict | None) -> dict:
    """The wall service's spoiler sync, as its state carries it: {show, default, tiles: [{url, delay, src, game}]}."""
    s = (state or {}).get("sync") or {}
    return {"show": s.get("show", True), "default": float(s.get("default") or DEFAULT_DELAY),
            "tiles": [t for t in s.get("tiles") or [] if isinstance(t, dict)]}


async def panel(at_ms: float | None, state: dict | None, others: list[dict]) -> dict:
    now = bets.clock()
    sync = sync_info(state)
    picture = now - wall.cfg()["delay"]                        # where a synced viewer's picture is, in TV time
    if at_ms is not None:
        picture = min(picture, at_ms / 1000)                   # a viewer further behind gets older prices, never newer
    games, seen = [], set()
    for i, t in enumerate(sync["tiles"]):
        g = t.get("game")
        if not isinstance(g, dict) or not g.get("id"):
            continue
        d = float(t.get("delay") or sync["default"])
        seen.add(kw.game_key(g))
        games.append({"key": kw.game_key(g), "league": g.get("league"), "label": g.get("label") or f"{g.get('away')} @ {g.get('home')}",
                      "tile": i, "url": t.get("url") or "", "delay": d, "src": t.get("src") or "default", "as_of": (picture - d) * 1000,
                      "rows": bets.rows(g, picture - d)})
    d = sync["default"]
    for g in others:
        if kw.game_key(g) in seen:
            continue
        rows = bets.rows(g, picture - d)
        if rows:
            games.append({"key": kw.game_key(g), "league": g["league"], "label": f"{g['away']} @ {g['home']}", "tile": None,
                          "delay": d, "src": "default", "as_of": (picture - d) * 1000, "rows": rows})
    return {"games": games, "election": bets.election_rows(), "show": sync["show"], "default": d, "picture": picture * 1000,
            "now": now * 1000, "polled": bets.polled * 1000, "error": bets.error}


# ---- HTTP ------------------------------------------------------------------------------------------------------------
router = APIRouter(prefix="/api/v1/wall/bets", tags=["wall"], dependencies=wall.guarded)


def member(request: Request, read: bool = False) -> tuple[dict | None, JSONResponse | None]:
    """Members and friends (the keyed link's guests). A public viewer (public mode) may read the panel (wall.PUBLIC),
    never change anything (403); nobody else gets a 401."""
    user = wall.viewer(request)
    if user and not user.get("guest"):   # SharpLab members only: friends' links and the public don't see members' bets
        return user, None
    if user or wall.public_mode():
        return None, JSONResponse({"error": "Bets are for SharpLab members."}, status_code=403)
    return None, JSONResponse({"error": "log in"}, status_code=401)


@router.get("")
async def get_panel(request: Request, at: float | None = None):
    user, err = member(request, read=True)
    if err:
        return err
    bets.want()
    h = wall.hub
    if h.wall is None:
        await h.refresh_wall()
    out = await panel(at, h.wall, await other_live_games())
    out["owner"] = h.is_admin(user["id"])
    return out


@router.get("/me")
async def get_me(request: Request):
    user, err = member(request, read=True)
    if err:
        return err
    if user.get("guest") or user.get("public"):   # no Kalshi key here to share: the panel, read-only
        return {"share": False, "exact": False, "linked": False, "guest": True}
    o = await queries.get_kalshi_wall_optin(user["id"])
    return {"share": o["share"], "exact": o["exact"], "linked": await queries.get_kalshi_account(user["id"]) is not None}


@router.post("/me")
async def set_me(request: Request):
    user, err = member(request)
    if err:
        return err
    if user.get("guest"):
        return JSONResponse({"error": "Sharing positions needs a linked Kalshi account (Discord members)."}, status_code=403)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("share"), bool):
        return JSONResponse({"error": "share is true or false"}, status_code=422)
    share, exact = body["share"], body.get("exact") is True and body["share"]
    await queries.set_kalshi_wall_optin(user["id"], share, exact, user["name"])
    if share:
        bets.book.members[user["id"]] = {"name": user["name"], "exact": exact}
    else:
        bets.opt_out(user["id"])   # off the wall now, not at the next poll
    return {"share": share, "exact": exact, "linked": await queries.get_kalshi_account(user["id"]) is not None}


SYNC_KEYS = {"show", "default", "url", "nudge", "auto", "tap"}


@router.post("/sync")
async def set_sync(request: Request):
    """The owner / admins: hide bets on the overlay, set the default delay, nudge a tile's delay, tap-calibrate it.
    Forwarded to the wall service, which owns the setting (the TV, its phones and the stream all read it there)."""
    user, err = member(request)
    if err:
        return err
    if not wall.hub.is_admin(user["id"]):
        return JSONResponse({"error": "Only David and admins change the bets sync."}, status_code=403)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not set(body) <= SYNC_KEYS or not body:
        return JSONResponse({"error": f"body is an object with some of {sorted(SYNC_KEYS)}"}, status_code=422)
    if "tap" in body:   # the viewer's picture time (server ms) → the TV's clock: the service compares it with ESPN's play time
        tap = body["tap"]
        if not isinstance(tap, (int, float)) or abs(tap / 1000 - bets.clock()) > 600:
            return JSONResponse({"error": "tap is the picture's time in ms"}, status_code=422)
    c = wall.cfg()
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(c["service"] + "/sync", json=body, headers={"Authorization": f"Bearer {c['token']}"})
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return JSONResponse({"error": "wall service unreachable"}, status_code=502)
    await wall.hub.refresh_wall()
    return JSONResponse(data, status_code=r.status_code)


@router.post("/feed")
async def tv_feed(request: Request, x_api_key: str = Header("")):
    """The wall service (homelab) asks for its tiles' games, each delayed by that tile's D:
    {"games": [{league, id, away, home, start, delay}]} → {"games": {key: [rows]}}. Same opt-in as the page.
    With "election": {races, offices} (sent while an election board / page is up: the results for the races we named in
    "need" last time) the answer also has "election": [rows] (never delayed) and "need": [race ids]."""
    wall._need_key(x_api_key)
    try:
        body = await request.json()
    except ValueError:
        body = None
    games = body.get("games") if isinstance(body, dict) else None
    elec = body.get("election") if isinstance(body, dict) else None
    if not isinstance(games, list):
        return JSONResponse({"error": "games is a list"}, status_code=422)
    bets.want()
    now = bets.clock()
    out = {}
    for g in games[:8]:
        if not isinstance(g, dict) or not g.get("id"):
            continue
        d = g.get("delay")
        d = max(0.0, float(d)) if isinstance(d, (int, float)) else DEFAULT_DELAY
        out[kw.game_key(g)] = [{k: v for k, v in r.items() if k != "uid"} for r in bets.rows(g, now - d)]
    res = {"games": out, "now": now * 1000}
    if isinstance(elec, dict):
        if isinstance(elec.get("races"), dict):
            bets.races = {k: v for k, v in elec["races"].items() if isinstance(v, dict)}
        if isinstance(elec.get("offices"), dict):
            bets.offices = {k: v for k, v in elec["offices"].items() if isinstance(v, dict)}
        res["election"] = [{k: v for k, v in r.items() if k != "uid"} for r in bets.election_rows()]
        res["need"] = bets.election_need()
    return res
