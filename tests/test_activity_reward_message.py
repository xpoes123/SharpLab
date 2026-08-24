"""Per-message reward is 10 and effectively uncapped (past the old 500 cap)."""
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


def test_message_reward_is_ten_and_uncapped(tmp_db):
    async def go():
        total = 0
        for _ in range(200):  # 200 * 10 = 2000, far past the old 500 cap
            total += await _queries.grant_activity_reward("u1", "message", "2026-01-01")
        assert total == 2000
    _run(go())


def test_message_reward_amount():
    assert _queries.ACTIVITY_REWARDS["message"][0] == 10
    assert _queries.ACTIVITY_REWARDS["message"][1] >= 1_000_000  # effectively uncapped
