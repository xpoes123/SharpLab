"""wall/swing.py: the "vs 2024" tile — race swing (D−R now vs the 2024 presidential margin there), county swing (counties
≥ 95 % in, vote-weighted median), turnout vs 2024, and /p/election/swing.json. Fixtures: 2024's final night replayed
(its county files vs 2020's presidential ones)."""
from pathlib import Path

from wall import rotate
from wall.director import OPTIONS
from wall.election import Results, replay
from wall.swing import BASE, Swing, baseline, county_swing, dside, race_rows, turnout, wmedian

FX = Path(__file__).parent / "fixtures" / "wall" / "election"


def race(rid, office, st, cands, watch=True, district=None, pct=50.0):
    votes = sum(v for _, v in cands)
    return {"id": rid, "office": office, "state": st, "district": district, "label": rid, "watch": watch, "pct": pct,
            "votes": votes, "leader": "", "margin": None, "called": "",
            "cands": [{"party": p, "votes": v} for p, v in cands]}


def county(fips, d, r, pct=100, other=0):
    return {"countyFipsCode": fips, "countyName": fips, "percentReporting": pct, "totalVote": d + r + other,
            "candidates": [{"majorParty": "DEM", "voteNum": d}, {"majorParty": "REP", "voteNum": r}, {"majorParty": "LIB", "voteNum": other}]}


def test_baselines_are_dem_minus_rep():
    pa = BASE["states"]["PA"]
    assert pa[2] > 6_900_000 and baseline({"office": "S", "state": "PA"}) == round(100 * (pa[0] - pa[1]) / pa[2], 2) < 0   # Trump won PA
    assert baseline({"office": "G", "state": "CA"}) > 15                                    # Harris won CA by ~20
    assert baseline({"office": "H", "state": "AK", "district": 1}) < 0                       # at-large = district 1
    assert baseline({"office": "H", "state": "MO", "district": 1}) is None                   # MO's new map: no baseline
    assert sum(s[2] for s in BASE["states"].values()) > 150_000_000 and len(BASE["house"]) > 400


def test_race_swing_signs():
    # PA was ~R+1.7 in 2024: a D+4 race now is a swing of ~5.7 toward the Dems; an R+10 race is ~8.3 toward the Reps
    was = baseline({"office": "S", "state": "PA"})
    rows, watch = race_rows([race("S-PA", "S", "PA", [("D", 52), ("R", 48)]),
                             race("G-PA", "G", "PA", [("D", 450), ("R", 550)]),
                             race("S-XX", "S", "PA", [("D", 1), ("R", 1)], watch=False),
                             race("H-AK-1", "H", "AK", [], district=1)])
    assert [r["id"] for r in rows] == ["G-PA", "S-PA"]                     # counting only, most votes first
    s, g = rows[1], rows[0]
    assert s["now"] == 4.0 and s["swing"] == round(4.0 - was, 1) > 0
    assert g["now"] == -10.0 and g["swing"] == round(-10.0 - was, 1) < 0
    assert {w["id"] for w in watch} == {"S-PA", "G-PA", "H-AK-1"} and watch[0]["office"] == "S"   # key races, not S-XX


def test_no_democrat_counts_the_independent():
    assert dside([("I", 60), ("R", 40)]) == (60, 40, 100, "I")
    assert dside([("D", 30), ("I", 10), ("R", 60)]) == (30, 60, 100, "D")
    rows, _ = race_rows([race("S-NE", "S", "NE", [("I", 51), ("R", 49)])])
    assert rows[0]["side"] == "I" and rows[0]["now"] == 2.0 and rows[0]["swing"] > 20


def test_weighted_median():
    assert wmedian([(1.0, 1), (2.0, 1), (10.0, 1)]) == 2.0
    assert wmedian([(-5.0, 100), (3.0, 10), (4.0, 10)]) == -5.0      # the big county carries it
    assert wmedian([]) is None


def test_county_swing_only_counts_counties_95_in():
    base = [county("1", 50, 50), county("2", 30, 70), county("3", 60, 40), county("4", 10, 10)]
    now = [county("1", 60, 40, other=0),            # +20 swing, 100 votes
           county("2", 40, 60, pct=96),             # +20 → 0? 30-70=-40 → 40-60=-20: +20
           county("3", 50, 50, pct=80),             # under 95 %: left out
           county("4", 0, 0),                       # no votes: left out
           county("5", 10, 0)]                      # no baseline: left out
    cs = county_swing(now, base)
    assert cs["n"] == 2 and cs["of"] == 5 and cs["median"] == 20.0 and cs["mean"] == 20.0
    assert [m["name"] for m in cs["movers"]] == ["1", "2"] and cs["movers"][0]["was"] == 0.0
    big = county_swing([county("1", 900, 100), county("2", 10, 90)], [county("1", 500, 500), county("2", 50, 50)])
    assert big["median"] == 80.0 and big["mean"] == round((80 * 1000 - 80 * 100) / 1100, 1)   # weighted by votes now
    assert county_swing(now, None) is None


def test_turnout_vs_2024():
    rs = [race("S-PA", "S", "PA", [("D", 3_000_000), ("R", 517_103)]), race("G-PA", "G", "PA", [("D", 1), ("R", 1)]),
          race("H-NE-2", "H", "NE", [("D", 100_000), ("R", 90_000)], district=2),
          race("H-NE-1", "H", "NE", [("D", 50_000), ("R", 60_000)], watch=False, district=1)]
    t = {x["state"]: x for x in turnout(rs)}
    assert t["PA"]["votes"] == 3_517_103 and t["PA"]["share"] == round(100 * 3_517_103 / BASE["states"]["PA"][2], 1)
    assert t["NE"]["votes"] == 300_000                                   # no statewide race: the House races added up


def test_replay_and_endpoint(tmp_path):
    from starlette.testclient import TestClient
    from wall.llm import LLMGuard
    from wall.server import Cfg, build
    res = Results(replay(f"{FX}:0.6"), tmp_path)
    res.touch = lambda: None
    res.poll()
    sw = Swing(res)
    sw.touch = lambda: None
    assert sw.poll() >= 5                                               # the replay's county files (fixtures)
    snap = sw.snapshot()
    assert snap["races"] and all(r["votes"] for r in snap["races"]) and snap["watch"]
    pa = next(c for c in snap["counties"] if c["id"] == "S-PA")
    assert pa["base"] == "2020 presidential" and pa["n"] == 67 and pa["median"] is not None
    cfg = Cfg({"WALL_STATE_DIR": str(tmp_path), "WALL_TV": "0", "WALL_DISCORD": "0", "WALL_IMPORT_FROM": "", "WALL_API_TOKEN": "s3cret"})
    app = build(cfg, {}, guard=LLMGuard(mode="off"), results=res, swing=sw)
    c = TestClient(app, base_url="http://127.0.0.1:7795", client=("127.0.0.1", 5555))
    j = c.get("/p/election/swing.json").json()
    assert j["min_pct"] == 95 and {r["id"] for r in j["races"]} == {r["id"] for r in snap["races"]} and j["turnout"]
    assert c.get("/p/election/swing").status_code == 200
    remote = TestClient(app, base_url="http://127.0.0.1:7795", client=("100.81.31.34", 5555), headers={"Authorization": "Bearer s3cret"})
    assert remote.get("/p/election/swing.json").status_code == 200


def test_swing_rotates_and_jev_can_pick_it():
    b = "http://127.0.0.1:7795/p/election/"
    assert "swing" in rotate.SCREENS and rotate.screen(b + "swing") == "swing" and rotate.kind("swing") == "swing"
    assert rotate.next_url([b + "exitpoll", b + "map?view=S"], 0, now=0) == b + "swing"
    assert rotate.next_url([b + "exitpoll", b + "swing"], 0, now=0) == b + "map?view=S"   # swing is up: past it
    assert "swing" in OPTIONS and "2024" in OPTIONS["swing"]
