"""Tests for Stock Guess game logic (ELO model — no coins)."""

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.stockguess import (
    CURATED_TICKERS,
    DEFAULT_ROUNDS,
    StockGuessPlayer,
    StockGuessTable,
    compute_rankings,
    fetch_ytd_change,
    parse_guess,
    round_winners,
    _pick_next_stock,
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
    return {uid: StockGuessPlayer(user_id=uid, display_name=f"P{uid}") for uid in ids}


class TestComputeRankings:
    def test_sorted_by_accuracy(self):
        players = _make_players(1, 2, 3)
        guesses = {1: 10.0, 2: 15.0, 3: -5.0}
        actual = 12.5

        rankings = compute_rankings(players, guesses, actual)

        # P1: error=2.5, P2: error=2.5, P3: error=17.5
        assert rankings[0][0] == 1
        assert rankings[1][0] == 2
        assert rankings[2][0] == 3
        assert rankings[0][2] == pytest.approx(2.5)
        assert rankings[2][2] == pytest.approx(17.5)

    def test_no_guess_gets_worst_ranking(self):
        players = _make_players(1, 2, 3)
        guesses = {1: 10.0, 2: 15.0}  # P3 didn't guess
        actual = 12.5

        rankings = compute_rankings(players, guesses, actual)
        assert rankings[-1][0] == 3
        assert rankings[-1][2] == float("inf")
        assert math.isnan(rankings[-1][1])

    def test_exact_guess(self):
        players = _make_players(1)
        guesses = {1: 12.5}
        rankings = compute_rankings(players, guesses, 12.5)
        assert rankings[0][2] == pytest.approx(0.0)


# ── Round winners (scoring) ───────────────────────────────────────────────────


class TestRoundWinners:
    def test_single_closest_wins(self):
        rankings = [(1, 10.0, 1.0), (2, 20.0, 5.0), (3, -5.0, 17.0)]
        assert round_winners(rankings) == [1]

    def test_tie_shares_the_point(self):
        # Two players equally close — both win the round
        rankings = [(1, 10.0, 2.5), (2, 15.0, 2.5), (3, -5.0, 17.5)]
        assert set(round_winners(rankings)) == {1, 2}

    def test_no_finite_guesses_no_winner(self):
        rankings = [(1, float("nan"), float("inf")), (2, float("nan"), float("inf"))]
        assert round_winners(rankings) == []

    def test_scores_accumulate_across_rounds(self):
        table = StockGuessTable(channel_id=1, host_id=1, host_name="host", total_rounds=2)
        table.players = _make_players(1, 2)
        # Round 1: P1 closest; Round 2: P2 closest
        for uid in round_winners([(1, 0.0, 1.0), (2, 0.0, 5.0)]):
            table.players[uid].score += 1
        for uid in round_winners([(1, 0.0, 9.0), (2, 0.0, 2.0)]):
            table.players[uid].score += 1
        assert table.players[1].score == 1
        assert table.players[2].score == 1


# ── YTD fetch (mocked) ──────────────────────────────────────────────────────


class TestFetchYtdChange:
    @pytest.mark.asyncio
    async def test_fetch_ytd_empty_raises(self):
        mock_hist = MagicMock()
        mock_hist.empty = True
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_hist
        with patch("yfinance.Ticker", return_value=mock_ticker):
            with pytest.raises((ValueError, Exception)):
                await fetch_ytd_change("INVALID")


# ── StockGuessTable fields ───────────────────────────────────────────────────


class TestStockGuessTable:
    def test_default_rounds(self):
        table = StockGuessTable(channel_id=1, host_id=1, host_name="host")
        assert table.total_rounds == DEFAULT_ROUNDS

    def test_custom_rounds(self):
        table = StockGuessTable(channel_id=1, host_id=1, host_name="host", total_rounds=3)
        assert table.total_rounds == 3

    def test_initial_round_num_is_zero(self):
        table = StockGuessTable(channel_id=1, host_id=1, host_name="host")
        assert table.round_num == 0

    def test_players_start_with_zero_score(self):
        p = StockGuessPlayer(user_id=1, display_name="x")
        assert p.score == 0


# ── _pick_next_stock ─────────────────────────────────────────────────────────


class TestPickNextStock:
    @pytest.mark.asyncio
    async def test_returns_ticker_from_curated_list(self):
        table = StockGuessTable(channel_id=1, host_id=1, host_name="host")
        with patch("bot.cogs.stockguess.fetch_ytd_change", new_callable=AsyncMock, return_value=5.0):
            ticker, company, ytd_pct = await _pick_next_stock(table)
        assert ticker in CURATED_TICKERS
        assert company == CURATED_TICKERS[ticker]
        assert ytd_pct == 5.0

    @pytest.mark.asyncio
    async def test_avoids_used_tickers(self):
        table = StockGuessTable(channel_id=1, host_id=1, host_name="host")
        table.used_tickers = set(CURATED_TICKERS.keys()) - {"AAPL"}
        with patch("bot.cogs.stockguess.fetch_ytd_change", new_callable=AsyncMock, return_value=12.5):
            ticker, company, _ = await _pick_next_stock(table)
        assert ticker == "AAPL"
        assert company == "Apple"

    @pytest.mark.asyncio
    async def test_resets_when_all_used(self):
        table = StockGuessTable(channel_id=1, host_id=1, host_name="host")
        table.used_tickers = set(CURATED_TICKERS.keys())
        with patch("bot.cogs.stockguess.fetch_ytd_change", new_callable=AsyncMock, return_value=5.0):
            ticker, _, _ = await _pick_next_stock(table)
        assert len(table.used_tickers) == 1
        assert ticker in CURATED_TICKERS

    @pytest.mark.asyncio
    async def test_no_repeat_across_calls(self):
        table = StockGuessTable(channel_id=1, host_id=1, host_name="host")
        with patch("bot.cogs.stockguess.fetch_ytd_change", new_callable=AsyncMock, return_value=5.0):
            ticker1, _, _ = await _pick_next_stock(table)
            ticker2, _, _ = await _pick_next_stock(table)
        assert ticker1 != ticker2
