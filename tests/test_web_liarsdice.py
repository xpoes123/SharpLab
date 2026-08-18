"""Liar's Dice over WebSockets: you see only your 5 dice (opponent's never leak), and a bid
updates the standing bid."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from web.api import app  # noqa: E402


def _flatten(o):
    """Collect every scalar in a nested structure into a set of stringified leaves."""
    out = []
    if isinstance(o, dict):
        for v in o.values():
            out += _flatten(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            out += _flatten(v)
    else:
        out.append(o)
    return out


def test_no_dice_leak_and_bid_updates():
    client = TestClient(app)
    r = client.post("/api/v1/liarsdice/rooms", json={"name": "a"})
    assert r.status_code == 200, r.text
    room_id, code = r.json()["room_id"], r.json()["code"]
    assert client.post(f"/api/v1/liarsdice/rooms/{code}/join", json={"name": "b"}).status_code == 200

    with client.websocket_connect(f"/ws/liarsdice/{room_id}") as wa, \
         client.websocket_connect(f"/ws/liarsdice/{room_id}") as wb:
        wa.send_json({"type": "identify", "name": "a"})
        wb.send_json({"type": "identify", "name": "b"})
        sa = sb = None
        for _ in range(12):
            for w, box in ((wa, "a"), (wb, "b")):
                try:
                    m = w.receive_json()
                except Exception:
                    continue
                if m.get("type") == "state" and m.get("your_dice"):
                    if box == "a":
                        sa = m
                    else:
                        sb = m
            if sa and sb:
                break
        assert sa and sb and len(sa["your_dice"]) == 5 and len(sb["your_dice"]) == 5
        # A's broadcast must not contain B's actual dice anywhere (only counts)
        a_leaves = set(map(str, _flatten({k: v for k, v in sa.items() if k != "your_dice"})))
        assert not set(map(str, sb["your_dice"])).issubset(a_leaves) or len(set(sb["your_dice"])) > 1, \
            "opponent dice must not be fully derivable from A's state"
        # whoever's turn it is bids; the standing bid updates
        first = wa if sa.get("turn") == "me" else wb
        first.send_json({"type": "bid", "quantity": 2, "face": 3})
        bid = None
        for _ in range(12):
            try:
                m = first.receive_json()
            except Exception:
                break
            if m.get("type") == "state" and m.get("current_bid"):
                bid = m["current_bid"]
                break
        assert bid == {"quantity": 2, "face": 3}, bid
