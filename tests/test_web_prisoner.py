"""Iterated Prisoner's Dilemma over WebSockets: mutual cooperation scores +3 each."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from web.api import app  # noqa: E402


def test_mutual_cooperation_scores_three():
    client = TestClient(app)
    r = client.post("/api/v1/prisoner/rooms", json={"name": "a"})
    assert r.status_code == 200, r.text
    room_id, code = r.json()["room_id"], r.json()["code"]
    assert client.post(f"/api/v1/prisoner/rooms/{code}/join", json={"name": "b"}).status_code == 200

    with client.websocket_connect(f"/ws/prisoner/{room_id}") as wa, \
         client.websocket_connect(f"/ws/prisoner/{room_id}") as wb:
        wa.send_json({"type": "identify", "name": "a"})
        wb.send_json({"type": "identify", "name": "b"})
        wa.send_json({"type": "choice", "move": "cooperate"})
        wb.send_json({"type": "choice", "move": "cooperate"})
        final = None
        for _ in range(16):
            try:
                m = wa.receive_json()
            except Exception:
                break
            if m.get("type") == "state" and m.get("last", {}).get("me") is not None:
                final = m
                break
        assert final is not None and final["totals"]["me"] == 3, final
