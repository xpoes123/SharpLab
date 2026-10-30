"""Sub-account tagging: trades carry an optional account; per-account positions
split a ticker held in several accounts while the overall holding stays merged."""
from __future__ import annotations

import asyncio

import pytest

import db.schema as _schema
import db.queries as _queries
from scripts.bulk_stock import apply_portfolio, parse_csv


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


def test_untagged_user_has_no_split(tmp_db):
    _run(_queries.add_stock_trade("u1", "VOO", "buy", 1, 500.0))
    assert _run(_queries.get_stock_positions_by_account("u1")) == {}
    assert _run(_queries.get_stock_accounts("u1")) == []


def test_import_splits_same_ticker_across_accounts(tmp_db):
    rows = parse_csv("ticker,shares,price,account\n"
                     "VOO,80,500,Roth IRA\nVOO,20,600,HSA\nMSFT,4,372,Roth IRA\n")
    res = _run(apply_portfolio("u1", rows, 1000.0))
    assert res["positions"] == 2
    voo = _run(_queries.get_stock_holding("u1", "VOO"))
    assert voo["shares"] == 100 and voo["dca_price"] == pytest.approx(520.0)
    split = _run(_queries.get_stock_positions_by_account("u1"))
    assert split["VOO"]["Roth IRA"]["shares"] == 80
    assert split["VOO"]["HSA"]["dca_price"] == 600
    assert set(split["MSFT"]) == {"Roth IRA"}
    assert _run(_queries.get_stock_accounts("u1")) == ["HSA", "Roth IRA"]


def test_sell_from_one_account_and_untagged_goes_to_other(tmp_db):
    _run(_queries.add_stock_trade("u1", "VOO", "buy", 10, 500.0, account="HSA"))
    _run(_queries.add_stock_trade("u1", "VOO", "buy", 5, 500.0, account="Roth IRA"))
    _run(_queries.add_stock_trade("u1", "VOO", "sell", 10, 550.0, account="HSA"))
    _run(_queries.add_stock_trade("u1", "AAPL", "buy", 1, 200.0))
    split = _run(_queries.get_stock_positions_by_account("u1"))
    assert split["VOO"] == {"Roth IRA": split["VOO"]["Roth IRA"]}   # HSA closed out
    assert split["AAPL"]["Other"]["shares"] == 1
