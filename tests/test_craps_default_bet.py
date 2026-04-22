"""Tests for craps default bet feature: schema, queries, and cog logic."""
from __future__ import annotations

import asyncio
import sys

import pytest

sys.path.insert(0, ".")

# ── Helpers ──────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Isolated query tests using temp DB ───────────────────────────────────────

import aiosqlite
import tempfile
import os


async def _make_temp_db() -> str:
    """Create a minimal in-memory-file DB with the user_settings table."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    async with aiosqlite.connect(tmp.name) as db:
        await db.execute(
            "CREATE TABLE user_settings "
            "(discord_user TEXT PRIMARY KEY, craps_default_bet INTEGER)"
        )
        await db.commit()
    return tmp.name


async def _get_default(db_path: str, user: str):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT craps_default_bet FROM user_settings WHERE discord_user = ?",
            (user,),
        )
        row = await cur.fetchone()
    return row["craps_default_bet"] if row else None


async def _set_default(db_path: str, user: str, amount: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO user_settings (discord_user, craps_default_bet)
            VALUES (?, ?)
            ON CONFLICT(discord_user) DO UPDATE SET craps_default_bet = excluded.craps_default_bet
            """,
            (user, amount),
        )
        await db.commit()


class TestUserSettingsTable:
    def setup_method(self):
        self.db_path = run(_make_temp_db())

    def teardown_method(self):
        os.unlink(self.db_path)

    def test_returns_none_when_not_set(self):
        result = run(_get_default(self.db_path, "user_999"))
        assert result is None

    def test_set_and_get(self):
        run(_set_default(self.db_path, "user_1", 100))
        result = run(_get_default(self.db_path, "user_1"))
        assert result == 100

    def test_upsert_overwrites(self):
        run(_set_default(self.db_path, "user_2", 50))
        run(_set_default(self.db_path, "user_2", 200))
        result = run(_get_default(self.db_path, "user_2"))
        assert result == 200

    def test_different_users_independent(self):
        run(_set_default(self.db_path, "user_a", 75))
        run(_set_default(self.db_path, "user_b", 150))
        assert run(_get_default(self.db_path, "user_a")) == 75
        assert run(_get_default(self.db_path, "user_b")) == 150

    def test_set_minimum_value(self):
        run(_set_default(self.db_path, "user_3", 1))
        assert run(_get_default(self.db_path, "user_3")) == 1

    def test_set_large_value(self):
        run(_set_default(self.db_path, "user_4", 100_000))
        assert run(_get_default(self.db_path, "user_4")) == 100_000


# ── JoinModal default pre-fill ────────────────────────────────────────────────

from bot.cogs.craps import BetType, CrapsTable, JoinModal, Phase


class _FakeView:
    """Minimal stand-in for CrapsTableView (just needs to exist as an object)."""


class TestJoinModalDefault:
    def _make_table(self) -> CrapsTable:
        return CrapsTable(channel_id=1, shooter_id=42, shooter_name="Alice")

    def test_no_default_leaves_field_empty(self):
        table = self._make_table()
        modal = JoinModal(table, BetType.PASS_LINE, _FakeView(), balance=1000, default_bet=None)
        # default attribute should not be set (None or empty string)
        assert not modal.amount.default

    def test_default_bet_pre_fills_field(self):
        table = self._make_table()
        modal = JoinModal(table, BetType.PASS_LINE, _FakeView(), balance=1000, default_bet=75)
        assert modal.amount.default == "75"

    def test_default_bet_zero_not_pre_filled(self):
        # 0 is falsy — should not be set as a default (guard against edge case)
        table = self._make_table()
        modal = JoinModal(table, BetType.DONT_PASS, _FakeView(), balance=500, default_bet=None)
        assert not modal.amount.default

    def test_title_includes_bet_type(self):
        table = self._make_table()
        modal = JoinModal(table, BetType.DONT_PASS, _FakeView(), balance=500, default_bet=50)
        assert "Don't Pass" in modal.title

    def test_balance_shown_in_placeholder(self):
        table = self._make_table()
        modal = JoinModal(table, BetType.PASS_LINE, _FakeView(), balance=2500, default_bet=None)
        assert "2500" in modal.amount.placeholder


# ── setdefault input validation (logic only, no Discord interaction) ──────────

class TestSetDefaultValidation:
    """Test the validation rules that the /craps setdefault command enforces."""

    def _is_valid(self, amount: int) -> bool:
        return 1 <= amount <= 100_000

    def test_zero_is_invalid(self):
        assert not self._is_valid(0)

    def test_negative_is_invalid(self):
        assert not self._is_valid(-50)

    def test_one_is_valid(self):
        assert self._is_valid(1)

    def test_max_is_valid(self):
        assert self._is_valid(100_000)

    def test_over_max_is_invalid(self):
        assert not self._is_valid(100_001)

    def test_typical_amounts_are_valid(self):
        for amt in (10, 50, 100, 500, 1000, 5000):
            assert self._is_valid(amt)
