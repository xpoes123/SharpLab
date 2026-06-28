"""A short opened via the HQ trade panel must (a) take the position negative and
(b) credit the sale proceeds to cash — otherwise the short reads as an instant loss.
This mirrors the data operations hq_stock_trade performs (no HTTP needed)."""
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


async def _web_stock_trade(uid, ticker, side, shares, price):
    """Exactly what hq_stock_trade does to the DB: record + move cash like Discord."""
    await _queries.add_stock_trade(uid, ticker, side, shares, price)
    flow = shares * price * (-1 if side == "buy" else 1)
    return await _queries.adjust_stock_cash(uid, flow, allow_negative=True)


def test_oversell_opens_short_and_credits_cash(tmp_db):
    _run(_queries.set_stock_cash("u1", 10_000.0))
    # own 10, then sell 30 → net −20 (short), proceeds credited at each sell.
    _run(_web_stock_trade("u1", "AAPL", "buy", 10, 250.0))    # −2500
    _run(_web_stock_trade("u1", "AAPL", "sell", 30, 250.0))   # +7500
    holding = _run(_queries.get_stock_holding("u1", "AAPL"))
    assert holding["shares"] == pytest.approx(-20.0)          # short 20
    assert _run(_queries.get_stock_cash("u1")) == pytest.approx(15_000.0)  # 10000 −2500 +7500


def test_short_value_offset_by_cash_is_flat_at_entry(tmp_db):
    # Shorting from flat: position value −(shares·price) must be offset by the cash
    # proceeds, so net account value is unchanged at entry (then profits as price falls).
    _run(_queries.set_stock_cash("u1", 0.0))
    cash = _run(_web_stock_trade("u1", "TSLA", "sell", 5, 100.0))   # short 5 @ 100
    holding = _run(_queries.get_stock_holding("u1", "TSLA"))
    position_value = holding["shares"] * 100.0                      # −500 at entry price
    assert cash == pytest.approx(500.0)
    assert position_value + cash == pytest.approx(0.0)              # flat at entry


def test_buy_to_cover_reduces_short(tmp_db):
    _run(_queries.set_stock_cash("u1", 0.0))
    _run(_web_stock_trade("u1", "NVDA", "sell", 10, 100.0))   # short 10
    _run(_web_stock_trade("u1", "NVDA", "buy", 4, 80.0))      # cover 4 cheaper
    holding = _run(_queries.get_stock_holding("u1", "NVDA"))
    assert holding["shares"] == pytest.approx(-6.0)           # still short 6
    assert _run(_queries.get_stock_cash("u1")) == pytest.approx(1000.0 - 320.0)


if __name__ == "__main__":
    import subprocess, sys
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
