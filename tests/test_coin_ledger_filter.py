"""get_coin_ledger hides sub-50 gains (the per-message trickle) from the page."""
from __future__ import annotations
import asyncio
import aiosqlite
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


async def _add(uid, amt, reason):
    async with aiosqlite.connect(_queries.DB_PATH) as db:
        await db.execute(
            "INSERT INTO coin_ledger (discord_user, amount, reason, created_at) VALUES (?,?,?,?)",
            (uid, amt, reason, "2026-01-01T00:00:00Z"))
        await db.commit()


def test_sub_50_gains_hidden(tmp_db):
    async def go():
        await _add("u1", 10, "Message")       # hidden
        await _add("u1", 49, "Message")       # hidden
        await _add("u1", 50, "Login streak")  # shown
        await _add("u1", 500, "Box")          # shown
        await _add("u1", -100, "Bet")         # shown (debit)
        rows = await _queries.get_coin_ledger("u1")
        amounts = sorted(r["amount"] for r in rows)
        assert amounts == [-100, 50, 500]
    _run(go())
