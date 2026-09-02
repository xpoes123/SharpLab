"""Iterated Prisoner's Dilemma over WebSockets, end-to-end: 2 guests join, submit
hidden Cooperate/Defect moves, and the server reveals both moves with the correct
per-round payoffs and cumulative scoring across rounds.

Also asserts the payoff matrix directly against the engine's PAYOFF table."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from web.api import app  # noqa: E402
from web.pd import PAYOFF  # noqa: E402


def _drain_to_reveal(ws, want_round=None, cap=40):
    """Read state messages until a revealed round lands (optionally a specific one)."""
    for _ in range(cap):
        m = ws.receive_json()
        if m.get("type") == "state" and m.get("revealed"):
            if want_round is None or m.get("round") == want_round:
                return m
    return None


def test_payoff_matrix():
    """The canonical PD payoffs: CC 3/3, DC 5/0, CD 0/5, DD 1/1."""
    assert PAYOFF[("C", "C")] == (3, 3)
    assert PAYOFF[("D", "C")] == (5, 0)
    assert PAYOFF[("C", "D")] == (0, 5)
    assert PAYOFF[("D", "D")] == (1, 1)


def test_pd_rounds_reveal_and_cumulative_scoring():
    client = TestClient(app)
    r = client.post("/api/v1/pd/rooms", json={"name": "a"})
    assert r.status_code == 200, r.text
    room_id, code = r.json()["room_id"], r.json()["code"]
    assert len(code) == 4

    assert client.post(f"/api/v1/pd/rooms/{code}/join", json={"name": "b"}).status_code == 200

    with client.websocket_connect(f"/ws/pd/{room_id}") as wa, \
         client.websocket_connect(f"/ws/pd/{room_id}") as wb:
        wa.send_json({"type": "identify", "name": "a"})
        wb.send_json({"type": "identify", "name": "b"})

        # ── Round 1 ── both Cooperate → 3/3 ──────────────────────────────────
        wa.send_json({"type": "submit", "choice": "C"})
        wb.send_json({"type": "submit", "choice": "C"})
        rev = _drain_to_reveal(wa, want_round=1)
        assert rev is not None, "no reveal broadcast received"
        # 'a' is host (slot A); from a's perspective me == a.
        assert rev["your_choice"] == "C" and rev["opp_choice"] == "C", rev
        assert rev["last_payoff"] == {"me": 3, "opp": 3}, rev
        assert rev["scores"] == {"me": 3, "opp": 3}, rev
        assert rev["history"] == {"me": ["C"], "opp": ["C"]}, rev

        # ── Round 2 ── a Defects vs b Cooperates → attacker +5, victim +0 ─────
        # 'next' broadcasts an un-revealed round-2 state; _drain_to_reveal skips it.
        wa.send_json({"type": "next"})
        wa.send_json({"type": "submit", "choice": "D"})
        wb.send_json({"type": "submit", "choice": "C"})
        rev = _drain_to_reveal(wa, want_round=2)
        assert rev is not None
        assert rev["last_payoff"] == {"me": 5, "opp": 0}, rev
        # Cumulative: a = 3 + 5 = 8, b = 3 + 0 = 3
        assert rev["scores"] == {"me": 8, "opp": 3}, rev
        assert rev["history"] == {"me": ["C", "D"], "opp": ["C", "C"]}, rev

        # ── Round 3 ── both Defect → 1/1 each ────────────────────────────────
        wa.send_json({"type": "next"})
        wa.send_json({"type": "submit", "choice": "D"})
        wb.send_json({"type": "submit", "choice": "D"})
        rev = _drain_to_reveal(wa, want_round=3)
        assert rev is not None
        assert rev["last_payoff"] == {"me": 1, "opp": 1}, rev
        # Cumulative: a = 8 + 1 = 9, b = 3 + 1 = 4
        assert rev["scores"] == {"me": 9, "opp": 4}, rev

        # ── Host ends the match → a (9) beats b (4) ──────────────────────────
        wa.send_json({"type": "end_match"})
        # a's view: winner "you"; b's view: winner "opp".
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


def test_pd_choice_hidden_until_both_submit():
    """A player's move must not leak to the opponent before both have submitted."""
    client = TestClient(app)
    r = client.post("/api/v1/pd/rooms", json={"name": "x"}).json()
    room_id, code = r["room_id"], r["code"]
    client.post(f"/api/v1/pd/rooms/{code}/join", json={"name": "y"})

    with client.websocket_connect(f"/ws/pd/{room_id}") as wx, \
         client.websocket_connect(f"/ws/pd/{room_id}") as wy:
        wx.send_json({"type": "identify", "name": "x"})
        wy.send_json({"type": "identify", "name": "y"})
        wx.send_json({"type": "submit", "choice": "D"})

        # y should see that x submitted, but NOT what x chose (no reveal yet).
        seen = None
        for _ in range(10):
            m = wy.receive_json()
            if m.get("type") == "state" and m.get("opp_submitted"):
                seen = m
                break
        assert seen is not None, "y never saw x's submission flag"
        assert seen["revealed"] is False, seen
        assert seen["opp_choice"] is None, seen
