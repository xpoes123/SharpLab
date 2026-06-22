"""Day P/L includes realized gains booked today. get_day_realized_pnl uses the
realized(all) − realized(before boundary) trick so each close is valued at the
average cost basis it actually closed against."""
from __future__ import annotations

import asyncio

import pytest

import db.schema as _schema
import db.queries as _queries


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    orig_s, orig_q = _schema.DB_PATH, _queries.DB_PATH
    _schema.DB_PATH = _queries.DB_PATH = db_path
    _run(_schema.init_db())
    yield db_path
    _schema.DB_PATH, _queries.DB_PATH = orig_s, orig_q


BOUNDARY = "2026-06-22T04:00:00+00:00"  # ET-midnight as UTC


def test_sell_today_counts_buy_yesterday_does_not(tmp_db):
    # Bought 10 @ 100 yesterday, sold 10 @ 110 today → +100 realized today.
    _run(_queries.add_stock_trade("u1", "AAA", "buy", 10, 100,
                                  executed_at="2026-06-21T15:00:00+00:00"))
    _run(_queries.add_stock_trade("u1", "AAA", "sell", 10, 110,
                                  executed_at="2026-06-22T15:00:00+00:00"))
    assert _run(_queries.get_day_realized_pnl("u1", BOUNDARY)) == pytest.approx(100.0)


def test_realized_before_boundary_excluded(tmp_db):
    # Whole round-trip happened yesterday → nothing realized today.
    _run(_queries.add_stock_trade("u1", "BBB", "buy", 5, 50,
                                  executed_at="2026-06-21T14:00:00+00:00"))
    _run(_queries.add_stock_trade("u1", "BBB", "sell", 5, 60,
                                  executed_at="2026-06-21T16:00:00+00:00"))
    assert _run(_queries.get_day_realized_pnl("u1", BOUNDARY)) == pytest.approx(0.0)


def test_manual_adjustment_today_included(tmp_db):
    _run(_queries.add_realized_adjustment("u1", 25.0))  # stamped now (today)
    # Use a far-past boundary so "now" counts as on/after it.
    assert _run(_queries.get_day_realized_pnl("u1", "2000-01-01T00:00:00+00:00")) \
        == pytest.approx(25.0)
