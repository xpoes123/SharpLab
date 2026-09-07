"""End-to-end drive of the Kalshi auto-logger activities against a temp DB with a fake
client: a fill is mirrored to bets, its price is tracked, and settlement finalizes metrics.

NOTE: everything is referenced through `temporal.activities` (A.queries / A.schema) so the
test patches the SAME module instances the activities use. Under pytest's import gymnastics
the suite can hold a second `db.queries` module; patching our own import wouldn't reach the
one the activity actually writes through. (Production imports db.queries exactly once.)"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import temporal.activities as A

_queries = A.queries
_schema = A.schema


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


class _FakeKalshi:
    def __init__(self, fill, market):
        self._fill, self._market = fill, market
    async def fills(self, min_ts=None, limit=200):
        return [self._fill], None
    async def market(self, ticker):
        return self._market
    async def aclose(self):
        pass


def test_full_logger_flow(tmp_db, monkeypatch):
    created = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    fill = {"trade_id": "tX", "order_id": "oX", "ticker": "KXNFLGAME-26SEP21NYGLAR-NYG",
            "action": "buy", "side": "yes", "count": 10, "yes_price": 42, "created_time": created}
    active = {"ticker": "KXNFLGAME-26SEP21NYGLAR-NYG", "yes_bid_c": 46, "yes_ask_c": 50,
              "mid_c": 48.0, "status": "active", "result": "", "close_time": None}

    async def go():
        # 1. poll fills -> a bet + a kalshi_fill are created (long YES @42c, 10 contracts)
        monkeypatch.setattr(A, "_kalshi_signed_client", lambda: _FakeKalshi(fill, active))
        res = await A.poll_kalshi_fills(0)
        assert res["count"] == 1
        bets = await _queries.get_bets_for_user(A.KALSHI_LOG_USER)
        assert len(bets) == 1 and bets[0].book == "kalshi" and bets[0].market == "moneyline"
        assert bets[0].units == 10 * 42 / 100  # dollars risked

        # re-poll: dedup, no second bet
        await A.poll_kalshi_fills(0)
        assert len(await _queries.get_bets_for_user(A.KALSHI_LOG_USER)) == 1

        # 2. snapshot while active -> trajectory grows, price moved to 48 (favorable +6)
        n = await A.snapshot_kalshi_positions()
        assert n == 1
        assert await _queries.get_trajectory_mids("tX") == [
            (await _queries.get_last_trajectory_time("tX"), 48.0)]

        # 3. settle YES -> finalize; long YES @42 wins => pnl +58c/contract
        settled = {**active, "status": "settled", "result": "yes"}
        monkeypatch.setattr(A, "_kalshi_signed_client", lambda: _FakeKalshi(fill, settled))
        await A.snapshot_kalshi_positions()
        assert not await _queries.get_open_kalshi_fills()  # settled

        s = await _queries.kalshi_edge_summary()
        # n + P&L count all settled bets; drift absent here (settled before a +5m snapshot)
        assert s["n"] == 1 and s["mean_pnl_c"] == 58.0
        assert s["total_pnl_dollars"] == 58.0 * 10 / 100
        assert s["n_drift"] == 0 and s["mean_drift_c"] is None
        # graded bet reflects the win
        assert (await _queries.get_bets_for_user(A.KALSHI_LOG_USER))[0].status == "won"

    _run(go())
