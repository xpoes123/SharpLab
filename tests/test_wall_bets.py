"""The Bets panel + the TV feed (web/wall_bets.py): opt-in enforcement, guests never see it, the per-viewer spoiler delay,
one shared poller behind a rate limit. No network: Kalshi, the DB and the wall service are stubbed."""
import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from shared import kalshi_wall as kw
from web import auth, wall, wall_bets

OWNER = "100"
LAL = {"league": "nba", "id": "401909921", "away": "MIA", "home": "LAL", "start": "2026-11-03T03:30Z", "label": "MIA @ LAL"}
GSW = {"league": "nba", "id": "401909919", "away": "PHX", "home": "GS", "start": "2026-11-03T03:00Z"}
SPREAD = "KXNBASPREAD-26NOV02MIALAL-LAL3"
ML_GS = "KXNBAGAME-26NOV02PHXGSW-GSW"


class Clock:
    def __init__(self, t=10_000.0):
        self.t = t

    def __call__(self):
        return self.t


def market(ticker, bid, ask):
    return {"ticker": ticker, "event_ticker": kw.event_ticker_of(ticker), "floor_strike": 3.5,
            "yes_bid_dollars": f"{bid / 100:.4f}", "yes_ask_dollars": f"{ask / 100:.4f}"}


class Kalshi:
    """Stub Kalshi: positions per uid, a price per ticker; counts calls."""

    def __init__(self):
        self.pos = {"1": {SPREAD: ("YES", 120, 50.4)}, "2": {SPREAD: ("NO", 8, 4.48), ML_GS: ("YES", 30, 15.0)},
                    "3": {SPREAD: ("YES", 5, 2.0)}}
        self.px = {SPREAD: (42, 44), ML_GS: (55, 57)}
        self.calls = []

    async def positions(self, uid):
        self.calls.append(("pos", uid))
        return self.pos.get(uid)

    async def markets(self, tickers):
        self.calls.append(("mkt", tuple(tickers)))
        return [market(t, *self.px[t]) for t in tickers if t in self.px]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("WALL_OWNER_ID", OWNER)
    monkeypatch.setenv("WALL_SYNC_DELAY_S", "9")
    monkeypatch.setattr(wall, "check_api_key", lambda k: k == "sekrit")
    clock, k = Clock(), Kalshi()
    optin = {"1": {"share": True, "exact": False, "name": "David"}, "2": {"share": True, "exact": True, "name": "Steph"},
             "3": {"share": False, "exact": False, "name": "Kim"}}

    async def sharers():
        return [{"uid": u, "name": o["name"], "exact": o["exact"]} for u, o in optin.items() if o["share"]]

    async def get_optin(uid):
        return optin.get(uid, {"share": False, "exact": False, "name": ""})

    async def set_optin(uid, share, exact, name=""):
        optin[uid] = {"share": share, "exact": exact, "name": name or optin.get(uid, {}).get("name", "")}

    async def get_account(uid):
        return ("kid", b"x") if uid in ("1", "2", "3") else None

    monkeypatch.setattr(wall_bets.queries, "get_kalshi_wall_optin", get_optin)
    monkeypatch.setattr(wall_bets.queries, "set_kalshi_wall_optin", set_optin)
    monkeypatch.setattr(wall_bets.queries, "get_kalshi_account", get_account)

    async def nosleep(s):
        return None
    b = wall_bets.reset_bets(clock=clock, positions=k.positions, markets=k.markets, sharers=sharers, sleep=nosleep,
                             limiter=kw.RateLimit(rate=1000, burst=1000, clock=clock))
    b.want = lambda: setattr(b, "demand", clock())   # no background task in tests: poll() by hand

    state = {"running": True, "urls": ["https://t/lakers"], "sync": {"show": True, "default": 45,
             "tiles": [{"url": "https://t/lakers", "delay": 40, "src": "auto", "game": LAL}]}}

    async def fetch_state():
        return state
    hub = wall.reset_hub(clock=clock, fetch_state=fetch_state)
    hub.wall = state

    async def others():
        return [LAL, GSW]
    monkeypatch.setattr(wall_bets, "other_live_games", others)
    app = FastAPI()
    app.include_router(wall.router)
    app.include_router(wall_bets.router)
    return TestClient(app), b, clock, k, optin


def login(client, uid, name="User"):
    client.cookies.clear()
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie({"id": uid, "username": name, "avatar": None}))


def poll(b):
    asyncio.run(b.poll())


def test_only_opted_in_members_appear(env):
    client, b, clock, k, optin = env
    poll(b)
    login(client, "1", "David")
    clock.t += 100
    g = client.get("/api/v1/wall/bets").json()["games"]
    lal = next(x for x in g if x["key"] == "nba:401909921")
    assert {r["who"] for r in lal["rows"]} == {"David", "Steph"}   # Kim holds the market but never opted in
    assert all(r["uid"] != "3" for x in g for r in x["rows"])
    assert ("pos", "3") not in k.calls                              # a non-sharer's account isn't even read
    gs = next(x for x in g if x["key"] == "nba:401909919")
    assert gs["tile"] is None and [r["who"] for r in gs["rows"]] == ["Steph"]
    steph = next(r for r in lal["rows"] if r["who"] == "Steph")
    david = next(r for r in lal["rows"] if r["who"] == "David")
    assert steph["size"] == "8 ct" and "pnl" in steph               # exact numbers: she chose them
    assert david["size"] == "50+ ct" and "pnl" not in david         # bucket + % only


def test_opt_out_is_immediate(env):
    client, b, clock, k, optin = env
    poll(b)
    clock.t += 100
    login(client, "2", "Steph")
    assert client.post("/api/v1/wall/bets/me", json={"share": False}).json()["share"] is False
    g = client.get("/api/v1/wall/bets").json()["games"]
    assert all(r["who"] != "Steph" for x in g for r in x["rows"])
    r = client.post("/api/v1/wall/bets/feed", json={"games": [{**LAL, "delay": 0}]}, headers={"X-Api-Key": "sekrit"})
    assert [x["who"] for x in r.json()["games"]["nba:401909921"]] == ["David"]


def test_members_bets_are_for_members_only(env, monkeypatch):
    client, b, clock, k, optin = env
    poll(b)
    monkeypatch.setenv("WALL_DEMO", "1")
    monkeypatch.setenv("WALL_DEMO_KEY", "k" * 20)
    client.cookies.clear()
    r = client.post("/api/v1/wall/guest", json={"k": "k" * 20, "name": "Kim"})
    client.cookies.set(wall.GUEST_COOKIE, r.headers["set-cookie"].split(";")[0].split("=", 1)[1])
    assert client.get("/api/v1/wall/me").json()["tier"] == "friend"   # a friend of the watch page …
    clock.t += 100
    assert client.get("/api/v1/wall/bets").status_code == 403          # … never sees members' bets (shared widely tonight)
    assert client.get("/api/v1/wall/bets/me").status_code == 403
    assert client.post("/api/v1/wall/bets/me", json={"share": True}).status_code == 403
    assert client.post("/api/v1/wall/bets/sync", json={"show": False}).status_code == 403
    client.cookies.clear()
    monkeypatch.delenv("WALL_PUBLIC", raising=False)
    monkeypatch.delenv("WALL_GUEST_OPEN", raising=False)
    assert client.get("/api/v1/wall/bets").status_code == 401
    monkeypatch.setenv("WALL_GUEST_OPEN", "1")                          # public mode: read-only too
    assert client.get("/api/v1/wall/bets").status_code == 403          # public: no bets either
    assert client.post("/api/v1/wall/bets/me", json={"share": True}).status_code == 403
    assert client.post("/api/v1/wall/bets/sync", json={"show": False}).status_code == 403


def test_web_viewer_sees_prices_delayed_by_tile_delay_plus_sync_delay(env):
    client, b, clock, k, optin = env
    poll(b)                                    # 42¢ seen at t0
    t0 = clock.t
    clock.t += 300
    k.px[SPREAD] = (51, 53)
    poll(b)                                    # the play: 51¢ seen at t1
    t1 = clock.t
    login(client, "1", "David")
    mark = lambda at=None: next(r for x in client.get("/api/v1/wall/bets" + (f"?at={at}" if at else "")).json()["games"]
                                if x["key"] == "nba:401909921" for r in x["rows"] if r["who"] == "David")["mark"]
    # the tile's D = 40 s, the page's sync delay = 9 s: invisible until t1 + 49 s
    for dt in (0, 20, 48.9):
        clock.t = t1 + dt
        assert mark() == 42
    clock.t = t1 + 49
    assert mark() == 51
    # a viewer whose picture is 5 s further behind (its own PDT) gets the older price; asking for the future doesn't help
    assert mark(at=(t1 + 49 - 9 - 5) * 1000) == 42
    assert mark(at=(t1 + 1000) * 1000) == 51
    clock.t = t1 + 10
    assert mark(at=(t1 + 1000) * 1000) == 42
    assert t1 - t0 == 300


def test_tv_feed_delays_per_tile_and_needs_the_key(env):
    client, b, clock, k, optin = env
    poll(b)
    clock.t += 300
    k.px[SPREAD] = (60, 62)
    poll(b)
    t1 = clock.t
    feed = lambda d: client.post("/api/v1/wall/bets/feed", json={"games": [{**LAL, "delay": d}]},
                                 headers={"X-Api-Key": "sekrit"}).json()["games"]["nba:401909921"]
    clock.t = t1 + 30
    assert {r["mark"] for r in feed(45) if r["side"] == "YES"} == {42}
    assert {r["mark"] for r in feed(30) if r["side"] == "YES"} == {60}
    assert all("uid" not in r for r in feed(30))
    assert client.post("/api/v1/wall/bets/feed", json={"games": []}, headers={"X-Api-Key": "nope"}).status_code == 401


def test_one_poll_serves_every_viewer_and_batches_markets(env):
    client, b, clock, k, optin = env
    poll(b)
    n = len(k.calls)
    assert [c for c in k.calls if c[0] == "mkt"] == [("mkt", (ML_GS, SPREAD))]   # one batch for every ticker held
    for uid in ("1", "2"):
        login(client, uid)
        for _ in range(5):
            client.get("/api/v1/wall/bets")
    client.post("/api/v1/wall/bets/feed", json={"games": [LAL]}, headers={"X-Api-Key": "sekrit"})
    assert len(k.calls) == n                   # viewers read the shared book; only the poller calls Kalshi


def test_poller_waits_on_the_rate_limit(env):
    client, b, clock, k, optin = env
    slept = []

    async def sleep(s):
        slept.append(s)
    b.sleep = sleep
    b.limiter = kw.RateLimit(rate=2, burst=1, clock=clock)   # 3 calls: 2 positions + 1 batch, clock frozen
    poll(b)
    assert slept == [0.5, 1.0]


def test_only_owner_changes_the_sync(env):
    client, b, clock, k, optin = env
    login(client, "1")
    assert client.post("/api/v1/wall/bets/sync", json={"show": False}).status_code == 403


def test_kalshi_auth_error_drops_member(env):
    client, b, clock, k, optin = env
    poll(b)

    async def refused(uid):
        raise wall_bets.ka.KalshiAuthError("401")
    b.positions = refused
    poll(b)
    clock.t += 100
    assert b.rows(LAL, clock.t) == []
