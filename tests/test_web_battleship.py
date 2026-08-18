"""Battleship over WebSockets: both auto-place → phase becomes battle, and the enemy board
NEVER leaks un-hit ship positions (only ""/hit/miss)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from web.api import app  # noqa: E402


def test_battleship_battle_transition_and_no_leak():
    client = TestClient(app)
    r = client.post("/api/v1/battleship/rooms", json={"name": "p1"})
    assert r.status_code == 200, r.text
    room_id, code = r.json()["room_id"], r.json()["code"]
    assert client.post(f"/api/v1/battleship/rooms/{code}/join", json={"name": "p2"}).status_code == 200

    with client.websocket_connect(f"/ws/battleship/{room_id}") as w1, \
         client.websocket_connect(f"/ws/battleship/{room_id}") as w2:
        w1.send_json({"type": "identify", "name": "p1"})
        w2.send_json({"type": "identify", "name": "p2"})
        w1.send_json({"type": "autoplace"})
        w2.send_json({"type": "autoplace"})
        st = None
        for _ in range(24):
            try:
                m = w1.receive_json()
            except Exception:
                break
            if m.get("type") == "state" and m.get("phase") == "battle":
                st = m
                break
        assert st is not None, "never reached battle phase"
        enemy = [c for row in st["enemy_board"] for c in row]
        assert set(enemy) <= {"", "hit", "miss"}, f"enemy board leaked: {set(enemy)}"
        own = [c for row in st["your_board"] for c in row]
        assert "ship" in own, "own board should show my fleet"
