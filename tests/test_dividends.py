"""Tests for the stock dividend payout loop.

Covers the three load-bearing invariants:
  1. a dividend whose ex-date is within the last 10 days is credited at shares × $/share,
  2. an ex-date older than 10 days is NOT paid (no back-paying history), and
  3. a second run does not double-pay (dedupe via dividends_paid).

yfinance is mocked with a fake `.dividends` pandas Series so no network is touched.
"""
from __future__ import annotations

import asyncio
import sys
import types
from datetime import date, timedelta

import pandas as pd
import pytest

import db.schema as _schema
import db.queries as _queries
import bot.cogs.stock as stock
from bot.cogs.stock import StockCog


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    orig_s, orig_q = _schema.DB_PATH, _queries.DB_PATH
    _schema.DB_PATH = _queries.DB_PATH = db_path
    _run(_schema.init_db())
    stock._dividend_cache.clear()  # module-level cache — don't leak across tests
    yield db_path
    _schema.DB_PATH, _queries.DB_PATH = orig_s, orig_q
    stock._dividend_cache.clear()


# ── Fake yfinance ─────────────────────────────────────────────────────────────


def _install_fake_yfinance(monkeypatch, dividends_by_sym: dict[str, list[tuple[date, float]]]):
    """Install a fake `yfinance` module whose Ticker(sym).dividends is a tz-aware
    pandas Series of ex-date -> $/share, matching what fetch_dividends consumes."""

    class _FakeTicker:
        def __init__(self, sym: str) -> None:
            self.sym = sym

        @property
        def dividends(self) -> pd.Series:
            rows = dividends_by_sym.get(self.sym, [])
            if not rows:
                return pd.Series(dtype="float64")
            idx = pd.DatetimeIndex(
                [pd.Timestamp(d, tz="America/New_York") for d, _ in rows]
            )
            return pd.Series([amt for _, amt in rows], index=idx)

    fake = types.ModuleType("yfinance")
    fake.Ticker = _FakeTicker
    monkeypatch.setitem(sys.modules, "yfinance", fake)


class _FakeSelf:
    """Minimal stand-in for StockCog: captures the digest instead of posting it."""

    def __init__(self) -> None:
        self.posted: dict | None = None

    async def _post_dividend_digest(self, paid) -> None:
        self.posted = paid


async def _run_loop(fake_self: _FakeSelf) -> None:
    # dividend_loop is a tasks.Loop; .coro is the underlying async function taking self.
    await StockCog.dividend_loop.coro(fake_self)


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_recent_dividend_is_credited(tmp_db, monkeypatch):
    async def go():
        await _queries.add_stock_trade("U1", "AAPL", "buy", 10.0, 150.0)
        ex = date.today() - timedelta(days=3)  # within the 10-day window
        _install_fake_yfinance(monkeypatch, {"AAPL": [(ex, 0.26)]})

        self = _FakeSelf()
        await _run_loop(self)

        # 10 shares × $0.26 = $2.60 credited to brokerage cash.
        assert round(await _queries.get_stock_cash("U1"), 2) == 2.60
        assert round(await _queries.get_dividend_income("U1"), 2) == 2.60
        assert await _queries.dividend_paid("U1", "AAPL", ex.isoformat()) is True
        # A digest was produced with the credit recorded.
        assert self.posted is not None
        (sym, ex_iso, per_share), credits = next(iter(self.posted.items()))
        assert sym == "AAPL" and per_share == 0.26
        assert credits[0][1] == pytest.approx(2.60)

    _run(go())


def test_old_dividend_is_skipped(tmp_db, monkeypatch):
    async def go():
        await _queries.add_stock_trade("U1", "MSFT", "buy", 5.0, 400.0)
        ex = date.today() - timedelta(days=20)  # older than the 10-day cutoff
        _install_fake_yfinance(monkeypatch, {"MSFT": [(ex, 0.75)]})

        self = _FakeSelf()
        await _run_loop(self)

        assert await _queries.get_stock_cash("U1") == 0.0
        assert await _queries.get_dividend_income("U1") == 0.0
        assert await _queries.dividend_paid("U1", "MSFT", ex.isoformat()) is False
        assert self.posted is None  # nothing paid → no digest

    _run(go())


def test_second_run_does_not_double_pay(tmp_db, monkeypatch):
    async def go():
        await _queries.add_stock_trade("U1", "KO", "buy", 100.0, 60.0)
        ex = date.today() - timedelta(days=2)
        _install_fake_yfinance(monkeypatch, {"KO": [(ex, 0.485)]})

        # First run credits 100 × $0.485 = $48.50.
        await _run_loop(_FakeSelf())
        assert round(await _queries.get_stock_cash("U1"), 2) == 48.50

        # Second run over the exact same dividend must be a no-op.
        self2 = _FakeSelf()
        await _run_loop(self2)
        assert round(await _queries.get_stock_cash("U1"), 2) == 48.50
        assert round(await _queries.get_dividend_income("U1"), 2) == 48.50
        assert self2.posted is None  # nothing new paid → no second digest

    _run(go())
