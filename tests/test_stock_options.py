"""Tests for stock cash positions and option trade accounting in db/queries.py
plus the pure option P/L helpers in bot/cogs/stock.py."""
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
    """Temp SQLite DB with the full schema, DB_PATH patched in both modules."""
    db_path = str(tmp_path / "test.db")
    orig_schema = _schema.DB_PATH
    orig_queries = _queries.DB_PATH
    _schema.DB_PATH = db_path
    _queries.DB_PATH = db_path
    _run(_schema.init_db())
    yield db_path
    _schema.DB_PATH = orig_schema
    _queries.DB_PATH = orig_queries


def _T(side, ct, px):
    return {"side": side, "contracts": ct, "premium": px}


def _S(side, shares, price):
    return {"side": side, "shares": shares, "price": price}


# ── Stock average-cost aggregator (invested / % return denominator) ──────────


class TestAggregateStockTrades:
    def test_invested_sums_buys(self):
        # buy 10 @100 ($1000), sell 5 @120 (realized +100), buy 2 @110 ($220)
        r = _queries._aggregate_trades(
            [_S("buy", 10, 100), _S("sell", 5, 120), _S("buy", 2, 110)]
        )
        assert r["realized_pnl"] == pytest.approx(100.0)  # 5 * (120 - 100)
        assert r["invested"] == pytest.approx(1220.0)     # 1000 + 220, capital deployed
        assert r["shares"] == pytest.approx(7)

    def test_closed_position_keeps_invested(self):
        # round trip: buy 4 @50 ($200), sell 4 @75 (+100). closed but invested stays.
        r = _queries._aggregate_trades([_S("buy", 4, 50), _S("sell", 4, 75)])
        assert r["closed"]
        assert r["realized_pnl"] == pytest.approx(100.0)
        assert r["invested"] == pytest.approx(200.0)
        # % return = total P/L / invested = 100 / 200 = 50%
        assert (r["realized_pnl"] / r["invested"]) == pytest.approx(0.5)


# ── Signed average-cost option aggregator ────────────────────────────────────


class TestAggregateOptionTrades:
    def test_long_round_trip(self):
        # buy 2 @3.50, sell 2 @5.00 -> +300 realized (×100), flat
        r = _queries._aggregate_option_trades([_T("buy", 2, 3.50), _T("sell", 2, 5.00)])
        assert r["closed"]
        assert r["realized_pnl"] == pytest.approx(300.0)
        assert r["invested"] == pytest.approx(700.0)  # 2 * 3.50 * 100

    def test_short_round_trip(self):
        # write 1 @4 then buy-to-cover 1 @2.50 -> +150 realized
        r = _queries._aggregate_option_trades([_T("sell", 1, 4.0), _T("buy", 1, 2.50)])
        assert r["closed"]
        assert r["realized_pnl"] == pytest.approx(150.0)
        assert r["invested"] == pytest.approx(400.0)  # opening the short: 1 * 4 * 100

    def test_position_flip(self):
        # long 2 @3, sell 5 @4 -> close 2 long (+200), open short 3 @4
        r = _queries._aggregate_option_trades([_T("buy", 2, 3.0), _T("sell", 5, 4.0)])
        assert r["net_contracts"] == -3
        assert r["avg_premium"] == pytest.approx(4.0)
        assert r["realized_pnl"] == pytest.approx(200.0)
        # capital in: 2*3*100 (long) + 3*4*100 (flipped-into short) = 600 + 1200
        assert r["invested"] == pytest.approx(1800.0)

    def test_average_on_adds(self):
        # buy 1 @2, buy 1 @4 -> long 2 @3 avg, no realized
        r = _queries._aggregate_option_trades([_T("buy", 1, 2.0), _T("buy", 1, 4.0)])
        assert r["net_contracts"] == 2
        assert r["avg_premium"] == pytest.approx(3.0)
        assert r["realized_pnl"] == 0.0
        assert r["invested"] == pytest.approx(600.0)  # 200 + 400

    def test_partial_cover_of_short(self):
        # write 3 @5, buy 1 @3 -> cover 1 (+200), short 2 @5
        r = _queries._aggregate_option_trades([_T("sell", 3, 5.0), _T("buy", 1, 3.0)])
        assert r["net_contracts"] == -2
        assert r["avg_premium"] == pytest.approx(5.0)
        assert r["realized_pnl"] == pytest.approx(200.0)
        # only the opening write deploys capital; the buy-to-cover doesn't
        assert r["invested"] == pytest.approx(1500.0)  # 3 * 5 * 100

    def test_losing_long(self):
        r = _queries._aggregate_option_trades([_T("buy", 1, 5.0), _T("sell", 1, 1.0)])
        assert r["closed"]
        assert r["realized_pnl"] == pytest.approx(-400.0)
        assert r["invested"] == pytest.approx(500.0)


# ── Option trade persistence + derived positions ─────────────────────────────


class TestOptionPositions:
    def test_round_trip_and_grouping(self, tmp_db):
        async def go():
            u = "42"
            await _queries.add_option_trade(u, "aapl", "call", 200, "2099-01-15", "buy", 2, 3.50)
            await _queries.add_option_trade(u, "AAPL", "call", 200, "2099-01-15", "buy", 1, 4.50)
            await _queries.add_option_trade(u, "TSLA", "put", 250, "2099-02-20", "sell", 1, 6.00)
            return await _queries.get_option_positions_full(u)

        pos = _run(go())
        assert len(pos) == 2
        aapl = next(p for p in pos if p["underlying"] == "AAPL")
        assert aapl["net_contracts"] == 3
        assert aapl["avg_premium"] == pytest.approx((2 * 3.5 + 4.5) / 3)
        tsla = next(p for p in pos if p["underlying"] == "TSLA")
        assert tsla["net_contracts"] == -1  # short
        assert tsla["avg_premium"] == pytest.approx(6.0)

    def test_ticker_normalized_uppercase(self, tmp_db):
        async def go():
            await _queries.add_option_trade("1", "nvda", "call", 1000, "2099-01-17", "buy", 1, 2.0)
            return await _queries.get_option_trades("1", "NVDA")

        trades = _run(go())
        assert len(trades) == 1
        assert trades[0]["underlying"] == "NVDA"

    def test_rejects_bad_inputs(self, tmp_db):
        with pytest.raises(ValueError):
            _run(_queries.add_option_trade("1", "X", "straddle", 1, "2099-01-01", "buy", 1, 1.0))
        with pytest.raises(ValueError):
            _run(_queries.add_option_trade("1", "X", "call", 1, "2099-01-01", "buy", 0, 1.0))


# ── Cash positions ───────────────────────────────────────────────────────────


class TestStockCash:
    def test_set_get_adjust_and_floor(self, tmp_db):
        async def go():
            u = "7"
            assert await _queries.get_stock_cash(u) == 0.0
            assert await _queries.set_stock_cash(u, 5000) == 5000.0
            assert await _queries.adjust_stock_cash(u, -1500) == 3500.0
            assert await _queries.adjust_stock_cash(u, 2000) == 5500.0
            # over-withdraw floors at 0, never negative
            assert await _queries.adjust_stock_cash(u, -999999) == 0.0
            # set also floors negatives
            assert await _queries.set_stock_cash(u, -50) == 0.0

        _run(go())


# ── Pure P/L helpers (bot/cogs/stock.py) ─────────────────────────────────────


class TestOptionPnlHelpers:
    def _pos(self, net, avg, **kw):
        base = {
            "underlying": "AAPL", "opt_type": "call", "strike": 200,
            "expiry": "2099-01-15", "net_contracts": net, "avg_premium": avg,
            "realized_pnl": 0.0, "closed": net == 0,
        }
        base.update(kw)
        return base

    def test_long_unrealized_and_value(self):
        import bot.cogs.stock as s
        pl = s._option_position_pnl(self._pos(3, (2 * 3.5 + 4.5) / 3), 5.00)
        assert pl["unrealized"] == pytest.approx(350.0)
        assert pl["value"] == pytest.approx(1500.0)
        assert pl["priced"] is True

    def test_short_profit_and_negative_value(self):
        import bot.cogs.stock as s
        # short put @6 avg, now worth 4 -> +200; value is a -400 liability
        pl = s._option_position_pnl(self._pos(-1, 6.0, opt_type="put", strike=250), 4.00)
        assert pl["unrealized"] == pytest.approx(200.0)
        assert pl["value"] == pytest.approx(-400.0)

    def test_unpriced_keeps_basis(self):
        import bot.cogs.stock as s
        pl = s._option_position_pnl(self._pos(2, 3.0), None)
        assert pl["priced"] is False
        assert pl["value"] == 0.0
        assert pl["unrealized"] == 0.0
        assert pl["cost"] == pytest.approx(600.0)  # 2 * 3.0 * 100

    def test_label_and_expiry_helpers(self):
        import bot.cogs.stock as s
        assert s._option_label(self._pos(1, 1.0)) == "AAPL $200C 1/15/99"
        assert s._parse_expiry("2099-01-15") == "2099-01-15"
        assert s._parse_expiry("1/15/2099") == "2099-01-15"
        with pytest.raises(ValueError):
            s._parse_expiry("not-a-date")
        assert s._is_expired("2000-01-01") is True
        assert s._is_expired("2099-01-01") is False
