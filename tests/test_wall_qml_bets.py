"""The overlay's bets badges, rendered for real (qml6 offscreen, Preview.qml against a throwaway server): they draw
without QML errors and only when bets are shown. Skipped where qml6 isn't installed."""
import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

QML = Path(__file__).resolve().parent.parent / "wall" / "qml"
ROWS = [{"who": "David", "side": "YES", "label": "GSW −3.5", "entry": 42, "mark": 51, "dir": "up", "pct": 21, "size": "50+ ct"},
        {"who": "Steph", "side": "NO", "label": "Over 224.5", "entry": 56, "mark": 49, "dir": "down", "pct": 13, "size": "8 ct", "pnl": -0.56}]


def render(tmp_path, show, name):
    bets = {"show": show, "n": 2, "view": "lbar", "default": 45, "delays": {"1": 40},
            "tiles": [{"i": 0, "delay": 40, "src": "auto", "rows": ROWS}, {"i": 1, "delay": 45, "src": "default", "rows": []}]}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = ({"view": "lbar", "rev": 1, "chat": None} if self.path.startswith("/api/overlay/view") else
                    bets if self.path.startswith("/api/overlay/bets") else {"board": "nba", "shown": "nba", "data": None})
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
    api = f"http://127.0.0.1:{srv.server_port}"
    try:
        r = subprocess.run(["qml6", str(QML / "Preview.qml"), "--", "--layout", "lbar", "--size", "1920x1080", "--api", api,
                            "--bets", api, "--out", str(out), "--wait", "2500"],
                           env={"QT_QPA_PLATFORM": "offscreen", "QT_FORCE_STDERR_LOGGING": "1", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
                           capture_output=True, text=True, timeout=30)
    finally:
        srv.shutdown()
        srv.server_close()
    errs = [l for l in r.stderr.splitlines() if "Error" in l or "Unable to assign" in l or "is not defined" in l or "TypeError" in l]
    return out, errs


@pytest.mark.skipif(shutil.which("qml6") is None, reason="qml6 not installed")
def test_overlay_bets_render_and_hide(tmp_path):
    on, errs_on = render(tmp_path, True, "on")
    off, errs_off = render(tmp_path, False, "off")
    assert errs_on == [] and errs_off == []
    assert on.exists() and off.exists() and on.read_bytes() != off.read_bytes()
