"""Wall Street Poker — pure Hand engine (auctions, forced takes, trades, folds, showdown)
plus one WebSocket round-trip with real session cookies and a fake coin ledger."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from web import auth, wallstreet  # noqa: E402
from web.api import app  # noqa: E402
from web.wallstreet import Hand  # noqa: E402

FILLER = [r + s for s in "♣♦" for r in ("2", "3", "4", "6", "7", "8", "9", "J")]


def two_player(street: list[str], a_hole=("A♠", "A♥"), b_hole=("2♠", "7♥"), button=0, stack=100):
    """A=seat0 (button), B=seat1 → B is left of the button: forced taker + tiebreak winner."""
    deck = list(a_hole) + list(b_hole) + street + FILLER
    return Hand(["A", "B"], {"A": stack, "B": stack}, button, forced=5, deck=deck)


def test_deal_and_first_auction_opens():
    h = two_player(["K♠", "K♥", "3♦"])
    assert h.hole == {"A": ["A♠", "A♥"], "B": ["2♠", "7♥"]}
    assert h.card == "K♠" and h.street == ["K♥", "3♦"]
    assert h.bidders == ["B", "A"]  # left of button first
    assert h.order() == ["B", "A"]


def test_high_bid_wins_and_pays_pot():
    h = two_player(["K♠", "K♥", "3♦"])
    assert h.bid("A", 12) is None
    assert h.card == "K♠"  # still sealed until everyone bids
    assert h.bid("B", 7) is None
    assert h.board["A"] == ["K♠"] and h.stack["A"] == 88 and h.pot == 12
    assert h.log[-1]["bids"] == {"A": 12, "B": 7}
    assert h.card == "K♥"


def test_tie_goes_left_of_button():
    h = two_player(["K♠", "K♥", "3♦"])
    h.bid("A", 10)
    h.bid("B", 10)
    assert h.board["B"] == ["K♠"] and h.pot == 10


def test_all_zero_forced_taker_pays_forced_price():
    h = two_player(["K♠", "K♥", "3♦"])
    h.bid("A", 0)
    h.bid("B", 0)
    assert h.board["B"] == ["K♠"] and h.stack["B"] == 95 and h.log[-1]["how"] == "forced"


def test_forced_price_capped_by_stack():
    h = two_player(["K♠", "K♥", "3♦"], stack=3)
    h.bid("A", 0)
    h.bid("B", 0)
    assert h.stack["B"] == 0 and h.pot == 3


def test_bid_validation():
    h = two_player(["K♠", "K♥", "3♦"])
    assert h.bid("A", 101)
    assert h.bid("A", -1)
    assert h.bid("A", 1.5)
    assert h.bid("A", True)
    assert h.bid("Z", 1)


def test_full_player_stops_bidding_and_other_eats_rest_at_forced():
    h = two_player(["K♠", "K♥", "3♦", "Q♠", "Q♥", "5♦"])
    for _ in range(3):  # A wins three cards → full
        h.bid("A", 1)
        h.bid("B", 0)
    assert h.need("A") == 0
    # B is the only one needing cards → every remaining card auto-awarded at forced price
    assert h.result is not None
    assert len(h.board["B"]) == 3
    assert h.stack["B"] == 100 - 3 * 5


def test_showdown_conserves_chips_and_best_hand_wins():
    h = two_player(["K♠", "K♥", "3♦", "Q♠", "Q♥", "5♦"])
    for _ in range(3):
        h.bid("A", 4)
        h.bid("B", 0)
    r = h.result
    assert r["winners"] == ["A"]  # aces + kings beats B's junk
    assert sum(r["final"].values()) == 200
    assert r["final"]["A"] == 100 - 12 + r["pot"]
    assert "A" in r["hands"] and "B" in r["hands"]


def test_split_pot_odd_chip_left_of_button():
    # Both end up with the same straight 2-6 (different suits).
    h = Hand(["A", "B"], {"A": 50, "B": 50}, 0, forced=6,
             deck=["2♠", "3♠", "2♥", "3♥", "4♠", "4♥", "5♠", "5♥", "6♠", "6♥"] + FILLER)
    for bid_a, bid_b in [(1, 0), (0, 2), (1, 0), (0, 2), (1, 0)]:
        h.bid("A", bid_a)
        h.bid("B", bid_b)
    # A is full after 6♠ → B alone eats 6♥ at the forced price
    r = h.result
    assert sorted(r["winners"]) == ["A", "B"]
    assert r["pot"] == 3 + 2 + 2 + 6
    assert r["winners"][0] == "B"  # odd chip goes left of the button
    assert r["final"] == {"A": 50 - 3 + 6, "B": 50 - 10 + 7}


def test_fold_hands_pot_to_last_player():
    h = two_player(["K♠", "K♥", "3♦"])
    h.bid("A", 20)
    h.bid("B", 0)
    assert h.fold("A") is None
    r = h.result
    assert r["winners"] == ["B"] and r["final"] == {"A": 80, "B": 120}


def test_fold_mid_auction_three_players():
    deck = ["A♠", "A♥", "2♠", "7♥", "9♣", "9♦", "K♠", "K♥", "3♦"] + FILLER
    h = Hand(["A", "B", "C"], {u: 100 for u in "ABC"}, 0, forced=5, deck=deck)
    h.bid("B", 3)
    h.bid("C", 4)
    h.fold("A")  # last unsubmitted bidder folds → auction resolves without them
    assert h.board["C"] == ["K♠"]


def test_trade_moves_card_and_money_and_seller_rejoins():
    h = two_player(["K♠", "K♥", "3♦", "Q♠", "Q♥", "5♦"])
    h.bid("A", 0)
    h.bid("B", 9)  # B wins K♠
    assert h.offer("A", "B", "K♠", 15) is None
    oid = next(iter(h.offers))
    assert h.accept("A", oid)  # only the seller can accept
    assert h.accept("B", oid) is None
    assert h.board["A"] == ["K♠"] and h.board["B"] == []
    assert h.stack == {"A": 85, "B": 106}
    assert h.pot == 9  # trade money never touches the pot


def test_offer_respects_locked_bid():
    h = two_player(["K♠", "K♥", "3♦"])
    h.bid("A", 0)
    h.bid("B", 9)
    h.bid("A", 95)  # A has 100, 95 locked behind the sealed bid on K♥
    assert h.offer("A", "B", "K♠", 10) == "Buyer can't cover that price."
    assert h.offer("A", "B", "K♠", 5) is None


def test_trade_that_fills_buyer_drops_their_bid():
    h = two_player(["K♠", "K♥", "3♦", "Q♠", "Q♥", "5♦"])
    h.bid("A", 1); h.bid("B", 0)   # A: K♠
    h.bid("A", 1); h.bid("B", 0)   # A: K♥
    h.bid("A", 0); h.bid("B", 2)   # B: 3♦
    assert h.card == "Q♠" and h.need("A") == 1
    h.bid("A", 50)
    h.offer("A", "B", "3♦", 1)
    h.accept("B", next(iter(h.offers)))
    # A is full → out of the Q♠ auction; B (now needs 3) is the only bidder → forced
    assert h.need("A") == 0
    assert "Q♠" in h.board["B"]


def test_timeout_zero_fills_missing_bids():
    h = two_player(["K♠", "K♥", "3♦"])
    h.bid("A", 6)
    h.timeout(h.card_seq - 1)  # stale timer is ignored
    assert h.card == "K♠"
    h.timeout(h.card_seq)
    assert h.board["A"] == ["K♠"]


def test_cannot_buy_from_or_as_folded():
    deck = ["A♠", "A♥", "2♠", "7♥", "9♣", "9♦", "K♠", "K♥", "3♦"] + FILLER
    h = Hand(["A", "B", "C"], {u: 100 for u in "ABC"}, 0, forced=5, deck=deck)
    h.bid("A", 5); h.bid("B", 0); h.bid("C", 0)
    h.fold("A")
    assert h.offer("B", "A", "K♠", 5)


# ── WebSocket round-trip ─────────────────────────────────────────────────────


@pytest.fixture
def ledger(monkeypatch):
    bal = {"u1": 1000, "u2": 1000}
    logs = []

    async def update(uid, delta):
        if bal.get(uid, 0) + delta < 0:
            raise ValueError("insufficient")
        bal[uid] = bal.get(uid, 0) + delta
        return bal[uid]

    async def log(uid, game, wager, payout):
        logs.append((uid, game, wager, payout))

    monkeypatch.setattr(wallstreet.queries, "update_casino_balance", update)
    monkeypatch.setattr(wallstreet.queries, "log_casino_result", log)
    return bal, logs


def _client(uid, name):
    c = TestClient(app)
    c.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie({"id": uid, "username": name}))
    return c


def _state(ws, pred, cap=40):
    for _ in range(cap):
        m = ws.receive_json()
        if m.get("type") == "state" and pred(m):
            return m
    raise AssertionError("state never matched")


def test_ws_buyin_play_fold_settles(ledger):
    bal, logs = ledger
    c1, c2 = _client("u1", "Ann"), _client("u2", "Bob")
    r = c1.post("/api/v1/wallstreet/rooms", json={"buy_in": 100}).json()
    assert c2.post(f"/api/v1/wallstreet/rooms/{r['code']}/join").json()["room_id"] == r["room_id"]
    assert TestClient(app).post("/api/v1/wallstreet/rooms", json={}).status_code == 401

    with c1.websocket_connect(f"/ws/wallstreet/{r['room_id']}") as w1, \
         c2.websocket_connect(f"/ws/wallstreet/{r['room_id']}") as w2:
        w1.send_json({"type": "buyin"})
        w2.send_json({"type": "buyin"})
        _state(w1, lambda m: all(p["bought_in"] for p in m["players"]))
        assert bal == {"u1": 900, "u2": 900}

        w2.send_json({"type": "start"})  # not host
        w1.send_json({"type": "start"})
        s = _state(w1, lambda m: m["hand"] is not None)
        assert len(s["hand"]["my_hole"]) == 2 and s["hand"]["card"]

        w1.send_json({"type": "bid", "amount": 30})
        w2.send_json({"type": "bid", "amount": 0})
        _state(w1, lambda m: m["hand"] and m["hand"]["pot"] == 30)
        w1.send_json({"type": "fold"})
        end = _state(w2, lambda m: m["hand"] is None and m["last_result"])
        assert end["last_result"]["winners"] == ["Bob"]

    assert bal == {"u1": 970, "u2": 1030}
    assert sorted(logs) == [("u1", "wallstreet", 100, 70), ("u2", "wallstreet", 100, 130)]


def test_ws_rejects_unseated_user(ledger):
    r = _client("u1", "Ann").post("/api/v1/wallstreet/rooms", json={}).json()
    with pytest.raises(Exception):
        with _client("u9", "Eve").websocket_connect(f"/ws/wallstreet/{r['room_id']}") as w:
            w.receive_json()


@pytest.mark.asyncio
async def test_refund_all_returns_chips_mid_hand(ledger):
    bal, _ = ledger
    room = wallstreet.Room(room_id="x", code="ZZZZ", host="u1", buy_in=100)
    room.seats = [wallstreet.Seat("u1", "Ann"), wallstreet.Seat("u2", "Bob")]
    room.hand = two_player(["K♠", "K♥", "3♦"])
    room.hand.seats = ["u1", "u2"]  # rename seats onto real ids
    room.hand.stack = {"u1": 80, "u2": 95}
    room.hand.paid = {"u1": 20, "u2": 5}
    wallstreet.rooms["x"] = room
    await wallstreet.refund_all_wallstreet_rooms()
    assert bal == {"u1": 1100, "u2": 1100} and "x" not in wallstreet.rooms
