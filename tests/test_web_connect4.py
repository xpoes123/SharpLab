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

    # Wait for the server to apply each step before sending the next: moves on two sockets
    # are handled by two concurrent server tasks, so an unsynchronised Y move can land before
    # R's, get ignored as out-of-turn, and the game never reaches a win.
    def wait_state(w, pred):
        for _ in range(32):
            m = w.receive_json()
            if m.get("type") == "state" and pred(m):
                return m
        raise AssertionError("expected state never arrived")

    def pieces(m):
        return sum(cell is not None and cell != "" for row in m["board"] for cell in row)

    with client.websocket_connect(f"/ws/connect4/{room_id}") as wr, \
         client.websocket_connect(f"/ws/connect4/{room_id}") as wy:
        wr.send_json({"type": "identify", "name": "red"})
        wait_state(wr, lambda m: True)
        wy.send_json({"type": "identify", "name": "yel"})
        wait_state(wy, lambda m: all(p["connected"] for p in m["players"]))
        # R fills the bottom row cols 0-3; Y stacks on cols 0-2 above → R wins horizontally
        final = None
        for n, (col, w) in enumerate([(0, wr), (0, wy), (1, wr), (1, wy), (2, wr), (2, wy), (3, wr)], 1):
            w.send_json({"type": "move", "col": col})
            final = wait_state(w, lambda m, n=n: pieces(m) == n)
        assert final["winner"] == "R", final
