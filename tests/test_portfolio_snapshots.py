"""Tests for the /stock graph machinery: portfolio_snapshots queries, the
trade share-walk used by backfill reconstruction, and PNG rendering."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, ".")

import db.schema as _schema  # noqa: E402
import db.queries as _queries  # noqa: E402
from bot.cogs import stock  # noqa: E402


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


# ── snapshot queries ─────────────────────────────────────────────────────────


class TestSnapshotQueries:
    def test_insert_and_fetch_ordered(self, tmp_db):
        async def go():
            await _queries.insert_portfolio_snapshot("u1", 1000, 800, 0, 200,
                                                     captured_at="2026-05-01T21:00:00+00:00")
            await _queries.insert_portfolio_snapshot("u1", 1100, 900, 0, 200,
                                                     captured_at="2026-05-03T21:00:00+00:00")
            await _queries.insert_portfolio_snapshot("u1", 1050, 850, 0, 200,
                                                     captured_at="2026-05-02T21:00:00+00:00")
            return await _queries.get_portfolio_snapshots("u1")
        rows = _run(go())
        assert [r["account_value"] for r in rows] == [1000, 1050, 1100]  # sorted by captured_at

    def test_since_filter(self, tmp_db):
        async def go():
            await _queries.insert_portfolio_snapshot("u1", 1, 1, 0, 0,
                                                     captured_at="2026-05-01T00:00:00+00:00")
            await _queries.insert_portfolio_snapshot("u1", 2, 2, 0, 0,
                                                     captured_at="2026-05-10T00:00:00+00:00")
            return await _queries.get_portfolio_snapshots("u1", since_utc_iso="2026-05-05T00:00:00+00:00")
        rows = _run(go())
        assert len(rows) == 1 and rows[0]["account_value"] == 2

    def test_bulk_insert_and_has_backfill(self, tmp_db):
        async def go():
            assert not await _queries.has_backfill_snapshots("u1")
            n = await _queries.insert_portfolio_snapshots_bulk([
                {"discord_user": "u1", "captured_at": "2026-05-01T21:00:00+00:00",
                 "account_value": 500, "stock_value": 500, "options_value": 0,
                 "cash": 0, "kind": "backfill"},
                {"discord_user": "u1", "captured_at": "2026-05-02T21:00:00+00:00",
                 "account_value": 600, "stock_value": 600, "options_value": 0,
                 "cash": 0, "kind": "backfill"},
            ])
            return n, await _queries.has_backfill_snapshots("u1")
        n, has = _run(go())
        assert n == 2 and has is True

    def test_get_all_portfolio_users_union(self, tmp_db):
        async def go():
            await _queries.add_stock_trade("stockguy", "AAPL", "buy", 1, 100,
                                           executed_at="2026-05-01T00:00:00+00:00")
            await _queries.set_stock_cash("cashguy", 500)
            await _queries.set_stock_cash("brokeguy", 0)  # balance 0 -> excluded
            return await _queries.get_all_portfolio_users()
        users = _run(go())
        assert "stockguy" in users and "cashguy" in users
        assert "brokeguy" not in users

    def test_latest_snapshot_at(self, tmp_db):
        async def go():
            assert await _queries.get_latest_snapshot_at("u1") is None
            await _queries.insert_portfolio_snapshot("u1", 1, 1, 0, 0,
                                                     captured_at="2026-05-01T00:00:00+00:00")
            await _queries.insert_portfolio_snapshot("u1", 2, 2, 0, 0,
                                                     captured_at="2026-05-09T00:00:00+00:00")
            return await _queries.get_latest_snapshot_at("u1")
        assert _run(go()) == "2026-05-09T00:00:00+00:00"


# ── share-walk (backfill reconstruction core) ────────────────────────────────


class TestSharesHeldAsOf:
    def _t(self, side, shares, when):
        return {"side": side, "shares": shares, "price": 1, "executed_at": when}

    def test_cumulative_buys_minus_sells(self):
        trades = [
            self._t("buy", 10, "2026-05-01T00:00:00+00:00"),
            self._t("sell", 4, "2026-05-03T00:00:00+00:00"),
            self._t("buy", 2, "2026-05-05T00:00:00+00:00"),
        ]
        asof = lambda d: stock._shares_held_asof(trades, datetime.fromisoformat(d))
        assert asof("2026-04-30T00:00:00+00:00") == 0      # before any trade
        assert asof("2026-05-02T00:00:00+00:00") == 10     # after first buy only
        assert asof("2026-05-04T00:00:00+00:00") == 6      # after the sell
        assert asof("2026-05-06T00:00:00+00:00") == 8      # after second buy

    def test_oversell_clamped(self):
        trades = [
            self._t("buy", 5, "2026-05-01T00:00:00+00:00"),
            self._t("sell", 50, "2026-05-02T00:00:00+00:00"),  # phantom oversell
        ]
        asof = stock._shares_held_asof(trades, datetime(2026, 5, 3, tzinfo=timezone.utc))
        assert asof == 0  # never goes negative

    def test_naive_timestamp_treated_as_utc(self):
        trades = [self._t("buy", 3, "2026-05-01T00:00:00")]  # no tz
        asof = stock._shares_held_asof(trades, datetime(2026, 5, 2, tzinfo=timezone.utc))
        assert asof == 3


# ── PNG rendering ─────────────────────────────────────────────────────────────


class TestRenderEquityCurve:
    def test_returns_png_bytes(self):
        pts = [
            (datetime(2026, 5, 1, tzinfo=timezone.utc), 1000.0),
            (datetime(2026, 5, 2, tzinfo=timezone.utc), 1100.0),
            (datetime(2026, 5, 3, tzinfo=timezone.utc), 1050.0),
        ]
        png = stock._render_equity_curve_png("Tester", pts)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic
        assert len(png) > 1000


# ── leaderboard ranking per period ───────────────────────────────────────────


def _row(uid, total=0, day_gain=0, account_value=0, week_base=None, ytd_base=None):
    return {
        "user_id": uid, "total": total, "invested": 1000,
        "pct": total / 10.0, "account_value": account_value,
        "day_gain": day_gain, "day_base": 1000,
        "week_base": week_base, "ytd_base": ytd_base,
    }


def _order(embed):
    """Pull the user ids out of a rendered leaderboard embed, in rank order."""
    ids = []
    for line in embed.description.splitlines():
        for tok in line.split():
            if tok.startswith("**") and tok.endswith("**"):
                ids.append(tok.strip("*"))
                break
    return ids


class TestLeaderboardRender:
    def test_all_time_sorted_by_total(self):
        rows = [_row("a", total=50), _row("b", total=300), _row("c", total=-10)]
        names = {"a": "a", "b": "b", "c": "c"}
        assert _order(stock._render_leaderboard_embed(rows, "all", names)) == ["b", "a", "c"]

    def test_daily_sorted_by_day_gain(self):
        rows = [_row("a", day_gain=5), _row("b", day_gain=80), _row("c", day_gain=-3)]
        names = {"a": "a", "b": "b", "c": "c"}
        assert _order(stock._render_leaderboard_embed(rows, "daily", names)) == ["b", "a", "c"]

    def test_weekly_excludes_users_without_baseline(self):
        rows = [
            _row("a", account_value=1200, week_base=1000),   # +200
            _row("b", account_value=1500, week_base=1000),   # +500
            _row("c", account_value=900),                    # no baseline -> hidden
        ]
        names = {"a": "a", "b": "b", "c": "c"}
        embed = stock._render_leaderboard_embed(rows, "weekly", names)
        assert _order(embed) == ["b", "a"]
        assert "1 hidden" in embed.footer.text

    def test_ytd_ranks_by_account_delta(self):
        rows = [
            _row("a", account_value=2000, ytd_base=1000),    # +1000
            _row("b", account_value=1100, ytd_base=1000),    # +100
        ]
        names = {"a": "a", "b": "b"}
        assert _order(stock._render_leaderboard_embed(rows, "ytd", names)) == ["a", "b"]
