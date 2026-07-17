"""Cash now moves with trades: buys debit, sells credit, options too (premium×100).
Trade-driven debits are floored at 0 (a buy bigger than the balance is funded by
money the user already had — cash matters once selling generates proceeds), and
manual realized-gain injection credits cash while logging to the realized total."""
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


def test_adjust_floors_at_zero_by_default(tmp_db):
    _run(_queries.set_stock_cash("u1", 100.0))
    assert _run(_queries.adjust_stock_cash("u1", -500.0)) == 0.0  # floored


def test_adjust_allows_negative_when_requested(tmp_db):
    _run(_queries.set_stock_cash("u1", 100.0))
    bal = _run(_queries.adjust_stock_cash("u1", -500.0, allow_negative=True))
    assert bal == -400.0  # overdraft allowed


def test_buy_then_sell_cash_round_trip(tmp_db):
    # Mirror the command flow: a buy debits cost (floored at 0), a sell credits.
    _run(_queries.set_stock_cash("u1", 1000.0))
    _run(_queries.adjust_stock_cash("u1", -(10 * 100.0)))  # buy 10 @100
    assert _run(_queries.get_stock_cash("u1")) == 0.0
    _run(_queries.adjust_stock_cash("u1", 10 * 150.0))      # sell 10 @150
    assert _run(_queries.get_stock_cash("u1")) == 1500.0  # +500 realized gain landed in cash


def test_buy_over_balance_floors_no_deposit(tmp_db):
    # The never-deposit model: cash starts at 0, a buy spends "prior capital" and
    # must NOT drive cash negative — otherwise account value goes negative and the
    # return-% math flips sign. Selling then creates real, positive proceeds.
    assert _run(_queries.get_stock_cash("u1")) == 0.0
    _run(_queries.adjust_stock_cash("u1", -(10 * 100.0)))   # buy 10 @100 on $0 cash
    assert _run(_queries.get_stock_cash("u1")) == 0.0       # floored, not −1000
    _run(_queries.adjust_stock_cash("u1", 10 * 150.0))      # sell 10 @150
    assert _run(_queries.get_stock_cash("u1")) == 1500.0


def test_option_premium_moves_cash_x100(tmp_db):
    # Buy 2 contracts @ $3.00 → −$600 (floored); sell 2 @ $5.00 → +$1000.
    _run(_queries.set_stock_cash("u1", 1000.0))
    _run(_queries.adjust_stock_cash("u1", -(2 * 3.0 * 100)))
    assert _run(_queries.get_stock_cash("u1")) == 400.0
    _run(_queries.adjust_stock_cash("u1", 2 * 5.0 * 100))
    assert _run(_queries.get_stock_cash("u1")) == 1400.0


def test_realized_adjustment_total_and_bulk(tmp_db):
    assert _run(_queries.get_realized_adjustment_total("u1")) == 0.0
    _run(_queries.add_realized_adjustment("u1", 500.0, "robinhood seed"))
    _run(_queries.add_realized_adjustment("u1", -120.0, "a loss"))
    _run(_queries.add_realized_adjustment("u2", 50.0, None))
    assert _run(_queries.get_realized_adjustment_total("u1")) == 380.0
    assert _run(_queries.get_all_realized_adjustments()) == {"u1": 380.0, "u2": 50.0}


def test_gains_injection_credits_cash(tmp_db):
    # The /stock gains flow: log the adjustment AND credit cash.
    _run(_queries.set_stock_cash("u1", 200.0))
    _run(_queries.add_realized_adjustment("u1", 500.0, "seed"))
    _run(_queries.adjust_stock_cash("u1", 500.0, allow_negative=True))
    assert _run(_queries.get_stock_cash("u1")) == 700.0
    assert _run(_queries.get_realized_adjustment_total("u1")) == 500.0
