"""Keynesian Beauty Contest over WebSockets, end-to-end: 3 guests join, submit
guesses, and the server reveals the correct average/target/winner with cumulative
scoring across rounds."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from web.api import app  # noqa: E402


def _drain_to_reveal(ws, cap=40):
    """Read state messages until a reveal lands (or we give up)."""
    for _ in range(cap):
        m = ws.receive_json()
        if m.get("type") == "state" and m.get("phase") == "reveal":
            return m
    return None


def test_beauty_reveal_math_and_scoring():
    client = TestClient(app)
    r = client.post("/api/v1/beauty/rooms", json={"name": "a"})
    assert r.status_code == 200, r.text
    room_id, code = r.json()["room_id"], r.json()["code"]
    assert len(code) == 4

    assert client.post(f"/api/v1/beauty/rooms/{code}/join", json={"name": "b"}).status_code == 200
    assert client.post(f"/api/v1/beauty/rooms/{code}/join", json={"name": "c"}).status_code == 200

    with client.websocket_connect(f"/ws/beauty/{room_id}") as wa, \
         client.websocket_connect(f"/ws/beauty/{room_id}") as wb, \
         client.websocket_connect(f"/ws/beauty/{room_id}") as wc:
        wa.send_json({"type": "identify", "name": "a"})
        wb.send_json({"type": "identify", "name": "b"})
        wc.send_json({"type": "identify", "name": "c"})

        # ── Round 1 ── guesses 30, 60, 90 → avg 60 → target 40 → 'a' (30) wins
        wa.send_json({"type": "start_round"})
        wa.send_json({"type": "submit", "guess": 30})
        wb.send_json({"type": "submit", "guess": 60})
        wc.send_json({"type": "submit", "guess": 90})

        reveal = _drain_to_reveal(wa)
        assert reveal is not None, "no reveal broadcast received"
        res = reveal["result"]
        assert res["average"] == 60.0, res
        assert res["target"] == 40.0, res
        assert res["winners"] == ["a"], res
        scores = {p["name"]: p["score"] for p in reveal["players"]}
        assert scores == {"a": 1, "b": 0, "c": 0}, scores

        # ── Round 2 ── same guesses → 'a' wins again, cumulative score → 2
        wa.send_json({"type": "start_round"})
        wa.send_json({"type": "submit", "guess": 30})
        wb.send_json({"type": "submit", "guess": 60})
        wc.send_json({"type": "submit", "guess": 90})

        reveal2 = _drain_to_reveal(wa)
        assert reveal2 is not None
        assert reveal2["result"]["round_num"] == 2
        scores2 = {p["name"]: p["score"] for p in reveal2["players"]}
        assert scores2 == {"a": 2, "b": 0, "c": 0}, scores2


def test_beauty_tie_both_win():
    """Two players equidistant from the target both win the round."""
    client = TestClient(app)
    r = client.post("/api/v1/beauty/rooms", json={"name": "x"}).json()
    room_id, code = r["room_id"], r["code"]
    client.post(f"/api/v1/beauty/rooms/{code}/join", json={"name": "y"})

    with client.websocket_connect(f"/ws/beauty/{room_id}") as wx, \
         client.websocket_connect(f"/ws/beauty/{room_id}") as wy:
        wx.send_json({"type": "identify", "name": "x"})
        wy.send_json({"type": "identify", "name": "y"})
        wx.send_json({"type": "start_round"})
        # Both guess the same number → equidistant from the target → both win.
        wx.send_json({"type": "submit", "guess": 50})
        wy.send_json({"type": "submit", "guess": 50})
        reveal = _drain_to_reveal(wx)
        assert reveal is not None
        assert set(reveal["result"]["winners"]) == {"x", "y"}, reveal["result"]
