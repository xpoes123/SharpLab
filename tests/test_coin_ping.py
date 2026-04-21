"""Unit tests for coin ping notification system."""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from unittest import mock

import aiosqlite
import pytest
import pytest_asyncio

from db import queries, schema


@pytest_asyncio.fixture
async def tmp_db(tmp_path):
    """Create a temp DB with schema, patching DB_PATH for both schema and queries."""
    db_path = str(tmp_path / "test.db")
    with mock.patch.object(schema, "DB_PATH", db_path), \
         mock.patch.object(queries, "DB_PATH", db_path):
        await schema.init_db()
        yield db_path


@pytest.mark.asyncio
class TestClaimSetsNotifyFlag:
    async def test_new_wallet_sets_notify(self, tmp_db):
        """New wallet creation sets notify_on_ready = 1."""
        balance, credited = await queries.get_or_create_wallet("user1")
        assert credited is True
        async with aiosqlite.connect(tmp_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT notify_on_ready FROM wallets WHERE discord_user = ?",
                ("user1",),
            )
            row = await cursor.fetchone()
        assert row["notify_on_ready"] == 1

    async def test_cooldown_credit_sets_notify(self, tmp_db):
        """Claiming coins after cooldown sets notify_on_ready = 1."""
        # Create wallet first
        await queries.get_or_create_wallet("user2")
        # Clear the flag manually
        async with aiosqlite.connect(tmp_db) as db:
            await db.execute(
                "UPDATE wallets SET notify_on_ready = 0, last_daily = ? WHERE discord_user = ?",
                ((datetime.now(timezone.utc) - timedelta(hours=9)).isoformat(), "user2"),
            )
            await db.commit()
        # Claim again — should credit and set flag
        balance, credited = await queries.get_or_create_wallet("user2")
        assert credited is True
        async with aiosqlite.connect(tmp_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT notify_on_ready FROM wallets WHERE discord_user = ?",
                ("user2",),
            )
            row = await cursor.fetchone()
        assert row["notify_on_ready"] == 1


@pytest.mark.asyncio
class TestGetUsersReadyForCoinPing:
    async def test_returns_eligible(self, tmp_db):
        """User with last_daily 9h ago and notify_on_ready=1 is returned."""
        nine_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat()
        async with aiosqlite.connect(tmp_db) as db:
            await db.execute(
                "INSERT INTO wallets (discord_user, balance, last_daily, notify_on_ready) VALUES (?, ?, ?, ?)",
                ("user_ready", 100, nine_hours_ago, 1),
            )
            await db.commit()
        ready = await queries.get_users_ready_for_coin_ping()
        assert "user_ready" in ready

    async def test_skips_not_ready(self, tmp_db):
        """User with last_daily 1h ago should NOT be returned."""
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        async with aiosqlite.connect(tmp_db) as db:
            await db.execute(
                "INSERT INTO wallets (discord_user, balance, last_daily, notify_on_ready) VALUES (?, ?, ?, ?)",
                ("user_too_soon", 100, one_hour_ago, 1),
            )
            await db.commit()
        ready = await queries.get_users_ready_for_coin_ping()
        assert "user_too_soon" not in ready

    async def test_skips_already_notified(self, tmp_db):
        """User with notify_on_ready=0 should NOT be returned even if cooldown expired."""
        nine_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat()
        async with aiosqlite.connect(tmp_db) as db:
            await db.execute(
                "INSERT INTO wallets (discord_user, balance, last_daily, notify_on_ready) VALUES (?, ?, ?, ?)",
                ("user_already_pinged", 100, nine_hours_ago, 0),
            )
            await db.commit()
        ready = await queries.get_users_ready_for_coin_ping()
        assert "user_already_pinged" not in ready


@pytest.mark.asyncio
class TestClearCoinPing:
    async def test_clears_flag(self, tmp_db):
        """clear_coin_ping sets notify_on_ready = 0."""
        async with aiosqlite.connect(tmp_db) as db:
            await db.execute(
                "INSERT INTO wallets (discord_user, balance, last_daily, notify_on_ready) VALUES (?, ?, ?, ?)",
                ("user_clear", 100, datetime.now(timezone.utc).isoformat(), 1),
            )
            await db.commit()
        await queries.clear_coin_ping("user_clear")
        async with aiosqlite.connect(tmp_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT notify_on_ready FROM wallets WHERE discord_user = ?",
                ("user_clear",),
            )
            row = await cursor.fetchone()
        assert row["notify_on_ready"] == 0
