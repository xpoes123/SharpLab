"""Tests for reaction-role bindings in db/queries.py."""
from __future__ import annotations

import asyncio
import sys

import pytest

sys.path.insert(0, ".")

import db.schema as _schema  # noqa: E402
import db.queries as _queries  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    orig_s, orig_q = _schema.DB_PATH, _queries.DB_PATH
    _schema.DB_PATH = _queries.DB_PATH = db_path
    _run(_schema.init_db())
    yield db_path
    _schema.DB_PATH, _queries.DB_PATH = orig_s, orig_q


class TestReactionRoleQueries:
    def test_add_get_remove(self, tmp_db):
        async def go():
            await _queries.add_reaction_role("m1", "🏀", "role_nba", "g", "c")
            await _queries.add_reaction_role("m1", "⚾", "role_mlb", "g", "c")
            got = await _queries.get_reaction_role("m1", "🏀")
            ids = await _queries.get_reaction_role_message_ids()
            binds = await _queries.get_reaction_roles_for_message("m1")
            removed = await _queries.remove_reaction_role("m1", "🏀")
            after = await _queries.get_reaction_role("m1", "🏀")
            return got, ids, binds, removed, after
        got, ids, binds, removed, after = _run(go())
        assert got == "role_nba"
        assert ids == {"m1"}
        assert len(binds) == 2
        assert removed is True
        assert after is None

    def test_rebind_updates_role(self, tmp_db):
        async def go():
            await _queries.add_reaction_role("m1", "🟢", "old", "g", "c")
            await _queries.add_reaction_role("m1", "🟢", "new", "g", "c")  # same emoji
            return await _queries.get_reaction_role("m1", "🟢")
        assert _run(go()) == "new"

    def test_unknown_is_none(self, tmp_db):
        assert _run(_queries.get_reaction_role("nope", "🏀")) is None
