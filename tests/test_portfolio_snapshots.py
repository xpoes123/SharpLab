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

    def test_oversell_opens_short(self):
        trades = [
            self._t("buy", 5, "2026-05-01T00:00:00+00:00"),
            self._t("sell", 50, "2026-05-02T00:00:00+00:00"),  # sells past flat → short
        ]
        asof = stock._shares_held_asof(trades, datetime(2026, 5, 3, tzinfo=timezone.utc))
        assert asof == -45  # net is signed: a short position

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


class TestServerEquityPoints:
    def test_forward_fill_and_sum(self):
        rows = [
            {"discord_user": "a", "captured_at": "2026-05-01T21:00:00+00:00", "account_value": 100},
            {"discord_user": "a", "captured_at": "2026-05-03T21:00:00+00:00", "account_value": 120},
            {"discord_user": "b", "captured_at": "2026-05-02T21:00:00+00:00", "account_value": 50},
        ]
        pts = stock._server_equity_points(rows)
        # Days 05-01, 05-02, 05-03; b appears day 2, a forward-fills.
        assert [round(v, 2) for _, v in pts] == [100, 150, 170]

    def test_empty(self):
        assert stock._server_equity_points([]) == []


def _server_data():
    per_ticker = {
        "AAPL": {"shares": 10, "value": 2000, "cost": 1500, "day_change": 50,
                 "prev_value": 1950, "price": 200, "holders": 2},
        "TSLA": {"shares": 5, "value": 1000, "cost": 1200, "day_change": -30,
                 "prev_value": 1030, "price": 200, "holders": 1},
    }
    return {
        "per_ticker": per_ticker, "num_traders": 3, "num_holders": 2,
        "stock_value": 3000, "stock_cost": 2700, "day_change": 20, "day_pct": 0.67,
        "options_value": 0, "options_cost": 0, "cash": 500, "account_value": 3500,
        "realized_total": 100, "unrealized": 300, "unrealized_pct": 11.1,
        "total_pnl": 400, "has_options": False,
    }


def _syms(embed):
    import re
    return re.findall(r"\[`([A-Z]+)`\]", embed.description)


class TestServerEmbeds:
    def test_overview_orders_by_value(self):
        embed = stock._build_server_overview_embed("MyServer", _server_data())
        assert _syms(embed) == ["AAPL", "TSLA"]  # AAPL ($2000) before TSLA ($1000)
        assert "Server Portfolio" in embed.title

    def test_today_orders_by_day_change_and_resolves_movers(self):
        embed = stock._build_server_today_embed("MyServer", _server_data())
        assert _syms(embed) == ["AAPL", "TSLA"]  # +50 before -30
        fields = {f.name: f.value for f in embed.fields}
        assert "AAPL" in fields["Top gainer"]
        assert "TSLA" in fields["Top loser"]


class TestComposition:
    META = {
        "AAPL": {"quote_type": "EQUITY", "sector": "Technology"},
        "NVDA": {"quote_type": "EQUITY", "sector": "Technology"},
        "XOM": {"quote_type": "EQUITY", "sector": "Energy"},
        "SPY": {"quote_type": "ETF", "category": "Large Blend"},
        "VOO": {"quote_type": "ETF", "category": "Large Blend"},
    }

    def test_stock_vs_etf_split(self):
        comp = stock._compute_composition(
            [("AAPL", 6000), ("XOM", 2000), ("SPY", 2000)],
            options_value=0, cash=0, meta=self.META,
        )
        assert comp["by_type"] == {"Stocks": 8000.0, "ETFs": 2000.0}
        assert comp["invested_total"] == 10000.0
        # largest single position weight
        assert round(comp["max_weight"], 1) == 60.0 and comp["top_sym"] == "AAPL"

    def test_sector_and_etf_bucket(self):
        comp = stock._compute_composition(
            [("AAPL", 1000), ("NVDA", 1000), ("XOM", 500), ("SPY", 500)],
            options_value=0, cash=0, meta=self.META,
        )
        assert comp["by_sector"]["Technology"] == 2000.0
        assert comp["by_sector"]["Energy"] == 500.0
        assert comp["by_sector"]["ETF / Fund"] == 500.0

    def test_slices_include_cash_and_options(self):
        comp = stock._compute_composition(
            [("AAPL", 5000)], options_value=1500, cash=2000, meta=self.META,
        )
        assert comp["slices"]["Stocks"] == 5000.0
        assert comp["slices"]["Options"] == 1500.0
        assert comp["slices"]["Cash"] == 2000.0
        assert comp["account_total"] == 8500.0

    def test_short_options_excluded_from_donut(self):
        comp = stock._compute_composition(
            [("AAPL", 5000)], options_value=-800, cash=0, meta=self.META,
        )
        assert "Options" not in comp["slices"]  # net-short premium isn't a positive slice

    def test_persona_index_zen(self):
        comp = stock._compute_composition(
            [("SPY", 7000), ("VOO", 2000), ("AAPL", 1000)],
            options_value=0, cash=0, meta=self.META,
        )
        assert "Index Zen" in stock._portfolio_persona(comp)

    def test_persona_all_in(self):
        comp = stock._compute_composition(
            [("NVDA", 9000), ("AAPL", 1000)], options_value=0, cash=0, meta=self.META,
        )
        assert stock._portfolio_persona(comp) == "🎯 All-In NVDA"

    def test_persona_theta_gang(self):
        comp = stock._compute_composition(
            [("AAPL", 1000)], options_value=500, cash=0, meta=self.META,
        )
        assert "Theta Gang" in stock._portfolio_persona(comp)

    def test_donut_png(self):
        png = stock._render_allocation_donut("X", {"Stocks": 8000, "ETFs": 2000, "Cash": 1000})
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


class TestRiskAndTradeQuality:
    META = {"NVDA": {"beta": 2.2}, "AAPL": {"beta": 1.07}, "SPY": {"beta": None}}

    def test_max_drawdown(self):
        assert round(stock._max_drawdown([100, 120, 60, 90]), 1) == -50.0  # 120 -> 60
        assert stock._max_drawdown([100]) is None  # need >= 2

    def test_concentrated_book_is_high_risk(self):
        r = stock._compute_risk([("NVDA", 9000), ("AAPL", 1000)], 0, 0, 10000, self.META, [])
        assert r["score"] >= 80 and "Degenerate" in r["label"]
        assert round(r["beta"], 2) == 2.09  # value-weighted
        assert round(r["largest_pct"]) == 90 and r["top_sym"] == "NVDA"

    def test_cash_cushion_lowers_score(self):
        full = stock._compute_risk([("SPY", 10000)], 0, 0, 10000, self.META, [])
        half = stock._compute_risk([("SPY", 5000)], 0, 5000, 10000, self.META, [])
        assert half["score"] < full["score"]
        assert round(half["beta"], 2) == 1.0  # ETF beta defaults to market

    def test_var_scales_with_beta(self):
        r = stock._compute_risk([("NVDA", 10000)], 0, 0, 10000, self.META, [])
        # VaR = value * beta * 0.01 * 1.645
        assert round(r["var_1d"], 0) == round(10000 * 2.2 * 0.01 * 1.645, 0)

    def test_trade_quality_fifo(self):
        trades = [
            {"ticker": "AAPL", "side": "buy", "shares": 10, "price": 100,
             "executed_at": "2026-01-01T00:00:00+00:00"},
            {"ticker": "AAPL", "side": "sell", "shares": 5, "price": 120,
             "executed_at": "2026-01-11T00:00:00+00:00"},
            {"ticker": "AAPL", "side": "sell", "shares": 5, "price": 90,
             "executed_at": "2026-01-21T00:00:00+00:00"},
        ]
        tq = stock._trade_quality(trades)
        assert tq["n"] == 2
        assert tq["win_rate"] == 50.0
        assert tq["profit_factor"] == 2.0          # +100 win vs 50 loss
        assert tq["avg_win"] == 100.0 and tq["avg_loss"] == 50.0
        assert tq["best"] == 100 and tq["worst"] == -50
        assert tq["avg_hold"] == 15.0              # share-weighted (10d & 20d)

    def test_trade_quality_no_closed_lots(self):
        trades = [{"ticker": "AAPL", "side": "buy", "shares": 1, "price": 10,
                   "executed_at": "2026-01-01T00:00:00+00:00"}]
        assert stock._trade_quality(trades) is None


class TestBenchmark:
    def _pts(self, vals):
        from datetime import date as _date
        base = _date(2026, 1, 1)
        return [
            (datetime(2026, 1, 1, tzinfo=timezone.utc).replace(day=1) + timedelta(days=i), v)
            for i, v in enumerate(vals)
        ]

    def test_benchmark_scaled_to_portfolio_start(self):
        from datetime import date as _date
        pts = [
            (datetime(2026, 1, 1, tzinfo=timezone.utc), 10000),
            (datetime(2026, 1, 2, tzinfo=timezone.utc), 11000),
        ]
        spy = {_date(2026, 1, 1): 400.0, _date(2026, 1, 2): 440.0}  # +10%
        bench = stock._benchmark_points(pts, spy)
        assert round(bench[0][1]) == 10000  # starts at same dollars
        assert round(bench[1][1]) == 11000  # +10% -> 11000

    def test_alpha_is_return_gap(self):
        from datetime import date as _date
        pts = [
            (datetime(2026, 1, 1, tzinfo=timezone.utc), 10000),
            (datetime(2026, 1, 2, tzinfo=timezone.utc), 12000),  # +20%
        ]
        spy = {_date(2026, 1, 1): 400.0, _date(2026, 1, 2): 440.0}  # +10%
        bench = stock._benchmark_points(pts, spy)
        stats = stock._benchmark_stats(pts, bench)
        assert round(stats["alpha"], 1) == 10.0  # 20% - 10%

    def test_benchmark_none_without_spy(self):
        pts = [(datetime(2026, 1, 1, tzinfo=timezone.utc), 100),
               (datetime(2026, 1, 2, tzinfo=timezone.utc), 110)]
        assert stock._benchmark_points(pts, {}) is None

    def test_sharpe_needs_enough_history(self):
        assert stock._sharpe_ratio([100, 101, 102]) is None  # too few
        vals = [100 * (1.001 ** i) for i in range(40)]  # steady climb, 39 returns
        assert stock._sharpe_ratio(vals) is not None


class TestQuoteCache:
    def test_cache_get_respects_ttl(self):
        import time
        cache = {"X": (time.monotonic(), {"v": 1})}
        assert stock._cache_get(cache, "X", ttl=10) == {"v": 1}      # fresh
        cache["X"] = (time.monotonic() - 100, {"v": 1})
        assert stock._cache_get(cache, "X", ttl=10) is None          # stale
        assert stock._cache_get(cache, "missing", ttl=10) is None

    def test_fetch_quotes_serves_from_cache(self):
        import time
        stock._quote_cache.clear()
        # Prime a fake ticker; if the cache works it's returned without any
        # network call (a real fetch of FAKEZZZ would fail and be skipped).
        stock._quote_cache["FAKEZZZ"] = (
            time.monotonic(), {"price": 1.23, "prev_close": 1.0, "currency": "USD"}
        )
        out = asyncio.run(stock.fetch_quotes(["FAKEZZZ"]))
        assert out["FAKEZZZ"]["price"] == 1.23
        stock._quote_cache.clear()


class TestCrypto:
    def test_normalize_symbol(self):
        assert stock._normalize_symbol("btc") == "BTC-USD"
        assert stock._normalize_symbol(" eth ") == "ETH-USD"
        assert stock._normalize_symbol("BTC-USD") == "BTC-USD"      # already canonical
        assert stock._normalize_symbol("DOGE-USD") == "DOGE-USD"
        assert stock._normalize_symbol("aapl") == "AAPL"            # normal stock untouched
        assert stock._normalize_symbol("") == ""

    def test_tickers_that_are_real_companies_stay_equities(self):
        # Stock-first: a bare ticker that's a real US-listed security must resolve
        # to that security, never a coin. (LTC=LTC Properties, SUI=Sun Communities,
        # VET=Vermilion, etc.) The coin is still reachable via the explicit -USD form.
        for sym in ("LTC", "SUI", "VET", "AXS", "BCH", "ATOM", "LINK", "APT", "TRX", "NEAR", "ARB"):
            assert stock._normalize_symbol(sym) == sym, f"{sym} should stay an equity"
            assert stock._normalize_symbol(sym.lower()) == sym
            assert stock._normalize_symbol(f"{sym}-USD") == f"{sym}-USD"  # explicit coin still works
            assert sym not in stock.CRYPTO_SYMBOLS

    def test_iconic_coins_still_map_to_usd(self):
        # Coins whose only equity match is a crypto fund (or none) still go bare->-USD.
        for sym in ("BTC", "ETH", "XRP", "MANA", "SOL", "DOGE"):
            assert stock._normalize_symbol(sym) == f"{sym}-USD"

    def test_crypto_composition_bucket(self):
        meta = {
            "BTC-USD": {"quote_type": "CRYPTOCURRENCY", "beta": None},
            "AAPL": {"quote_type": "EQUITY", "sector": "Technology", "beta": 1.1},
        }
        comp = stock._compute_composition([("BTC-USD", 6000), ("AAPL", 4000)], 0, 0, meta)
        assert comp["by_type"] == {"Crypto": 6000.0, "Stocks": 4000.0}
        assert comp["by_sector"]["Crypto"] == 6000.0
        assert stock._portfolio_persona(comp) == "🪙 Crypto Degen"

    def test_crypto_default_beta_is_high(self):
        meta = {"BTC-USD": {"quote_type": "CRYPTOCURRENCY", "beta": None}}
        r = stock._compute_risk([("BTC-USD", 10000)], 0, 0, 10000, meta, [])
        assert r["beta"] == 3.0          # not the 1.0 market default
        assert r["score"] >= 80          # all-in crypto is high risk


class TestTickerMetaQueries:
    def test_upsert_and_bulk_get(self, tmp_db):
        async def go():
            await _queries.upsert_ticker_meta("aapl", "EQUITY", "Technology", None, 1.07, "Apple")
            await _queries.upsert_ticker_meta("spy", "ETF", None, "Large Blend", None, "SPDR")
            return await _queries.get_ticker_meta_bulk(["AAPL", "SPY", "MISSING"])
        rows = _run(go())
        assert rows["AAPL"]["quote_type"] == "EQUITY" and rows["AAPL"]["sector"] == "Technology"
        assert rows["SPY"]["quote_type"] == "ETF" and rows["SPY"]["category"] == "Large Blend"
        assert "MISSING" not in rows


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


# ── negative starting-equity guard (aitik bug: +$17k but -1360%) ──────────────

def test_period_return_pct_negative_base_returns_none():
    # base < 0 (overdrafted equity) → a real gain must NOT read as a negative %.
    assert stock._period_return_pct(-1266.72, 17232.53) is None
    assert stock._period_return_pct(0.0, 100.0) is None


def test_period_return_pct_positive_base_normal():
    assert stock._period_return_pct(1000.0, 100.0) == pytest.approx(10.0)


def test_benchmark_stats_no_alpha_when_base_negative():
    # portfolio series starts negative → alpha undefined, not a garbage number.
    dt0 = datetime(2026, 6, 29, tzinfo=timezone.utc)
    dt1 = datetime(2026, 7, 17, tzinfo=timezone.utc)
    points = [(dt0, -1266.72), (dt1, 15965.81)]
    bench = [(dt0, 1000.0), (dt1, 1050.0)]
    assert stock._benchmark_stats(points, bench)["alpha"] is None
