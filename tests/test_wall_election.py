"""wall/election.py: the results feeds (CNN → NYT/AP → Fox), the balance of power, the calls log, the board, and the
service's /p/election endpoints. Fixtures: CNN's own files, 2024's final night (still served) + 2026's pre-election Senate."""
import gzip
import json
from pathlib import Path

import pytest

from wall.election import Cnn, Fox, Nyt, Results, balance, board, partial, party, replay

FX = Path(__file__).parent / "fixtures" / "wall" / "election"


def load(name):
    return json.loads(gzip.decompress((FX / f"{name}.json.gz").read_bytes()))


def test_party_codes():
    assert [party(x) for x in ("DEM", "Dem.", "D", "REP", "GOP", "R", "IND", "LIB", "", None)] == list("DDDRRRIOOO")


def test_cnn_final_2024_night():
    r = Results(replay(str(FX)))
    assert r.poll() and r.source == "CNN"
    s = r.snapshot()
    sen, house = s["offices"]["S"], s["offices"]["H"]
    assert sen["counted"]["R"] >= 52   # 2024: R 53 (CNN's notUp + the races; CA's two races are one seat)
    assert sen["winner"] == "R" and sen["net"] == {"D": -4, "R": 4} and sen["out"] == 0
    assert house["winner"] == "R" and house["counted"]["D"] + house["counted"]["R"] >= 435
    az = next(x for x in s["races"] if x["id"] == "S-AZ")
    assert az["called"] == "D" and az["caller"] == "CNN" and az["cands"][0]["name"] == "Gallego" and az["margin"] > 0
    assert az["label"] == "Arizona" and az["watch"]
    # the first round's calls are history ("earlier", t = 0), all of them there for the sidebar
    assert s["calls"] and all(c["t"] == 0 for c in s["calls"]) and any("Gallego (D) wins" in c["text"] for c in s["calls"])


def test_partial_replay_has_leaders_and_fewer_calls():
    r = Results(replay(f"{FX}:0.6"))
    r.poll()
    s = r.snapshot()
    sen = s["offices"]["S"]
    assert 0 < sen["called"] < sen["up"] and sen["seats"]["D"]["lead"] + sen["seats"]["R"]["lead"] > 0
    open_ = [x for x in s["races"] if not x["called"] and x["votes"] and len(x["cands"]) > 1]
    assert open_ and all(x["leader"] and x["margin"] is not None for x in open_)


def test_cnn_2026_before_polls_close():
    races = [Cnn().race(x) for x in load("cnn-2026-SG")]
    assert len(races) == 35 and not any(r["called"] or r["votes"] or r["leader"] for r in races)
    fl = next(r for r in races if r["state"] == "FL")
    assert fl["special"] and fl["id"] == "S-FL-special" and fl["closes"] > 1.79e9
    ak = next(r for r in races if r["state"] == "AK")
    assert [c["name"] for c in ak["cands"][:2]] == ["Sullivan", "Peltola"]   # majors (incumbent first) before minor names
    b = balance(races, "S")
    assert b["counted"] == {"D": 34, "R": 31, "I": 0} and b["out"] == 35 and b["winner"] == ""
    assert board({"races": races, "offices": {o: balance(races, o) for o in "SHG"}, "now": 1.79e9})["next"]


def test_new_calls_are_logged_with_their_time(tmp_path):
    pre = [dict(x) for x in load("cnn-2024-SG")]
    for x in pre:
        x["editorialStatus"], x["status"] = "scheduled", "U"
    rounds = [pre, load("cnn-2024-SG")]

    def fetch(url):
        if "/bop/" in url:
            raise OSError("no bop")
        return rounds[0] if "SG" in url else []
    clock = [100.0]
    r = Results([Cnn(2024, fetch)], tmp_path, clock=lambda: clock[0])
    r.poll()
    assert r.snapshot()["calls"] == []
    rounds.pop(0)
    clock[0] = 200.0
    r.poll()
    calls = r.snapshot()["calls"]
    assert calls and all(c["t"] == 200.0 for c in calls) and calls[0]["by"] == "CNN"
    assert Results([], tmp_path).calls == r.calls   # kept across restarts


def test_fallback_chain_and_errors():
    class Down:
        name = "Down"
        def poll(self): raise OSError("timed out")

    class Empty:
        name = "Empty"
        def poll(self): return {"races": [], "net": {}}
    good = replay(str(FX))[0]
    r = Results([Down(), Empty(), good])
    assert r.poll() and r.source == "CNN"
    assert [t["source"] for t in r.tried] == ["Down", "Empty", "CNN"] and [t["ok"] for t in r.tried] == [False, False, True]
    r.sources = [Down()]
    assert not r.poll() and "kept CNN" in r.snapshot()["error"] and r.snapshot()["races"]   # the last good results stay


def test_nyt_and_fox_parsers():
    nyt = {"races": [{"nyt_id": "TX-G-S-2026-11-03", "office_id": "S", "type": "General", "coverage": "state", "rating": "tossup",
                      "outcome": {"won": ["paxton-k"], "called_by": ["ap"]},
                      "candidate_metadata": {"paxton-k": {"last_name": "Paxton", "first_name": "Ken", "party": {"nyt_id": "GOP"}},
                                             "talarico-j": {"last_name": "Talarico", "party": {"nyt_id": "DEM"}}},
                      "top_reporting_unit": {"state_postal": "TX", "eevp": 91, "total_votes": 1000,
                                             "candidates": [{"nyt_id": "talarico-j", "votes": {"total": 480}},
                                                            {"nyt_id": "paxton-k", "votes": {"total": 520}}]}}]}
    r = Nyt(fetch=lambda u: nyt if "senate" in u else {"races": []}).poll()["races"][0]
    assert r["called"] == "R" and r["caller"] == "AP" and r["cands"][0]["full"] == "Ken Paxton" and r["margin"] == 4.0
    assert r["watch"] and r["pct"] == 91
    fox = [{"stateCode": "GA", "called": 1, "expectedPercentage": 88, "raceType": "General", "isFlipped": True,
            "results": [{"candidate": {"firstName": "Jon", "lastName": "Ossoff"}, "partyCode": "D", "isWinner": True,
                         "votes": {"count": 600, "percentage": 60}},
                        {"candidate": {"firstName": "Mike", "lastName": "Collins"}, "partyCode": "R", "isWinner": False,
                         "votes": {"count": 400, "percentage": 40}}]}]
    g = Fox(fetch=lambda u: fox if "Senate" in u else []).poll()["races"][0]
    assert g["called"] == "D" and g["caller"] == "Fox News" and g["flip"] and g["label"] == "Georgia"


def test_board_payload():
    r = Results(replay(f"{FX}:0.6"))
    r.poll()
    b = board(r.snapshot())
    assert b["kind"] == "election" and [o["office"] for o in b["offices"]] == ["S", "H", "G"] and b["source"] == "CNN"
    assert b["calls"] and b["note"] == "" and not b["official"]
    assert all(w["office"] in ("S", "G") and w["votes"] > 0 for w in b["watch"])


def test_service_endpoints(tmp_path):
    from starlette.testclient import TestClient
    from wall.server import Cfg, build
    from wall.llm import LLMGuard
    cfg = Cfg({"WALL_STATE_DIR": str(tmp_path), "WALL_TV": "0", "WALL_DISCORD": "0", "WALL_IMPORT_FROM": ""})
    res = Results(replay(f"{FX}:0.6"), tmp_path)
    res.touch = lambda: None
    res.poll()
    app = build(cfg, {}, guard=LLMGuard(mode="off"), results=res)
    c = TestClient(app, base_url="http://127.0.0.1:7795", client=("127.0.0.1", 5555))
    d = c.get("/p/election/data.json").json()
    assert d["source"] == "CNN" and d["offices"]["S"]["up"] > 30 and not d["official"]
    assert c.get("/p/election/board.json").json()["kind"] == "election"
    for page in ("/p/election/map", "/p/election/races", "/p/election/election.js", "/p/election/election.css", "/p/us-states.json"):
        assert c.get(page).status_code == 200, page
    for bad in ("/p/../server.py", "/p/election/nope", "/p/%2e%2e/server.py"):
        assert c.get(bad).status_code == 404, bad


def _overlay_render(tmp_path, layout, positions=None):
    import shutil
    import subprocess
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    r = Results(replay(f"{FX}:0.6"))
    r.poll()
    data = board(r.snapshot(), positions)

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = {"view": layout, "rev": 1} if self.path.startswith("/api/overlay/view") else {
                "board": "election", "shown": "election", "visible": True, "layout": layout, "view": layout, "rev": 1,
                "boards": [{"id": "election", "name": "Election"}], "data": data, "updated": time.time(), "error": ""}
            b = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out = tmp_path / f"{layout}.png"
    try:
        p = subprocess.run(["qml6", str(Path(__file__).resolve().parent.parent / "wall" / "qml" / "Preview.qml"), "--", "--layout", layout,
                            "--size", "1920x1080", "--api", f"http://127.0.0.1:{srv.server_port}", "--out", str(out), "--wait", "2500"],
                           env={"QT_QPA_PLATFORM": "offscreen", "QT_FORCE_STDERR_LOGGING": "1", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
                           capture_output=True, text=True, timeout=30)
    finally:
        srv.shutdown()
        srv.server_close()
    return out, [l for l in p.stderr.splitlines() if "Error" in l or "Unable to assign" in l or "is not defined" in l or "TypeError" in l]


@pytest.mark.skipif(__import__("shutil").which("qml6") is None, reason="qml6 not installed")
@pytest.mark.parametrize("layout", ["lbar", "full"])
def test_election_board_renders_in_the_overlay(tmp_path, layout):
    out, errs = _overlay_render(tmp_path, layout)
    assert errs == [] and out.exists() and out.stat().st_size > 20_000


@pytest.mark.skipif(__import__("shutil").which("qml6") is None, reason="qml6 not installed")
@pytest.mark.parametrize("layout", ["lbar", "full"])
def test_election_board_with_trades_renders(tmp_path, layout):
    """The Trades section + the ticker's calls / positions draw without a QML error (a 2024 race stands in for one held)."""
    rows = []
    for i, (who, side, won) in enumerate((("Hansen", "YES", None), ("David", "NO", None), ("Hansen", "YES", True))):
        rows.append({"who": who, "side": side, "label": f"PA Senate · Pick{i}", "entry": 40.0, "mark": 60.0, "pct": 50.0, "dir": "up",
                     "size": "50+ ct", "kind": "election", "election": {"class": {"type": "control", "office": "S"},
                     "pick": {"party": "R", "name": ""}, "label": "Senate control · Rep", "strike": {}}})
    out, errs = _overlay_render(tmp_path, layout, rows)
    assert errs == [] and out.exists() and out.stat().st_size > 20_000
