"""RPS over WebSockets: two clients throw simultaneously, choices stay private until both are
in, then the round resolves and the winner scores."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from web.api import app  # noqa: E402


def test_rps_reveal_and_score():
    client = TestClient(app)
    r = client.post("/api/v1/rps/rooms", json={"name": "alice"})
    assert r.status_code == 200, r.text
    room_id, code = r.json()["room_id"], r.json()["code"]
    assert client.post(f"/api/v1/rps/rooms/{code}/join", json={"name": "bob"}).status_code == 200

    with client.websocket_connect(f"/ws/rps/{room_id}") as wa, \
         client.websocket_connect(f"/ws/rps/{room_id}") as wb:
        wa.send_json({"type": "identify", "name": "alice"})
        wb.send_json({"type": "identify", "name": "bob"})
        wa.send_json({"type": "throw", "choice": "rock"})       # rock beats
        wb.send_json({"type": "throw", "choice": "scissors"})   # scissors
        final = None
        for _ in range(16):
            try:
                m = wa.receive_json()
            except Exception:
                break
            if m.get("type") == "state" and m.get("revealed") and m.get("last_result"):
                final = m
                break
        assert final is not None and final["last_result"] == "win", final
        assert final["scores"]["me"] == 1
