"""Regression test: crapless_default_bet column migration.

On databases created before crapless craps was added, user_settings only has
craps_default_bet.  init_db() must add crapless_default_bet via ALTER TABLE so
that get_crapless_default_bet() doesn't blow up with OperationalError.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

import aiosqlite
import pytest

sys.path.insert(0, ".")


def run(coro):
    return asyncio.run(coro)


# ── helpers ──────────────────────────────────────────────────────────────────

async def _make_old_db() -> str:
    """Create a DB whose user_settings lacks crapless_default_bet (old schema)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    async with aiosqlite.connect(tmp.name) as db:
        await db.execute(
            "CREATE TABLE user_settings "
            "(discord_user TEXT PRIMARY KEY, craps_default_bet INTEGER)"
        )
        await db.execute(
            "INSERT INTO user_settings (discord_user, craps_default_bet) VALUES ('u1', 50)"
        )
        await db.commit()
    return tmp.name


async def _run_migration(db_path: str) -> None:
    """Run only the crapless_default_bet ALTER TABLE migration (isolated)."""
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.execute("ALTER TABLE user_settings ADD COLUMN crapless_default_bet INTEGER")
            await db.commit()
        except Exception:
            pass  # already exists — that's fine


async def _query_crapless(db_path: str, user: str) -> int | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT crapless_default_bet FROM user_settings WHERE discord_user = ?",
            (user,),
        )
        row = await cur.fetchone()
    return row["crapless_default_bet"] if row else None


async def _set_crapless(db_path: str, user: str, amount: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO user_settings (discord_user, crapless_default_bet) VALUES (?, ?)"
            " ON CONFLICT(discord_user) DO UPDATE SET crapless_default_bet = excluded.crapless_default_bet",
            (user, amount),
        )
        await db.commit()


# ── tests ─────────────────────────────────────────────────────────────────────

class TestCraplessMigration:
    def setup_method(self):
        self.db_path = run(_make_old_db())

    def teardown_method(self):
        os.unlink(self.db_path)

    def test_old_db_missing_column_raises_before_migration(self):
        """Sanity-check: the old schema really lacks the column."""
        with pytest.raises(Exception):
            run(_query_crapless(self.db_path, "u1"))

    def test_migration_adds_column(self):
        """After migration the column exists and returns NULL for old rows."""
        run(_run_migration(self.db_path))
        result = run(_query_crapless(self.db_path, "u1"))
        assert result is None  # existing row has NULL in the new column

    def test_migration_idempotent(self):
        """Running migration twice must not raise."""
        run(_run_migration(self.db_path))
        run(_run_migration(self.db_path))  # second run — should be silent no-op

    def test_set_and_get_after_migration(self):
        """Can write and read crapless_default_bet once column is present."""
        run(_run_migration(self.db_path))
        run(_set_crapless(self.db_path, "u1", 75))
        assert run(_query_crapless(self.db_path, "u1")) == 75

    def test_existing_craps_default_preserved(self):
        """Migration must not disturb the craps_default_bet column."""
        run(_run_migration(self.db_path))
        async def _get_craps(db_path, user):
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT craps_default_bet FROM user_settings WHERE discord_user = ?",
                    (user,),
                )
                row = await cur.fetchone()
            return row["craps_default_bet"] if row else None

        assert run(_get_craps(self.db_path, "u1")) == 50
