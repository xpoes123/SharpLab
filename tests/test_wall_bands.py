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
from wall.engine import LBAR_JS, Session
from wall.instance import Instance
from wall.llm import LLMGuard
from wall.presets import PresetStore
from wall.server import Cfg, build
from wall.sync import SyncStore

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
    """live_ids stand in for wall/sync.py tile_games (the endpoint test runs the real one)."""
    games = [{"league": live, "id": g} if g else None for g in live_ids]
    return build_view(urls=list(urls), games=games, sound=sound, lbar=lbar, view="", title="Live NFL", live_league=live,
                      scores=scores, tape=tape, delay=delay, chat=chat, now=now)


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


def test_pregame_shows_its_start_even_before_the_tape_catches_up():
    s, tape = Seq(board("nfl")), ScoreTape()
    v = view(s, tape, 1000, urls=[url("nfl", "rams")], live_ids=["401873032"])
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


def test_a_tile_without_a_live_game_says_whose_page_it_is():
    nfl = board("nfl")
    s, tape = Seq(nfl), ScoreTape()
    view(s, tape, 900)
    v = view(s, tape, 1000, urls=[url("nfl", "packers"), url("nfl", "bears"), "https://example.com/x"], live_ids=[])
    final, team, other = v["tiles"]
    assert final["game"]["state"] == "post" and final["game"]["status"] == "Final" and final["game"]["home"]["score"] == 27
    assert team["game"] is None and team["label"] == "Bears · CHI"     # Monday's game isn't the tile's (sync maps it live)
    assert other["game"] is None and other["label"] == "" and other["host"] == "example.com"
    v = view(s, tape, 1000, urls=[url("nba", "clippers")], live_ids=[])   # no NBA scoreboard at all: the page's slug
    assert v["tiles"][0]["label"] == "clippers"


def test_status_strings():
    g = {"state": "in", "period": 2, "clock": "0:00", "detail": "Halftime"}
    assert status(g) == "Halftime"
    assert status({"state": "in", "period": 5, "clock": "3:10", "detail": "3:10 - OT"}) == "OT 3:10"
    assert status({"state": "post", "detail": "Final/OT"}) == "Final/OT"


def _app(tmp_path, monkeypatch, lbar=True):
    monkeypatch.setattr("wall.server.tv_active", lambda api: None)
    cfg = Cfg({"WALL_STATE_DIR": str(tmp_path), "WALL_API_TOKEN": "s3cret", "WALL_IMPORT_FROM": "", "WALL_TV_OUTPUT": ""})
    store = PresetStore(str(tmp_path))
    data = {"nfl": board("nfl")}
    drv = FakeDriver()
    drv.lbar = lbar
    tv = Instance("tv", Session("tv"), store, FakeScores(data), driver=drv, run=lambda f: None)
    sync = SyncStore(tmp_path / "sync.json")
    app = build(cfg, {"tv": tv}, FakeScores(data), store, guard=LLMGuard(mode="off"), sync=sync)
    return app, sync, TestClient(app, base_url="http://127.0.0.1:7795", client=("127.0.0.1", 5555))


def test_bands_endpoint_uses_the_sync_stores_delays(tmp_path, monkeypatch):
    app, sync, c = _app(tmp_path, monkeypatch)
    assert c.get("/i/tv/bands").json()["n"] == 0
    c.post("/i/tv/start", json={"live": "nfl", "tiles": 2})
    urls = c.get("/i/tv").json()["urls"]
    sync.set_manual(urls[1], 30)
    v = c.get("/i/tv/bands").json()
    assert v["n"] == 2 and v["lbar"] is True and v["title"] == "Live NFL" and v["league"] == "nfl"
    assert [(t["delay"], t["src"]) for t in v["tiles"]] == [(45.0, "default"), (30.0, "manual")]
    assert all(t["game"] for t in v["tiles"])


def test_bands_and_the_boards_share_one_tile_game_mapping_and_delays(tmp_path, monkeypatch):
    """/bands (the headers) and /api/overlay/bets (the delays the L-bar's boards are held back by) agree per game."""
    app, sync, c = _app(tmp_path, monkeypatch)
    c.post("/i/tv/start", json={"urls": [url("nfl", "bengals"), url("nfl", "rams"), "https://example.com/x"]})
    sync.set_manual(url("nfl", "bengals"), 20)
    bands, bets = c.get("/i/tv/bands").json(), c.get("/i/tv/api/overlay/bets").json()
    a, b, x = bands["tiles"]
    assert a["game"]["id"] == TEN_CIN and b["game"]["state"] == "pre" and x["game"] is None and x["host"] == "example.com"
    assert {t["game"]["id"]: t["delay"] for t in bands["tiles"] if t["game"]} == bets["delays"]
    assert bands["delay"] == bets["default"]


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
    assert g["bottom"]["y"] + g["bottom"]["h"] == area_h and g["top"]["y"] == 0 and g["outer"]["h"] == area_h


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_tiles_placed_for_the_l_bar_without_it_on_screen_leave_the_rest_to_the_bands():
    """The real-KWin case: tiles in the L-bar's corner but no L-bar drawn — the bands own the whole screen, so the
    kiosk / a panel never shows below or beside the tiles."""
    js = LBAR_JS + "\nconsole.log(JSON.stringify([wallBands(2, 2560, 1440, true, false), wallBands(4, 2560, 1440, true, false)]));"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    two, four = json.loads(out.stdout)
    assert two["outer"] == {"x": 0, "y": 0, "w": 2560, "h": 1440}
    assert (two["top"]["h"], two["bottom"]["y"], two["bottom"]["h"], two["bottom"]["w"]) == (292, 877, 563, 2560)
    assert two["right"] == {"x": 2080, "y": 292, "w": 480, "h": 585}
    assert four["top"]["h"] == 0 and four["bottom"] == {"x": 0, "y": 1170, "w": 2560, "h": 270}
    assert four["right"] == {"x": 2080, "y": 0, "w": 480, "h": 1170} and four["head"] == ["", "", "bottom", "bottom"]


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
            b = json.dumps(body if self.path.endswith("/bands") else {"default": 45, "delays": {}, "show": False, "n": 0, "tiles": []} if self.path.endswith("/bets") else {}).encode()
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
        args = ["qml6", str(QML / "Preview.qml"), "--", "--layout", "none", "--n", "2", "--size", "1920x1080",
                "--out", str(out), "--wait", "2500"] + (["--bets", f"http://127.0.0.1:{srv.server_port}/i/tv"] if bands else [])
        r = subprocess.run(args, env={"QT_QPA_PLATFORM": "offscreen", "QT_FORCE_STDERR_LOGGING": "1", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
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


BJ = QML / "bands.js"
BOARD = QML / "board.js"


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_header_and_sidebar_agree():
    """The header takes the board's delayed copy of its game (board.js delayed, the sidebar's data) whenever the board
    has it: even if the service's own tape caught a newer or older snapshot, both show the same score."""
    lib = lambda p: p.read_text().replace(".pragma library", "")
    prog = f"""
const B = (function () {{ {lib(BOARD)}; return {{ delayed }}; }})();
const H = (function () {{ {lib(BJ)}; return {{ headerGame }}; }})();
const g = (a, h) => ({{id: "1", state: "in", period: 4, clock: "2:41", detail: "2:41 - 4th", away: {{abbr: "BOS", score: a}}, home: {{abbr: "ORL", score: h}}}});
const snap = (a, h) => ({{shown: "nba", updated: 0, data: {{kind: "scores", league: "nba", games: [g(a, h)]}}}});
const hist = [{{t: 1000, s: snap(88, 91)}}, {{t: 1050, s: snap(158, 91)}}];   // the 158 is a spoiler until 1095
const tile = {{league: "nba", id: "1", state: "in", status: "Q4 2:40", away: {{abbr: "BOS", color: "#0", score: 90}}, home: {{abbr: "ORL", color: "#1", score: 91}}}};
const out = [];
for (const now of [1060, 1094, 1095]) {{
  const side = B.delayed(hist, now, {{"1": 45}}, 45).data;
  const head = H.headerGame(tile, side);
  out.push([side.games[0].away.score, head.away.score, head.status, head.away.color]);
}}
const syncing = B.delayed([hist[1]], 1060, {{"1": 45}}, 45).data;   // the board has nothing that old: the service's version
out.push(H.headerGame(tile, syncing).away.score);
process.stdout.write(JSON.stringify(out));"""
    r = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [[88, 88, "Q4 2:41", "#0"], [88, 88, "Q4 2:41", "#0"], [158, 158, "Q4 2:41", "#0"], 90]


# ---- the TV OS's active app gates the overlay (never over Dreamscape / YouTube / News) ----------------------------------
def _tv(tmp_path, app):
    from tests.test_wall_service import inst
    i, d, clock = inst(tmp_path, {"nfl": board("nfl")})
    i.host_app = lambda: app["now"]
    return i, d, clock


def test_overlay_draws_only_while_the_tv_os_is_on_its_wall(tmp_path):
    app = {"now": "wall"}
    i, d, clock = _tv(tmp_path, app)
    i.start(urls=["https://a.tv", "https://b.tv"], board="nfl", layout="lbar")
    assert i.tv_owned and i.overlay_on() and i.overlay_view()["view"] == "lbar"
    app["now"] = "dreamscape"
    clock.t += 3
    i.tick()
    assert not i.overlay_on() and i.overlay_view()["view"] == "" and i.state()["tv_app"] == "dreamscape"
    clock.t += 30
    i.tick()
    assert i.urls   # the TV OS started it: it stops it; we only hide
    app["now"] = "wall"
    clock.t += 3
    i.tick()
    assert i.overlay_on()


def test_a_wall_the_tv_os_didnt_start_stops_when_the_tv_moves_on(tmp_path):
    app = {"now": None}   # started through the service API while the TV OS sat on its home screen
    i, d, clock = _tv(tmp_path, app)
    i.start(urls=["https://a.tv", "https://b.tv"])
    assert not i.tv_owned and not i.overlay_on()
    clock.t += 30
    i.tick()
    assert i.urls   # home / unreachable: hidden, but nobody went anywhere
    app["now"] = "dreamscape"
    clock.t += 3
    i.tick()
    assert i.urls and not i.overlay_on()
    clock.t += 5
    i.tick()
    assert i.urls   # 5 s away: not yet
    clock.t += 4
    i.tick()
    assert not i.urls and "dreamscape" in i.state()["stopped"] and d.stopped


def test_bands_and_bets_carry_on(tmp_path, monkeypatch):
    app, sync, c = _app(tmp_path, monkeypatch)
    tv = app.state.instances["tv"]
    tv.host_app = lambda: "youtube"
    c.post("/i/tv/start", json={"urls": [url("nfl", "bengals"), url("nfl", "rams")]})
    assert c.get("/i/tv/bands").json()["on"] is False and c.get("/i/tv/api/overlay/bets").json()["on"] is False
    tv.host_app = lambda: "wall"
    tv._app_at = 0
    tv.tick()
    assert c.get("/i/tv/bands").json()["on"] is True


# ---- bets live in the L layout -----------------------------------------------------------------------------------------
def test_one_or_four_tiles_bring_the_l_bar_up_for_bets(tmp_path):
    from tests.test_wall_service import inst
    i, d, clock = inst(tmp_path, {"nfl": board("nfl")})
    i.start(urls=["https://a.tv"])
    i.bets_lbar(True, 100)
    assert i.auto_lbar and d.overlays[-1] == ("", True)       # re-tiled into the L-bar's corner
    i.bets_lbar(False, 110)
    assert i.auto_lbar                                          # a quiet spell doesn't flap it
    i.bets_lbar(False, 131)
    assert not i.auto_lbar and d.overlays[-1] == ("", False)
    i.overlay("lbar", True)
    i.bets_lbar(True, 200)
    assert not i.auto_lbar                                      # the L-bar is already up
    i.overlay("", False)
    i.set_tile(1, "https://b.tv")
    i.bets_lbar(True, 300)
    assert not i.auto_lbar                                      # 2 tiles: the bands carry the bets


def test_bets_payload_carries_ticker_and_auto_lbar(tmp_path, monkeypatch):
    app, sync, c = _app(tmp_path, monkeypatch, lbar=False)
    c.post("/i/tv/start", json={"urls": [url("nfl", "bengals")]})
    v = c.get("/i/tv/api/overlay/bets").json()
    assert v["ticker"] is False and v["auto_lbar"] is False
    assert c.post("/sync", json={"ticker": True}).json()["ticker"] is True
    assert c.get("/i/tv/api/overlay/bets").json()["ticker"] is True and SyncStore(tmp_path / "sync.json").ticker
    assert c.post("/sync", json={"ticker": "yes"}).status_code == 422


@pytest.mark.skipif(shutil.which("qml6") is None, reason="qml6 not installed")
def test_band_layer_carries_the_bets(tmp_path):
    """2-up, no L-bar: the bets go to the header chips + the bottom band, and draw without QML errors."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    s, tape = Seq(board("nfl")), ScoreTape()
    view(s, tape, 900)
    bands = view(s, tape, 1000, urls=[url("nfl", "bills"), url("nfl", "bengals")], live_ids=[BAL_BUF, TEN_CIN])
    rows = [{"who": "David", "side": "YES", "label": "BUF −3.5", "entry": 42, "mark": 51, "dir": "up", "pct": 21, "size": "50+ ct"}]

    def bets(show):
        return {"show": show, "n": 2, "view": "", "default": 45, "delays": {}, "on": True, "ticker": False, "auto_lbar": False,
                "tiles": [{"i": 0, "delay": 45, "src": "default", "label": "BAL @ BUF", "rows": rows}, {"i": 1, "delay": 45, "rows": []}]}

    state = {"show": True}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = bands if self.path.endswith("/bands") else bets(state["show"]) if self.path.endswith("/bets") else {}
            b = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def render(name):
        out = tmp_path / f"{name}.png"
        r = subprocess.run(["qml6", str(QML / "Preview.qml"), "--", "--layout", "none", "--size", "1920x1080",
                            "--bets", f"http://127.0.0.1:{srv.server_port}/i/tv", "--out", str(out), "--wait", "2500"],
                           env={"QT_QPA_PLATFORM": "offscreen", "QT_FORCE_STDERR_LOGGING": "1", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
                           capture_output=True, text=True, timeout=30)
        return out, [l for l in r.stderr.splitlines() if "Error" in l or "Unable to assign" in l or "is not defined" in l]

    try:
        on, e1 = render("on")
        state["show"] = False
        off, e2 = render("off")
    finally:
        srv.shutdown()
        srv.server_close()
    assert e1 == [] and e2 == [] and on.read_bytes() != off.read_bytes()
