"""Tests for tip_casino_coins() in db/queries.py."""
from __future__ import annotations

import asyncio
import os
import sys

import aiosqlite
import pytest

sys.path.insert(0, ".")

import db.schema as _schema  # noqa: E402
import db.queries as _queries  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path):
    """Temp SQLite DB with the full schema, DB_PATH patched in both modules."""
    db_path = str(tmp_path / "test.db")
    orig_schema = _schema.DB_PATH
    orig_queries = _queries.DB_PATH
    _schema.DB_PATH = db_path
    _queries.DB_PATH = db_path
    _run(_schema.init_db())
    yield db_path
    _schema.DB_PATH = orig_schema
    _queries.DB_PATH = orig_queries


def _balance(db_path: str, user: str) -> int | None:
    """Read raw balance directly from DB (bypasses min-balance logic)."""
    async def _fetch():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT balance FROM casino_wallets WHERE discord_user = ?", (user,)
            )
            row = await cur.fetchone()
        return row["balance"] if row else None
    return _run(_fetch())


class TestTipCasinoCoins:
    def test_successful_tip(self, tmp_db):
        """Sender balance decreases, recipient balance increases by the correct amount."""
        _run(_queries.get_or_create_casino_wallet("sender"))
        _run(_queries.give_casino_coins("sender", 500))  # sender: 1000 + 500 = 1500
        _run(_queries.get_or_create_casino_wallet("recipient"))

        sender_bal, recipient_bal = _run(_queries.tip_casino_coins("sender", "recipient", 200))

        assert sender_bal == 1300  # 1500 - 200
        assert recipient_bal == 1200  # 1000 (starting) + 200

    def test_insufficient_funds_raises(self, tmp_db):
        """ValueError raised when tip would drop sender below CASINO_MIN_BALANCE."""
        # Sender has minimum balance (1000). Tipping ANY amount ≥ 1 must fail
        # because balance - amount < CASINO_MIN_BALANCE (sender can't go below 1000).
        _run(_queries.get_or_create_casino_wallet("sender"))
        _run(_queries.get_or_create_casino_wallet("recipient"))

        with pytest.raises(ValueError, match="Insufficient|No casino wallet"):
            _run(_queries.tip_casino_coins("sender", "recipient", 1))

    def test_min_balance_floor_does_not_mint_coins(self, tmp_db):
        """Coin conservation: tip must be rejected if result would drop sender below CASINO_MIN_BALANCE.

        Covers the exploit where balance - amount falls between 0 and CASINO_MIN_BALANCE:
        sender has 1500, tips 600 → result 900 < CASINO_MIN_BALANCE → must be rejected entirely,
        not silently floored so the sender only loses 500 while recipient gains 600.
        """
        _run(_queries.get_or_create_casino_wallet("sender"))
        _run(_queries.give_casino_coins("sender", 500))  # sender: 1500
        _run(_queries.get_or_create_casino_wallet("recipient"))

        sender_before = _balance(tmp_db, "sender")   # 1500
        recipient_before = _balance(tmp_db, "recipient")  # 1000

        # 1500 - 600 = 900 < CASINO_MIN_BALANCE(1000) → must be rejected
        with pytest.raises(ValueError, match="Insufficient|No casino wallet"):
            _run(_queries.tip_casino_coins("sender", "recipient", 600))

        assert _balance(tmp_db, "sender") == sender_before
        assert _balance(tmp_db, "recipient") == recipient_before

    def test_insufficient_funds_no_side_effects(self, tmp_db):
        """Failed tip leaves both balances unchanged."""
        _run(_queries.get_or_create_casino_wallet("sender"))
        _run(_queries.get_or_create_casino_wallet("recipient"))

        sender_before = _balance(tmp_db, "sender")
        recipient_before = _balance(tmp_db, "recipient")

        with pytest.raises(ValueError):
            _run(_queries.tip_casino_coins("sender", "recipient", 1))

        assert _balance(tmp_db, "sender") == sender_before
        assert _balance(tmp_db, "recipient") == recipient_before

    def test_tip_to_user_without_wallet(self, tmp_db):
        """Recipient wallet is auto-created if it doesn't exist; tip succeeds."""
        _run(_queries.get_or_create_casino_wallet("sender"))
        _run(_queries.give_casino_coins("sender", 500))  # sender: 1500

        # Verify recipient has no wallet yet
        assert _balance(tmp_db, "new_recipient") is None

        sender_bal, recipient_bal = _run(
            _queries.tip_casino_coins("sender", "new_recipient", 100)
        )

        assert sender_bal == 1400  # 1500 - 100
        assert recipient_bal == 1100  # 1000 (new wallet) + 100
        assert _balance(tmp_db, "new_recipient") == 1100

    def test_tip_logs_casino_history_for_both_users(self, tmp_db):
        """Both sender and recipient get a 'tip' row in casino_history."""
        _run(_queries.get_or_create_casino_wallet("alice"))
        _run(_queries.give_casino_coins("alice", 500))
        _run(_queries.get_or_create_casino_wallet("bob"))

        _run(_queries.tip_casino_coins("alice", "bob", 150))

        async def _history(user):
            async with aiosqlite.connect(tmp_db) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT game, wagered, payout FROM casino_history "
                    "WHERE discord_user = ? AND game = 'tip'",
                    (user,),
                )
                return await cur.fetchall()

        alice_rows = _run(_history("alice"))
        bob_rows = _run(_history("bob"))

        assert len(alice_rows) == 1
        assert alice_rows[0]["wagered"] == 150
        assert alice_rows[0]["payout"] == 0

        assert len(bob_rows) == 1
        assert bob_rows[0]["wagered"] == 0
        assert bob_rows[0]["payout"] == 150
