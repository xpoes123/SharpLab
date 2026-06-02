"""Tests for stock price monitors: trigger logic + DB CRUD."""
from __future__ import annotations

import asyncio

import aiosqlite
import pytest

import db.schema as _schema
import db.queries as _queries
from bot.cogs.stock import (
    _manual_triggered, _swing_pct, _display_ticker, price_within_tolerance,
    _fmt_price, SWING_MIN_PRICE,
)


def test_fmt_price_adaptive_precision():
    # >= $1: two decimals, with thousands separators
    assert _fmt_price(510.69) == "510.69"
    assert _fmt_price(1234.5) == "1,234.50"
    assert _fmt_price(1.0) == "1.00"
    assert _fmt_price(0.0) == "0.00"
    # sub-dollar down to a cent: four decimals (ERBB's DCA was $0.0115)
    assert _fmt_price(0.0115) == "0.0115"
    assert _fmt_price(0.5) == "0.5000"
    # sub-cent: enough decimals to actually show the value, no $0.00 collapse
    assert _fmt_price(0.0003) == "0.0003"   # the ERBB price that read "$0.00 → $0.00"
    assert _fmt_price(0.0002) == "0.0002"
    # currency suffix is optional and omitted by default
    assert _fmt_price(12.5, "USD") == "12.50 USD"
    assert _fmt_price(0.0003, "USD") == "0.0003 USD"
    # negative prices keep their sign
    assert _fmt_price(-0.0003) == "-0.0003"


def test_swing_min_price_floor_excludes_penny_stocks():
    # ERBB ticked $0.0002 -> $0.0003: a real +50% but pure penny-stock noise.
    assert _swing_pct(0.0003, 0.0002) == pytest.approx(50.0)
    assert 0.0003 < SWING_MIN_PRICE        # so the swing loop skips it
    assert not (5.0 < SWING_MIN_PRICE)     # a normal $5 stock still alerts


def test_price_within_tolerance():
    assert price_within_tolerance(971, 971)          # exact
    assert price_within_tolerance(970, 971)          # ~0.1% off
    assert price_within_tolerance(900, 971)          # ~7% off (within 15%)
    assert not price_within_tolerance(728, 971)      # 25% off — inarush's stale MU buy
    assert not price_within_tolerance(246.82, 320)   # 23% off — stale IBM buy
    assert not price_within_tolerance(100, 971, tol=0.05)  # custom tighter tolerance
    assert not price_within_tolerance(50, 0)         # no live price → can't validate as ok


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


# ── Pure trigger logic ────────────────────────────────────────────────────────


def test_manual_triggered_above():
    assert _manual_triggered("above", 100, 100)   # >= fires at the target
    assert _manual_triggered("above", 100, 120)
    assert not _manual_triggered("above", 100, 99)


def test_manual_triggered_below():
    assert _manual_triggered("below", 100, 100)
    assert _manual_triggered("below", 100, 80)
    assert not _manual_triggered("below", 100, 101)


def test_swing_pct():
    assert _swing_pct(110, 100) == pytest.approx(10.0)
    assert _swing_pct(90, 100) == pytest.approx(-10.0)
    assert _swing_pct(100, None) == 0.0
    assert _swing_pct(100, 0) == 0.0  # guard against div-by-zero


def test_display_ticker():
    assert _display_ticker("BTC-USD") == "BTC"
    assert _display_ticker("AAPL") == "AAPL"


# ── DB CRUD ───────────────────────────────────────────────────────────────────


def test_monitor_crud(tmp_db):
    async def go():
        mid = await _queries.add_stock_monitor("u1", "c1", "MU", "above", 120.0)
        active = await _queries.get_active_stock_monitors()
        mine = await _queries.get_stock_monitors_for_user("u1")
        return mid, active, mine

    mid, active, mine = _run(go())
    assert mid == 1
    assert len(active) == 1
    assert active[0]["ticker"] == "MU"
    assert active[0]["direction"] == "above"
    assert active[0]["target_price"] == 120.0
    assert len(mine) == 1


def test_monitor_deactivate_hides_from_active(tmp_db):
    async def go():
        mid = await _queries.add_stock_monitor("u1", "c1", "MU", "above", 120.0)
        await _queries.deactivate_stock_monitor(mid)
        return await _queries.get_active_stock_monitors()

    assert _run(go()) == []


def test_monitor_delete_only_own(tmp_db):
    async def go():
        mid = await _queries.add_stock_monitor("u1", "c1", "MU", "above", 120.0)
        not_owner = await _queries.delete_stock_monitor(mid, "u2")  # wrong user
        owner = await _queries.delete_stock_monitor(mid, "u1")
        remaining = await _queries.get_active_stock_monitors()
        return not_owner, owner, remaining

    not_owner, owner, remaining = _run(go())
    assert not_owner is False
    assert owner is True
    assert remaining == []


def test_bot_settings_roundtrip_and_upsert(tmp_db):
    async def go():
        assert await _queries.get_bot_setting("stock_alerts_channel") is None
        await _queries.set_bot_setting("stock_alerts_channel", "123")
        first = await _queries.get_bot_setting("stock_alerts_channel")
        await _queries.set_bot_setting("stock_alerts_channel", "456")  # overwrite
        second = await _queries.get_bot_setting("stock_alerts_channel")
        return first, second

    first, second = _run(go())
    assert first == "123"
    assert second == "456"


def test_get_all_stock_holdings(tmp_db):
    # Holdings are computed from the trade log (the dead stock_holdings table is no
    # longer read), so seed stock_trades. u4 buys then sells out → 0 shares, filtered.
    async def go():
        async with aiosqlite.connect(tmp_db) as db:
            await db.executemany(
                "INSERT INTO stock_trades (discord_user, ticker, side, shares, price, executed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("u1", "MU", "buy", 10.0, 90.0, "2026-01-01T00:00:00+00:00"),
                    ("u2", "MU", "buy", 5.0, 95.0, "2026-01-01T00:00:00+00:00"),
                    ("u3", "NVDA", "buy", 2.0, 800.0, "2026-01-01T00:00:00+00:00"),
                    ("u4", "ZERO", "buy", 5.0, 1.0, "2026-01-01T00:00:00+00:00"),
                    ("u4", "ZERO", "sell", 5.0, 2.0, "2026-01-02T00:00:00+00:00"),  # net 0 → filtered
                ],
            )
            await db.commit()
        return await _queries.get_all_stock_holdings()

    holdings = _run(go())
    by_ticker: dict[str, list[str]] = {}
    for h in holdings:
        by_ticker.setdefault(h["ticker"], []).append(h["discord_user"])
    assert set(by_ticker["MU"]) == {"u1", "u2"}
    assert by_ticker["NVDA"] == ["u3"]
    assert "ZERO" not in by_ticker  # zero-share holdings excluded
