"""Login streak: ramps +100 to 1000, resets on a gap, idempotent per day."""
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


def test_streak_ramps_resets_and_is_idempotent(tmp_db):
    async def go():
        r1 = await _queries.claim_login_streak("u1", "2026-01-01")
        assert (r1["granted"], r1["streak"], r1["already"]) == (200, 1, False)
        # same day again → already, 0 granted
        r1b = await _queries.claim_login_streak("u1", "2026-01-01")
        assert (r1b["granted"], r1b["already"]) == (0, True)
        # next day → +100
        r2 = await _queries.claim_login_streak("u1", "2026-01-02")
        assert (r2["granted"], r2["streak"]) == (300, 2)
        # jump to a far day → reset to 1
        r3 = await _queries.claim_login_streak("u1", "2026-01-10")
        assert (r3["granted"], r3["streak"]) == (200, 1)
        # verify the cap at 1000 (day1=200 .. day9=1000)
        await _queries.get_or_create_casino_wallet("u2")
        day = 1
        last = None
        from datetime import date, timedelta
        d = date(2026, 2, 1)
        for i in range(12):
            last = await _queries.claim_login_streak("u2", (d + timedelta(days=i)).isoformat())
        assert last["granted"] == 1000  # capped
        assert last["streak"] == 12
    _run(go())
