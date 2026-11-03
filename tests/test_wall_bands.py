"""The bands around the wall's tiles (wall/bands.py + lbar.js wallBands + qml/Bands.qml): which game each tile shows,
scores only as of now − that tile's spoiler delay, the strip's other games, the chat, the band geometry, and a real
offscreen render of the band layer."""
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from tests.test_wall_service import FakeDriver, FakeHeadless, FakeScores, board, url
from wall.bands import DEFAULT_DELAY_S, ScoreTape, build_view, default_delay, status
from wall.engine import LBAR_JS, Session, overlay_cmd
from wall.instance import Instance
from wall.llm import LLMGuard
from wall.presets import PresetStore
from wall.server import Cfg, build

QML = Path(__file__).resolve().parent.parent / "wall" / "qml"
BAL_BUF = "401873026"   # BAL 7–10 BUF, Q2 14:14 in the fixture
TEN_CIN = "401873031"


def bump(data, gid, away=None, home=None):
    d = copy.deepcopy(data)
    g = next(x for x in d["games"] if x["id"] == gid)
    if away is not None:
        g["away"]["score"] = away
    if home is not None:
        g["home"]["score"] = home
    return d


class Seq:
    """scores.get that returns the next scoreboard each time the test says so."""
    def __init__(self, nfl): self.data = {"nfl": nfl}
    def get(self, league, fresh=False): return self.data.get(league)


def view(scores, tape, now, urls=(), live_ids=(), live="nfl", delay=default_delay, chat=None, sound=0, lbar=False):
    return build_view(urls=list(urls), sound=sound, lbar=lbar, view="", title="Live NFL", live_league=live,
                      live_ids=list(live_ids), scores=scores, tape=tape, delay=delay, chat=chat, now=now)


def test_tape_is_as_of_and_forgets_a_stale_run():
    t = ScoreTape(keep_s=600)
    a, b = {"games": [1]}, {"games": [2]}
    t.see("nfl", 100, a)
    t.see("nfl", 100, a)
    t.see("nfl", 130, b)
    assert t.at("nfl", 99) is None and t.at("nfl", 100) is a and t.at("nfl", 129) is a and t.at("nfl", 500) is b
    c = {"games": [3]}
    t.see("nfl", 2000, c)   # nothing seen for > keep_s: an old run's history is not "delayed", it's stale
    assert t.at("nfl", 2000 - 45) is None and t.latest("nfl") is c


def test_headers_show_the_score_as_of_now_minus_the_tile_delay():
    nfl = board("nfl")
    s, tape = Seq(nfl), ScoreTape()
    v = view(s, tape, 1000, urls=[url("nfl", "bills")], live_ids=[BAL_BUF])
    g = v["tiles"][0]["game"]
    # just started: no snapshot 45 s old yet → the teams, never the score
    assert g["away"]["abbr"] == "BAL" and g["away"]["score"] is None and g["status"] == "syncing" and not v["tiles"][0]["synced"]
    s.data["nfl"] = bump(nfl, BAL_BUF, away=14)   # BAL scores at t=1030
    view(s, tape, 1030, urls=[url("nfl", "bills")], live_ids=[BAL_BUF])
    v = view(s, tape, 1050, urls=[url("nfl", "bills")], live_ids=[BAL_BUF])   # as of 1005: still 7–10
    g = v["tiles"][0]["game"]
    assert (g["away"]["score"], g["home"]["score"], g["status"]) == (7, 10, "Q2 14:14") and v["tiles"][0]["synced"]
    assert v["tiles"][0]["delay"] == DEFAULT_DELAY_S and v["tiles"][0]["src"] == "default"
    v = view(s, tape, 1075, urls=[url("nfl", "bills")], live_ids=[BAL_BUF])   # as of 1030: now the picture has it
    assert v["tiles"][0]["game"]["away"]["score"] == 14


def test_each_tile_has_its_own_delay():
    nfl = board("nfl")
    s, tape = Seq(nfl), ScoreTape()
    view(s, tape, 1000)
    s.data["nfl"] = bump(nfl, BAL_BUF, away=14)
    view(s, tape, 1030)
    delays = {url("nfl", "bills"): (10.0, "manual"), url("nfl", "bengals"): (60.0, "auto")}
    v = view(s, tape, 1045, urls=list(delays), live_ids=[BAL_BUF, TEN_CIN], delay=lambda u: delays.get(u, (45.0, "default")))
    a, b = v["tiles"]
    assert a["game"]["away"]["score"] == 14 and (a["delay"], a["src"]) == (10.0, "manual")   # as of 1035
    assert b["game"]["away"]["score"] is None and b["game"]["status"] == "syncing"            # as of 985: none yet


def test_tiles_without_a_live_plan_find_their_game_by_team_page():
    nfl = board("nfl")
    s, tape = Seq(nfl), ScoreTape()
    view(s, tape, 900, live="")
    v = view(s, tape, 1000, live="", urls=[url("nfl", "bengals"), url("nfl", "rams"), "https://www.example.com/x"])
    a, b, c = v["tiles"]
    assert a["game"]["id"] == TEN_CIN and a["game"]["away"]["score"] == 13
    assert b["game"]["state"] == "pre" and b["game"]["status"] == "4:05 PM" and b["synced"]
    assert c["game"] is None and c["host"] == "example.com"
    assert v["league"] == "nfl"


def test_pregame_shows_its_start_even_before_the_tape_catches_up():
    s, tape = Seq(board("nfl")), ScoreTape()
    v = view(s, tape, 1000, live="", urls=[url("nfl", "rams")])
    assert v["tiles"][0]["game"]["status"] == "4:05 PM" and v["tiles"][0]["synced"]


def test_the_strip_has_the_other_games_delayed_live_first():
    nfl = board("nfl")
    s, tape = Seq(nfl), ScoreTape()
    view(s, tape, 980)
    s.data["nfl"] = bump(nfl, TEN_CIN, home=14)
    v = view(s, tape, 1030, urls=[url("nfl", "bills")], live_ids=[BAL_BUF])
    ids = [g["id"] for g in v["ticker"]]
    assert BAL_BUF not in ids and ids and len(ids) <= 12
    assert [g["state"] for g in v["ticker"]][:7] == ["in"] * 7
    cin = next(g for g in v["ticker"] if g["id"] == TEN_CIN)
    assert cin["home"]["score"] == 7   # as of 985 (the default delay), not the 14 seen at 1030


def test_chat_lines_only_while_the_party_is_on():
    s, tape = Seq(board("nfl")), ScoreTape()
    msgs = [{"id": i, "ts": 1000 - 60 + i * 10, "kind": "chat", "name": f"p{i}", "guest": i == 2, "text": f"m{i}"} for i in range(6)]
    v = view(s, tape, 1000, chat={"on": True, "messages": msgs})
    assert [l["text"] for l in v["chat"]] == ["m2", "m3", "m4", "m5"]
    assert [l["fresh"] for l in v["chat"]] == [False, False, False, True] and v["chat"][0]["guest"]
    assert view(s, tape, 1000, chat={"on": False, "messages": msgs})["chat"] == []


def test_status_strings():
    g = {"state": "in", "period": 2, "clock": "0:00", "detail": "Halftime"}
    assert status(g) == "Halftime"
    assert status({"state": "in", "period": 5, "clock": "3:10", "detail": "3:10 - OT"}) == "OT 3:10"
    assert status({"state": "post", "detail": "Final/OT"}) == "Final/OT"


def test_bands_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr("wall.server.tv_active", lambda api: None)
    cfg = Cfg({"WALL_STATE_DIR": str(tmp_path), "WALL_API_TOKEN": "s3cret", "WALL_IMPORT_FROM": "", "WALL_TV_OUTPUT": ""})
    store = PresetStore(str(tmp_path))
    store.put([{"name": "Live NFL", "urls": [], "live": "nfl"}])
    data = {"nfl": board("nfl")}
    drv = FakeDriver()
    drv.lbar = True
    tv = Instance("tv", Session("tv"), store, FakeScores(data), driver=drv, run=lambda f: None)
    app = build(cfg, {"tv": tv}, FakeScores(data), store, guard=LLMGuard(mode="off"),
                delay=lambda u: (30.0, "manual"))
    c = TestClient(app, base_url="http://127.0.0.1:7795", client=("127.0.0.1", 5555))
    assert c.get("/i/tv/bands").json()["n"] == 0
    c.post("/i/tv/start", json={"live": "nfl", "tiles": 2})
    v = c.get("/i/tv/bands").json()
    assert v["n"] == 2 and v["lbar"] is True and v["title"] == "Live NFL" and v["league"] == "nfl"
    assert [t["delay"] for t in v["tiles"]] == [30.0, 30.0] and all(t["game"] for t in v["tiles"])


def test_overlay_gets_its_bands_url():
    assert overlay_cmd("http://r", "http://127.0.0.1:7795/i/tv")[-2:] == ["--bands", "http://127.0.0.1:7795/i/tv"]
    assert "--bands" not in overlay_cmd("http://r")
    srv = build(Cfg({"WALL_IMPORT_FROM": "", "WALL_PORT": "7795", "WALL_DISCORD": "0"}), guard=LLMGuard(mode="off"))
    assert srv.state.instances["tv"].session.bands_api == "http://127.0.0.1:7795/i/tv"


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
@pytest.mark.parametrize("n,lbar,top,bottom,head", [
    (1, False, 0, 0, [""]),
    (4, True, 0, 0, ["", "", "", ""]),
    (2, False, 360, 360, ["top", "top"]),
    (2, True, 292, 293, ["top", "top"]),
    (3, False, 243, 243, ["top", "top", "bottom"]),
    (3, True, 198, 198, ["top", "top", "bottom"]),
])
def test_band_geometry(n, lbar, top, bottom, head):
    js = LBAR_JS + f"\nconsole.log(JSON.stringify(wallBands({n}, 2560, 1440, {'true' if lbar else 'false'})));"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    g = json.loads(out.stdout)
    assert (g["top"]["h"], g["bottom"]["h"], g["head"]) == (top, bottom, head)
    area_h = 1170 if lbar else 1440
    assert g["bottom"]["y"] + g["bottom"]["h"] == area_h and g["top"]["y"] == 0


@pytest.mark.skipif(shutil.which("qml6") is None, reason="qml6 not installed")
def test_band_layer_renders(tmp_path):
    """Preview.qml with the band layer against a stub /bands: draws without QML errors, and differs with vs without."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    s, tape = Seq(board("nfl")), ScoreTape()
    view(s, tape, 900)
    body = view(s, tape, 1000, urls=[url("nfl", "bills"), url("nfl", "bengals")], live_ids=[BAL_BUF, TEN_CIN],
                chat={"on": True, "messages": [{"id": 1, "ts": 990, "kind": "chat", "name": "Kim", "guest": True, "text": "refs!!"}]})

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            b = json.dumps(body if self.path.endswith("/bands") else {}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def render(name, bands):
        out = tmp_path / f"{name}.png"
        args = ["qml6", str(QML / "Preview.qml"), "--", "--layout", "none", "--tiles", "2", "--size", "1920x1080",
                "--out", str(out), "--wait", "2500"] + (["--bands", f"http://127.0.0.1:{srv.server_port}/i/tv"] if bands else [])
        r = subprocess.run(args, env={"QT_QPA_PLATFORM": "offscreen", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
                           capture_output=True, text=True, timeout=30)
        return out, [l for l in r.stderr.splitlines() if "Error" in l or "Unable to assign" in l or "is not defined" in l
                     or "TypeError" in l or "ReferenceError" in l]

    try:
        on, errs_on = render("on", True)
        off, errs_off = render("off", False)
    finally:
        srv.shutdown()
        srv.server_close()
    assert errs_on == [] and errs_off == []
    assert on.exists() and off.exists() and on.read_bytes() != off.read_bytes()
