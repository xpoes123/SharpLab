"""Casino coin economy: no per-play faucet, losses floor at 0, and the 500/day message reward."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db.queries as q  # noqa: E402
import db.schema as sch  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _fresh_db():
    d = tempfile.mkdtemp()
    sch.DB_PATH = q.DB_PATH = os.path.join(d, "t.db")


def test_no_faucet_and_floor_zero():
    _fresh_db()

    async def go():
        await sch.init_db()
        u = "u1"
        assert await q.get_or_create_casino_wallet(u) == 1000  # new wallet grant
        await q.update_casino_balance(u, -800)
        # get_or_create must NOT top a low balance back up (the old faucet)
        assert await q.get_or_create_casino_wallet(u) == 200
        await q.update_casino_balance(u, -200)
        assert await q.get_or_create_casino_wallet(u) == 0  # can lose to 0, floor is 0 not 1000
        try:
            await q.update_casino_balance(u, -50)  # can't overdraw
            assert False
        except ValueError:
            pass

    _run(go())


def test_daily_message_reward_once_per_day():
    _fresh_db()

    async def go():
        await sch.init_db()
        u = "u2"
        assert await q.grant_daily_message_reward(u, "2026-08-18") == 500  # first message today
        assert await q.grant_daily_message_reward(u, "2026-08-18") == 0    # already claimed
        assert await q.get_or_create_casino_wallet(u) == 1500              # 1000 grant + 500
        assert await q.grant_daily_message_reward(u, "2026-08-19") == 500  # next day pays again

    _run(go())
