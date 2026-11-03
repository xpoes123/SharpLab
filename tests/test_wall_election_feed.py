"""Election night's vote updates + higher-fidelity race detail (wall/election_feed.py, wall/election_counties.py,
wall/election_sim.py, web/wall_election_live.py): diffing snapshots into updates, significance ranking, the history
surviving a restart, county shaping, the sped-up 2024 replay, the fast lane, the HTTP views. Fixtures only (CNN's final
2024 files + their county files, a trimmed NYT 2026 race page), no network."""
import asyncio
import copy
import gzip
import json
from pathlib import Path

import pytest

from test_wall_bets import env, login  # noqa: F401 — env is the fixture
from wall import election as we
from wall import election_counties as ec
from wall import election_feed as ef
from wall import election_sim as es
from web import wall_election as wel
from web import wall_election_live as wlv

FX = Path(__file__).parent / "fixtures" / "wall" / "election"


def fx(name):
    return json.loads(gzip.decompress((FX / f"{name}.json.gz").read_bytes()))


def race(rid="S-TX", votes=(0, 0), names=("Talarico", "Paxton"), parties=("D", "R"), called="", pct=0.0, watch=True, closes=0, office="S"):
    cands = [{"name": n, "full": n, "party": p, "votes": v, "pct": 0, "winner": bool(called) and p == called, "inc": False}
             for n, p, v in zip(names, parties, votes)]
    return we.race(office, rid.split("-")[1], None, False, cands, pct=pct, votes=sum(votes), called=called, caller="CNN",
                   flip=False, watch=watch, closes=closes)


# ---- diffing ------------------------------------------------------------------------------------------------------------
def test_first_sight_is_not_an_update():
    assert ef.diff(race(votes=(100, 90)), None, 1.0) == []


def test_routine_drop_and_its_text():
    a = race(votes=(500_000, 490_000), pct=40)
    last = ef.state_of(a)
    b = race(votes=(502_000, 490_400), pct=41)
    [u] = ef.diff(b, last, 100.0)
    assert u["kind"] == "drop" and u["sig"] == ef.SIG["drop"] and u["dv"] == 2_400
    assert u["text"] == "+2,400 votes · Talarico +1.0 → +1.2" and u["head"] == "TX SEN"
    assert u["lead"] == "Talarico" and u["party"] == "D" and u["mv"] == 11_600 and u["feed"]


def test_lead_change_beats_everything():
    last = ef.state_of(race(votes=(490_000, 500_000), pct=50))
    [u] = ef.diff(race(votes=(540_000, 520_000), pct=55), last, 100.0)
    assert u["kind"] == "lead" and u["sig"] == 4 and u["text"].startswith("Talarico takes the lead · +70,000 votes · Talarico −1.0 → +1.9")


def test_big_drop_only_in_a_close_race():
    close = ef.state_of(race(votes=(500_000, 480_000)))
    [u] = ef.diff(race(votes=(530_000, 500_000)), close, 1)
    assert u["kind"] == "big"
    [u] = ef.diff(race(votes=(530_000, 500_000)), ef.state_of(race()), 1)
    assert u["kind"] == "drop" and u["text"] == "First votes · Talarico +2.9"   # the first count: not a "big drop"
    blowout = ef.state_of(race(votes=(700_000, 300_000)))
    [u] = ef.diff(race(votes=(740_000, 320_000)), blowout, 1)
    assert u["kind"] == "drop"


def test_small_house_drops_stay_out_of_the_feed():
    last = ef.state_of(race("H-CA", votes=(10_000, 9_000), office="H", watch=False))
    [u] = ef.diff(race("H-CA", votes=(10_300, 9_100), office="H", watch=False), last, 1)
    assert not u["feed"]
    [u] = ef.diff(race("H-CA", votes=(10_300, 9_100), office="H", watch=True), last, 1)
    assert u["feed"]   # a key race: every drop


def test_call_and_backwards_counts():
    last = ef.state_of(race(votes=(600_000, 500_000), pct=80))
    ups = ef.diff(race(votes=(600_000, 500_000), pct=80, called="D"), last, 1)
    assert [u["kind"] for u in ups] == ["call"] and ups[0]["text"] == "Talarico (D) wins · call: CNN"
    f = ef.Feed()
    f.observe([race(votes=(600_000, 500_000))], 1)
    assert f.observe([race(votes=(590_000, 500_000))], 2) == []   # a stale copy: ignored, the higher count kept
    assert f.last["S-TX"]["votes"] == 1_100_000


def test_significance_ranking():
    def u(kind, dv=0, t=0, key=True, office="S"):
        return {"kind": kind, "sig": ef.SIG[kind], "dv": dv, "t": t, "key": key, "office": office}
    items = [u("drop", 90_000, 5), u("call", 0, 4), u("big", 30_000, 3), u("lead", 1_000, 1), u("polls", 0, 6, office=""),
             u("lead", 1_000, 9, key=False, office="H")]
    got = [(x["kind"], x["office"]) for x in ef.rank(items)]
    assert got == [("lead", "S"), ("lead", "H"), ("big", "S"), ("polls", ""), ("call", "S"), ("drop", "S")]


def test_poll_closings_and_upcoming():
    rs = [race("S-GA", closes=100), race("S-VA", closes=100), race("S-PA", closes=200), race("S-AZ", closes=300)]
    [c] = ef.closings(rs, 50, 150)
    assert c["kind"] == "polls" and c["states"] == ["GA", "VA"] and c["t"] == 100 and c["text"] == "Polls closed · GA VA"
    assert ef.upcoming(rs, 150) == [{"t": 200, "states": ["PA"]}, {"t": 300, "states": ["AZ"]}]
    f = ef.Feed()
    f.observe(rs, 10)                     # before 6 pm: nothing closed, the feed is just the upcoming closings
    assert f.items() == []
    f.observe(rs, 250)
    assert [p["states"] for p in f.items()] == [["PA"], ["GA", "VA"]]


# ---- the history survives a restart -------------------------------------------------------------------------------------
def test_timeline_persists_and_resumes(tmp_path):
    f = ef.Feed(tmp_path)
    f.observe([race(votes=(100_000, 90_000))], 1000)
    f.observe([race(votes=(150_000, 140_000))], 1030, price=lambda r: {"who": "Talarico", "party": "D", "prob": 61.0})
    f.save(1030, force=True)
    assert (tmp_path / "election-history.json").exists()
    g = ef.Feed(tmp_path)                 # restarted
    [u] = g.timeline("S-TX")
    assert u["dv"] == 100_000 and u["t"] == 1030 and u["px"] == {"who": "Talarico", "party": "D", "prob": 61.0}
    new = g.observe([race(votes=(160_000, 141_000))], 1060)   # diffed against the saved last state, not from zero
    assert [x["dv"] for x in new] == [11_000]
    assert [x["t"] for x in g.timeline("S-TX")] == [1060, 1030]


def test_history_is_rolling(tmp_path):
    f = ef.Feed(tmp_path)
    for i in range(ef.PER_RACE["H"] + 10):
        f.observe([race("H-CA", votes=(1_000 + 100 * i, 900), office="H")], i)
    assert len(f.timeline("H-CA", 999)) == ef.PER_RACE["H"]


def test_a_bad_history_file_is_ignored(tmp_path):
    (tmp_path / "election-history.json").write_text("{nope")
    assert ef.Feed(tmp_path).items() == []


# ---- counties -----------------------------------------------------------------------------------------------------------
def test_county_shaping_cnn_with_presidential_swing():
    c = ec.shape(fx("cnn-county-2024-SG-PA"), fx("cnn-county-2020-PG-PA"), None, "2020 presidential")
    assert c["n"] == 67 and c["reporting"] == 67 and c["swing_label"] == "2020 presidential"
    assert all(abs(a["m"]) <= abs(b["m"]) for a, b in zip(c["closest"], c["closest"][1:]))
    assert {x["name"] for x in c["biggest"][:2]} == {"Allegheny", "Philadelphia"}
    # CNN's final file still has Philadelphia at 96 %, Berks 97, Northampton 98: the only ones with an estimate left
    assert [x["name"] for x in c["outstanding"]] == ["Philadelphia", "Berks", "Northampton"]
    phl = next(x for x in c["biggest"] if x["name"] == "Philadelphia")
    assert phl["lead"] == "Casey" and phl["party"] == "D" and phl["swing"] is not None
    assert c["swing_all"] is not None and -10 < c["swing_all"] < 10


def test_county_shaping_mid_count_and_nyt_expected_vote():
    cnn = fx("cnn-county-2024-SG-PA")
    for x in cnn:
        if x["countyName"] == "Philadelphia":
            x["percentReporting"], x["totalVote"] = 50, x["totalVote"] // 2
            for cand in x["candidates"]:
                cand["voteNum"] //= 2
    c = ec.shape(cnn, None, None)
    assert c["outstanding"][0]["name"] == "Philadelphia" and c["outstanding"][0]["left"] > 300_000
    assert c["swing"] == [] and c["swing_all"] is None
    page = {"races": [{"top_reporting_unit": {"eevp": 50, "total_expected_vote": 7_000_000,
                                               "historical_comparison_race_data": {"PA-G-P-2024": {"margin": -1.7}},
                                               "historical_comparison_race_meta": {"PA-G-P-2024": {"label": "2024 presidential"}}},
                       "reporting_units": [{"level": "county", "fips_state": "42", "fips_county": "101", "eevp": 48,
                                            "total_expected_vote": 800_000,
                                            "historical_comparison_race_data": {"x": {"margin": 60.0}}}]}]}
    c = ec.shape(cnn, None, page)
    phl = c["outstanding"][0]
    assert phl["name"] == "Philadelphia" and phl["pct"] == 48 and phl["left"] == 800_000 - phl["votes"]
    assert c["swing_label"] == "2024 presidential" and phl["swing"] is not None
    t = ec.totals(race("S-PA", votes=(1_800_000, 1_700_000), pct=50), page)
    assert t["expected"] == 7_000_000 and t["left"] == 3_500_000 and t["pct"] == 50 and "AP" in t["left_how"]
    assert t["margin_votes"] == 100_000 and t["leader"] == "Talarico" and t["history"]["margin"] == -1.7


def test_totals_without_nyt_estimate_from_pct():
    t = ec.totals(race(votes=(300, 100), pct=40))
    assert t["left"] == 600 and t["left_how"] == "from % reporting" and t["margin"] == 50.0
    assert ec.totals(race())["left"] is None and ec.totals(race())["margin"] is None


def test_keys_and_urls():
    assert ec.baseline_key("2026-SG-TX") == "2024-PG-TX" and ec.baseline_key("2024-SG-PA") == "2020-PG-PA"
    r = race("S-OH")
    r["special"] = True
    assert ec.nyt_url(r, "Ohio").endswith("/results-ohio-us-senate-special.json")
    assert ec.nyt_url(race("G-NH", office="G"), "New Hampshire").endswith("/results-new-hampshire-governor.json")
    assert ec.nyt_url(race("H-CA", office="H"), "California") is None
    assert wlv.cnn_key({"office": "H", "state": "PA", "district": 7, "special": False}) == "2026-HG-PA-7"
    assert wlv.cnn_key(r) == "2026-SP-OH"


# ---- a simulated night ------------------------------------------------------------------------------------------------
class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def sim_night(tmp_path=None, steps=60, dur=600.0):
    clk = Clock()
    src = es.SimCnn(FX, dur, clock=clk)
    r = we.Results([src], tmp_path, clock=clk)
    for _ in range(steps):
        r.poll()
        clk.t += dur / steps
    return r, src, clk


def test_sim_night_has_closings_drops_leads_and_calls(tmp_path):
    r, src, clk = sim_night(tmp_path)
    kinds = {u["kind"] for u in r.feed.items()}
    assert {"polls", "drop", "lead", "call"} <= kinds
    assert r.source == "CNN (2024 replay)"
    first = r.feed.items()[-1]
    assert first["kind"] == "polls" and first["t"] > 1000 + 600 / 9 - 1   # the first thing that happens: polls closing
    pa = r.feed.timeline("S-PA")
    assert len(pa) > 5 and all(a["t"] >= b["t"] for a, b in zip(pa, pa[1:]))
    latest = r.snapshot()["latest"]
    assert latest and all(u["sig"] >= 2 or u["key"] for u in latest)
    b = we.board(r.snapshot())
    assert b["latest"] and set(b["latest"][0]) >= {"head", "text", "kind", "t"}
    r.feed.save(clk.t, force=True)
    assert len(ef.Feed(tmp_path).timeline("S-PA")) == len(pa)


def test_sim_before_six_is_only_poll_closings():
    clk = Clock()
    r = we.Results([es.SimCnn(FX, 800.0, clock=clk)], clock=clk)
    r.poll()
    clk.t += 10
    r.poll()
    assert r.feed.items() == [] and all(x["votes"] == 0 for x in r.races)
    up = ef.upcoming(r.races, clk.t)
    assert up and up[0]["t"] == pytest.approx(1000 + 800 / 9, abs=1)   # 6 pm ET, a ninth of the way through


def test_sim_counties_climb_with_the_race():
    clk = Clock()
    src = es.SimCnn(FX, 800.0, clock=clk)
    clk.t += 400   # 10 pm
    mid = ec.shape(src.fetch(f"{src.base}/county-races/2024-SG-PA.json"), src.fetch(f"{src.base}/county-races/2020-PG-PA.json"))
    assert 0 < mid["reporting"] <= 67 and mid["outstanding"]
    clk.t += 10_000
    end = ec.shape(src.fetch(f"{src.base}/county-races/2024-SG-PA.json"))
    assert end["outstanding"] == [] and end["reporting"] == 67


def test_replay_spec_picks_the_sim():
    [s] = we.replay(f"{FX}:sim=120")
    assert isinstance(s, es.SimCnn) and s.dur == 120


# ---- the fast lane + the web views --------------------------------------------------------------------------------------
def test_fast_lane_merges_fresher_races():
    r, src, clk = sim_night(steps=30)
    wlv.focus.clear()
    wlv.focus["S-PA"] = clk.t + 60
    before = next(x for x in r.races if x["id"] == "S-PA")["votes"]
    clk.t += 20
    assert wlv.fast_poll(r, clk.t) == 1
    after = next(x for x in r.races if x["id"] == "S-PA")
    assert after["votes"] >= before and after["key"] == "2024-SG-PA"
    wlv.focus.clear()
    assert wlv.fast_poll(r, clk.t) == 0


def test_cached_price_comes_from_the_markets_cache_only():
    from test_wall_election_markets import NOW, FakeKalshi, races_now
    fk = FakeKalshi()
    wel.reset(fetch=fk, clock=lambda: NOW, cache_clock=lambda: 0.0)
    ga = next(x for x in races_now() if x["id"] == "S-GA")
    assert wlv.cached_price(ga) is None and fk.calls == []        # nothing cached: no price, and no Kalshi call
    asyncio.run(wel.markets.state_markets("GA"))                  # someone opened Georgia
    n = len(fk.calls)
    px = wlv.cached_price(ga)
    assert px == {"who": "Jon Ossoff", "party": "D", "prob": 97.0} and len(fk.calls) == n


def test_web_live_and_race_views(env):  # noqa: F811
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    client = env[0]
    r, src, clk = sim_night(steps=40)
    r.touch = lambda: None
    wel.reset(res=r)
    wlv.cache.data.clear()
    wlv.focus.clear()
    app = FastAPI()
    app.include_router(wlv.router)
    c = TestClient(app)
    assert c.get("/api/v1/wall/election/live").status_code == 401
    c.cookies = client.cookies
    login(c, "1")
    got = c.get("/api/v1/wall/election/live?scope=all").json()
    assert got["items"] and got["items"][0]["t"] >= got["items"][-1]["t"]
    key = c.get("/api/v1/wall/election/live").json()
    assert key["items"] and all(u["key"] for u in key["items"])
    d = c.get("/api/v1/wall/election/race/S-PA").json()
    assert d["race"]["id"] == "S-PA" and d["totals"]["votes"] > 0 and d["timeline"]
    assert d["counties"]["n"] == 67 and d["counties"]["swing_label"] == "2020 presidential"
    assert "S-PA" in wlv.focus
    assert c.get("/api/v1/wall/election/race/S-ZZ").status_code == 404
    assert c.get("/api/v1/wall/election/race/nope").status_code == 404
    wlv.focus.clear()


@pytest.mark.skipif(__import__("shutil").which("qml6") is None, reason="qml6 not installed")
@pytest.mark.parametrize("layout", ["lbar", "full"])
def test_tv_latest_renders_in_the_overlay(tmp_path, layout):
    """The Election board's "Latest" (lead changes, big drops, calls, poll closings from a sped-up night) draws cleanly."""
    from test_wall_election import _overlay_render
    r, src, clk = sim_night(steps=40)
    assert r.snapshot()["latest"]
    out, errs = _overlay_render(tmp_path, layout, results=r)
    assert errs == [] and out.exists() and out.stat().st_size > 20_000
