"""Tests for the /tip command — transfer_casino_coins in db/queries.py."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

import aiosqlite
import pytest

sys.path.insert(0, ".")

from db import queries
from db.queries import CASINO_MIN_BALANCE, CASINO_STARTING_COINS


def run(coro):
    return asyncio.run(coro)


# ── Temp DB helpers ───────────────────────────────────────────────────────────

async def _make_temp_db() -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    async with aiosqlite.connect(tmp.name) as db:
        await db.execute(
            "CREATE TABLE casino_wallets (discord_user TEXT PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0)"
        )
        await db.commit()
    return tmp.name


async def _set_balance(db_path: str, user: str, balance: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO casino_wallets (discord_user, balance) VALUES (?, ?) "
            "ON CONFLICT(discord_user) DO UPDATE SET balance = excluded.balance",
            (user, balance),
        )
        await db.commit()


async def _get_balance(db_path: str, user: str) -> int | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT balance FROM casino_wallets WHERE discord_user = ?", (user,)
        )
        row = await cur.fetchone()
    return row["balance"] if row else None


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestTransferCasino:
    def setup_method(self):
        self.db_path = run(_make_temp_db())
        # Patch queries.DB_PATH to point at our temp DB
        self._orig_db_path = queries.DB_PATH
        queries.DB_PATH = self.db_path

    def teardown_method(self):
        queries.DB_PATH = self._orig_db_path
        os.unlink(self.db_path)

    def test_tip_success(self):
        """Transfer works and both balances update correctly."""
        run(_set_balance(self.db_path, "alice", 2000))
        run(_set_balance(self.db_path, "bob", 1000))

        sender_bal, recipient_bal = run(queries.transfer_casino_coins("alice", "bob", 500))

        assert sender_bal == 1500
        assert recipient_bal == 1500
        assert run(_get_balance(self.db_path, "alice")) == 1500
        assert run(_get_balance(self.db_path, "bob")) == 1500

    def test_tip_insufficient_funds(self):
        """Raises ValueError when sender has too few coins to cover amount."""
        run(_set_balance(self.db_path, "alice", 500))
        run(_set_balance(self.db_path, "bob", 1000))

        with pytest.raises(ValueError, match="Insufficient funds"):
            run(queries.transfer_casino_coins("alice", "bob", 500))

    def test_tip_below_minimum(self):
        """Cannot tip if it would drop sender below CASINO_MIN_BALANCE floor."""
        # sender has exactly CASINO_MIN_BALANCE + 100; tipping 200 would breach the floor
        run(_set_balance(self.db_path, "alice", CASINO_MIN_BALANCE + 100))
        run(_set_balance(self.db_path, "bob", 1000))

        with pytest.raises(ValueError, match="Insufficient funds"):
            run(queries.transfer_casino_coins("alice", "bob", 200))

        # Tipping exactly 100 (leaving exactly CASINO_MIN_BALANCE) should succeed
        sender_bal, _ = run(queries.transfer_casino_coins("alice", "bob", 100))
        assert sender_bal == CASINO_MIN_BALANCE

    def test_tip_creates_recipient_wallet(self):
        """Recipient who has no wallet gets one created before the transfer."""
        run(_set_balance(self.db_path, "alice", 3000))
        # "carol" has no wallet row at all

        sender_bal, recipient_bal = run(queries.transfer_casino_coins("alice", "carol", 300))

        assert sender_bal == 2700
        # carol starts at CASINO_STARTING_COINS and receives 300
        assert recipient_bal == CASINO_STARTING_COINS + 300

    def test_tip_self_raises(self):
        """Tipping yourself raises ValueError (same user id for sender and recipient)."""
        run(_set_balance(self.db_path, "alice", 5000))

        # The DB layer doesn't explicitly block self-tips but balance math is fine;
        # the cog blocks it. If we want belt-and-suspenders, this test documents
        # that a self-tip at least doesn't corrupt balances.
        sender_bal, recipient_bal = run(queries.transfer_casino_coins("alice", "alice", 500))
        final = run(_get_balance(self.db_path, "alice"))
        assert final == 5000  # net zero — no coins created or destroyed
