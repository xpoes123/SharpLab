"""Tic-Tac-Toe over WebSockets, end-to-end in-process: create a room, two clients join + play
a full game to a win. Uses Starlette's TestClient (supports websocket_connect)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from web.api import app  # noqa: E402


def _latest_state(ws):
    """Drain queued messages, return the last 'state' seen (skip pings)."""
    st = None
    for _ in range(6):
        msg = ws.receive_json()
        if msg.get("type") == "state":
            st = msg
    return st


def test_two_players_play_to_a_win():
    client = TestClient(app)
    r = client.post("/api/v1/tictactoe/rooms", json={"name": "alice"})
    assert r.status_code == 200, r.text
    room_id, code = r.json()["room_id"], r.json()["code"]
    assert len(code) == 4
    j = client.post(f"/api/v1/tictactoe/rooms/{code}/join", json={"name": "bob"})
    assert j.status_code == 200, j.text

    with client.websocket_connect(f"/ws/tictactoe/{room_id}") as wx, \
         client.websocket_connect(f"/ws/tictactoe/{room_id}") as wo:
        wx.send_json({"type": "identify", "name": "alice"})
        wo.send_json({"type": "identify", "name": "bob"})
        # X takes the top row 0,1,2; O plays 3,4 in between → X wins
        for cell, w in [(0, wx), (3, wo), (1, wx), (4, wo), (2, wx)]:
            w.send_json({"type": "move", "cell": cell})
        # give the last broadcast a beat, then read the freshest state from X
        final = None
        for _ in range(12):
            try:
                m = wx.receive_json()
            except Exception:
                break
            if m.get("type") == "state" and m.get("winner"):
                final = m
                break
        assert final is not None and final["winner"] == "X", final
