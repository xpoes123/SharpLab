"""DB roundtrip for the Kalshi auto-logger tables."""
from __future__ import annotations

import asyncio
import pytest

import db.schema as _schema
import db.queries as _queries


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path):
    p = str(tmp_path / "t.db")
    a, b = _schema.DB_PATH, _queries.DB_PATH
    _schema.DB_PATH = _queries.DB_PATH = p
    _run(_schema.init_db())
    yield p
    _schema.DB_PATH, _queries.DB_PATH = a, b


def test_fill_dedup_trajectory_and_summary(tmp_db):
    async def go():
        row = {"trade_id": "t1", "order_id": "o1", "ticker": "KXNFLGAME-26SEP21NYGLAR-NYG",
               "action": "buy", "side": "yes", "long_yes": 1, "count": 10, "yes_price_c": 42,
               "created_time": "2026-09-21T20:00:00Z", "bet_id": None, "is_live": 1}
        assert not await _queries.kalshi_fill_exists("t1")
        await _queries.insert_kalshi_fill(row)
        assert await _queries.kalshi_fill_exists("t1")

        # dedup: re-inserting the same trade_id is a no-op (still one open fill)
        await _queries.insert_kalshi_fill(row)
        opens = await _queries.get_open_kalshi_fills()
        assert len(opens) == 1 and opens[0]["ticker"].endswith("NYG")

        await _queries.append_trajectory("t1", "2026-09-21T20:00:30Z", 45, 49, 47.0)
        assert (await _queries.get_last_trajectory_time("t1")) == "2026-09-21T20:00:30Z"
        assert await _queries.get_trajectory_mids("t1") == [("2026-09-21T20:00:30Z", 47.0)]

        # settle it: long YES, entry 42, won -> pnl +58c
        await _queries.finalize_kalshi_fill(
            "t1", {"clv_c": None, "drift_5m_c": 5.0, "mfe_c": 7.0, "mae_c": -1.0, "pnl_c": 58.0},
            bet_status="won")
        assert not await _queries.get_open_kalshi_fills()  # settled -> no longer open

        s = await _queries.kalshi_edge_summary()
        assert s["n"] == 1 and s["mean_drift_c"] == 5.0 and s["pct_positive"] == 1.0
        assert s["total_pnl_dollars"] == 58.0 * 10 / 100  # pnl_c * count / 100

    _run(go())
