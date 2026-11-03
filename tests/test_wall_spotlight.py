"""The TV's state spotlight (wall/spotlight.py + the wall service's /i/{name}/spotlight + the overlay's Spot.qml): a state
picked on the TV's phone or by a watch-page vote shows ~30 s in the sidebar with the web's card; no positions on it. No
network: the web is a stub; the QML render (qml6, offscreen) is skipped where qml6 isn't installed."""
import json
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from test_wall_bets import Clock
from wall.election import Results, replay
from wall.spotlight import Spotlight, state_list

FX = Path(__file__).parent / "fixtures" / "wall" / "election"
CARD = {"state": "GA", "name": "Georgia", "more": 13, "error": "", "races": [
    {"id": "S-GA", "office": "S", "label": "Georgia", "status": "41% in · Ossoff +6.2", "short": "41% in", "called": "", "pct": 41.0,
     "cands": [{"name": "Ossoff", "party": "D", "pct": 52.8, "winner": False}, {"name": "Collins", "party": "R", "pct": 46.6, "winner": False}],
     "markets": [{"who": "Jon Ossoff", "party": "D", "prob": 97.0, "bid": 97.0, "ask": 97.1, "chg24": 0.2},
                 {"who": "Mike Collins", "party": "R", "prob": 3.0, "bid": 3.0, "ask": 3.1, "chg24": -0.2}],
     "spark": [96.5, 96.8, 97.0, 96.9, 97.1, 97.0, 97.2, 97.0], "spark_party": "D"},
    {"id": "G-GA", "office": "G", "label": "Georgia", "status": "✓ Bottoms (D) · CNN", "short": "✓ D · CNN", "called": "D", "pct": 88.0,
     "cands": [{"name": "Bottoms", "party": "D", "pct": 54.1, "winner": True}, {"name": "Jackson", "party": "R", "pct": 45.2, "winner": False}],
     "markets": [{"who": "Keisha Lance Bottoms", "party": "D", "prob": 99.0, "bid": 98.0, "ask": 99.5, "chg24": 27.5},
                 {"who": "Rick Jackson", "party": "R", "prob": 1.0, "bid": 0.5, "ask": 2.0, "chg24": -27.5}],
     "spark": [71, 72, 70, 74, 80, 88, 95, 99], "spark_party": "D"},
    {"id": "H-GA-6", "office": "H", "label": "GA-6", "status": "no votes yet", "called": "", "pct": 0.0,
     "cands": [{"name": "McBath", "party": "D", "pct": 0.0, "winner": False}, {"name": "Smith", "party": "R", "pct": 0.0, "winner": False}],
     "markets": [{"who": "Lucy McBath", "party": "D", "prob": 55.0, "bid": 54.0, "ask": 56.0, "chg24": -3.0}],
     "spark": [60, 58, 57, 55], "spark_party": "D"}]}


class Web:
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, body):
        self.calls.append((method, path))
        st = path.rsplit("/", 1)[1].split("?")[0]
        return (200, {**CARD, "state": st}) if st == "GA" else (200, {"state": st, "name": st, "races": [], "more": 0, "error": ""})


def test_spotlight_shows_for_30s_then_goes(monkeypatch):
    clock, web = Clock(1000.0), Web()
    sp = Spotlight(web, clock=clock, spawn=lambda f: f(), show_s=30)
    v = sp.set("ga", by="Ann")
    assert web.calls == [("GET", "/api/v1/wall/election/spot/GA")]
    v = sp.view()
    assert v["state"] == "GA" and v["name"] == "Georgia" and v["by"] == "Ann" and v["card"]["races"][0]["id"] == "S-GA"
    assert "positions" not in json.dumps(v)
    clock.t += 14
    assert not sp.due()
    clock.t += 2
    assert sp.due()                                   # refreshed every 15 s while it shows
    clock.t += 15
    assert sp.view() is None and not sp.due()         # 31 s: gone
    sp.set("ME")
    assert sp.view()["state"] == "ME"
    sp.set("")
    assert sp.view() is None                          # closed
    with pytest.raises(ValueError):
        sp.set("XX")


def test_a_new_state_never_shows_the_old_card(monkeypatch):
    clock = Clock(1000.0)
    held = []
    sp = Spotlight(lambda m, p, b: held.append(p) or (200, {**CARD, "state": p[-2:]}), clock=clock, spawn=lambda f: None)
    sp.set("GA")
    sp.refresh("GA")
    assert sp.view()["card"]["state"] == "GA"
    sp.set("ME")                                      # ME's fetch hasn't come back yet
    assert sp.view()["card"] is None and sp.view()["state"] == "ME"


def test_queue_newest_on_top_races_and_eviction():
    clock, web = Clock(1000.0), Web()
    sp = Spotlight(web, clock=clock, spawn=lambda f: f())
    sp.set("ME")
    sp.set("GA", by="race call", race="G-GA")         # one race: its card is just that race
    v = sp.view()
    assert [i["key"] for i in v["items"]] == ["G-GA", "ME"] and v["items"][0]["card"]["races"][0]["id"] == "G-GA"
    assert v["items"][0]["name"] == "Georgia" and ("GET", "/api/v1/wall/election/spot/GA?race=G-GA") in web.calls
    sp.set("NC"); sp.set("ME")                        # ME moves back up; the queue keeps 3, the oldest (G-GA) goes
    assert [i["key"] for i in sp.view()["items"]] == ["ME", "NC", "G-GA"]
    sp.set("TX")
    assert [i["key"] for i in sp.view()["items"]] == ["TX", "ME", "NC"]
    with pytest.raises(ValueError):
        sp.set("GA", race="S-ME")                     # a race of another state
    with pytest.raises(ValueError):
        sp.set("", race="nope")


def test_web_down_says_so_on_the_card(monkeypatch):
    def down(m, p, b):
        raise OSError("refused")
    sp = Spotlight(down, clock=Clock(1000.0), spawn=lambda f: f())
    sp.set("GA")
    assert sp.view()["card"] is None and "web unreachable" in sp.view()["error"]


def test_state_list_puts_key_races_first():
    r = Results(replay(f"{FX}:0.6"))
    r.poll()
    sts = state_list(r.snapshot())
    keys = [s for s in sts if s["key"]]
    assert keys and sts[: len(keys)] == keys and all(s["n"] >= 1 for s in sts)
    assert len({s["st"] for s in sts}) == len(sts) == 50


def _service(tmp_path, web):
    from test_wall_service import FakeDriver, FakeScores
    from wall.engine import Session
    from wall.instance import Instance
    from wall.llm import LLMGuard
    from wall.presets import PresetStore
    from wall.server import Cfg, build
    cfg = Cfg({"WALL_STATE_DIR": str(tmp_path), "WALL_TV": "0", "WALL_DISCORD": "0", "WALL_IMPORT_FROM": "", "WALL_API_TOKEN": "tok"})
    res = Results(replay(f"{FX}:0.6"), tmp_path)
    res.touch = lambda: None
    res.poll()
    store = PresetStore(str(tmp_path))
    inst = {k: Instance(k, Session(k), store, FakeScores({}), driver=FakeDriver(), run=lambda f: None) for k in ("tv", "discord")}
    sp = Spotlight(web, spawn=lambda f: f())
    return build(cfg, inst, FakeScores({}), store, guard=LLMGuard(mode="off"), results=res, spot=sp), sp


def test_service_endpoints_phone_overlay_and_watch_page(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    app, sp = _service(tmp_path, Web())
    tv = TestClient(app, base_url="http://127.0.0.1:7795", client=("127.0.0.1", 5555))   # the TV router (loopback)
    got = tv.get("/i/tv/spotlight").json()
    assert got["spot"] is None and got["states"][0]["key"] and got["configured"]
    r = tv.post("/i/tv/spotlight", json={"state": "GA", "by": "TV remote"}).json()
    assert r["spot"]["state"] == "GA"
    ov = tv.get("/i/tv/api/overlay/spot").json()["spot"]
    assert ov["card"]["name"] == "Georgia" and ov["by"] == "TV remote" and 0 < ov["left"] <= 600
    assert tv.post("/i/tv/spotlight", json={"state": "ZZ"}).status_code == 422
    assert tv.post("/i/tv/spotlight", json={"state": 5}).status_code == 422
    # the watch page (sharplab-web over the tailnet, with the token): a passed vote / the owner → /i/discord/spotlight
    web = TestClient(app, base_url="http://100.81.31.34:7795", client=("100.81.31.34", 5555))
    assert web.post("/i/discord/spotlight", json={"state": "ME"}).status_code == 401
    r = web.post("/i/discord/spotlight", json={"state": "ME"}, headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200 and r.json()["spot"]["state"] == "ME" and r.json()["spot"]["by"] == "watch party"
    assert tv.get("/i/tv/api/overlay/spot").json()["spot"]["state"] == "ME"   # one spotlight: the TV shows what the stream shows
    assert web.post("/i/tv/spotlight", json={"state": "GA"}, headers={"Authorization": "Bearer tok"}).status_code == 403
    tv.post("/i/tv/spotlight", json={"state": ""})
    assert tv.get("/i/tv/api/overlay/spot").json()["spot"] is None


# ---- the card in the overlay (qml6 offscreen): renders without a QML error -------------------------------------------------
def render(tmp_path, spot, out):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            r = Results(replay(f"{FX}:0.6"))
            r.poll()
            from wall.election import board
            if self.path.startswith("/api/overlay/spot"):
                body = {"spot": spot}
            elif self.path.startswith("/api/overlay/view"):
                body = {"view": "lbar", "rev": 1}
            else:
                body = {"board": "election", "shown": "election", "visible": True, "layout": "lbar", "view": "lbar", "rev": 1,
                        "boards": [{"id": "election", "name": "Election"}], "data": board(r.snapshot()), "updated": time.time(), "error": ""}
            b = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        p = subprocess.run(["qml6", str(Path(__file__).resolve().parent.parent / "wall" / "qml" / "Preview.qml"), "--", "--layout", "lbar",
                            "--size", "1920x1080", "--api", base, "--spot", base, "--out", str(out), "--wait", "3000"],
                           env={"QT_QPA_PLATFORM": "offscreen", "QT_FORCE_STDERR_LOGGING": "1", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
                           capture_output=True, text=True, timeout=40)
    finally:
        srv.shutdown()
        srv.server_close()
    return [l for l in p.stderr.splitlines() if "Error" in l or "Unable to assign" in l or "is not defined" in l or "TypeError" in l]


@pytest.mark.skipif(shutil.which("qml6") is None, reason="qml6 not installed")
def test_spot_card_renders_in_the_sidebar(tmp_path):
    spot = {"state": "GA", "name": "Georgia", "by": "TV remote", "left": 22.0, "show_s": 30.0, "card": CARD, "error": "", "rev": 3}
    out = tmp_path / "spot.png"
    errs = render(tmp_path, spot, out)
    assert errs == [] and out.exists() and out.stat().st_size > 20_000
    loading = tmp_path / "loading.png"
    assert render(tmp_path, {**spot, "card": None}, loading) == [] and loading.exists()


@pytest.mark.skipif(shutil.which("qml6") is None, reason="qml6 not installed")
def test_spot_queue_renders(tmp_path):
    one = {**CARD, "races": [CARD["races"][1]], "more": 0}
    items = [{"key": "G-GA", "state": "GA", "race": "G-GA", "name": "Georgia · Governor", "by": "race call", "left": 590.0, "show_s": 600.0, "card": one, "error": ""},
             {"key": "GA", "state": "GA", "race": "", "name": "Georgia", "by": "TV remote", "left": 400.0, "show_s": 600.0, "card": CARD, "error": ""},
             {"key": "ME", "state": "ME", "race": "", "name": "Maine", "by": "watch party", "left": 300.0, "show_s": 600.0, "card": None, "error": ""}]
    out = tmp_path / "queue.png"
    assert render(tmp_path, {**items[0], "items": items, "rev": 5}, out) == [] and out.stat().st_size > 20_000
