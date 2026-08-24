"""Achievement unlock pays XP*5; backfill tops up already-earned by the delta, once."""
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


def test_unlock_pays_xp_times_five(tmp_db):
    # Real path: log a casino round → first_game unlocks via evaluate_user_achievements,
    # which must credit xp_reward*5 (not the old flat 150).
    async def go():
        from bot.cogs.progression import evaluate_user_achievements
        from shared.achievements import ACHIEVEMENTS_BY_ID
        await _queries.get_or_create_casino_wallet("u1")
        b0 = await _queries.get_casino_balance("u1")
        await _queries.log_casino_result("u1", "slots", 10, 0)  # rounds>=1 → first_game; payout 0 so no first_win
        newly = await evaluate_user_achievements("u1")
        assert "first_game" in newly
        b1 = await _queries.get_casino_balance("u1")
        # only first_game should unlock from all-zero stats; bounty = 10*5 = 50
        assert b1 - b0 == ACHIEVEMENTS_BY_ID["first_game"].xp_reward * 5
    _run(go())


def test_backfill_tops_up_by_delta_once(tmp_db):
    async def go():
        from scripts.backfill_achievement_bounties import backfill
        from shared.achievements import ACHIEVEMENTS_BY_ID
        await _queries.get_or_create_casino_wallet("u2")
        # user already unlocked a high-XP achievement (got the old flat 150)
        await _queries.unlock_achievement("u2", "level_50")  # xp 1000 -> new bounty 5000
        b0 = await _queries.get_casino_balance("u2")
        n1 = await backfill()
        b1 = await _queries.get_casino_balance("u2")
        assert b1 - b0 == ACHIEVEMENTS_BY_ID["level_50"].xp_reward * 5 - 150  # 4850
        # idempotent: a second run pays nothing
        n2 = await backfill()
        b2 = await _queries.get_casino_balance("u2")
        assert b2 == b1
    _run(go())
