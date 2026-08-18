"""Earnings-results summary: yfinance parsing + embed rendering (no network)."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd

import bot.cogs.stock as stock
from bot.cogs.stock import StockCog


def _run(coro):
    return asyncio.run(coro)


def _patch_yf(df):
    class FakeTk:
        def __init__(self, s):
            pass

        def get_earnings_dates(self, limit=8):
            return df

    import yfinance as yf
    yf.Ticker = FakeTk


def _df():
    idx = pd.to_datetime(
        ["2026-10-29 16:00", "2026-07-30 16:00", "2026-06-05 08:00"]
    ).tz_localize("America/New_York")
    return pd.DataFrame(
        {"EPS Estimate": [1.98, 1.89, 0.50],
         "Reported EPS": [np.nan, 2.02, 0.44],
         "Surprise(%)": [np.nan, 6.74, -12.0]},
        index=idx,
    )


def test_fetch_earnings_result_parsing():
    _patch_yf(_df())
    reported = _run(stock.fetch_earnings_result("X", "2026-07-30"))
    assert reported == {"eps_actual": 2.02, "eps_estimate": 1.89,
                        "surprise_pct": 6.74, "when": "after close"}
    # a pre-market (BMO) miss
    bmo = _run(stock.fetch_earnings_result("X", "2026-06-05"))
    assert bmo["when"] == "before open" and bmo["eps_actual"] == 0.44
    # reported date exists but actual EPS not published yet (NaN) → None
    assert _run(stock.fetch_earnings_result("X", "2026-10-29")) is None
    # date with no report → None
    assert _run(stock.fetch_earnings_result("X", "2020-01-01")) is None


def test_earnings_result_embed():
    cog = StockCog.__new__(StockCog)
    q = {"price": 245.10, "prev_close": 235.00, "extended": {"session": "POST"}}
    beat = cog._earnings_result_embed(
        "AAPL", "Apple Inc",
        {"eps_actual": 2.02, "eps_estimate": 1.89, "surprise_pct": 6.74, "when": "after close"}, q)
    assert "🟢 Beat" in beat.description and "+6.7%" in beat.description
    assert "+4.3% after hours" in beat.description
    assert beat.colour.value == 0x9ECE6A
    miss = cog._earnings_result_embed(
        "Z", "Zebra",
        {"eps_actual": 0.44, "eps_estimate": 0.50, "surprise_pct": -12.0, "when": "before open"}, None)
    assert "🔴 Missed" in miss.description and miss.colour.value == 0xF7768E
