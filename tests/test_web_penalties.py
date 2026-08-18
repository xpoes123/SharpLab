"""Penalty Shootout over WebSockets: shooter + keeper pick different corners → GOAL, scored."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from web.api import app  # noqa: E402


def _state(ws, pred, tries=14):
    for _ in range(tries):
        try:
            m = ws.receive_json()
        except Exception:
            return None
        if m.get("type") == "state" and pred(m):
            return m
    return None


def test_mismatched_corners_is_a_goal():
    client = TestClient(app)
    r = client.post("/api/v1/penalties/rooms", json={"name": "a"})
    assert r.status_code == 200, r.text
    room_id, code = r.json()["room_id"], r.json()["code"]
    assert client.post(f"/api/v1/penalties/rooms/{code}/join", json={"name": "b"}).status_code == 200

    with client.websocket_connect(f"/ws/penalties/{room_id}") as wa, \
         client.websocket_connect(f"/ws/penalties/{room_id}") as wb:
        wa.send_json({"type": "identify", "name": "a"})
        wb.send_json({"type": "identify", "name": "b"})
        sa = _state(wa, lambda m: "your_role" in m)
        sb = _state(wb, lambda m: "your_role" in m)
        assert sa and sb
        shooter, keeper = (wa, wb) if sa["your_role"] == "shoot" else (wb, wa)
        shooter.send_json({"type": "shoot", "dir": "left"})
        keeper.send_json({"type": "dive", "dir": "right"})  # mismatch → goal
        after = _state(shooter, lambda m: m.get("last") and m["last"].get("goal") is True)
        assert after is not None, "mismatched corners should be a goal"
        assert after["scores"]["me"] == 1  # the shooter scored
