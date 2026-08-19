"""Daily Games DB layer — puzzle cache, one-submit, streaks."""
import asyncio
import os
import tempfile

from db import queries as q
from db import schema as sch


def _run(c):
    return asyncio.run(c)


def _fresh():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")


def test_puzzle_cache_is_stable():
    _fresh()

    async def go():
        await sch.init_db()
        a = await q.get_or_create_daily_puzzle("2026-06-10")
        b = await q.get_or_create_daily_puzzle("2026-06-10")   # cached read
        assert a["payload"] == b["payload"] and a["par"] == b["par"]
        assert a["game_id"] == "trappig"

    _run(go())


def test_one_submit_enforced():
    _fresh()

    async def go():
        await sch.init_db()
        ok1 = await q.record_daily_result("trappig", "2026-06-10", "u", solved=True,
                                          primary=5, secondary=9000)
        ok2 = await q.record_daily_result("trappig", "2026-06-10", "u", solved=True,
                                          primary=3, secondary=1000)  # better, but too late
        assert ok1 is True and ok2 is False
        row = await q.get_daily_result("trappig", "2026-06-10", "u")
        assert row["primary_score"] == 5  # first submission stuck

    _run(go())


def test_streak_increments_then_resets():
    _fresh()

    async def go():
        await sch.init_db()
        assert await q.update_daily_streak("u", "__overall__", "2026-06-10") == 1
        assert await q.update_daily_streak("u", "__overall__", "2026-06-11") == 2
        assert await q.update_daily_streak("u", "__overall__", "2026-06-11") == 2  # same day, no double
        # skip a day → resets
        assert await q.update_daily_streak("u", "__overall__", "2026-06-13") == 1
        s = await q.get_daily_streak("u", "__overall__")
        assert s["current"] == 1 and s["longest"] == 2

    _run(go())


def test_results_range_for_season():
    _fresh()

    async def go():
        await sch.init_db()
        await q.record_daily_result("trappig", "2026-06-02", "a", solved=True, primary=4, secondary=1)
        await q.record_daily_result("trappig", "2026-06-20", "a", solved=True, primary=6, secondary=1)
        await q.record_daily_result("trappig", "2026-07-01", "a", solved=True, primary=2, secondary=1)
        june = await q.get_daily_results_range("2026-06-01", "2026-06-30")
        assert len(june) == 2

    _run(go())
