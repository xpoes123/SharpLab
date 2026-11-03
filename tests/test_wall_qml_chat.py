"""The overlay's chat section, rendered for real (qml6 offscreen, Preview.qml against a throwaway view server): it draws
without QML errors, and only when the view carries a chat. Skipped where qml6 isn't installed."""
import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

QML = Path(__file__).resolve().parent.parent / "wall" / "qml"
CHAT = {"count": 3, "who": "David, Steph, Kim", "lines": [
    {"id": 1, "name": "", "guest": False, "text": "Marco proposes to stop the wall", "sys": True, "fresh": False},
    {"id": 2, "name": "Kim", "guest": True, "text": "refs!!", "sys": False, "fresh": True},
    {"id": 3, "name": "David", "guest": False, "text": "from the couch", "sys": False, "fresh": True}]}


def render(tmp_path, chat, name):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = {"view": "lbar", "rev": 1, "chat": chat} if self.path.startswith("/api/overlay/view") else {"board": "nba"}
            b = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out = tmp_path / f"{name}.png"
    try:
        r = subprocess.run(["qml6", str(QML / "Preview.qml"), "--", "--layout", "lbar", "--size", "1920x1080",
                            "--api", f"http://127.0.0.1:{srv.server_port}", "--out", str(out), "--wait", "2500"],
                           env={"QT_QPA_PLATFORM": "offscreen", "QT_FORCE_STDERR_LOGGING": "1", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
                           capture_output=True, text=True, timeout=30)
    finally:
        srv.shutdown()
        srv.server_close()
    errs = [l for l in r.stderr.splitlines() if "Error" in l or "Unable to assign" in l or "is not defined" in l]
    return out, errs


@pytest.mark.skipif(shutil.which("qml6") is None, reason="qml6 not installed")
def test_overlay_chat_renders_and_only_with_a_chat(tmp_path):
    on, errs_on = render(tmp_path, CHAT, "on")
    off, errs_off = render(tmp_path, None, "off")
    assert errs_on == [] and errs_off == []
    assert on.exists() and off.exists() and on.read_bytes() != off.read_bytes()


# ---- the chat floating over the tiles (ChatFloat.qml, /bands tvchat) ----------------------------------------------------
TV_LINES = [
    {"id": 10, "name": "Kim", "guest": True, "text": "this is old, never shown", "sys": False, "fresh": False},
    {"id": 11, "name": "", "guest": False, "text": "🎬 Jev: CNN on tile 3, the sound to tile 3", "sys": True, "fresh": True},
    {"id": 12, "name": "Steph", "guest": False, "text": "PA is so close", "sys": False, "fresh": True},
    {"id": 13, "name": "Marco", "guest": True, "text": "refs!! a long one that has to wrap onto a second line at TV size to check it", "sys": False, "fresh": True},
    {"id": 14, "name": "David", "guest": False, "text": "<b>not bold</b> & fine", "sys": False, "fresh": True}]


@pytest.mark.skipif(shutil.which("qml6") is None, reason="qml6 not installed")
@pytest.mark.parametrize("n,sound,lbar", [(4, 1, False), (3, 1, False), (2, 1, True)])
def test_tv_chat_floats_over_the_tiles_beside_the_badge(tmp_path, n, sound, lbar):
    import os
    from tests.test_wall_speaker import bands, render as render_bands
    on, off = tmp_path / "on.png", tmp_path / "off.png"
    b = bands(n, sound, lbar)
    assert render_bands(tmp_path, {**b, "tvchat": {"on": True, "lines": TV_LINES}}, on, "lbar" if lbar else "none") == []
    assert render_bands(tmp_path, {**b, "tvchat": {"on": False, "lines": TV_LINES}}, off, "lbar" if lbar else "none") == []
    assert on.read_bytes() != off.read_bytes()
    keep = os.environ.get("WALL_CHAT_PNG")   # a dir: keep the renders to look at
    if keep:
        shutil.copy(on, Path(keep) / f"tvchat-{n}-{sound}-{'lbar' if lbar else 'full'}.png")


# ---- the TV chat docked in the L-bar's sidebar (election night: it takes the TRADES box's room while active) ------------
def render_side(tmp_path, tvchat, out, layout="lbar"):
    import time
    from tests.test_wall_election import FX
    from tests.test_wall_speaker import bands
    from wall.election import Results, board, replay
    r = Results(replay(f"{FX}:0.6"))
    r.poll()
    rows = [{"who": who, "side": side, "label": f"PA Senate · Pick{i}", "entry": 40.0, "mark": 60.0, "pct": 50.0, "dir": "up",
             "size": "50+ ct", "kind": "election", "election": {"class": {"type": "control", "office": "S"},
             "pick": {"party": "R", "name": ""}, "label": "Senate control · Rep", "strike": {}}}
            for i, (who, side) in enumerate((("Hansen", "YES"), ("David", "NO")))]
    data = board(r.snapshot(), rows)
    b = {**bands(4, 1, layout == "lbar"), "tvchat": tvchat}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            p = self.path
            body = b if p.endswith("/bands") else {"default": 0, "delays": {}, "show": False, "n": 0, "tiles": []} \
                if p.endswith("/bets") else {} if p.endswith("/spot") else {"view": layout, "rev": 1} \
                if p.startswith("/api/overlay/view") else {
                    "board": "election", "shown": "election", "visible": True, "layout": layout, "view": layout, "rev": 1,
                    "boards": [{"id": "election", "name": "Election"}], "data": data, "updated": time.time(), "error": ""}
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        p = subprocess.run(["qml6", str(QML / "Preview.qml"), "--", "--layout", layout, "--size", "1920x1080",
                            "--bets", f"http://127.0.0.1:{srv.server_port}/i/tv", "--api", f"http://127.0.0.1:{srv.server_port}",
                            "--out", str(out), "--wait", "3000"],
                           env={"QT_QPA_PLATFORM": "offscreen", "QT_FORCE_STDERR_LOGGING": "1", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
                           capture_output=True, text=True, timeout=30)
    finally:
        srv.shutdown()
        srv.server_close()
    return [l for l in p.stderr.splitlines() if "Error" in l or "Unable to assign" in l or "is not defined" in l
            or "TypeError" in l or "ReferenceError" in l]


@pytest.mark.skipif(shutil.which("qml6") is None, reason="qml6 not installed")
def test_tv_chat_docks_in_the_sidebar_in_place_of_trades(tmp_path):
    import os
    active, quiet, off = tmp_path / "active.png", tmp_path / "quiet.png", tmp_path / "off.png"
    stale = [{**l, "fresh": False} for l in TV_LINES]
    assert render_side(tmp_path, {"on": True, "lines": TV_LINES}, active) == []
    assert render_side(tmp_path, {"on": True, "lines": stale}, quiet) == []        # nothing in the last 2 min: TRADES
    assert render_side(tmp_path, {"on": False, "lines": TV_LINES}, off) == []      # the owner's toggle off: TRADES
    assert active.read_bytes() != quiet.read_bytes() and active.read_bytes() != off.read_bytes()
    keep = os.environ.get("WALL_CHAT_PNG")
    if keep:
        for f in (active, quiet, off):
            shutil.copy(f, Path(keep) / f"tvchat-side-{f.stem}.png")
