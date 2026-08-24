"""Zetamac leaderboard is HIGHEST-score-wins (skill_scores direction flag) + reward key."""
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


def test_reward_key_present():
    assert "zetamac_win" in _queries.ACTIVITY_REWARDS
    assert "zetamac" in _queries.SKILL_HIGHER_IS_BETTER


def test_zetamac_keeps_highest_and_orders_desc(tmp_db):
    async def go():
        # a improves 20 -> 35 (higher is better, so 35 is kept); a lower run is ignored
        best, new = await _queries.record_skill_best("zetamac", "a", 20)
        assert (best, new) == (20, True)
        best, new = await _queries.record_skill_best("zetamac", "a", 35)
        assert (best, new) == (35, True)
        best, new = await _queries.record_skill_best("zetamac", "a", 12)
        assert (best, new) == (35, False)  # a worse run doesn't lower the best
        await _queries.record_skill_best("zetamac", "b", 50)
        await _queries.record_skill_best("zetamac", "c", 30)
        lb = await _queries.get_skill_leaderboard("zetamac", 10)
        assert [r["discord_user"] for r in lb] == ["b", "a", "c"]  # 50, 35, 30 desc
        assert await _queries.get_skill_rank("zetamac", "b") == 1
        assert await _queries.get_skill_rank("zetamac", "c") == 3
    _run(go())


def test_timed_games_still_lowest_wins(tmp_db):
    async def go():
        await _queries.record_skill_best("mastermind", "a", 5000)  # ms
        await _queries.record_skill_best("mastermind", "b", 3000)
        lb = await _queries.get_skill_leaderboard("mastermind", 10)
        assert [r["discord_user"] for r in lb] == ["b", "a"]  # fastest first
    _run(go())
