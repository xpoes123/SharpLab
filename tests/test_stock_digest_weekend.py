"""The daily portfolio report must not fire on weekends (market closed)."""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import bot.cogs.stock as stock
from bot.cogs.stock import StockCog

ET = ZoneInfo("America/New_York")


def _run_digest_at(monkeypatch, when: datetime) -> AsyncMock:
    """Invoke daily_digest with a frozen ET clock; return the get_bot_setting mock.

    get_bot_setting is the first query *after* the weekday/hour guards, so whether
    it was awaited tells us if the guards let the digest proceed."""
    fake_dt = MagicMock()
    fake_dt.now.return_value = when
    monkeypatch.setattr(stock, "datetime", fake_dt)

    # Make the dedup check short-circuit immediately if we get that far, so the
    # digest never reaches channel/leaderboard work during the test.
    get_setting = AsyncMock(return_value=when.date().isoformat())
    monkeypatch.setattr(stock.queries, "get_bot_setting", get_setting)

    asyncio.run(StockCog.daily_digest.coro(object()))
    return get_setting


def test_skips_saturday(monkeypatch):
    # 2026-06-06 is a Saturday, 5pm ET — past the close but no market that day.
    get_setting = _run_digest_at(monkeypatch, datetime(2026, 6, 6, 17, 0, tzinfo=ET))
    get_setting.assert_not_awaited()


def test_skips_sunday(monkeypatch):
    # 2026-06-07 is a Sunday.
    get_setting = _run_digest_at(monkeypatch, datetime(2026, 6, 7, 17, 0, tzinfo=ET))
    get_setting.assert_not_awaited()


def test_runs_on_weekday_after_close(monkeypatch):
    # 2026-06-08 is a Monday, 5pm ET — guards pass, digest proceeds to dedup.
    get_setting = _run_digest_at(monkeypatch, datetime(2026, 6, 8, 17, 0, tzinfo=ET))
    get_setting.assert_awaited()


def test_skips_weekday_before_close(monkeypatch):
    # Monday 10am ET — before the close, should not run yet.
    get_setting = _run_digest_at(monkeypatch, datetime(2026, 6, 8, 10, 0, tzinfo=ET))
    get_setting.assert_not_awaited()


def test_period_reports_due():
    from datetime import date
    keys = lambda d: [k for k, _, _ in stock._period_reports_due(d)]
    assert keys(date(2026, 11, 9)) == []                         # plain Monday
    assert keys(date(2026, 11, 13)) == ["week_base"]             # Friday
    assert keys(date(2026, 11, 30)) == ["month_base"]            # Mon, last weekday of Nov
    assert keys(date(2026, 10, 30)) == ["week_base", "month_base"]  # Fri, Oct 31 is Sat
    assert keys(date(2026, 10, 29)) == []                        # Thu before that Friday


def test_period_report_lines_ranks_by_pct():
    rows = [{"user_id": "a", "account_value": 110.0, "week_base": 100.0},
            {"user_id": "b", "account_value": 1100.0, "week_base": 1000.0 * 1.2},
            {"user_id": "c", "account_value": 50.0, "week_base": None}]
    lines = stock._period_report_lines(rows, "week_base", {"a": "A", "b": "B"})
    assert len(lines) == 2 and "**A**" in lines[0] and "+10.00%" in lines[0]
    assert "**B**" in lines[1] and "🔴" in lines[1]
