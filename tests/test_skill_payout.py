"""Skill payout: once per day; top-3 per game get 2000/1000/500."""
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


def test_payout_day_claimed_once(tmp_db):
    async def go():
        assert await _queries.record_skill_payout_day("2026-01-01") is True
        assert await _queries.record_skill_payout_day("2026-01-01") is False
        assert await _queries.record_skill_payout_day("2026-01-02") is True
    _run(go())


def test_pay_skill_leaderboards_top3(tmp_db):
    async def go():
        from bot.cogs.progression import PRIZES, pay_skill_leaderboards
        # three ranked runs on one game (lower ms = better)
        await _queries.record_skill_best("mastermind", "a", 1000)
        await _queries.record_skill_best("mastermind", "b", 2000)
        await _queries.record_skill_best("mastermind", "c", 3000)
        paid = await pay_skill_leaderboards()  # returns total coins minted
        assert paid == sum(PRIZES)  # 2000+1000+500
        assert await _queries.get_casino_balance("a") == _queries.CASINO_STARTING_COINS + PRIZES[0]
        assert await _queries.get_casino_balance("c") == _queries.CASINO_STARTING_COINS + PRIZES[2]
    _run(go())
