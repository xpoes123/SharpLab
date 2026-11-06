"""The wall service (wall/): presets, live-game planning, the engine's KWin script + tile bookkeeping, instances, the HTTP
gate, and the headless capture's plumbing. Ported from the TV OS's tests/test_wall.py + test_livewall.py where the code moved."""
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

import wall.engine as engine
from wall.engine import TILES, Driver, Session, tile_js, tile_map, tile_of
from wall.headless import Headless, ffmpeg_cmd, find_cast_node, gst_cmd
from wall.instance import Instance, WallError
from wall.llm import LLMGuard
from wall.live import ET, REFRESH_S, STREAM, TEAMS, LiveRun, assign, carries, live_games, matchup, parse_scoreboard, rank, stream_url, team_slug
from wall.presets import PresetStore, clean_url
from wall.server import Cfg, build

FX = Path(__file__).parent / "fixtures" / "wall" / "espn"
NOW = datetime(2026, 11, 1, 14, 0, tzinfo=ET)


def raw(league):
    return json.loads((FX / f"{league}.json").read_text())


def board(league, edit=None):
    d = raw(league)
    if edit:
        edit(d)
    return parse_scoreboard(d, league, NOW)


def event(d, short):
    return next(e for e in d["events"] if e["shortName"] == short)


def url(lg, slug):
    return f"{STREAM}/{lg}/{slug}"


class FakeDriver:
    def __init__(self):
        self.started, self.live, self.stopped, self.sound, self.overlays, self.focused = [], [], 0, 0, [], []
        self.volume = 45

    def start(self, urls): self.started.append(list(urls))
    def stop(self): self.stopped += 1
    def focus(self, i): self.focused.append(i); self.sound = i
    def set_tile(self, i, url): self.live.append(("set", i, url))
    def add_tile(self, url): self.live.append(("add", url))
    def remove_tile(self, i): self.live.append(("rm", i)); self.sound = min(self.sound - (self.sound > i), 3)
    def move_tile(self, i, j): self.live.append(("mv", i, j))
    def overlay(self, view, lbar=False): self.overlays.append((view, lbar))
    def set_volume(self, v): self.volume = max(0, min(100, v)); return self.volume


class FakeScores:
    def __init__(self, data): self.data = data
    def get(self, league, fresh=False): return self.data.get(league)


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def inst(tmp_path, data, presets=None, check=None, headless=None, **kw):
    store = PresetStore(str(tmp_path))
    store.put(presets or [{"name": "Live NFL", "urls": [], "live": "nfl"}])
    clock, d = Clock(), FakeDriver()
    kw.setdefault("sysload", lambda: (1.0, 64000))
    i = Instance("tv", Session("tv"), store, FakeScores(data), driver=d, check=lambda lg, u: (check or (lambda u: True))(u),
                 run=lambda f: f(), clock=clock, headless=headless, **kw)
    return i, d, clock


# ---- presets ---------------------------------------------------------------------------------------------------------------
def test_presets_validate_persist_and_import_the_tvs(tmp_path):
    s = PresetStore(str(tmp_path))
    assert [p["name"] for p in s.presets] == ["Sample", "Live NFL", "Live NBA", "Election Night", "Election + NBA", "Election map + NBA"]
    assert s.put([{"name": "L", "urls": [], "live": "nba", "tiles": 2}]) == [{"name": "L", "urls": [], "live": "nba", "tiles": 2}]
    assert s.put([{"name": "L", "urls": [], "live": "nfl", "tiles": 4}]) == [{"name": "L", "urls": [], "live": "nfl"}]
    for bad in ([{"name": "x", "urls": []}], [{"name": "x", "urls": [], "live": "mlb"}], [{"name": "x", "urls": ["--flag"]}],
                [{"name": "x", "urls": [], "live": "nfl", "tiles": 5}], [{"name": "x", "urls": ["file:///etc/passwd"]}]):
        with pytest.raises(ValidationError):
            s.put(bad)
    assert PresetStore(str(tmp_path)).presets == s.presets
    tv = tmp_path / "tv-wall.json"
    tv.write_text(json.dumps([{"name": "Sunday", "urls": ["https://a.tv"]}]))
    imported = PresetStore(str(tmp_path / "new"), str(tv))
    assert imported.presets[0] == {"name": "Sunday", "urls": ["https://a.tv"]} and imported.presets[1]["name"] == "Election Night" and (tmp_path / "new" / "wall.json").exists()
    assert imported.find("sunday") == imported.find(0) and imported.find(5) is None


def test_clean_url():
    assert clean_url("topstreamer.site/nfl/bills") == "https://topstreamer.site/nfl/bills"
    for bad in ("javascript:alert(1)", "--remote-debugging-port=1", "a b"):
        with pytest.raises(ValueError):
            clean_url(bad)


# ---- live games (from the TV's test_livewall.py) ------------------------------------------------------------------------
def test_live_games_first_close_and_late_on_top_no_finals():
    assert [g["state"] for g in rank(board("nfl"))] == ["in"] * 8

    def late(d):
        event(d, "LV @ NYJ")["status"]["period"] = 4
    r = rank(board("nfl", late))
    assert r[0]["home"]["abbr"] == "NYJ" and r[0]["close"]


def test_upcoming_only_when_nothing_is_live():
    r = rank(board("nba"))
    assert [g["home"]["abbr"] for g in r] == ["ORL", "IND", "GS", "LAL"]
    assert assign([], set(), r, 3) == [g["id"] for g in r[:3]]

    def one_live(d):
        e = event(d, "LAC @ GS")
        e["status"]["type"]["state"] = "in"
        e["status"]["period"] = 2
    r = rank(board("nba", one_live))
    assert [g["home"]["abbr"] for g in r] == ["GS"]
    assert assign(["gone", r[0]["id"]], set(), r, 4) == ["", r[0]["id"]]


def test_every_team_maps_to_its_topstreamer_slug():
    for lg, n in (("nfl", 32), ("nba", 30)):
        teams = json.loads((FX / f"{lg}-teams.json").read_text())["teams"]
        assert len(teams) == n and {team_slug(lg, {"name": t["shortDisplayName"]}) for t in teams} == set(TEAMS[lg])


def test_home_page_unless_only_the_away_one_carries_the_game():
    g = next(g for g in board("nfl")["games"] if g["home"]["abbr"] == "BUF")
    assert stream_url("nfl", g) == url("nfl", "bills")
    assert stream_url("nfl", g, check=lambda u: u.endswith("/ravens")) == url("nfl", "ravens")
    titles = {url("nfl", "bills"): "NFL: Ravens at Bills", url("nfl", "cardinals"): "CARDINALS"}
    assert carries("nfl", url("nfl", "bills"), titles.get) is True
    assert carries("nfl", url("nfl", "cardinals"), titles.get) is False
    assert carries("nfl", url("nfl", "x"), lambda u: 1 / 0) is None
    assert matchup(g) == "BAL 7–10 BUF · Q2"
    assert [x["label"] for x in live_games(board("nfl"), "nfl", 2)] == [matchup(x) for x in rank(board("nfl"))[:2]]


def test_live_plan_follows_live_edits():
    lv = LiveRun("nfl", 4)
    lv.games, lv.pinned = ["a", "b", "c"], {0, 2}
    lv.move(0, 2)
    assert lv.games == ["b", "c", "a"] and lv.pinned == {1, 2}
    lv.remove(1)
    assert lv.games == ["b", "a"] and lv.pinned == {1}
    lv.set_by_hand(0)
    assert lv.games == [None, "a"]


# ---- instances -------------------------------------------------------------------------------------------------------------
def test_start_a_live_preset_fills_tiles_with_the_best_games(tmp_path):
    data = {"nfl": board("nfl")}
    i, d, _ = inst(tmp_path, data, [{"name": "Live NFL", "urls": [], "live": "nfl", "tiles": 3, "overlay": "nfl"}])
    st = i.start(preset="Live NFL")
    best = rank(data["nfl"])[:3]
    assert d.started == [[stream_url("nfl", g) for g in best]] and st["live"]["games"] == [g["id"] for g in best]
    assert st["live"]["labels"] == [matchup(g) for g in best] and st["running"] and st["name"] == "Live NFL"
    assert st["overlay"] == {"board": "nfl", "visible": True, "layout": "lbar", "view": "lbar"} and d.overlays[-1] == ("lbar", True)


def test_a_final_game_swaps_only_its_tile(tmp_path):
    data = {"nfl": board("nfl")}
    i, d, clock = inst(tmp_path, data, [{"name": "Live NFL", "urls": [], "live": "nfl", "tiles": 2}])
    i.start(preset=0)
    clock.t += 5
    i.tick()
    i.tick()
    assert d.live == []
    first, second = list(i.live.games)
    gone = next(e for e in raw("nfl")["events"] if e["id"] == second)["shortName"]

    def final(dd):
        event(dd, gone)["status"]["type"]["state"] = "post"
    data["nfl"] = board("nfl", final)
    i.tick()
    assert d.live == []   # no re-plan before REFRESH_S
    rev = i.rev
    clock.t += REFRESH_S
    i.tick()
    i.tick()
    nxt = rank(data["nfl"])[1]
    assert i.live.games == [first, nxt["id"]] and d.live == [("set", 1, stream_url("nfl", nxt))] and i.rev > rev


def test_pinned_and_by_hand_tiles_never_swap(tmp_path):
    data = {"nfl": board("nfl")}
    i, d, clock = inst(tmp_path, data, [{"name": "Live NFL", "urls": [], "live": "nfl", "tiles": 3}])
    i.start(preset=0)
    games = list(i.live.games)
    assert i.pin(1)["live"]["pinned"] == [1]
    i.set_tile(2, "https://twitch.tv/x")
    assert i.live.games[2] is None and d.live == [("set", 2, "https://twitch.tv/x")]
    with pytest.raises(WallError):
        i.pin(2)

    def all_final(dd):
        for e in dd["events"]:
            if e["id"] in games:
                e["status"]["type"]["state"] = "post"
    data["nfl"] = board("nfl", all_final)
    clock.t += REFRESH_S
    i.tick()
    i.tick()
    assert i.live.games[1:] == [games[1], None] and i.live.games[0] not in games


def test_upcoming_fillers_close_once_a_game_is_live(tmp_path):
    data = {"nba": board("nba")}
    i, d, clock = inst(tmp_path, data, [{"name": "Live NBA", "urls": [], "live": "nba"}])
    i.start(preset=0)

    def tip(dd):
        event(dd, "BOS @ ORL")["status"]["type"]["state"] = "in"
    data["nba"] = board("nba", tip)
    clock.t += REFRESH_S
    i.tick()
    i.tick()
    assert sorted(x for op, x in d.live if op == "rm") == [1, 2, 3] and len(i.urls) == 1


def test_no_games_falls_back_to_fixed_urls_or_refuses(tmp_path):
    i, d, _ = inst(tmp_path, {"nfl": {"games": []}}, [{"name": "Live NFL", "urls": [], "live": "nfl"},
                                                      {"name": "Sunday", "urls": ["https://a.tv"], "live": "nfl"}])
    with pytest.raises(WallError, match="no NFL games"):
        i.start(preset=0)
    assert i.start(preset=1)["urls"] == ["https://a.tv"] and i.live is None


def test_tile_edits_sound_and_move(tmp_path):
    i, d, _ = inst(tmp_path, {})
    with pytest.raises(WallError):
        i.set_tile(0, "https://a.tv")
    i.start(urls=["https://a.tv/1", "https://a.tv/2"])
    assert i.set_tile(2, "https://a.tv/3")["urls"][2] == "https://a.tv/3" and d.live[-1] == ("add", "https://a.tv/3")
    i.set_tile(3, "https://a.tv/4")
    with pytest.raises(WallError):
        i.set_tile(4, "https://a.tv/5")
    with pytest.raises(ValueError):
        i.set_tile(0, "--flag")
    assert i.sound(2)["sound"] == 2
    st = i.move(2, 0)
    assert st["urls"][0] == "https://a.tv/3" and st["sound"] == 0
    st = i.move(1, 3)
    assert st["sound"] == 0 and st["urls"][3] == "https://a.tv/1"
    i.remove_tile(0)
    assert len(i.urls) == 3
    assert not i.stop()["running"] and d.stopped >= 1


def test_board_and_overlay(tmp_path):
    i, d, _ = inst(tmp_path, {"nfl": board("nfl")})
    i.start(urls=["https://a.tv"])
    st = i.set_board("nfl", True, "full")
    assert st["overlay"]["view"] == "full" and d.overlays[-1] == ("full", False)
    assert i.overlay_view()["view"] == "full"
    data = i.overlay_data()
    assert data["shown"] == "nfl" and data["data"]["kind"] == "scores" and {b["id"] for b in data["boards"]} == {"nfl", "nba", "election"}
    assert i.set_board(visible=False)["overlay"]["view"] == ""
    with pytest.raises(WallError):
        i.set_board("fantasy")
    assert i.overlay("matchup", True)["overlay"]["view"] == "matchup" and d.overlays[-1] == ("matchup", True)


class FakeHeadless:
    def __init__(self, tmp): self.hls, self.up, self.calls, self.mode = tmp, True, [], ""
    def start(self): self.calls.append("start"); self.mode = "tiles"; return {"WAYLAND_DISPLAY": "wall-x"}
    def begin_capture(self): self.calls.append("capture")
    def capture_screen(self, output, sink=""): self.calls.append(("screen", output)); self.mode = "screen"
    def stop(self): self.calls.append("stop"); self.mode = ""


def test_headless_idle_auto_stop_and_vram_budget(tmp_path):
    h = FakeHeadless(tmp_path)
    i, d, clock = inst(tmp_path, {}, headless=h, idle_s=600, first_viewer_s=900, max_hours=6, min_free_mb=2000, vram=lambda: 8000)
    st = i.start(urls=["https://a.tv"])
    assert h.calls == ["stop", "start", "capture"] and st["stream"]["hls"] == "/hls/tv/index.m3u8" and i.session.env["WAYLAND_DISPLAY"] == "wall-x"
    clock.t += 800
    i.tick()
    assert i.urls   # nobody yet, but under the first-viewer grace
    i.seen()
    clock.t += 599
    i.tick()
    assert i.urls
    clock.t += 2
    i.tick()
    assert not i.urls and i.state()["stopped"] == "auto-stopped: nobody watching" and h.calls[-1] == "stop"
    i.start(urls=["https://a.tv"])
    for _ in range(25):   # watched all along, but capped at max_hours
        clock.t += 900
        i.seen()
        i.tick()
    assert not i.urls and "ran 6 h" in i.state()["stopped"]
    i.vram = lambda: 500
    with pytest.raises(WallError, match="GPU busy"):
        i.start(urls=["https://a.tv"])


# ---- engine ----------------------------------------------------------------------------------------------------------------
def test_tile_map_and_tile_of():
    aw, ah, rects = tile_map(3)
    assert (aw, ah) == (48, 18) and len(rects) == 3 and TILES["4"]
    tree = {300: 200, 200: 100, 100: 1}
    assert tile_of(300, [50, 100], lambda p: tree.get(p, 0)) == 1 and tile_of(7, [100], lambda p: 0) is None


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
@pytest.mark.parametrize("n,lbar,want", [
    (1, False, [(0, 0, 2560, 1440)]),
    (2, True, [(0, 292, 1040, 585), (1040, 292, 1040, 585)]),
    (3, True, [(8, 198, 1376, 774), (1384, 198, 688, 387), (1384, 585, 688, 387)]),
    (4, True, [(0, 0, 1040, 585), (1040, 0, 1040, 585), (0, 585, 1040, 585), (1040, 585, 1040, 585)]),
])
def test_tile_js_places_windows(n, lbar, want):
    pids = list(range(100, 100 + n))
    fake = """const placed = {}; const KWin = {FullScreenArea: 0};
const workspace = {activeScreen: 0, currentDesktop: 0, activeWindow: null,
  clientArea: () => ({x: 0, y: 0, width: 2560, height: 1440}),
  windowList: () => PIDS.map((pid) => ({pid, set frameGeometry(g) { placed[pid] = [g.x, g.y, g.width, g.height]; }}))};
"""
    js = fake.replace("PIDS", json.dumps(pids + [999])) + tile_js(pids, lbar, 0) + \
        "\nconsole.log(JSON.stringify(PIDS.map((p) => placed[p])));".replace("PIDS", json.dumps(pids))
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    assert [tuple(r) for r in json.loads(out.stdout)] == want


def test_driver_live_edits_relaunch_only_that_tile(monkeypatch):
    launched = []

    class P:
        def __init__(self, cmd, **kw):
            self.cmd, self.env, self.alive, self.pid = cmd, kw.get("env") or {}, True, 500 + len(launched)
            launched.append(self)
        def poll(self): return None if self.alive else 0
        def terminate(self): self.alive = False
        def wait(self, timeout=None): return 0

    monkeypatch.setattr(engine.subprocess, "Popen", P)
    monkeypatch.setattr(engine.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())
    d = Driver(Session("discord", env={"WAYLAND_DISPLAY": "wall-discord", "PULSE_SINK": "wall_discord"}, cdp_base=9241,
                       profiles="/s", prefix="discord-", overlay_api="http://127.0.0.1:7795/i/discord"))
    d.start(["https://a.tv/1", "https://a.tv/2", "https://a.tv/3"])
    tiles = [p for p in launched if p.cmd[0] != "qml6"]
    ov = next(p for p in launched if p.cmd[0] == "qml6")
    port = lambda p: next(a for a in p.cmd if a.startswith("--remote-debugging-port="))
    assert [port(p)[-4:] for p in tiles] == ["9241", "9242", "9243"]
    assert all(p.env["WAYLAND_DISPLAY"] == "wall-discord" and p.env["PULSE_SINK"] == "wall_discord" for p in tiles)
    assert ov.cmd[-1] == "http://127.0.0.1:7795/i/discord" and ov.env["QT_WAYLAND_SHELL_INTEGRATION"] == "layer-shell"
    d.set_tile(1, "https://b.tv/x")
    new = launched[-1]
    assert not tiles[1].alive and tiles[0].alive and "--app=https://b.tv/x" in new.cmd and port(new).endswith("9242")
    assert "--user-data-dir=/s/chromium-wall-discord-1" in new.cmd
    d.remove_tile(0)
    assert [s for _, s in d.tiles] == [1, 2]
    d.add_tile("https://c.tv")
    assert d.tiles[-1][1] == 0 and port(launched[-1]).endswith("9241")
    d.stop()
    assert not any(p.alive for p in launched) and d.tiles == []


# ---- headless plumbing -------------------------------------------------------------------------------------------------------
def test_find_cast_node_only_takes_our_nested_kwins():
    dump = json.dumps([
        {"id": 32, "type": "PipeWire:Interface:Client", "info": {"props": {"application.process.id": 1992}}},   # the TV's kwin
        {"id": 106, "type": "PipeWire:Interface:Client", "info": {"props": {"application.process.id": 5003}}},
        {"id": 90, "type": "PipeWire:Interface:Node", "info": {"props": {"media.name": "kwin-screencast-Virtual-0", "client.id": 32}}},
        {"id": 104, "type": "PipeWire:Interface:Node", "info": {"props": {"media.name": "kwin-screencast-Virtual-0", "client.id": 106}}},
    ])
    tree = {5003: 5001, 5001: 5000, 1992: 1}
    assert find_cast_node(5000, dump, lambda p: tree.get(p, 0)) == 104
    assert find_cast_node(7777, dump, lambda p: tree.get(p, 0)) is None


def test_capture_commands(tmp_path):
    g = gst_cmd(104, "wall_discord")
    assert "path=104" in g and "nvh264enc" in g and "gop-size=30" in g and "target-object=wall_discord" in g and g[-1] == "fd=1"
    f = ffmpeg_cmd(tmp_path)
    assert "program_date_time" in f[f.index("-hls_flags") + 1] and f[f.index("-hls_time") + 1] == "1" and f[-1].endswith("index.m3u8")
    h = Headless("discord", tmp_path)
    assert h.sink == "wall_discord" and h.hls == tmp_path / "hls" / "discord" and not h.up


# ---- the HTTP API ----------------------------------------------------------------------------------------------------------
@pytest.fixture
def api(tmp_path, monkeypatch):
    fake_dns = {"a.tv": "93.184.216.34", "b.tv": "93.184.216.35", "lan.example": "192.168.1.1", "ts.example": "100.81.31.34", "localhost": "127.0.0.1"}
    monkeypatch.setattr("wall.server.resolve", lambda h: [(2, 1, 6, "", (fake_dns.get(h) or h, 0))])
    monkeypatch.setattr("wall.server.tv_active", lambda api: None)
    cfg = Cfg({"WALL_STATE_DIR": str(tmp_path), "WALL_API_TOKEN": "s3cret", "WALL_IMPORT_FROM": "", "WALL_TV_OUTPUT": ""})
    store = PresetStore(str(tmp_path))
    data = {"nfl": board("nfl")}
    tv = Instance("tv", Session("tv"), store, FakeScores(data), driver=FakeDriver(), run=lambda f: None)
    h = FakeHeadless(tmp_path / "hls")
    (tmp_path / "hls").mkdir()
    disc = Instance("discord", Session("discord"), store, FakeScores(data), driver=FakeDriver(), run=lambda f: None, headless=h,
                    vram=lambda: None, sysload=lambda: (1.0, 64000))
    app = build(cfg, {"tv": tv, "discord": disc}, FakeScores(data), store, guard=LLMGuard(mode="off"))
    return app, tmp_path


def client(app, ip="127.0.0.1", token=None):
    return TestClient(app, base_url="http://127.0.0.1:7795", client=(ip, 5555),
                      headers={"Authorization": f"Bearer {token}"} if token else {})


def test_api_gate(api):
    app, _ = api
    assert client(app, "203.0.113.9", "s3cret").get("/i/discord").status_code == 403   # not loopback, not the tailnet
    assert client(app, "100.81.31.34").get("/i/discord").status_code == 401
    assert client(app, "100.81.31.34", "nope").get("/i/discord").status_code == 401
    remote = client(app, "100.81.31.34", "s3cret")
    assert remote.get("/i/discord").status_code == 200 and remote.get("/presets").status_code == 200
    assert remote.get("/i/tv").status_code == 403 and remote.post("/i/tv/stop").status_code == 403   # the TV is loopback-only
    assert remote.put("/presets", json=[]).status_code == 403
    assert client(app).get("/i/tv").status_code == 200


def test_remote_may_get_exactly_the_election_dashboards(api):
    """The watch page's pop-out (web/wall.py POP_FILE): the dashboards, their assets + data, read-only, with the bearer only."""
    from wall.server import REMOTE_OK
    app, _ = api
    remote = client(app, "100.81.31.34", "s3cret")
    for path, ctype in [("/p/election/map", "text/html"), ("/p/election/swing", "text/html"), ("/p/election/election.css", "text/css"),
                        ("/p/election/election.js", "text/javascript"), ("/p/us-states.json", "application/json")]:
        r = remote.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith(ctype), path
    for f in ("data", "odds", "turnout", "exitpoll", "swing"):
        assert REMOTE_OK.match(f"/p/election/{f}.json")
    assert client(app, "100.81.31.34").get("/p/election/map").status_code == 401          # no bearer
    assert client(app, "100.81.31.34", "nope").get("/p/election/map").status_code == 401
    assert client(app, "203.0.113.9", "s3cret").get("/p/election/map").status_code == 403  # off the tailnet
    for path in ("/p/election/board.json", "/p/election/map.html", "/p/election/other", "/p/election/", "/p/elsewhere.html",
                 "/p/election/map/x", "/p/us-states.jsonx"):
        assert remote.get(path).status_code == 403, path
    assert remote.post("/p/election/data.json").status_code == 403
    assert remote.put("/p/election/map").status_code == 403


def test_loopback_refuses_web_pages(api):
    """A tile page on this box can POST to 127.0.0.1 without CORS (text/plain) or via DNS rebinding: refuse browsers."""
    app, _ = api
    c = client(app)
    body = {"urls": ["https://a.tv"]}
    assert c.post("/i/tv/start", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/i/tv/start", content='{"urls": ["https://a.tv"]}', headers={"Content-Type": "text/plain",
                  "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.get("/i/tv", headers={"Host": "rebind.evil.example:7795"}).status_code == 403
    assert c.get("/i/tv", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.get("/i/tv", headers={"Host": "localhost:7795"}).status_code == 200
    assert c.post("/i/tv/start", json=body).json()["running"]


def test_discord_tiles_must_be_public(api):
    app, _ = api
    c = client(app)
    for bad in ("http://127.0.0.1:7790/", "http://localhost:9241/json", "http://192.168.1.1/", "http://lan.example/",
                "http://ts.example/", "http://[::1]/", "http://169.254.169.254/", "file:///etc/passwd"):
        assert c.post("/i/discord/start", json={"urls": [bad]}).status_code == 422, bad
    assert c.post("/i/discord/start", json={"urls": ["https://a.tv"]}).json()["running"]
    assert c.post("/i/discord/tile", json={"i": 1, "url": "http://192.168.1.1/"}).status_code == 422
    assert c.post("/i/tv/start", json={"urls": ["http://192.168.1.50:8096/"]}).json()["running"]   # the TV may show LAN pages
    assert c.post("/i/discord/stop").status_code == 200


def test_api_flow(api):
    app, tmp = api
    c = client(app)
    assert c.put("/presets", json=[{"name": "x", "urls": []}]).status_code == 422
    assert c.put("/presets", json=[{"name": "Two", "urls": ["https://a.tv/1", "https://a.tv/2"]}]).json()[0]["name"] == "Two"
    st = c.post("/i/discord/start", json={"preset": "Two", "board": "nfl"}).json()
    assert st["running"] and st["urls"] == ["https://a.tv/1", "https://a.tv/2"] and st["overlay"]["board"] == "nfl"
    assert c.post("/i/discord/tile", json={"i": 0, "url": "javascript:x"}).status_code == 422
    assert c.post("/i/discord/tile", json={"i": 9, "url": "https://b.tv"}).status_code == 409
    assert c.post("/i/discord/tile", json={"i": 2, "url": "https://b.tv"}).json()["urls"][2] == "https://b.tv"
    assert c.post("/i/discord/sound", json={"i": 2}).json()["sound"] == 2
    assert c.post("/i/discord/move", json={"i": 2, "j": 0}).json()["urls"][0] == "https://b.tv"
    assert c.delete("/i/discord/tile/1").json()["urls"] == ["https://b.tv", "https://a.tv/2"]
    assert c.post("/i/discord/board", json={"board": "nba", "visible": True, "layout": "full"}).json()["overlay"]["view"] == "full"
    assert c.get("/i/discord/api/overlay/view").json()["view"] == "full"
    assert c.get("/i/nope").status_code == 404
    assert len(c.get("/live/nfl?n=2").json()) == 2 and c.get("/live/mlb").status_code == 404
    assert "bills" in c.get("/teams").json()["nfl"]
    # HLS: only the playlist / segment names, and a playlist fetch counts as a viewer
    (tmp / "hls" / "index.m3u8").write_text("#EXTM3U\n")
    (tmp / "hls" / "seg000001.ts").write_bytes(b"\x47" * 188)
    r = c.get("/hls/discord/index.m3u8")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and app.state.instances["discord"].last_fetch > 0
    assert c.get("/hls/discord/seg000001.ts").content[:1] == b"\x47"
    assert c.get("/hls/discord/..%2Fwall.json").status_code == 404 and c.get("/hls/tv/index.m3u8").status_code == 404
    assert not c.post("/i/discord/stop").json()["running"]


def test_discord_wall_starts_only_while_the_tv_is_idle(api, monkeypatch):
    app, _ = api
    c = client(app, "100.81.31.34", "s3cret")
    monkeypatch.setattr("wall.server.tv_active", lambda api: "football")
    r = c.post("/i/discord/start", json={"urls": ["https://a.tv"]})
    assert r.status_code == 409 and "TV is in use" in r.json()["error"]
    monkeypatch.setattr("wall.server.tv_active", lambda api: None)
    assert c.post("/i/discord/start", json={"urls": ["https://a.tv"]}).json()["running"]
    monkeypatch.setattr("wall.server.tv_active", lambda api: "football")   # already running: changes still go through
    assert c.post("/i/discord/start", json={"urls": ["https://b.tv"]}).status_code == 200


def test_discord_wall_starts_past_an_app_left_open_on_a_tv_that_is_off(api, monkeypatch):
    app, _ = api
    c = client(app, "100.81.31.34", "s3cret")
    monkeypatch.setattr("wall.server.tv_active", lambda api: "football")
    monkeypatch.setattr("wall.server.tv_off", lambda api: True)
    assert c.post("/i/discord/start", json={"urls": ["https://a.tv"]}).json()["running"]


def test_a_bare_discord_start_with_nothing_to_mirror_asks_for_live_nba(api, monkeypatch):
    app, _ = api
    c = client(app, "100.81.31.34", "s3cret")
    monkeypatch.setattr("wall.server.tv_active", lambda api: None)
    asked = []
    monkeypatch.setattr("wall.instance.Instance.start", lambda self, **k: asked.append(k) or self.state())
    assert c.post("/i/discord/start", json={"mirror": True}).status_code == 200
    assert asked[0]["live"] == "nba" and asked[0]["tiles"] == 4


def test_tv_off_reads_healthz_and_an_unreachable_tv_is_not_off(monkeypatch):
    import io, json
    from wall import server
    answers = [{"tv_power": "off"}, {"tv_power": "on"}, {"tv_power": "unknown"}]
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=0: io.BytesIO(json.dumps(answers.pop(0)).encode()))
    assert [server.tv_off("http://tv") for _ in range(3)] == [True, False, False]

    def down(url, timeout=0):
        raise OSError("no route")
    monkeypatch.setattr("urllib.request.urlopen", down)
    assert server.tv_off("http://tv") is False


def test_discord_wall_mirrors_the_tv_wall_when_the_tv_is_on_it(api, monkeypatch):
    app, _ = api
    local, remote = client(app), client(app, "100.81.31.34", "s3cret")
    assert local.post("/i/tv/start", json={"urls": ["https://a.tv", "https://b.tv"], "board": "nfl"}).json()["running"]
    monkeypatch.setattr("wall.server.tv_active", lambda api: "wall")
    st = remote.post("/i/discord/start", json={"urls": ["https://b.tv"]}).json()   # asked for b.tv: gets the TV's tiles
    assert st["urls"] == ["https://a.tv", "https://b.tv"] and st["overlay"]["board"] == "nfl"
    remote.post("/i/discord/stop")
    monkeypatch.setattr("wall.server.tv_active", lambda api: "casino")
    assert remote.post("/i/discord/start", json={"urls": ["https://a.tv"]}).status_code == 409


# ---- crash-storm guards ------------------------------------------------------------------------------------------------------
class FakeProc:
    def __init__(self, exits_on_close=True):
        self.code, self.exits_on_close, self.signals, self.pid = None, exits_on_close, [], 4242
    def poll(self): return self.code
    def wait(self, timeout=None):
        if self.code is None:
            raise subprocess.TimeoutExpired("chromium", timeout)
        return self.code
    def terminate(self): self.signals.append("TERM"); self.code = -15
    def kill(self): self.signals.append("KILL"); self.code = -9


def test_tiles_close_through_cdp_before_any_signal(monkeypatch):
    polite, stuck = FakeProc(), FakeProc(exits_on_close=False)
    closed = []
    def close(port):
        closed.append(port)
        if port == 9241:
            polite.code = 0
        return True
    monkeypatch.setattr(engine, "cdp_close", close)
    engine.close_tiles([(polite, 0), (stuck, 2), (None, 3)], 9241, grace=0.05)
    assert closed == [9241, 9243] and polite.signals == [] and stuck.signals == ["TERM"]


def test_nested_session_bus_activates_nothing():
    conf = (Path(engine.__file__).parent / "nested-bus.conf").read_text()
    assert "<servicedir" not in conf and "standard_session_servicedirs" not in conf and "<type>session</type>" in conf
    import wall.headless as hl
    assert hl.QUIET_ENV["GTK_USE_PORTAL"] == "0" and "--password-store=basic" in engine.QUIET_FLAGS
    assert "VaapiOnNvidiaGPUs" in engine.GPU_FLAGS[0] and "VaapiVideoDecoder" in engine.GPU_FLAGS[0]


# ---- stall watchdog --------------------------------------------------------------------------------------------------------
def test_stall_watchdog_jumps_gaps_then_the_edge_then_reloads():
    st = engine.Stall()
    gap = {"t": 333.8, "paused": False, "ended": False, "ready": 1, "buffered": [[348.1, 354.0]], "seekable_end": 360.0}
    assert engine.stall_action(st, gap, 0) is None          # first look
    assert engine.stall_action(st, gap, 2) is None          # not 3 s yet
    assert engine.stall_action(st, gap, 3.5) == ("seek", pytest.approx(348.4))
    playing = {**gap, "t": 348.5}
    assert engine.stall_action(st, playing, 4) is None and st.since == 4   # moving again: reset
    stuck = {**gap, "t": 350.0, "buffered": [[340.0, 350.0]], "seekable_end": 362.0}
    assert engine.stall_action(st, stuck, 5) is None
    assert engine.stall_action(st, stuck, 9) is None                 # nothing buffered ahead: wait for 10 s
    assert engine.stall_action(st, stuck, 15.5) == ("seek", 359.0)   # live edge − 3, once
    assert engine.stall_action(st, stuck, 20) is None
    assert engine.stall_action(st, stuck, 35.5) == ("reload", 0.0)
    assert engine.stall_action(st, {**stuck, "paused": True}, 40) is None and st.t == -1   # paused by the user: not a stall
    assert engine.stall_action(st, None, 41) is None


class FakeCdp:
    """A fake tile: answers Runtime.evaluate (fullscreen, probe, seek) and Page.reload."""
    def __init__(self, probe):
        self.probe, self.calls = probe, []

    def __call__(self, port):
        from contextlib import contextmanager

        @contextmanager
        def cm():
            def call(method, params=None):
                expr = (params or {}).get("expression", "")
                self.calls.append((method, expr[:40]))
                if expr == engine.PROBE_JS:
                    return {"result": {"value": self.probe}}
                if "currentTime =" in expr:
                    self.probe = {**self.probe, "t": float(expr.split("currentTime = ")[1].split(";")[0])}
                return {"result": {"value": True}}
            yield call
        return cm()


def test_stall_watchdog_over_cdp(monkeypatch):
    fake = FakeCdp({"t": 333.8, "paused": False, "ended": False, "ready": 1, "buffered": [[348.1, 354.0]], "seekable_end": 360.0})
    monkeypatch.setattr(engine, "cdp_page", fake)
    t = [100.0]
    monkeypatch.setattr(engine.time, "monotonic", lambda: t[0])
    d = Driver(Session("tv", cdp_base=9231))
    d._tend(3)
    t[0] += 4
    d._tend(3)
    assert fake.probe["t"] == pytest.approx(348.4) and any("currentTime" in e or m == "Runtime.evaluate" for m, e in fake.calls)
    fake.probe = {**fake.probe, "buffered": [], "seekable_end": 0}
    for _ in range(20):
        t[0] += 2
        d._tend(3)
    assert ("Page.reload", "") in fake.calls


# ---- mirroring the TV ------------------------------------------------------------------------------------------------------
def _mirror(api, monkeypatch, screen=False):
    app, _ = api
    local, remote = client(app), client(app, "100.81.31.34", "s3cret")
    tv, disc = app.state.instances["tv"], app.state.instances["discord"]
    disc.capture_output = "HDMI-A-1" if screen else ""
    assert local.post("/i/tv/start", json={"urls": ["https://a.tv/1", "https://b.tv/2"], "board": "nfl"}).json()["running"]
    monkeypatch.setattr("wall.server.tv_active", lambda api: "wall")
    st = remote.post("/i/discord/start", json={"mirror": True, "origin": {"guild": "1", "channel": "22"}}).json()
    assert st["mirror"] and st["origin"] == {"guild": "1", "channel": "22"} and st["name"] == "Mirroring the TV"
    return local, remote, tv, disc, st


def test_mirror_follows_the_tv_live(api, monkeypatch):
    local, remote, tv, disc, st = _mirror(api, monkeypatch)
    assert st["urls"] == ["https://a.tv/1", "https://b.tv/2"] and st["capture"] == "tiles" and st["overlay"]["board"] == "nfl"
    local.post("/i/tv/tile", json={"i": 1, "url": "https://b.tv/3"})       # swap
    local.post("/i/tv/tile", json={"i": 2, "url": "https://a.tv/4"})       # add
    local.post("/i/tv/sound", json={"i": 2})
    local.post("/i/tv/board", json={"board": "nba", "visible": True, "layout": "full"})
    disc.tick()
    st = remote.get("/i/discord").json()
    assert st["urls"] == ["https://a.tv/1", "https://b.tv/3", "https://a.tv/4"] and st["sound"] == 2 and st["mirror"]
    assert st["overlay"]["board"] == "nba" and st["overlay"]["layout"] == "full" and st["overlay"]["visible"]
    assert ("set", 1, "https://b.tv/3") in disc.driver.live and ("add", "https://a.tv/4") in disc.driver.live
    local.post("/i/tv/move", json={"i": 2, "j": 0})                        # a reorder moves tiles, never reloads them
    disc.driver.live.clear()
    disc.tick()
    assert remote.get("/i/discord").json()["urls"][0] == "https://a.tv/4" and all(x[0] == "mv" for x in disc.driver.live)
    local.delete("/i/tv/tile/0")                                           # remove
    disc.tick()
    assert remote.get("/i/discord").json()["urls"] == ["https://a.tv/1", "https://b.tv/3"]
    local.post("/i/tv/stop")                                               # the TV stops: the stream keeps its last tiles
    disc.tick()
    st = remote.get("/i/discord").json()
    assert st["running"] and st["urls"] == ["https://a.tv/1", "https://b.tv/3"] and st["mirror"]


def test_a_change_on_the_stream_changes_the_tv_and_stays_in_sync(api, monkeypatch):
    local, remote, tv, disc, _ = _mirror(api, monkeypatch)
    st = remote.post("/i/discord/tile", json={"i": 0, "url": "https://b.tv/9"}).json()   # a vote that passed
    assert st["mirror"] and tv.urls == ["https://b.tv/9", "https://b.tv/2"]              # the TV changed; still mirroring
    remote.post("/i/discord/sound", json={"i": 1})
    remote.post("/i/discord/board", json={"board": "nba", "visible": True})
    assert tv.driver.sound == 1 and tv.board == "nba"
    disc.tick()
    st = remote.get("/i/discord").json()
    assert st["mirror"] and st["urls"] == ["https://b.tv/9", "https://b.tv/2"] and st["sound"] == 1
    assert remote.post("/i/discord/tile", json={"i": 0, "url": "http://192.168.1.1/cam"}).status_code == 422   # still no LAN pages
    assert tv.urls[0] == "https://b.tv/9"

def test_mirror_skips_tiles_that_may_not_be_streamed(api, monkeypatch):
    local, remote, tv, disc, _ = _mirror(api, monkeypatch)
    local.post("/i/tv/tile", json={"i": 0, "url": "http://192.168.1.1/cam"})   # a LAN page on the TV
    local.post("/i/tv/sound", json={"i": 1})
    disc.tick()
    st = remote.get("/i/discord").json()
    assert st["urls"] == ["https://b.tv/2"] and st["sound"] == 0 and "192.168.1.1" in st["notice"]["text"] and st["mirror"]
    n = st["notice"]["id"]
    local.post("/i/tv/sound", json={"i": 0})
    disc.tick()
    assert remote.get("/i/discord").json()["notice"]["id"] == n   # the same refusal isn't announced again


def test_mirror_start_with_only_refused_tiles_fails_cleanly(api, monkeypatch):
    app, _ = api
    local, remote = client(app), client(app, "100.81.31.34", "s3cret")
    local.post("/i/tv/start", json={"urls": ["http://192.168.1.50:8096/"]})
    monkeypatch.setattr("wall.server.tv_active", lambda api: "wall")
    r = remote.post("/i/discord/start", json={"mirror": True})
    assert r.status_code == 409 and "can be streamed" in r.json()["error"]
    monkeypatch.setattr("wall.server.tv_active", lambda api: None)
    r = remote.post("/i/discord/start", json={"mirror": True})
    assert r.status_code == 409 and "no NBA games" in r.json()["error"]   # nothing to mirror: it tries tonight's NBA instead


def test_mirror_streams_the_tvs_screen_and_leaves_with_it(api, monkeypatch):
    local, remote, tv, disc, st = _mirror(api, monkeypatch, screen=True)
    assert st["capture"] == "screen" and ("screen", "HDMI-A-1") in disc.headless.calls and not disc.driver.started
    local.post("/i/tv/tile", json={"i": 1, "url": "https://b.tv/3"})
    disc.tick()
    st = remote.get("/i/discord").json()
    assert st["urls"][1] == "https://b.tv/3" and st["capture"] == "screen" and not disc.driver.live   # state only: no tiles
    local.post("/i/tv/tile", json={"i": 0, "url": "http://192.168.1.1/"})   # a page we may not stream: our own tiles, without it
    disc.tick()
    st = remote.get("/i/discord").json()
    assert st["capture"] == "tiles" and st["urls"] == ["https://b.tv/3"] and st["mirror"]
    local.post("/i/tv/tile", json={"i": 0, "url": "https://a.tv/1"})       # fine again: back to the TV's screen
    disc.tick()
    assert remote.get("/i/discord").json()["capture"] == "screen"
    disc.seen()                                                             # someone watches; the TV leaves the wall
    monkeypatch.setattr("wall.server.tv_active", lambda api: "casino")
    disc._wall_at = 0
    disc.tick()
    st = remote.get("/i/discord").json()
    assert st["running"] and st["capture"] == "tiles" and st["urls"] == ["https://a.tv/1", "https://b.tv/3"]
    assert "left the wall" in st["notice"]["text"]


def test_screen_mirror_stops_when_the_tv_leaves_and_nobody_watches(api, monkeypatch):
    local, remote, tv, disc, _ = _mirror(api, monkeypatch, screen=True)
    monkeypatch.setattr("wall.server.tv_active", lambda api: "football")
    disc._wall_at = 0
    disc.tick()
    st = remote.get("/i/discord").json()
    assert not st["running"] and "left the wall" in st["stopped"]


def test_a_vote_on_the_screen_mirror_changes_the_tv_and_keeps_capturing_it(api, monkeypatch):
    local, remote, tv, disc, _ = _mirror(api, monkeypatch, screen=True)
    st = remote.post("/i/discord/sound", json={"i": 1}).json()
    assert st["capture"] == "screen" and st["mirror"] and tv.driver.sound == 1


def test_resource_guard(tmp_path):
    h = FakeHeadless(tmp_path)
    load = [20.0, 64000]
    i, d, clock = inst(tmp_path, {}, headless=h, sysload=lambda: tuple(load))
    with pytest.raises(WallError, match="busy"):
        i.start(urls=["https://a.tv"])
    load[:] = [2.0, 1000]
    with pytest.raises(WallError, match="memory"):
        i.start(urls=["https://a.tv"])
    load[:] = [2.0, 8000]
    assert i.start(urls=["https://a.tv"])["running"]
    i.seen()
    load[:] = [30.0, 8000]
    i.tick()
    clock.t += 20
    i.tick()
    assert i.urls   # a spike under 30 s is tolerated
    clock.t += 15
    i.seen()
    i.tick()
    assert not i.urls and "overloaded" in i.stopped_why


# ---- the local LLM steps aside ---------------------------------------------------------------------------------------------
def test_llm_guard_parks_models_while_a_wall_runs_and_never_warms_them_back():
    loaded = ["gemma4:12b"]
    calls, ctl = [], []
    def call(base, path, body=None, timeout=0):
        calls.append((path, body))
        if path == "/api/ps":
            return {"models": [{"name": m} for m in loaded]}
        if body and body.get("keep_alive") == 0:
            loaded.remove(body["model"])
        return {}
    g = LLMGuard("http://x:11434", call=call, systemctl=ctl.append)
    g.tick(True)
    assert loaded == [] and g.parked == ["gemma4:12b"]
    loaded.append("gemma4:12b")   # Sage called mid-wall: it reloads, the next tick evicts it again
    g.tick(True)
    assert loaded == [] and g.parked == ["gemma4:12b"] and ctl == []
    n = len(calls)
    g.tick(False)
    assert g.parked == [] and calls[n:] == []   # no warm-back: the next real request loads it
    s = LLMGuard(call=lambda *a, **k: {"models": []}, systemctl=ctl.append, mode="stop")
    s.tick(True); s.tick(True); s.tick(False)
    assert ctl == ["stop", "start"]


def test_a_start_waits_for_the_llm_to_leave_the_gpu(tmp_path):
    frees = iter([2259, 2259, 10886])   # Ollama answers the unload before the memory is back
    i, d, clock = inst(tmp_path, {}, headless=FakeHeadless(tmp_path), min_free_mb=2500, gpu_wait_s=5, vram=lambda: next(frees))
    parks = []
    i.before_start = lambda: parks.append(1)
    assert i.start(urls=["https://a.tv"])["urls"] == ["https://a.tv"]
    assert len(parks) == 3


def test_a_start_still_refuses_when_the_gpu_stays_full(tmp_path):
    i, d, clock = inst(tmp_path, {}, headless=FakeHeadless(tmp_path), min_free_mb=2500, gpu_wait_s=0.3, vram=lambda: 500)
    with pytest.raises(WallError, match="GPU busy: 500 MB"):
        i.start(urls=["https://a.tv"])


def test_the_guard_brings_ollama_back_after_a_service_restart():
    ctl = []
    g = LLMGuard(call=lambda *a, **k: {"models": []}, systemctl=ctl.append, mode="stop")
    g.tick(False); g.tick(False)
    assert ctl == ["start"]
    g.tick(True); g.tick(True)   # stopped: no more /api/ps calls or stops
    assert ctl == ["start", "stop"]


def test_the_guard_runs_before_a_headless_start(tmp_path):
    order = []
    h = FakeHeadless(tmp_path)
    i, d, clock = inst(tmp_path, {}, headless=h, vram=lambda: order.append("vram") or 9000)
    i.before_start = lambda: order.append("park")
    i.start(urls=["https://a.tv"])
    assert order == ["park", "vram"]


def test_switching_between_screen_and_tiles_is_one_session(api, monkeypatch):
    local, remote, tv, disc, st = _mirror(api, monkeypatch, screen=True)
    started, origin = st["started"], st["origin"]
    local.post("/i/tv/tile", json={"i": 0, "url": "http://192.168.1.1/"})
    disc.tick()
    st = remote.get("/i/discord").json()
    assert st["capture"] == "tiles" and st["started"] == started and st["origin"] == origin   # the bot won't re-announce


# ---- the sound tile's volume + the YouTube CSS pin -------------------------------------------------------------------------
def _sink(idx, pid, pct, mute):
    return {"index": idx, "mute": mute, "properties": {"application.process.id": str(pid)},
            "volume": {"front-left": {"value_percent": f"{pct}%"}, "front-right": {"value_percent": f"{pct}%"}}}


def test_sound_tile_plays_at_the_wall_volume(monkeypatch):
    assert engine.tile_volume({}) == 45 and engine.tile_volume({"WALL_TILE_VOLUME": "30%"}) == 30
    assert engine.tile_volume({"WALL_TILE_VOLUME": "loud"}) == 45 and engine.tile_volume({"WALL_TILE_VOLUME": "250"}) == 100
    sinks = [_sink(7, 101, 100, True), _sink(8, 102, 100, False)]
    ran = []
    monkeypatch.setattr(engine, "sink_inputs", lambda env: sinks)
    monkeypatch.setattr(engine.subprocess, "run", lambda cmd, **kw: ran.append(cmd[1:]))
    d = Driver(Session("tv"))
    d.tiles = [(type("P", (), {"pid": 101})(), 0), (type("P", (), {"pid": 102})(), 1)]
    d.sound = 0
    d._mute(d.pids())     # tile 0 (pid 101) gets the sound at 45 %, tile 1 is muted and keeps its level
    assert ran == [["set-sink-input-volume", "7", "45%"], ["set-sink-input-mute", "7", "0"], ["set-sink-input-mute", "8", "1"]]
    ran.clear()
    sinks[:] = [_sink(7, 101, 45, False), _sink(8, 102, 100, True)]
    d._mute(d.pids())
    assert ran == []      # already right: no pactl churn every round
    assert d.set_volume(60) == 60 and ran == [["set-sink-input-volume", "7", "60%"]]


def test_volume_over_the_api(api):
    app, _ = api
    c = client(app)
    assert c.get("/i/tv").json()["volume"] == 45
    assert c.post("/i/tv/volume", json={"delta": 5}).json()["volume"] == 50
    assert c.post("/i/tv/volume", json={"delta": -100}).json()["volume"] == 0
    assert c.post("/i/tv/volume", json={"volume": 35}).json()["volume"] == 35
    assert c.post("/i/tv/volume", json={}).status_code == 422 and c.post("/i/tv/volume", json={"delta": "up"}).status_code == 422


def test_css_pin_flattens_every_stacking_context_above_the_video():
    """YouTube's #player-container has view-transition-name and .html5-video-container z-index 10: a pinned video under
    them stayed beneath the masthead / sidebar / comments (NBC News NOW, election night). Every ancestor gets flattened."""
    js = engine.FULLSCREEN_JS
    assert "setAttribute('data-tvfs-a'" in js and "return flat();" in js and "return flat();        // already pinned" in js
    rule = js.split("[data-tvfs-a]{")[1].split("}")[0]
    for prop in ("z-index:auto", "view-transition-name:none", "transform:none", "contain:none", "isolation:auto", "filter:none",
                 "will-change:auto", "opacity:1"):
        assert prop + "!important" in rule, prop


def test_stream_gain_and_artifact_tiles():
    from wall.engine import ARTIFACT
    assert "volume=2" in gst_cmd(104, "wall_discord", gain=2.0) and "volume=1" in gst_cmd(104, "wall_discord")
    assert ARTIFACT.match("https://claude.ai/artifact/5wcVpgfhN4J5hggdCeJy4S") and not ARTIFACT.match("https://www.youtube.com/@x/live")


# ---- the TV chat overlay toggle (qml/ChatFloat.qml): owner's switch, kept across restarts, in /bands + the state ----
def test_tvchat_toggle_persists_and_reaches_the_overlay(api):
    import time as _time
    app, tmp = api
    c = client(app)
    assert c.get("/i/discord").json()["tvchat"] == {"on": True}            # default on
    b = c.get("/i/tv/bands").json()["tvchat"]
    assert b["on"] is True and b["lines"] == []                            # no party: no lines
    assert c.post("/i/discord/tvchat", json={"on": "no"}).status_code == 422
    remote = client(app, "100.81.31.34", "s3cret")                         # the web, over the tailnet
    assert remote.post("/i/discord/tvchat", json={"on": False}).json()["tvchat"] == {"on": False}
    assert (tmp / "tvchat-on").read_text() == "0"
    assert c.get("/i/tv/bands").json()["tvchat"]["on"] is False and c.get("/i/tv").json()["tvchat"]["on"] is False
    # a restart reads the file back
    cfg = Cfg({"WALL_STATE_DIR": str(tmp), "WALL_API_TOKEN": "s3cret", "WALL_IMPORT_FROM": "", "WALL_TV_OUTPUT": ""})
    app2 = build(cfg, app.state.instances, app.state.scores, app.state.store, guard=LLMGuard(mode="off"))
    assert client(app2).get("/i/tv/bands").json()["tvchat"]["on"] is False
    # the lines: the party's last eight, fresh = arrived in the last 20 s
    chat = app.state.chat
    chat.configured = True
    now = _time.time()
    chat.messages = [{"id": k, "ts": now - (100 if k < 5 else 1), "kind": "system" if k == 8 else "chat", "name": f"u{k}",
                      "guest": False, "text": f"t{k}"} for k in range(1, 10)]
    app.state.instances["discord"].urls = ["https://a.tv/x"]
    lines = c.get("/i/tv/bands").json()["tvchat"]["lines"]
    assert [m["id"] for m in lines] == [2, 3, 4, 5, 6, 7, 8, 9] and [m["fresh"] for m in lines] == [False] * 3 + [True] * 5
    assert lines[6]["sys"] is True and lines[7] == {"id": 9, "name": "u9", "guest": False, "text": "t9", "sys": False, "fresh": True}


def test_the_encoder_runs_on_its_own_thread():
    from wall.headless import ENCODER_QUEUE, gst_cmd
    cmd = gst_cmd(1, "s")
    enc = cmd.index("nvh264enc")
    assert cmd[enc - len(ENCODER_QUEUE) - 1:enc] == [*ENCODER_QUEUE, "!"]


def test_a_url_goes_on_the_wall_once(api):
    app, _ = api
    c = client(app)
    assert c.post("/i/discord/start", json={"urls": ["https://a.tv/1", "https://a.tv/1", "https://a.tv/2"]}).json()["urls"] == \
        ["https://a.tv/1", "https://a.tv/2"]
    assert c.post("/i/discord/tile", json={"i": 2, "url": "https://a.tv/1"}).status_code == 409   # add
    assert c.post("/i/discord/tile", json={"i": 1, "url": "https://a.tv/1"}).status_code == 409   # swap
    assert c.post("/i/discord/tile", json={"i": 1, "url": "https://a.tv/2"}).status_code == 200   # its own tile: a reload
    assert c.delete("/i/discord/tile/0").json()["urls"] == ["https://a.tv/2"]
    assert c.post("/i/discord/tile", json={"i": 1, "url": "https://a.tv/1"}).status_code == 200   # gone, so it may come back


def test_a_game_goes_on_the_wall_once(tmp_path):
    def team(name): return {"name": name, "abbr": name[:3].upper()}
    game = lambda state: {"state": state, "home": team("Warriors"), "away": team("Heat")}
    heat, gsw = "https://topstreamer.site/nba/heat", "https://topstreamer.site/nba/warriors"
    data = {"nba": {"games": [game("in")]}}
    i = Instance("discord", Session("discord"), PresetStore(str(tmp_path)), FakeScores(data), driver=FakeDriver(), run=lambda f: None)
    i.urls = [gsw, "https://a.tv"]
    with pytest.raises(WallError, match="already on the wall"):
        i.set_tile(2, heat)   # the other team's page: the same game
    assert i.set_tile(0, heat)["urls"][0] == heat   # another feed for that tile: fine
    data["nba"]["games"] = [game("post")]
    assert i.set_tile(2, gsw)["urls"][2] == gsw   # that game is over: these pages are about the next one


def test_encoder_vbv_holds_two_frames():
    assert "vbv-buffer-size=116" in gst_cmd(97, "sink", kbps=3500, fps=60)
