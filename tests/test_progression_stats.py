"""Regression tests for stat queries feeding the achievement checker.

A user with no tournament entries used to get wins=None (bare SUM over zero
rows), which crashed `_check_user_achievements` at `tourney_stats["wins"] >= 1`.
COUNT(*) is never NULL, so the existing `entries is not None` guard never
caught it. These tests pin the NULL-safe contract for the period stats."""
from __future__ import annotations

import asyncio
import sys

import pytest

sys.path.insert(0, ".")

import db.schema as _schema  # noqa: E402
import db.queries as _queries  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    orig_schema = _schema.DB_PATH
    orig_queries = _queries.DB_PATH
    _schema.DB_PATH = db_path
    _queries.DB_PATH = db_path
    _run(_schema.init_db())
    yield db_path
    _schema.DB_PATH = orig_schema
    _queries.DB_PATH = orig_queries


class TestStatsNullSafety:
    def test_tournament_stats_zero_entries_is_int_not_none(self, tmp_db):
        stats = _run(_queries.get_tournament_stats("nobody"))
        assert stats["entries"] == 0
        assert stats["wins"] == 0
        assert stats["wins"] is not None
        assert stats["total_payout"] == 0
        # The exact comparison that used to throw:
        assert (stats["wins"] >= 1) is False

    def test_duel_stats_zero_records_is_int_not_none(self, tmp_db):
        stats = _run(_queries.get_duel_stats("nobody"))
        assert stats["wins"] == 0 and stats["wins"] is not None
        assert (stats["wins"] >= 1) is False


from bot.cogs import progression as _prog  # noqa: E402


def _base_stats(**over):
    s = {
        "rounds": 0, "total_wagered": 0, "streak": 0, "max_profit": 0, "distinct": 0,
        "duel_wins": 0, "tourney_wins": 0, "balance": 0,
        "num_bets": 0, "bet_wins": 0, "pos_clv": 0,
        "num_trades": 0, "distinct_holdings": 0, "realized_pnl": 0,
        "traded_crypto": False, "has_options": False,
    }
    s.update(over)
    return s


def _earned(stats):
    return {aid for aid, ok in _prog._achievement_checks(stats) if ok}


class TestNewAchievementConditions:
    def test_betting(self):
        assert _earned(_base_stats(num_bets=1)) >= {"bet_first"}
        assert "bet_10" in _earned(_base_stats(num_bets=10))
        assert "bet_50" in _earned(_base_stats(num_bets=50))
        assert "bet_win" in _earned(_base_stats(bet_wins=1))
        assert "clv_beat" in _earned(_base_stats(pos_clv=1))
        assert "clv_10" in _earned(_base_stats(pos_clv=10))
        assert "clv_10" not in _earned(_base_stats(pos_clv=9))

    def test_investing(self):
        assert "stock_first" in _earned(_base_stats(num_trades=1))
        assert "stock_10" in _earned(_base_stats(num_trades=10))
        assert "stock_diversified" in _earned(_base_stats(distinct_holdings=10))
        assert "stock_green" in _earned(_base_stats(realized_pnl=1000))
        assert "stock_bull" in _earned(_base_stats(realized_pnl=10000))
        assert "stock_bull" not in _earned(_base_stats(realized_pnl=9999))
        assert "crypto_first" in _earned(_base_stats(traded_crypto=True))
        assert "options_first" in _earned(_base_stats(has_options=True))

    def test_empty_user_earns_nothing(self):
        assert _earned(_base_stats()) == set()

    def test_all_check_ids_are_real_achievements(self):
        ids = {a.id for a in _prog.ALL_ACHIEVEMENTS}
        for aid, _ in _prog._achievement_checks(_base_stats()):
            assert aid in ids


class TestEvaluateAndBackfill:
    def test_evaluate_unlocks_and_is_idempotent(self, tmp_db):
        async def go():
            await _queries.add_stock_trade("u", "AAPL", "buy", 10, 100)
            await _queries.add_stock_trade("u", "AAPL", "sell", 10, 250)  # +1500 realized
            await _queries.add_stock_trade("u", "BTC-USD", "buy", 0.1, 70000)
            first = await _prog.evaluate_user_achievements("u")
            second = await _prog.evaluate_user_achievements("u")
            return first, second
        first, second = _run(go())
        assert {"stock_first", "stock_green", "crypto_first"} <= set(first)
        assert second == []  # idempotent — already achieved
