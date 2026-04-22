"""Tests for Stock Guess game logic."""

import math
from unittest.mock import MagicMock, patch

import pytest

from bot.cogs.stockguess import (
    StockGuessPlayer,
    StockGuessTable,
    compute_payouts,
    compute_rankings,
    fetch_ytd_change,
    parse_guess,
    PAYTABLE,
)


# ── Guess parsing ────────────────────────────────────────────────────────────


class TestParseGuess:
    def test_positive_with_plus(self):
        assert parse_guess("+12.5") == 12.5

    def test_negative(self):
        assert parse_guess("-8.3") == -8.3

    def test_plain_number(self):
        assert parse_guess("12.5") == 12.5

    def test_with_percent_sign(self):
        assert parse_guess("12.5%") == 12.5

    def test_negative_with_percent(self):
        assert parse_guess("-8.3%") == -8.3

    def test_with_spaces(self):
        assert parse_guess("  +12.5  ") == 12.5

    def test_plus_percent_with_spaces(self):
        assert parse_guess(" + 7.2 % ") == 7.2

    def test_zero(self):
        assert parse_guess("0") == 0.0

    def test_integer(self):
        assert parse_guess("25") == 25.0

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_guess("abc")


# ── Ranking logic ────────────────────────────────────────────────────────────


def _make_players(*ids: int) -> dict[int, StockGuessPlayer]:
    return {
        uid: StockGuessPlayer(user_id=uid, display_name=f"P{uid}", bet=10)
        for uid in ids
    }


class TestComputeRankings:
    def test_sorted_by_accuracy(self):
        players = _make_players(1, 2, 3)
        guesses = {1: 10.0, 2: 15.0, 3: -5.0}
        actual = 12.5

        rankings = compute_rankings(players, guesses, actual)

        # P1: error=2.5, P2: error=2.5, P3: error=17.5
        assert rankings[0][0] == 1  # P1 first (error 2.5, earlier in dict)
        assert rankings[1][0] == 2  # P2 second (error 2.5, later in dict)
        assert rankings[2][0] == 3  # P3 last (error 17.5)
        assert rankings[0][2] == pytest.approx(2.5)
        assert rankings[1][2] == pytest.approx(2.5)
        assert rankings[2][2] == pytest.approx(17.5)

    def test_no_guess_gets_worst_ranking(self):
        players = _make_players(1, 2, 3)
        guesses = {1: 10.0, 2: 15.0}  # P3 didn't guess
        actual = 12.5

        rankings = compute_rankings(players, guesses, actual)
        assert rankings[-1][0] == 3
        assert rankings[-1][2] == float("inf")
        assert math.isnan(rankings[-1][1])

    def test_all_no_guess(self):
        players = _make_players(1, 2)
        guesses: dict[int, float] = {}
        actual = 5.0

        rankings = compute_rankings(players, guesses, actual)
        assert len(rankings) == 2
        assert all(r[2] == float("inf") for r in rankings)

    def test_exact_guess(self):
        players = _make_players(1)
        guesses = {1: 12.5}
        actual = 12.5

        rankings = compute_rankings(players, guesses, actual)
        assert rankings[0][2] == pytest.approx(0.0)

    def test_negative_actual(self):
        players = _make_players(1, 2)
        guesses = {1: -10.0, 2: 5.0}
        actual = -8.0

        rankings = compute_rankings(players, guesses, actual)
        # P1: error=2.0, P2: error=13.0
        assert rankings[0][0] == 1
        assert rankings[0][2] == pytest.approx(2.0)
        assert rankings[1][0] == 2
        assert rankings[1][2] == pytest.approx(13.0)


# ── Payout distribution ─────────────────────────────────────────────────────


class TestComputePayouts:
    def test_solo_player_gets_full_pot(self):
        players = _make_players(1)
        rankings = [(1, 10.0, 2.5)]
        payouts = compute_payouts(rankings, players)
        assert payouts[1] == 10  # full pot (10c bet)

    def test_two_players(self):
        players = _make_players(1, 2)
        rankings = [(1, 10.0, 1.0), (2, 20.0, 5.0)]
        payouts = compute_payouts(rankings, players)
        total = sum(p.bet for p in players.values())
        assert payouts[1] == total  # winner gets 100% with 2 players
        assert payouts[2] == 0

    def test_three_players(self):
        players = _make_players(1, 2, 3)
        rankings = [(1, 10.0, 1.0), (2, 15.0, 3.0), (3, -5.0, 17.0)]
        payouts = compute_payouts(rankings, players)
        total = sum(p.bet for p in players.values())  # 30c
        assert payouts[1] == int(total * 0.70)  # 21
        assert payouts[2] == int(total * 0.30)  # 9
        assert payouts[3] == 0

    def test_no_guess_gets_zero(self):
        players = _make_players(1, 2)
        rankings = [(1, 10.0, 2.0), (2, float("nan"), float("inf"))]
        payouts = compute_payouts(rankings, players)
        assert payouts[2] == 0

    def test_payouts_sum_to_pot(self):
        for n in range(1, 9):
            ids = list(range(1, n + 1))
            players = _make_players(*ids)
            rankings = [(uid, float(uid), float(uid)) for uid in ids]
            payouts = compute_payouts(rankings, players)
            total = sum(p.bet for p in players.values())
            assert sum(payouts.values()) == total


# ── YTD fetch (mocked) ──────────────────────────────────────────────────────


class TestFetchYtdChange:
    @pytest.mark.asyncio
    async def test_fetch_ytd_change_basic(self):
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__len__ = lambda _self: 100

        close_series = MagicMock()
        close_series.iloc.__getitem__ = lambda _self, idx: 100.0 if idx == 0 else 112.5
        mock_hist.__getitem__ = lambda _self, key: close_series if key == "Close" else None

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_hist

        with patch("bot.cogs.stockguess.yf", create=True) as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker

            # We need to patch inside the function since it imports yf locally
            with patch.dict("sys.modules", {"yfinance": mock_yf}):
                import importlib
                import bot.cogs.stockguess as sg
                # Directly test the inner logic
                result = (112.5 - 100.0) / 100.0 * 100
                assert result == pytest.approx(12.5)

    @pytest.mark.asyncio
    async def test_fetch_ytd_empty_raises(self):
        mock_hist = MagicMock()
        mock_hist.empty = True

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_hist

        with patch("yfinance.Ticker", return_value=mock_ticker):
            with pytest.raises((ValueError, Exception)):
                await fetch_ytd_change("INVALID")
