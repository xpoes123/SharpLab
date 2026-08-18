"""Connect Four over WebSockets, end-to-end: two clients join + play to a horizontal win for R."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from web.api import app  # noqa: E402


def test_two_players_connect4_win():
    client = TestClient(app)
    r = client.post("/api/v1/connect4/rooms", json={"name": "red"})
    assert r.status_code == 200, r.text
    room_id, code = r.json()["room_id"], r.json()["code"]
    assert len(code) == 4
    assert client.post(f"/api/v1/connect4/rooms/{code}/join", json={"name": "yel"}).status_code == 200

    with client.websocket_connect(f"/ws/connect4/{room_id}") as wr, \
         client.websocket_connect(f"/ws/connect4/{room_id}") as wy:
        wr.send_json({"type": "identify", "name": "red"})
        wy.send_json({"type": "identify", "name": "yel"})
        # R fills the bottom row cols 0-3; Y stacks on cols 0-2 above → R wins horizontally
        for col, w in [(0, wr), (0, wy), (1, wr), (1, wy), (2, wr), (2, wy), (3, wr)]:
            w.send_json({"type": "move", "col": col})
        final = None
        for _ in range(16):
            try:
                m = wr.receive_json()
            except Exception:
                break
            if m.get("type") == "state" and m.get("winner"):
                final = m
                break
        assert final is not None and final["winner"] == "R", final
