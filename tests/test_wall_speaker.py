"""The speaker badge on the tile with the sound (wall/bands.py speaker + qml/SpeakerBadge.qml): the /bands payload carries
the sound tile + its rect for every layout, none with < 2 video tiles or on our /p/ data pages, and the overlay draws it
(qml6 offscreen, Preview.qml against a stub /bands; skipped where qml6 isn't installed)."""
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.test_wall_bands import _app
from tests.test_wall_service import url
from wall.bands import ScoreTape, build_view, speaker
from wall.engine import page_url

QML = Path(__file__).resolve().parent.parent / "wall" / "qml"
V = [url("nfl", t) for t in ("bills", "bengals", "rams", "chiefs")]
MAP, VOTES = page_url("election/map"), page_url("election/races")


@pytest.mark.parametrize("n,sound,rect", [
    (2, 1, [0.5, 0.0, 0.5, 1.0]),
    (3, 0, [0.0, 0.0, 0.6667, 1.0]),
    (3, 2, [0.6667, 0.5, 0.3333, 0.5]),
    (4, 3, [0.5, 0.5, 0.5, 0.5]),
])
def test_speaker_is_the_sound_tile_and_its_rect(n, sound, rect):
    assert speaker(V[:n], sound) == {"tile": sound, "rect": rect}


def test_no_badge_with_one_video_tile_or_on_a_data_page():
    assert speaker(V[:1], 0) is None                    # one tile: nothing to tell apart
    assert speaker([], 0) is None
    assert speaker([MAP, V[0]], 1) is None              # one video tile beside a data page
    assert speaker([MAP, VOTES, V[0], V[1]], 0) is None  # the sound index on a /p/ page: no sound there
    assert speaker([MAP, VOTES, V[0], V[1]], 3) == {"tile": 3, "rect": [0.5, 0.5, 0.5, 0.5]}


def test_build_view_carries_it():
    v = build_view(urls=V[:3], games=[], sound=2, lbar=True, view="", title="", live_league="nfl",
                   scores=type("S", (), {"get": lambda self, lg, fresh=False: None})(), tape=ScoreTape(), now=1000)
    assert v["speaker"] == {"tile": 2, "rect": [0.6667, 0.5, 0.3333, 0.5]} and v["sound"] == 2


@pytest.mark.parametrize("lbar", [True, False])
def test_bands_endpoint_follows_the_sound(tmp_path, monkeypatch, lbar):
    app, sync, c = _app(tmp_path, monkeypatch, lbar=lbar)
    assert c.get("/i/tv/bands").json()["speaker"] is None          # no wall
    c.post("/i/tv/start", json={"urls": V[:1]})
    assert c.get("/i/tv/bands").json()["speaker"] is None          # one tile
    c.post("/i/tv/start", json={"urls": V[:4], "sound": 2})
    v = c.get("/i/tv/bands").json()
    assert v["lbar"] is lbar and v["speaker"] == {"tile": 2, "rect": [0.0, 0.5, 0.5, 0.5]}
    c.post("/i/tv/start", json={"urls": [MAP, V[0]], "sound": 1})
    assert c.get("/i/tv/bands").json()["speaker"] is None          # the other tile is ours: one stream


# ---- the badge in the overlay (qml6 offscreen) ----------------------------------------------------------------------------
def render(tmp_path, bands, out, layout="none"):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = bands if self.path.endswith("/bands") else {"default": 45, "delays": {}, "show": False, "n": 0, "tiles": []} \
                if self.path.endswith("/bets") else {}
            b = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = subprocess.run(["qml6", str(QML / "Preview.qml"), "--", "--layout", layout, "--size", "1920x1080",
                            "--bets", f"http://127.0.0.1:{srv.server_port}/i/tv", "--api", f"http://127.0.0.1:{srv.server_port}",
                            "--out", str(out), "--wait", "2500"],
                           env={"QT_QPA_PLATFORM": "offscreen", "QT_FORCE_STDERR_LOGGING": "1", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
                           capture_output=True, text=True, timeout=30)
    finally:
        srv.shutdown()
        srv.server_close()
    return [l for l in r.stderr.splitlines() if "Error" in l or "Unable to assign" in l or "is not defined" in l
            or "TypeError" in l or "ReferenceError" in l]


def bands(n, sound, lbar):
    return {"n": n, "sound": sound, "lbar": lbar, "view": "", "title": "", "league": "nfl", "delay": 45, "chat": [], "ticker": [],
            "tiles": [{"url": u, "host": "x", "label": "", "delay": 45, "src": "default", "game": None, "synced": False} for u in V[:n]],
            "speaker": speaker(V[:n], sound), "on": True}


@pytest.mark.skipif(shutil.which("qml6") is None, reason="qml6 not installed")
@pytest.mark.parametrize("n,sound,lbar", [(4, 1, False), (3, 2, True), (2, 0, False)])
def test_badge_renders_on_the_sound_tile(tmp_path, n, sound, lbar):
    on, off = tmp_path / "on.png", tmp_path / "off.png"
    assert render(tmp_path, bands(n, sound, lbar), on, "lbar" if lbar else "none") == []
    assert render(tmp_path, {**bands(n, sound, lbar), "speaker": None}, off, "lbar" if lbar else "none") == []
    assert on.exists() and off.exists() and on.read_bytes() != off.read_bytes()
    keep = os.environ.get("WALL_SPEAKER_PNG")   # a dir: keep the renders to look at
    if keep:
        shutil.copy(on, Path(keep) / f"speaker-{n}-{sound}-{'lbar' if lbar else 'full'}.png")


# ---- the outlet's name on every stream tile (bands tiles[].name, the same chip as the 🔊) ----------------------------------
def test_stream_names_catalog_local_pool_handle():
    from wall.bands import stream_name
    yt = lambda h: f"https://www.youtube.com/@{h}/live"   # noqa: E731
    assert stream_name(yt("TheYoungTurks")) == "TYT"                     # the Spectrum catalog
    assert stream_name(yt("abc3340")) == "ABC 33/40 Birmingham"          # the local news list
    assert stream_name(yt("PBSNewsHour")) == "PBS NewsHour"              # the ad-skip pool
    assert stream_name(yt("SomeChannel")) == "@SomeChannel"              # else the handle
    assert stream_name(MAP) is None and stream_name("https://example.com/x") is None and stream_name("") is None


def test_build_view_names_each_tile():
    urls = [MAP, "https://www.youtube.com/@abc3340/live"]
    v = build_view(urls=urls, games=[], sound=1, lbar=False, view="", title="", live_league="",
                   scores=type("S", (), {"get": lambda self, lg, fresh=False: None})(), tape=ScoreTape(), now=1000)
    assert [t["name"] for t in v["tiles"]] == [None, "ABC 33/40 Birmingham"]


@pytest.mark.skipif(shutil.which("qml6") is None, reason="qml6 not installed")
def test_name_chips_render_without_a_speaker(tmp_path):
    named = {**bands(2, 0, False), "speaker": None}
    named["tiles"] = [{**t, "name": n} for t, n in zip(named["tiles"], ["NBC News", "Fox News"])]
    on, off = tmp_path / "on.png", tmp_path / "off.png"
    assert render(tmp_path, named, on) == []
    assert render(tmp_path, {**named, "tiles": [{**t, "name": None} for t in named["tiles"]]}, off) == []
    assert on.read_bytes() != off.read_bytes()
