"""Data tiles: a tile can be one of the wall service's own pages (/p/…, served on loopback), not only a website. They have
no video, so the engine leaves them alone (no fullscreen, no stall watchdog → no reload loop) and never gives them the
sound; the discord wall may stream them; a "fixed" live preset keeps its pages and fills the rest with games."""
import pytest

import wall.engine as engine
from wall.engine import Driver, Session, own_page, page_url, sound_tile
from wall.presets import ELECTION_SEED, PresetStore

from tests.test_wall_service import FakeCdp, api, board, client, inst  # noqa: F401 — the api fixture
from wall.live import rank, stream_url

MAP, RACES = page_url("election/map"), page_url("election/races")


def test_own_page():
    assert own_page(MAP) and own_page("http://localhost:7795/p/election/races?x=1")
    for u in ("http://127.0.0.1:7790/p/x", "https://127.0.0.1:7795/p/x", "http://127.0.0.1:7795/i/tv", "http://a.tv:7795/p/x",
              "https://www.youtube.com/@NBCNews/live", "not a url"):
        assert not own_page(u), u


def test_sound_never_goes_to_a_data_tile():
    yt = "https://www.youtube.com/@NBCNews/live"
    assert sound_tile([MAP, RACES, yt], 0) == 2 and sound_tile([yt, MAP], 1) == 0 and sound_tile([MAP, yt], 1) == 1
    assert sound_tile([MAP, RACES], 0) == 0


def test_the_engine_leaves_data_tiles_alone(monkeypatch):
    """No video on the page: the CDP round is skipped, so "stuck" can never turn into a reload loop."""
    fake = FakeCdp({"t": 10.0, "paused": False, "ended": False, "ready": 1, "buffered": [], "seekable_end": 0})
    monkeypatch.setattr(engine, "cdp_page", fake)
    monkeypatch.setattr(engine.subprocess, "Popen", lambda *a, **k: type("P", (), {"pid": 1, "poll": lambda s: None})())
    monkeypatch.setattr(engine.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())
    monkeypatch.setattr(engine, "kwin", lambda *a, **k: None)
    monkeypatch.setattr(engine, "sink_inputs", lambda env: [])
    d = Driver(Session("tv"))
    d.start([MAP, "https://www.youtube.com/@NBCNews/live"])
    assert d.pages == {0}
    t = [100.0]
    monkeypatch.setattr(engine.time, "monotonic", lambda: t[0])
    for _ in range(40):
        t[0] += 2
        d._tend(0)
    assert fake.calls == []                              # nothing at all on the data tile
    d._tend(1)
    assert fake.calls                                    # the video tile is tended as before
    d.focus(0)
    assert d.sound == 0                                  # keys only… (sound was 0 already: move it first)
    d.sound = 1
    d.focus(0)
    assert d.sound == 1                                  # …a data tile never takes the sound
    d.set_tile(0, "https://a.tv/x")
    assert d.pages == set()
    d.stop = lambda: None


def test_discord_may_stream_our_pages_and_tiles_may_read_them(api):
    app, _ = api
    c = client(app)
    st = c.post("/i/discord/start", json={"urls": [MAP, "https://a.tv"]}).json()
    assert st["running"] and st["urls"][0] == MAP
    assert c.post("/i/discord/tile", json={"i": 1, "url": "http://127.0.0.1:7795/i/tv"}).status_code == 422   # only /p/
    # a tile page reads its own data (same-origin fetch from Chromium); it still can't touch the API
    page = {"Sec-Fetch-Site": "same-origin", "Host": "127.0.0.1:7795"}
    assert c.get("/p/election/map", headers=page).status_code == 200
    assert c.get("/i/tv", headers=page).status_code == 403
    assert c.post("/p/election/map", headers={**page, "Origin": "http://127.0.0.1:7795"}).status_code in (403, 405)


def test_fixed_live_preset_keeps_its_pages_and_fills_the_rest_with_games(tmp_path):
    data = {"nba": board("nba")}
    i, d, clock = inst(tmp_path, data, [{"name": "Election + NBA", "urls": [MAP, RACES], "live": "nba", "fixed": True,
                                         "overlay": "election"}])
    st = i.start(preset="Election + NBA")
    best = rank(data["nba"])[:2]
    assert st["urls"] == [MAP, RACES, *(stream_url("nba", g) for g in best)]
    assert st["live"]["games"] == [None, None, *(g["id"] for g in best)] and st["sound"] == 2
    clock.t += 200
    i.tick()   # a re-plan never swaps the pages
    assert i.urls[:2] == [MAP, RACES]


def test_fixed_preset_with_no_games_is_just_the_pages(tmp_path):
    i, d, _ = inst(tmp_path, {"nba": {"games": []}}, [{"name": "E", "urls": [MAP, RACES], "live": "nba", "fixed": True}])
    assert i.start(preset="E")["urls"] == [MAP, RACES]


def test_election_presets_are_seeded_once(tmp_path):
    s = PresetStore(str(tmp_path))
    assert [p["name"] for p in s.presets][-3:] == [p["name"] for p in ELECTION_SEED]
    s.put([p for p in s.presets if p["name"] != "Election Night"])
    assert "Election Night" not in [p["name"] for p in PresetStore(str(tmp_path)).presets]   # a delete sticks
    night = next(p for p in ELECTION_SEED if p["name"] == "Election Night")
    assert night["urls"][:2] == [MAP, RACES] and night["overlay"] == "election"


@pytest.mark.parametrize("seed", ELECTION_SEED)
def test_election_presets_validate(tmp_path, seed):
    assert PresetStore(str(tmp_path)).put([seed])[0]["name"] == seed["name"]
