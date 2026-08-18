"""Video Poker hand evaluation + Hi-Lo odds — the payout-determining logic."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import web.videopoker as vp  # noqa: E402
import web.hilo as hl  # noqa: E402


def test_videopoker_evaluate():
    assert vp.evaluate(["10♠", "J♠", "Q♠", "K♠", "A♠"]) == "royal_flush"
    assert vp.evaluate(["5♥", "6♥", "7♥", "8♥", "9♥"]) == "straight_flush"
    assert vp.evaluate(["A♠", "5♥", "4♦", "3♣", "2♠"]) == "straight"  # wheel A-2-3-4-5
    assert vp.evaluate(["9♠", "9♥", "9♦", "9♣", "2♠"]) == "four_kind"
    assert vp.evaluate(["9♠", "9♥", "9♦", "K♣", "K♠"]) == "full_house"
    assert vp.evaluate(["2♠", "5♠", "9♠", "J♠", "K♠"]) == "flush"
    assert vp.evaluate(["7♠", "7♥", "7♦", "2♣", "5♠"]) == "three_kind"
    assert vp.evaluate(["7♠", "7♥", "4♦", "4♣", "5♠"]) == "two_pair"
    assert vp.evaluate(["J♠", "J♥", "4♦", "8♣", "5♠"]) == "jacks_or_better"
    assert vp.evaluate(["9♠", "9♥", "4♦", "8♣", "5♠"]) == "none"  # low pair — no win
    assert vp.evaluate(["2♠", "7♥", "9♦", "J♣", "K♠"]) == "none"


def test_hilo_odds_symmetry_and_edges():
    # rank 8 (of 2..14): 6 higher (9-14), 6 lower (2-7)
    o = hl._odds(8)
    assert o["higher"]["count"] == 6 and o["lower"]["count"] == 6
    assert o["higher"]["mult"] > 1  # 0.95 * 13 / 6 ≈ 2.06
    # extremes: an ace (14) can't go higher; a 2 can't go lower
    assert hl._odds(14)["higher"]["count"] == 0 and hl._odds(14)["higher"]["mult"] == 0
    assert hl._odds(2)["lower"]["count"] == 0
