"""Reversi over WebSockets: a legal opening move outflanks + flips a disc (score 2/2 → 4/1)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from web.api import app  # noqa: E402


def _wait_state(ws, pred, tries=16):
    for _ in range(tries):
        try:
            m = ws.receive_json()
        except Exception:
            return None
        if m.get("type") == "state" and pred(m):
            return m
    return None


def test_reversi_opening_move_flips():
    client = TestClient(app)
    r = client.post("/api/v1/reversi/rooms", json={"name": "b"})
    assert r.status_code == 200, r.text
    room_id, code = r.json()["room_id"], r.json()["code"]
    assert client.post(f"/api/v1/reversi/rooms/{code}/join", json={"name": "w"}).status_code == 200

    with client.websocket_connect(f"/ws/reversi/{room_id}") as wb, \
         client.websocket_connect(f"/ws/reversi/{room_id}") as ww:
        wb.send_json({"type": "identify", "name": "b"})
        ww.send_json({"type": "identify", "name": "w"})
        # Black's state with its legal moves (standard opening has 4)
        st = _wait_state(wb, lambda m: m.get("you") == "B" and m.get("legal_moves"))
        assert st is not None and st["scores"] == {"B": 2, "W": 2}
        rc = st["legal_moves"][0]
        wb.send_json({"type": "move", "row": rc[0], "col": rc[1]})
        after = _wait_state(wb, lambda m: m.get("scores", {}).get("B") == 4)
        assert after is not None, "Black should have 4 discs (placed 1 + flipped 1)"
        assert after["scores"] == {"B": 4, "W": 1}
