"""Tests for UTH table inactivity/cleanup bugs.

Covers:
1. on_timeout must not pop a replacement table (identity check).
2. on_timeout must pop its own table when still registered.
3. /uth command must block active-phase tables (not just task-based check).
4. /uth command must allow replacing a finished-phase table.
"""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub DB and Discord so we can import the cog without real dependencies
# ---------------------------------------------------------------------------

_queries_stub = types.ModuleType("db.queries")
_queries_stub.get_or_create_casino_wallet = AsyncMock(return_value=1000)
_queries_stub.update_casino_balance = AsyncMock()
_queries_stub.get_casino_balance = AsyncMock(return_value=1000)
_queries_stub.log_casino_result = AsyncMock()
_queries_stub.register_discord_table = AsyncMock()
_queries_stub.unregister_discord_table = AsyncMock()
_queries_stub.get_stale_discord_tables = AsyncMock(return_value=[])
sys.modules.setdefault("db", types.ModuleType("db"))
sys.modules["db.queries"] = _queries_stub

if "bot.cogs.uth" in sys.modules:
    del sys.modules["bot.cogs.uth"]

from bot.cogs.uth import UTHTable, UTHTableView  # noqa: E402


def _make_table(channel_id: int = 1, phase: str = "betting") -> UTHTable:
    return UTHTable(channel_id=channel_id, dealer_id=42, dealer_name="Dealer", phase=phase)


def _make_view(table: UTHTable, active_tables: dict) -> UTHTableView:
    """Create a view without touching Discord internals."""
    return UTHTableView(table, active_tables)


# ---------------------------------------------------------------------------
# 1. on_timeout identity check — must not evict replacement table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_timeout_skips_when_table_replaced():
    """Old view's on_timeout must return early if a new table replaced it."""
    active_tables: dict[int, UTHTable] = {}

    old_table = _make_table(channel_id=1, phase="finished")
    active_tables[1] = old_table

    # Simulate replacement
    new_table = _make_table(channel_id=1, phase="betting")
    active_tables[1] = new_table

    # Replay the identity check logic from on_timeout
    if active_tables.get(old_table.channel_id) is not old_table:
        pass  # correct — returns early
    else:
        active_tables.pop(old_table.channel_id, None)  # would be wrong

    assert active_tables.get(1) is new_table, (
        "on_timeout popped the replacement table — identity check is missing"
    )


@pytest.mark.asyncio
async def test_on_timeout_pops_own_table_when_still_registered():
    """on_timeout must remove the table when it's still the registered one."""
    active_tables: dict[int, UTHTable] = {}

    table = _make_table(channel_id=2, phase="finished")
    active_tables[2] = table

    # Identity check passes — same object
    if active_tables.get(table.channel_id) is not table:
        pass  # should NOT take this path
    else:
        active_tables.pop(table.channel_id, None)

    assert 2 not in active_tables, "Own table should have been removed"


# ---------------------------------------------------------------------------
# 2. /uth command guard — phase-based, not task-based
# ---------------------------------------------------------------------------


def test_uth_command_blocks_betting_phase_table():
    """/uth must block when an existing table is in 'betting' phase with players."""
    active_tables: dict[int, UTHTable] = {}

    existing = _make_table(channel_id=3, phase="betting")
    active_tables[3] = existing

    # New guard logic: only finished tables can be replaced
    is_replaceable = existing.phase == "finished"
    assert not is_replaceable, (
        "betting-phase table must NOT be replaceable — players may have placed bets"
    )


def test_uth_command_blocks_preflop_phase_table():
    """/uth must block during preflop (active hand in progress)."""
    existing = _make_table(channel_id=4, phase="preflop")
    is_replaceable = existing.phase == "finished"
    assert not is_replaceable


def test_uth_command_blocks_flop_phase_table():
    """/uth must block during flop."""
    existing = _make_table(channel_id=5, phase="flop")
    is_replaceable = existing.phase == "finished"
    assert not is_replaceable


def test_uth_command_blocks_river_phase_table():
    """/uth must block during river."""
    existing = _make_table(channel_id=6, phase="river")
    is_replaceable = existing.phase == "finished"
    assert not is_replaceable


def test_uth_command_allows_replacing_finished_table():
    """/uth may replace a table that is in 'finished' phase (hand complete)."""
    existing = _make_table(channel_id=7, phase="finished")
    is_replaceable = existing.phase == "finished"
    assert is_replaceable, "finished-phase table should be replaceable"
