"""Contraband (Liar Game, 1v1) over WebSockets, end-to-end: 2 guests join, the
smuggler seals a hidden amount, the inspector passes/doubts, and the server
reveals the case with the correct per-round payoff and cumulative banks — with
roles alternating each round.

Also asserts the pure payoff function `smug()` directly against the spec cases."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from web.api import app  # noqa: E402
from web.contraband import smug, smuggler_slot  # noqa: E402


def _drain_to_reveal(ws, want_round=None, cap=40):
    """Read state messages until a revealed round lands (optionally a specific one)."""
    for _ in range(cap):
        m = ws.receive_json()
        if m.get("type") == "state" and m.get("revealed"):
            if want_round is None or (m.get("last_round") or {}).get("round") == want_round:
                return m
    return None


def _drain_to_round(ws, want_round, cap=40):
    """Read state until an un-revealed state for a specific round lands."""
    for _ in range(cap):
        m = ws.receive_json()
        if (m.get("type") == "state" and not m.get("revealed")
                and not m.get("match_over") and m.get("round") == want_round):
            return m
    return None


def test_smug_payoff_cases():
    """The spec's four canonical cases for the pure payoff function."""
    assert smug(7, False, 0) == 7    # pass → full amount slips through
    assert smug(7, True, 6) == 7     # under-guess (6 < 7) → slips
    assert smug(7, True, 7) == 0     # guessed high enough → caught
    assert smug(0, True, 8) == 4     # doubt an empty decoy → N/2


def test_smug_more():
    assert smug(0, False, 0) == 0    # empty, passed → nothing
    assert smug(5, True, 5) == 0     # exact guess → caught
    assert smug(10, True, 9) == 10   # under by one → slips
    assert smug(0, True, 3) == 1.5   # odd N over an empty decoy → N/2 fractional


def test_role_alternation():
    assert smuggler_slot(1) == "A"   # host smuggles first
    assert smuggler_slot(2) == "B"
    assert smuggler_slot(3) == "A"


def test_full_round_reveal_and_cumulative_scoring():
    client = TestClient(app)
    r = client.post("/api/v1/contraband/rooms", json={"name": "a"})
    assert r.status_code == 200, r.text
    room_id, code = r.json()["room_id"], r.json()["code"]
    assert len(code) == 4

    assert client.post(f"/api/v1/contraband/rooms/{code}/join",
                       json={"name": "b"}).status_code == 200

    with client.websocket_connect(f"/ws/contraband/{room_id}") as wa, \
         client.websocket_connect(f"/ws/contraband/{room_id}") as wb:
        wa.send_json({"type": "identify", "name": "a"})
        wb.send_json({"type": "identify", "name": "b"})

        # ── Round 1 ── host 'a' smuggles 7, inspector 'b' doubts N=5 → slips (banks 7)
        wa.send_json({"type": "seal", "amount": 7})
        wb.send_json({"type": "call", "action": "doubt", "guess": 5})
        rev = _drain_to_reveal(wa, want_round=1)
        assert rev is not None, "no reveal broadcast received"
        # 'a' is host (slot A) and the round-1 smuggler; me == a.
        assert rev["last_round"]["x"] == 7, rev
        assert rev["last_round"]["doubted"] is True and rev["last_round"]["guess"] == 5, rev
        assert rev["last_round"]["smuggler_is_you"] is True, rev
        assert rev["last_round"]["banked"] == 7, rev
        assert rev["scores"] == {"me": 7, "opp": 0}, rev

        # ── Advance to round 2 — roles swap; 'b' now smuggles ────────────────
        wa.send_json({"type": "next"})
        r2a = _drain_to_round(wa, want_round=2)
        assert r2a is not None
        assert r2a["your_role"] == "inspector", r2a   # a is inspector now
        assert r2a["smuggler_slot"] == "B", r2a
        r2b = _drain_to_round(wb, want_round=2)
        assert r2b is not None and r2b["your_role"] == "smuggler", r2b

        # 'b' smuggles 3, 'a' passes → b banks the full 3.
        wb.send_json({"type": "seal", "amount": 3})
        wa.send_json({"type": "call", "action": "pass"})
        rev2 = _drain_to_reveal(wb, want_round=2)
        assert rev2 is not None
        assert rev2["last_round"]["banked"] == 3, rev2
        # From b's perspective: b banked 3 (cumulative 3), a still 7.
        assert rev2["scores"] == {"me": 3, "opp": 7}, rev2

        # ── Host ends the match → a (7) beats b (3) ──────────────────────────
        wa.send_json({"type": "end_match"})
        over_a = None
        for _ in range(10):
            m = wa.receive_json()
            if m.get("type") == "state" and m.get("match_over"):
                over_a = m
                break
        assert over_a is not None and over_a["winner"] == "you", over_a

        over_b = None
        for _ in range(10):
            m = wb.receive_json()
            if m.get("type") == "state" and m.get("match_over"):
                over_b = m
                break
        assert over_b is not None and over_b["winner"] == "opp", over_b


def test_sealed_amount_hidden_from_inspector():
    """The inspector must not see the sealed amount before the round resolves."""
    client = TestClient(app)
    r = client.post("/api/v1/contraband/rooms", json={"name": "x"}).json()
    room_id, code = r["room_id"], r["code"]
    client.post(f"/api/v1/contraband/rooms/{code}/join", json={"name": "y"})

    with client.websocket_connect(f"/ws/contraband/{room_id}") as wx, \
         client.websocket_connect(f"/ws/contraband/{room_id}") as wy:
        wx.send_json({"type": "identify", "name": "x"})
        wy.send_json({"type": "identify", "name": "y"})
        wx.send_json({"type": "seal", "amount": 9})  # x is host → round-1 smuggler

        # y (inspector) should see the case is sealed, but not the amount.
        seen = None
        for _ in range(10):
            m = wy.receive_json()
            if m.get("type") == "state" and m.get("sealed"):
                seen = m
                break
        assert seen is not None, "y never saw the sealed flag"
        assert seen["revealed"] is False, seen
        assert seen["your_sealed_amount"] is None, seen  # inspector: hidden
        assert seen["your_role"] == "inspector", seen
