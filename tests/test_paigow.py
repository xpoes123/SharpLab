"""Tests for Pai Gow Poker hand evaluation, house way, and fortune bonus."""
import asyncio
import sys

sys.path.insert(0, ".")

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


def run(coro):
    return asyncio.run(coro)


# ── /paigow close command ────────────────────────────────────────────────────


class TestPaiGowCloseCommand:
    """Force-close command should refund players and remove table from active_tables."""

    def _make_table(self):
        from bot.cogs.paigow import PaiGowTable
        return PaiGowTable(channel_id=5, opener_id=10, opener_name="Alice")

    def test_close_removes_table(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from bot.cogs.paigow import PaiGowCog

        table = self._make_table()
        cog = PaiGowCog.__new__(PaiGowCog)
        cog.active_tables = {5: table}

        interaction = MagicMock()
        interaction.channel_id = 5
        interaction.user = MagicMock()
        interaction.user.display_name = "Admin"
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        async def _run():
            with patch("bot.cogs.paigow.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock(return_value=1000)
                table.message = None
                await cog.paigow_close.callback(cog, interaction)

        run(_run())
        assert 5 not in cog.active_tables
        assert table.phase == "finished"

    def test_close_no_table_in_channel(self):
        from unittest.mock import AsyncMock, MagicMock
        from bot.cogs.paigow import PaiGowCog

        cog = PaiGowCog.__new__(PaiGowCog)
        cog.active_tables = {}

        interaction = MagicMock()
        interaction.channel_id = 99
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        run(cog.paigow_close.callback(cog, interaction))
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True

    def test_close_refunds_players(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from bot.cogs.paigow import PaiGowCog, PaiGowTable, PaiGowSeat

        table = PaiGowTable(channel_id=7, opener_id=1, opener_name="Dealer")
        table.players[42] = PaiGowSeat(user_id=42, display_name="Bob", wager=100, fortune_bet=20)
        table.players[43] = PaiGowSeat(user_id=43, display_name="Carol", wager=50)

        cog = PaiGowCog.__new__(PaiGowCog)
        cog.active_tables = {7: table}

        refunded: dict[str, int] = {}

        async def fake_update(uid, amount):
            refunded[uid] = amount
            return 1000

        interaction = MagicMock()
        interaction.channel_id = 7
        interaction.user = MagicMock()
        interaction.user.display_name = "Admin"
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        async def _run():
            with patch("bot.cogs.paigow.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock(side_effect=fake_update)
                table.message = None
                await cog.paigow_close.callback(cog, interaction)

        run(_run())
        assert refunded.get("42") == 120  # wager + fortune_bet
        assert refunded.get("43") == 50
        assert 7 not in cog.active_tables

    def test_close_removes_table_even_if_db_fails(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from bot.cogs.paigow import PaiGowCog, PaiGowTable, PaiGowSeat

        table = PaiGowTable(channel_id=8, opener_id=1, opener_name="Dealer")
        table.players[99] = PaiGowSeat(user_id=99, display_name="Eve", wager=200)

        cog = PaiGowCog.__new__(PaiGowCog)
        cog.active_tables = {8: table}

        interaction = MagicMock()
        interaction.channel_id = 8
        interaction.user = MagicMock()
        interaction.user.display_name = "Admin"
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        async def _run():
            with patch("bot.cogs.paigow.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock(side_effect=RuntimeError("DB down"))
                table.message = None
                await cog.paigow_close.callback(cog, interaction)

        run(_run())
        assert 8 not in cog.active_tables
        assert table.phase == "finished"


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

    def test_foul_9high_five_card_vs_ace_high_two_card(self):
        """Regression: 9-high 5-card with ace-high 2-card is a foul (reported bug)."""
        high = ["2♦", "3♥", "5♣", "6♠", "9♦"]  # 9-high
        low = ["A♥", "K♠"]                        # ace-high
        assert _valid_setting(high, low) is False

    def test_foul_low_high_card_outranks_five_card(self):
        """Any 2-card high-card hand that beats the 5-card high card is a foul."""
        high = ["2♦", "3♥", "5♣", "6♠", "8♦"]  # 8-high
        low = ["9♥", "K♠"]                        # king-high
        assert _valid_setting(high, low) is False


class TestHouseWayNoFoul:
    def test_house_way_never_fouls(self):
        """house_way must always produce a valid (non-foul) setting."""
        import random
        from bot.cogs.paigow import _new_deck
        rng = random.Random(42)
        deck = _new_deck()
        rng.shuffle(deck)
        for _ in range(200):
            rng.shuffle(deck)
            cards = deck[:7]
            high, low = _house_way(cards)
            assert _valid_setting(high, low), (
                f"Foul produced by house_way: high={high}, low={low}"
            )


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
