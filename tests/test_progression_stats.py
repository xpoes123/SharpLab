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
