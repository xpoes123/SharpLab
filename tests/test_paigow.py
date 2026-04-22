"""Tests for Pai Gow Poker hand evaluation, house way, and fortune bonus."""
from bot.cogs.paigow import (
    JOKER,
    _best_5_from_7,
    _evaluate_2,
    _evaluate_5,
    _evaluate_5_no_joker,
    _fortune_payout,
    _hand_name_2,
    _hand_name_5,
    _house_way,
    _new_deck,
    _valid_setting,
)


# ── Deck ─────────────────────────────────────────────────────────────────────


def test_deck_has_53_cards():
    deck = _new_deck()
    assert len(deck) == 53
    assert JOKER in deck


def test_deck_unique():
    deck = _new_deck()
    assert len(set(deck)) == 53


# ── 5-card evaluation (no joker) ────────────────────────────────────────────


class TestEvaluate5NoJoker:
    def test_high_card(self):
        hand = ["2♠", "5♥", "8♦", "J♣", "A♠"]
        score = _evaluate_5_no_joker(hand)
        assert score[0] == 0  # high card
        assert score[1] == 14  # ace high

    def test_one_pair(self):
        hand = ["K♠", "K♥", "3♦", "7♣", "9♠"]
        score = _evaluate_5_no_joker(hand)
        assert score[0] == 1
        assert score[1] == 13  # pair of kings

    def test_two_pair(self):
        hand = ["A♠", "A♥", "5♦", "5♣", "9♠"]
        score = _evaluate_5_no_joker(hand)
        assert score[0] == 2
        assert score[1] == 14  # aces
        assert score[2] == 5   # fives

    def test_three_of_a_kind(self):
        hand = ["7♠", "7♥", "7♦", "2♣", "9♠"]
        score = _evaluate_5_no_joker(hand)
        assert score[0] == 3
        assert score[1] == 7

    def test_straight(self):
        hand = ["5♠", "6♥", "7♦", "8♣", "9♠"]
        score = _evaluate_5_no_joker(hand)
        assert score[0] == 4
        assert score[1] == 9  # 9-high straight

    def test_ace_low_straight(self):
        hand = ["A♠", "2♥", "3♦", "4♣", "5♠"]
        score = _evaluate_5_no_joker(hand)
        assert score[0] == 4
        assert score[1] == 5  # 5-high (wheel)

    def test_ace_high_straight(self):
        hand = ["10♠", "J♥", "Q♦", "K♣", "A♠"]
        score = _evaluate_5_no_joker(hand)
        assert score[0] == 4
        assert score[1] == 14

    def test_flush(self):
        hand = ["2♠", "5♠", "8♠", "J♠", "A♠"]
        score = _evaluate_5_no_joker(hand)
        assert score[0] == 5

    def test_full_house(self):
        hand = ["Q♠", "Q♥", "Q♦", "3♣", "3♠"]
        score = _evaluate_5_no_joker(hand)
        assert score[0] == 6
        assert score[1] == 12  # queens
        assert score[2] == 3   # threes

    def test_four_of_a_kind(self):
        hand = ["9♠", "9♥", "9♦", "9♣", "2♠"]
        score = _evaluate_5_no_joker(hand)
        assert score[0] == 7
        assert score[1] == 9

    def test_straight_flush(self):
        hand = ["5♥", "6♥", "7♥", "8♥", "9♥"]
        score = _evaluate_5_no_joker(hand)
        assert score[0] == 9
        assert score[1] == 9

    def test_royal_flush(self):
        hand = ["10♠", "J♠", "Q♠", "K♠", "A♠"]
        score = _evaluate_5_no_joker(hand)
        assert score[0] == 9
        assert score[1] == 14

    def test_pair_beats_high_card(self):
        pair = _evaluate_5_no_joker(["2♠", "2♥", "3♦", "4♣", "5♠"])
        high = _evaluate_5_no_joker(["A♠", "K♥", "Q♦", "J♣", "9♠"])
        assert pair > high

    def test_flush_beats_straight(self):
        flush = _evaluate_5_no_joker(["2♠", "5♠", "8♠", "J♠", "A♠"])
        straight = _evaluate_5_no_joker(["5♥", "6♦", "7♣", "8♠", "9♥"])
        assert flush > straight


# ── 5-card evaluation with joker ────────────────────────────────────────────


class TestEvaluate5WithJoker:
    def test_joker_makes_pair_of_aces(self):
        hand = ["A♠", JOKER, "3♦", "7♣", "9♠"]
        score = _evaluate_5(hand)
        # Joker should become an Ace for pair of aces (or better if straight/flush possible)
        assert score[0] >= 1  # at least a pair

    def test_joker_completes_straight(self):
        hand = ["5♠", "6♥", JOKER, "8♣", "9♠"]
        score = _evaluate_5(hand)
        assert score[0] == 4  # straight
        assert score[1] == 9  # 9-high

    def test_joker_completes_flush(self):
        hand = ["2♠", "5♠", JOKER, "J♠", "A♠"]
        score = _evaluate_5(hand)
        assert score[0] >= 5  # at least flush (could be straight flush)

    def test_joker_completes_straight_flush(self):
        hand = ["5♥", "6♥", "7♥", JOKER, "9♥"]
        score = _evaluate_5(hand)
        assert score[0] == 9  # straight flush

    def test_five_aces(self):
        hand = ["A♠", "A♥", "A♦", "A♣", JOKER]
        score = _evaluate_5(hand)
        assert score[0] == 10  # five aces — highest hand

    def test_five_aces_beats_royal(self):
        five_aces = _evaluate_5(["A♠", "A♥", "A♦", "A♣", JOKER])
        royal = _evaluate_5_no_joker(["10♠", "J♠", "Q♠", "K♠", "A♠"])
        assert five_aces > royal

    def test_joker_pairs_highest_card(self):
        hand = [JOKER, "3♦", "7♣", "9♠", "K♥"]
        score = _evaluate_5(hand)
        # Joker becomes K for pair of Kings (beats Ace-high)
        assert score[0] == 1  # one pair
        assert score[1] == 13  # pair of kings


# ── 2-card evaluation ───────────────────────────────────────────────────────


class TestEvaluate2:
    def test_pair(self):
        score = _evaluate_2(["K♠", "K♥"])
        assert score[0] == 1
        assert score[1] == 13

    def test_high_card(self):
        score = _evaluate_2(["A♠", "7♥"])
        assert score == (0, 14, 7)

    def test_pair_beats_high_card(self):
        pair = _evaluate_2(["2♠", "2♥"])
        high = _evaluate_2(["A♠", "K♥"])
        assert pair > high

    def test_joker_becomes_ace(self):
        score = _evaluate_2([JOKER, "7♥"])
        assert score == (0, 14, 7)  # ace-seven

    def test_joker_ace_pair(self):
        score = _evaluate_2(["A♠", JOKER])
        assert score == (1, 14)  # pair of aces


# ── Hand names ───────────────────────────────────────────────────────────────


class TestHandNames:
    def test_royal_flush_name(self):
        assert _hand_name_5((9, 14)) == "Royal Flush"

    def test_straight_flush_name(self):
        assert "Straight Flush" in _hand_name_5((9, 9))

    def test_pair_name(self):
        assert _hand_name_5((1, 13, 9, 7, 3)) == "Pair of Kings"

    def test_two_pair_name(self):
        name = _hand_name_5((2, 14, 5, 9))
        assert "Aces" in name and "Fives" in name

    def test_low_hand_pair(self):
        assert _hand_name_2((1, 13)) == "Pair of Kings"

    def test_low_hand_high_card(self):
        assert _hand_name_2((0, 14, 7)) == "Ace-Seven"


# ── Validation ───────────────────────────────────────────────────────────────


class TestValidSetting:
    def test_valid_pair_high_pair_low(self):
        high = ["K♠", "K♥", "3♦", "7♣", "9♠"]
        low = ["5♠", "2♥"]
        assert _valid_setting(high, low) is True

    def test_foul_high_card_vs_low_pair(self):
        high = ["A♠", "K♥", "8♦", "3♣", "2♠"]
        low = ["5♠", "5♥"]
        assert _valid_setting(high, low) is False

    def test_valid_both_high_card(self):
        high = ["A♠", "K♥", "Q♦", "J♣", "9♠"]
        low = ["7♠", "3♥"]
        assert _valid_setting(high, low) is True


# ── House way ────────────────────────────────────────────────────────────────


class TestHouseWay:
    def test_returns_5_and_2(self):
        cards = ["A♠", "K♥", "Q♦", "J♣", "9♠", "7♥", "3♦"]
        high, low = _house_way(cards)
        assert len(high) == 5
        assert len(low) == 2

    def test_all_cards_accounted_for(self):
        cards = ["A♠", "K♥", "Q♦", "J♣", "9♠", "7♥", "3♦"]
        high, low = _house_way(cards)
        combined = sorted(high + low)
        assert sorted(cards) == combined

    def test_valid_setting(self):
        cards = ["A♠", "K♥", "Q♦", "J♣", "9♠", "7♥", "3♦"]
        high, low = _house_way(cards)
        assert _valid_setting(high, low) is True

    def test_splits_full_house(self):
        """House way should split a full house: trips in high, pair in low."""
        cards = ["Q♠", "Q♥", "Q♦", "7♣", "7♠", "3♥", "2♦"]
        high, low = _house_way(cards)
        lo_score = _evaluate_2(low)
        # Low hand should have a pair (the 7s)
        assert lo_score[0] == 1  # pair in low hand

    def test_splits_two_pair(self):
        """With two pair, house way should split them."""
        cards = ["A♠", "A♥", "5♦", "5♣", "9♠", "7♥", "3♦"]
        high, low = _house_way(cards)
        lo_score = _evaluate_2(low)
        # Should put one pair in low hand
        assert lo_score[0] == 1  # pair

    def test_with_joker(self):
        cards = ["A♠", JOKER, "Q♦", "J♣", "9♠", "7♥", "3♦"]
        high, low = _house_way(cards)
        assert len(high) == 5
        assert len(low) == 2
        assert _valid_setting(high, low) is True


# ── Fortune Bonus ────────────────────────────────────────────────────────────


class TestFortune:
    def test_no_qualifying_hand(self):
        cards = ["2♠", "5♥", "8♦", "J♣", "A♠", "3♥", "6♦"]
        payout, label = _fortune_payout(cards, 10)
        assert payout == 0

    def test_straight(self):
        cards = ["5♠", "6♥", "7♦", "8♣", "9♠", "2♥", "K♦"]
        payout, label = _fortune_payout(cards, 10)
        assert payout == 20  # 2:1
        assert label == "Straight"

    def test_flush(self):
        cards = ["2♠", "5♠", "8♠", "J♠", "A♠", "3♥", "6♦"]
        payout, label = _fortune_payout(cards, 10)
        assert payout == 40  # 4:1

    def test_full_house(self):
        cards = ["Q♠", "Q♥", "Q♦", "7♣", "7♠", "3♥", "2♦"]
        payout, label = _fortune_payout(cards, 10)
        assert payout == 50  # 5:1

    def test_four_of_a_kind(self):
        cards = ["9♠", "9♥", "9♦", "9♣", "2♠", "5♥", "K♦"]
        payout, label = _fortune_payout(cards, 10)
        assert payout == 250  # 25:1

    def test_five_aces(self):
        cards = ["A♠", "A♥", "A♦", "A♣", JOKER, "5♥", "K♦"]
        payout, label = _fortune_payout(cards, 10)
        assert payout == 4000  # 400:1


# ── Best 5 from 7 ───────────────────────────────────────────────────────────


class TestBest5From7:
    def test_finds_flush_in_7(self):
        cards = ["2♠", "5♠", "8♠", "J♠", "A♠", "3♥", "6♦"]
        score = _best_5_from_7(cards)
        assert score[0] == 5  # flush

    def test_finds_straight_in_7(self):
        cards = ["3♠", "5♥", "6♦", "7♣", "8♠", "9♥", "K♦"]
        score = _best_5_from_7(cards)
        assert score[0] == 4  # straight
