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
                           env={"QT_QPA_PLATFORM": "offscreen", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
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
