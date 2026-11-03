"""Click a state → its races, Kalshi markets and charts (shared/election_markets.py + web/wall_election.py): the state →
races → markets mapping, chart shaping, the shared caches + rate limit, guest vs member visibility, the TV spotlight vote.
No network: Kalshi's answers are today's real responses (2026-11-03 ~04:00 ET, trimmed: the Elections series list, the
SENATEGA / GOVPARTYGA / CONTROLS / KXHOUSERACE markets, SENATEGA-26-D's hourly + minute candles); results are CNN's 2026
files (test_kalshi_election), a few races counted / called by hand."""
import asyncio
import gzip
import json
from pathlib import Path

import pytest

from shared import election_markets as em
from shared import kalshi_wall as kw
from test_kalshi_election import RACES, _count
from test_wall_bets import Clock, env, login, poll  # noqa: F401 — env is the fixture
from wall.election import Results
from web import wall, wall_election as wel

FX = Path(__file__).parent / "fixtures" / "wall" / "election"
SERIES = json.loads(gzip.decompress((FX / "kalshi-2026-11-03-series.json.gz").read_bytes()))
MKTS = json.loads(gzip.decompress((FX / "kalshi-2026-11-03-markets.json.gz").read_bytes()))
CANDLES = json.loads(gzip.decompress((FX / "kalshi-2026-11-03-candles-SENATEGA-26-D.json.gz").read_bytes()))
NOW = 1793696300.0   # 2026-11-03 03:58 ET, when the fixtures were taken
HANSEN = "688445043975127051"


class FakeKalshi:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        if self.fail:
            raise OSError("kalshi down")
        if path == "/series":
            return SERIES
        if path == "/markets" and "tickers" in params:
            want = params["tickers"].split(",")
            return {"markets": [m for ms in MKTS.values() for m in ms if m["ticker"] in want]}
        if path == "/markets":
            return {"markets": MKTS.get(params["series_ticker"], [])}
        if path.endswith("/candlesticks"):
            c = CANDLES[str(params["period_interval"])]["candlesticks"]
            return {"candlesticks": [x for x in c if params["start_ts"] <= x["end_period_ts"] <= params["end_ts"] + 60]}
        raise AssertionError(path)


def races_now():
    rs = dict(RACES)
    rs["S-GA"] = _count("S-GA", pct=40.0)
    rs["G-GA"] = _count("G-GA", winner=None, pct=55.0)
    return list(rs.values())


def results(races=None, calls=None):
    r = Results(sources=[])
    r.races = races if races is not None else races_now()
    r.source, r.updated = "CNN", NOW - 20
    r.calls = calls or []
    r.touch = lambda: None   # no poller thread in tests
    return r


@pytest.fixture
def eenv(env, monkeypatch):
    client, b, clock, k, optin = env
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    clock.t = NOW
    fk = FakeKalshi()
    cc = Clock(0.0)
    wel.reset(fetch=fk, res=results(), clock=clock, cache_clock=cc)
    app = FastAPI()
    app.include_router(wall.router)
    app.include_router(wel.router)
    return TestClient(app), b, clock, k, optin, fk, cc


def guest(client, monkeypatch):
    monkeypatch.setenv("WALL_DEMO", "1")
    monkeypatch.setenv("WALL_DEMO_KEY", "k" * 20)
    client.cookies.clear()
    r = client.post("/api/v1/wall/guest", json={"k": "k" * 20, "name": "Kim"})
    client.cookies.set(wall.GUEST_COOKIE, r.headers["set-cookie"].split(";")[0].split("=", 1)[1])


# ---- the mapping ---------------------------------------------------------------------------------------------------------
def test_state_series_from_the_real_elections_list():
    by = em.state_series(SERIES["series"])
    assert by["GA"] == ["GOVPARTYGA", "SENATEGA"]
    assert "HOUSEPA7" in by["PA"] and "GOVPARTYPA" in by["PA"]
    assert all("KXSTATELEGWY" not in v and "KXATTYGENOK" not in v for v in by.values())   # not our races
    assert all(em.HOUSERACE not in v for v in by.values())                                  # every state's, asked once


def test_state_races_get_their_own_markets_only():
    races = {r["id"]: r for r in races_now()}
    allm = MKTS["SENATEGA"] + MKTS["GOVPARTYGA"] + MKTS["KXHOUSERACE"] + MKTS["CONTROLS"]
    by = em.race_markets(allm, races, "GA")
    assert [m["ticker"] for m in by["S-GA"]] == ["SENATEGA-26-D", "SENATEGA-26-R"]       # not 2028's, likeliest first
    assert {m["ticker"] for m in by["G-GA"]} == {"GOVPARTYGA-26-D", "GOVPARTYGA-26-R"}
    house = [k for k in by if k.startswith("H-GA-")]
    assert house and all(m["ticker"].startswith("KXHOUSERACE-GA") for k in house for m in by[k])
    assert not any(k.startswith(("H-PA", "CTRL")) for k in by)                           # PA's districts, control: not GA's
    d = by["S-GA"][0]
    assert d["who"] == "Jon Ossoff" and d["party"] == "D"
    assert (d["bid"], d["ask"]) == (97.0, 97.1) and d["prob"] == 97.0                    # tight book → the mid
    assert d["chg24"] is not None and abs(d["chg24"]) < 5
    assert em.chart_tickers(by["S-GA"]) == ["SENATEGA-26-D", "SENATEGA-26-R"]


def test_state_view_orders_races_and_marks_calls():
    rs = races_now()
    rs = [r if r["id"] != "H-GA-1" else _count("H-GA-1", winner=RACES["H-GA-1"]["cands"][0]["name"]) for r in rs]
    v = em.state_view("GA", rs, {}, [{"id": "H-GA-1", "t": NOW - 60}])
    offices = [r["office"] for r in v["races"]]
    assert offices[:2] == ["S", "G"] and set(offices[2:]) == {"H"}
    h1 = next(r for r in v["races"] if r["id"] == "H-GA-1")
    assert h1["called_at"] == NOW - 60 and h1["status"].startswith("✓ ")
    sga = v["races"][0]
    assert sga["status"].startswith("40% in · ") and sga["closes"] == 1793750400.0


def test_control_markets_are_this_elections():
    rows = em.control_markets(MKTS["CONTROLS"], "S")
    assert [r["ticker"] for r in rows] == ["CONTROLS-2026-D", "CONTROLS-2026-R"]
    assert rows[0]["who"] == "Dem" and rows[0]["prob"] == 75.5


def test_only_midterm_markets_are_chartable():
    assert em.chartable("SENATEGA-26-D") and em.chartable("CONTROLH-2026-R") and em.chartable("KXHOUSERACE-GA06-26-R")
    assert not em.chartable("KXNBAGAME-26NOV02PHXGSW-GSW") and not em.chartable("CONTROLS-2028-D")


# ---- chart shaping -------------------------------------------------------------------------------------------------------
def test_candles_become_probability_points():
    hourly = CANDLES["60"]["candlesticks"]
    pts = em.points(hourly)
    assert len(pts) >= 90 and pts == sorted(pts)
    assert all(0 <= p <= 100 for _, p in pts) and 95 <= pts[-1][1] <= 99                   # Ossoff, ~97% all week
    c = {"end_period_ts": 1, "yes_bid": {"close_dollars": "0.4000"}, "yes_ask": {"close_dollars": "0.4400"}, "price": {}}
    assert em.candle_prob(c) == 42.0                                                         # tight: the mid
    c["yes_ask"]["close_dollars"] = "0.9000"
    c["price"] = {"close_dollars": "0.5100"}
    assert em.candle_prob(c) == 51.0                                                         # wide: the last trade
    assert em.candle_prob({"end_period_ts": 1, "price": {"previous_dollars": "0.3000"}}) == 30.0


def test_points_thin_and_keep_the_last():
    minute = CANDLES["1"]["candlesticks"]
    pts = em.points(minute, max_points=50)
    assert len(pts) == 50 and pts[-1][0] == max(c["end_period_ts"] for c in minute)


def test_windows_split_today_from_the_older_days_and_stay_put_within_the_hour():
    w = em.windows(NOW)
    assert w["older"][1] == w["split"] == w["today"][0]
    assert w["today"][1] == int(NOW) and w["older"][0] == w["split"] - em.DAYS_S + em.SESSION_S - 3600
    assert em.windows(NOW + 60)["split"] == w["split"]                                      # the cache keys don't churn


def test_chart_line_and_24h_change():
    w = em.windows(NOW)
    row = {"ticker": "SENATEGA-26-D", "who": "Jon Ossoff", "party": "D"}
    line = em.chart_line(row, CANDLES["60"]["candlesticks"], CANDLES["1"]["candlesticks"], w, NOW)
    assert line["older"] and line["today"]
    assert line["older"][-1][0] < w["split"] <= line["today"][0][0]
    assert line["chg24"] is not None and abs(line["chg24"]) < 5
    assert len(em.sparkline(line, 40)) == 40
    assert em.marks({"closes": 5.0, "called": "D"}, 9.0) == [{"t": 5.0, "label": "polls close"}, {"t": 9.0, "label": "called · D"}]


# ---- caches + rate limit -----------------------------------------------------------------------------------------------------
def test_cache_single_flight_ttl_and_stale_on_error():
    t = Clock(0.0)
    c = wel.Cache(clock=t)
    n = {"calls": 0}

    async def slow():
        n["calls"] += 1
        await asyncio.sleep(0.01)
        return n["calls"]

    async def go():
        return await asyncio.gather(*(c.get("k", 45, slow) for _ in range(5)))
    assert asyncio.run(go()) == [1] * 5 and n["calls"] == 1                               # five viewers, one upstream call
    assert asyncio.run(c.get("k", 45, slow)) == 1
    t.t = 46
    assert asyncio.run(c.get("k", 45, slow)) == 2                                          # expired → fetched again

    async def boom():
        raise OSError("down")
    t.t = 100
    assert asyncio.run(c.get("k", 45, boom)) == 2                                          # stale beats nothing
    with pytest.raises(OSError):
        asyncio.run(c.get("other", 45, boom))


def test_state_opens_share_one_fetch_and_refresh_after_the_ttl(eenv):
    client, b, clock, k, optin, fk, cc = eenv
    login(client, "1", "David")
    assert client.get("/api/v1/wall/election/state/GA").status_code == 200
    first = len(fk.calls)
    assert {c[1].get("series_ticker") for c in fk.calls if c[0] == "/markets"} == {"SENATEGA", "GOVPARTYGA", "KXHOUSERACE"}
    login(client, "2", "Steph")
    client.get("/api/v1/wall/election/state/GA")
    assert len(fk.calls) == first                                                           # a second viewer: all cached
    cc.t += wel.MARKETS_TTL + 1
    client.get("/api/v1/wall/election/state/GA")
    assert len(fk.calls) == first + 3 and [c for c in fk.calls if c[0] == "/series"] == [fk.calls[0]]   # prices again, not the list


def test_charts_cache_today_short_and_older_long(eenv):
    client, b, clock, k, optin, fk, cc = eenv
    login(client, "1", "David")
    d = client.get("/api/v1/wall/election/chart/S-GA").json()
    assert [ln["ticker"] for ln in d["lines"]] == ["SENATEGA-26-D", "SENATEGA-26-R"]
    assert d["lines"][0]["today"] and d["lines"][0]["older"] and d["marks"][0]["label"] == "polls close"
    candles = lambda: [c for c in fk.calls if c[0].endswith("/candlesticks")]   # noqa: E731
    assert sorted(c[1]["period_interval"] for c in candles()) == [1, 1, 60, 60]
    client.get("/api/v1/wall/election/chart/S-GA")
    assert len(candles()) == 4                                                              # shared, cached
    cc.t += wel.TODAY_TTL + 1
    client.get("/api/v1/wall/election/chart/S-GA")
    assert sorted(c[1]["period_interval"] for c in candles()[4:]) == [1, 1]                 # today again; the older days kept
    cc.t += wel.OLDER_TTL
    client.get("/api/v1/wall/election/chart/S-GA")
    assert sorted(c[1]["period_interval"] for c in candles()[6:]) == [1, 1, 60, 60]


def test_chart_draws_only_the_races_own_markets(eenv):
    client, b, clock, k, optin, fk, cc = eenv
    login(client, "1", "David")
    d = client.get("/api/v1/wall/election/chart/S-GA?t=SENATEGA-26-R,KXNBAGAME-26NOV02PHXGSW-GSW,CONTROLS-2026-D").json()
    assert [ln["ticker"] for ln in d["lines"]] == ["SENATEGA-26-R"]
    assert client.get("/api/v1/wall/election/chart/../../markets").status_code == 404
    assert client.get("/api/v1/wall/election/chart/S-GA;DROP").status_code == 404
    ctl = client.get("/api/v1/wall/election/chart/CTRL-S").json()
    assert [ln["ticker"] for ln in ctl["lines"]] == ["CONTROLS-2026-D", "CONTROLS-2026-R"]


def test_kalshi_calls_go_through_the_shared_rate_limit(eenv):
    client, b, clock, k, optin, fk, cc = eenv
    waits = []

    async def sleep(s):
        waits.append(s)
    b.sleep = sleep
    b.limiter = kw.RateLimit(rate=5, burst=2, clock=clock)
    login(client, "1", "David")
    client.get("/api/v1/wall/election/state/GA")
    assert len(waits) >= 2 and all(w > 0 for w in waits)                                    # 4 calls on a burst of 2
    assert b.calls >= 4                                                                     # counted with the bets poller's


def test_kalshi_down_keeps_the_results(eenv):
    client, b, clock, k, optin, fk, cc = eenv
    fk.fail = True
    login(client, "1", "David")
    d = client.get("/api/v1/wall/election/state/GA").json()
    assert d["races"] and all(r["markets"] == [] for r in d["races"])
    assert client.get("/api/v1/wall/election/chart/S-GA").status_code in (200, 502)


# ---- who sees what -------------------------------------------------------------------------------------------------------
def opted_in_hansen(b, k, optin):
    optin[HANSEN] = {"share": True, "exact": False, "name": "Hansen"}
    optin["3"]["share"] = False
    k.pos[HANSEN] = {"SENATEGA-26-D": ("YES", 400, 280.0)}
    k.pos["3"] = {"SENATEGA-26-R": ("YES", 5, 2.0)}
    k.px.update({"SENATEGA-26-D": (97, 98), "SENATEGA-26-R": (2, 3)})
    by = {m["ticker"]: m for m in MKTS["SENATEGA"]}

    async def markets(tickers):
        return [{**by[t], "yes_bid_dollars": f"{k.px[t][0] / 100:.4f}", "yes_ask_dollars": f"{k.px[t][1] / 100:.4f}"}
                for t in tickers if t in by] + [kw_market(t, k) for t in tickers if t not in by and t in k.px]
    b.markets = markets

    async def get_account(uid):
        return ("kid", b"x")
    import web.wall_bets as wb
    wb.queries.get_kalshi_account = get_account


def kw_market(t, k):
    return {"ticker": t, "event_ticker": kw.event_ticker_of(t), "floor_strike": 3.5,
            "yes_bid_dollars": f"{k.px[t][0] / 100:.4f}", "yes_ask_dollars": f"{k.px[t][1] / 100:.4f}"}


def test_members_friends_and_public_all_see_markets_and_positions(eenv, monkeypatch):
    client, b, clock, k, optin, fk, cc = eenv
    opted_in_hansen(b, k, optin)
    poll(b)
    login(client, "1", "David")
    d = client.get("/api/v1/wall/election/state/GA").json()
    assert {(p["who"], p["ticker"]) for p in d["positions"]} == {("Hansen", "SENATEGA-26-D")}   # Kim never opted in
    assert all("uid" not in p for p in d["positions"])
    sga = next(r for r in d["races"] if r["id"] == "S-GA")
    assert sga["markets"]                                                                   # and the public data, of course
    guest(client, monkeypatch)
    g = client.get("/api/v1/wall/election/state/GA")
    assert g.status_code == 200 and "positions" not in g.json()                         # a friend: markets, no positions
    assert next(r for r in g.json()["races"] if r["id"] == "S-GA")["markets"]
    assert "positions" not in client.get("/api/v1/wall/election/control/S").json()
    m = client.get("/api/v1/wall/election/map").json()
    assert m["member"] is True and m["public"] is False and m["races"] and "cands" not in m["races"][0]
    assert client.get("/api/v1/wall/election/chart/S-GA").status_code == 200
    client.cookies.clear()
    for path in ("/map", "/state/GA", "/control/S", "/chart/S-GA", "/shapes"):           # public mode off: nothing
        assert client.get("/api/v1/wall/election" + path).status_code == 401
    monkeypatch.setenv("WALL_GUEST_OPEN", "1")                                               # public mode: every read
    p = client.get("/api/v1/wall/election/state/GA").json()
    assert "positions" not in p
    assert next(r for r in p["races"] if r["id"] == "S-GA")["markets"]
    m = client.get("/api/v1/wall/election/map").json()
    assert m["member"] is False and m["public"] is True and m["owner"] is False
    for path in ("/map", "/state/GA", "/control/S", "/chart/S-GA", "/shapes"):
        assert client.get("/api/v1/wall/election" + path).status_code == 200, path


def test_opt_out_drops_positions_from_the_sheet(eenv):
    client, b, clock, k, optin, fk, cc = eenv
    opted_in_hansen(b, k, optin)
    poll(b)
    login(client, HANSEN, "Hansen")
    assert client.get("/api/v1/wall/election/state/GA").json()["positions"]
    b.opt_out(HANSEN)                                                                       # the toggle on the page
    assert client.get("/api/v1/wall/election/state/GA").json()["positions"] == []


def test_unknown_state_and_office(eenv):
    client, *_ = eenv
    login(client, "1", "David")
    assert client.get("/api/v1/wall/election/state/ZZ").status_code == 404
    assert client.get("/api/v1/wall/election/control/G").status_code == 404


# ---- the TV spotlight ----------------------------------------------------------------------------------------------------
def test_spotlight_action_is_checked_and_routed():
    assert wall.check_action({"op": "spotlight", "state": "PA"}) == {"op": "spotlight", "state": "PA"}
    assert wall.check_action({"op": "spotlight", "state": ""}) == {"op": "spotlight", "state": ""}
    for bad in ("pa", "PAX", 7, None, "P1"):
        with pytest.raises(ValueError):
            wall.check_action({"op": "spotlight", "state": bad})
    assert wall._route({"op": "spotlight", "state": "PA"}) == ("POST", "/i/discord/spotlight", {"state": "PA"})
    assert "spotlight PA" in wall.describe({"op": "spotlight", "state": "PA"})


def test_spotlight_is_a_vote_for_members_at_once_for_the_owner(eenv, monkeypatch):
    client, b, clock, k, optin, fk, cc = eenv
    done = []

    async def execute(a):
        done.append(a)
        return True, ""
    wall.hub.execute = execute
    wall.hub.presence.join(1, {"id": "1", "name": "David"}, clock())
    wall.hub.presence.join(2, {"id": "2", "name": "Steph"}, clock())
    wall.hub.presence.join(3, {"id": "3", "name": "Kim"}, clock())
    login(client, "1", "David")
    r = client.post("/api/v1/wall/election/spotlight", json={"state": "ga"})
    assert r.status_code == 200 and r.json()["status"] == "open" and not done               # a vote: 1 of 3 so far
    pid = r.json()["id"]
    asyncio.run(wall.hub.vote({"id": "2", "name": "Steph"}, pid, True))
    assert done == [{"op": "spotlight", "state": "GA"}]                                     # majority → on the TV
    login(client, "100", "Owner")
    r = client.post("/api/v1/wall/election/spotlight", json={"state": "PA"})
    assert r.json()["status"] == "override" and done[-1] == {"op": "spotlight", "state": "PA"}
    guest(client, monkeypatch)                                                               # a friend: a vote, like a member
    r = client.post("/api/v1/wall/election/spotlight", json={"state": "OH"})
    assert r.status_code == 200 and r.json()["status"] == "open"
    client.cookies.clear()
    assert client.post("/api/v1/wall/election/spotlight", json={"state": "PA"}).status_code == 401
    monkeypatch.setenv("WALL_GUEST_OPEN", "1")                                               # a public viewer: never
    n = len(wall.hub.votes.props)
    assert client.post("/api/v1/wall/election/spotlight", json={"state": "PA"}).status_code == 403
    assert len(wall.hub.votes.props) == n
    login(client, "1", "David")
    assert client.post("/api/v1/wall/election/spotlight", json={"state": "XX"}).status_code == 422


def test_public_viewers_cant_propose_over_the_socket_either():
    assert not hasattr(wall, "GUEST_OPS")   # friends propose like members; public sockets can't send anything (test_wall_web)


def test_spot_card_for_the_tv_needs_the_key_and_has_no_positions(eenv):
    client, b, clock, k, optin, fk, cc = eenv
    opted_in_hansen(b, k, optin)
    poll(b)
    assert client.get("/api/v1/wall/election/spot/GA").status_code == 401
    d = client.get("/api/v1/wall/election/spot/GA", headers={"X-Api-Key": "sekrit"}).json()
    assert d["name"] == "Georgia" and [r["id"] for r in d["races"]][:2] == ["S-GA", "G-GA"] and len(d["races"]) == 3
    s = d["races"][0]
    assert s["markets"][0]["who"] == "Jon Ossoff" and len(s["spark"]) == 40 and s["spark_party"] == "D"
    assert "positions" not in d and "Hansen" not in json.dumps(d)


def test_pulse_mine_only_the_viewers_own_pulse_positions(eenv, monkeypatch):
    client = eenv[0]
    asked = []

    async def account(uid):
        asked.append(uid)
        return ("kid", b"enc") if uid == "7" else None

    async def all_(c, key_id, key, path, field):
        return [{"ticker": "KXMIDTERMVOTETURN-NJSEN-3000000", "position_fp": "-40", "market_exposure_dollars": "12.4"},
                {"ticker": "SENATEGA-26-D", "position_fp": "5"}]
    monkeypatch.setattr(wel.queries, "get_kalshi_account", account)
    monkeypatch.setattr(wel.ka, "decrypt", lambda b: b)
    monkeypatch.setattr(wel.ka, "load_key", lambda b: object())
    monkeypatch.setattr(wel.ka, "_all", all_)
    client.cookies.clear()
    assert client.get("/api/v1/wall/election/pulse/mine").status_code == 401
    login(client, "8")
    assert client.get("/api/v1/wall/election/pulse/mine").json() == {"linked": False, "positions": {}}
    login(client, "7")
    got = client.get("/api/v1/wall/election/pulse/mine").json()
    assert got["positions"] == {"KXMIDTERMVOTETURN-NJSEN-3000000": {"side": "NO", "qty": 40.0, "cost": 12.4}}
    assert asked == ["8", "7"]


def test_pulse_feedback_members_post_key_replies(eenv, monkeypatch, tmp_path):
    client = eenv[0]
    monkeypatch.setattr(wel, "FEEDBACK", tmp_path / "fb.json")
    monkeypatch.setattr(wel, "_fb_last", {})
    client.cookies.clear()
    assert client.post("/api/v1/wall/election/pulse/feedback", json={"text": "hi"}).status_code == 401
    login(client, "7", "Hansen")
    fid = client.post("/api/v1/wall/election/pulse/feedback", json={"text": "add NJ counties"}).json()["id"]
    assert client.post("/api/v1/wall/election/pulse/feedback", json={"text": "again"}).status_code == 429
    assert client.post(f"/api/v1/wall/election/pulse/feedback/{fid}/reply", json={"text": "on it"}).status_code == 401
    assert client.post(f"/api/v1/wall/election/pulse/feedback/{fid}/reply", json={"text": "on it"},
                       headers={"X-API-Key": "sekrit"}).json() == {"ok": True}
    (it,) = client.get("/api/v1/wall/election/pulse/feedback").json()["items"]
    assert (it["who"], it["text"], it["reply"]) == ("Hansen", "add NJ counties", "on it") and "uid" not in it


# ---- the races tile's odds (the wall service asks with the key) ----------------------------------------------------------
def test_odds_for_the_wall_need_the_key_favourites_only_and_are_cached(eenv):
    client, b, clock, k, optin, fk, cc = eenv
    opted_in_hansen(b, k, optin)
    poll(b)
    wel.results().races = [{**r, "watch": True} if r["id"] == "H-GA-6" else r for r in wel.results().races]
    assert client.get("/api/v1/wall/election/odds").status_code == 401
    d = client.get("/api/v1/wall/election/odds", headers={"X-Api-Key": "sekrit"}).json()
    assert d["S-GA"]["who"] == "Jon Ossoff" and d["S-GA"]["party"] == "D" and d["S-GA"]["prob"] > 50
    assert set(d["S-GA"]) == {"who", "party", "prob", "chg24"}
    assert set(d) == {"S-GA", "G-GA", "H-GA-6"}                                  # races to watch only (H-GA-5 isn't one)
    watch = {r["id"] for r in wel.results().snapshot()["races"] if r["watch"]}
    assert set(d) <= watch
    assert "Hansen" not in json.dumps(d)
    series = {c[1].get("series_ticker") for c in fk.calls if c[0] == "/markets"}
    assert {"SENATEGA", "GOVPARTYGA", "KXHOUSERACE"} <= series and "CONTROLS" not in series
    batch = [c for c in fk.calls if "tickers" in c[1]]
    assert len(batch) == 1 and len(batch[0][1]["tickers"].split(",")) == 6     # 3 races x top 2 picks, one call
    n = len(fk.calls)
    client.get("/api/v1/wall/election/odds", headers={"X-Api-Key": "sekrit"})
    assert len(fk.calls) == n                                                    # cached for every caller
    cc.t += wel.ODDS_TTL + 1
    client.get("/api/v1/wall/election/odds", headers={"X-Api-Key": "sekrit"})
    assert [c for c in fk.calls[n:]] == [batch[0]]                               # fresh prices: just the one batched call
    cc.t += wel.ODDS_TTL + 1
    fk.fail = True
    assert client.get("/api/v1/wall/election/odds", headers={"X-Api-Key": "sekrit"}).json() == d   # Kalshi down: the last odds


def test_favourite_is_the_busiest_events_likeliest_priced_pick():
    rows = [{"event": "A", "who": "x", "party": "D", "prob": None, "chg24": None},
            {"event": "A", "who": "y", "party": "R", "prob": 40.0, "chg24": -2.0},
            {"event": "B", "who": "z", "party": "D", "prob": 90.0, "chg24": 1.0}]
    assert wel.favourite(rows) == {"who": "y", "party": "R", "prob": 40.0, "chg24": -2.0}
    assert wel.favourite([]) is None and wel.favourite(rows[:1]) is None
