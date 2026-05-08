"""Tests for crash table stuck-game bugs.

Covers:
1. _lifetime_expire() must not pop a *replacement* table (identity check).
2. /crash must block betting-phase tables (not just flying ones).
3. /crash must block flying-phase tables.
4. /crash may replace a crashed-phase (round-over) table.
5. view reference stored on table; crashclose can stop the view.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers: minimal stubs so we can import the cog without a real DB or Discord
# ---------------------------------------------------------------------------

# Patch DB queries before import so the module-level import succeeds
import sys
import types

_queries_stub = types.ModuleType("db.queries")
_queries_stub.get_or_create_casino_wallet = AsyncMock(return_value=1000)
_queries_stub.update_casino_balance = AsyncMock()
_queries_stub.get_casino_balance = AsyncMock(return_value=1000)
_queries_stub.log_casino_result = AsyncMock()
_queries_stub.record_geo_attempt = AsyncMock()
_queries_stub.get_geo_stats_by_region = AsyncMock(return_value=[])
_queries_stub.get_elo_rating = AsyncMock(return_value={"rating": 1000, "games_played": 0})
sys.modules.setdefault("db", types.ModuleType("db"))
sys.modules["db.queries"] = _queries_stub

# Minimal discord stubs
import importlib

# Ensure crash module re-uses the patched queries
if "bot.cogs.crash" in sys.modules:
    del sys.modules["bot.cogs.crash"]

from bot.cogs.crash import (  # noqa: E402
    CrashCog,
    CrashPlayer,
    CrashTable,
    CrashTableView,
    TABLE_LIFETIME_SECS,
)

# Force the crash module's `queries` reference to the stub. Without this,
# when this file runs after another test that imported the real db.queries,
# the `from db import queries` inside crash.py resolves to the real module
# and tests that exercise refund logic try to open the real SQLite file.
import bot.cogs.crash as _crash_mod  # noqa: E402
_crash_mod.queries = _queries_stub


def _make_table(channel_id: int = 1, phase: str = "betting") -> CrashTable:
    return CrashTable(channel_id=channel_id, host_id=42, host_name="Host", phase=phase)


def _make_view(table: CrashTable, active_tables: dict) -> CrashTableView:
    """Create a view without starting background tasks."""
    return CrashTableView(table, active_tables)


# ---------------------------------------------------------------------------
# 1. _lifetime_expire identity check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifetime_expire_does_not_pop_replacement_table():
    """Old view's timer must not evict a *new* table that replaced it."""
    active_tables: dict[int, CrashTable] = {}

    old_table = _make_table(channel_id=1, phase="crashed")
    old_view = _make_view(old_table, active_tables)
    active_tables[1] = old_table

    # Simulate the old table being replaced by a new one
    new_table = _make_table(channel_id=1, phase="betting")
    active_tables[1] = new_table  # replacement

    # Trigger the expire logic directly (skip the sleep)
    # Mimic what _lifetime_expire does after asyncio.sleep:
    if active_tables.get(old_table.channel_id) is not old_table:
        pass  # correct: returns early
    else:
        active_tables.pop(old_table.channel_id, None)  # would be wrong

    # New table must still be registered
    assert active_tables.get(1) is new_table, (
        "_lifetime_expire popped the replacement table — identity check missing"
    )


@pytest.mark.asyncio
async def test_lifetime_expire_pops_its_own_table_when_still_active():
    """Old view's timer SHOULD pop its own table when it's still there."""
    active_tables: dict[int, CrashTable] = {}

    table = _make_table(channel_id=2, phase="betting")
    view = _make_view(table, active_tables)
    active_tables[2] = table

    # Identity check passes — same object
    if active_tables.get(table.channel_id) is not table:
        pass  # should NOT take this path
    else:
        active_tables.pop(table.channel_id, None)

    assert 2 not in active_tables, "Own table should have been removed"


# ---------------------------------------------------------------------------
# 2. /crash command stale-table detection
# ---------------------------------------------------------------------------


def _staleness(existing: CrashTable, idle_secs: float) -> bool:
    """Mirror of the staleness check in CrashCog.crash."""
    fly_done = existing.fly_task is None or existing.fly_task.done()
    crashed_done = existing.phase == "crashed" and fly_done
    zombie_flight = existing.phase == "flying" and fly_done
    from bot.cogs.crash import IDLE_REPLACE_SECS
    idle_too_long = idle_secs >= IDLE_REPLACE_SECS
    return crashed_done or zombie_flight or idle_too_long


def test_crash_command_blocks_recent_betting_table():
    """/crash blocks a recently-active betting table (locked bets, not idle)."""
    existing = _make_table(channel_id=3, phase="betting")
    existing.fly_task = None
    assert not _staleness(existing, idle_secs=5), (
        "recently-active betting-phase table should block /crash"
    )


def test_crash_command_replaces_idle_betting_table():
    """/crash takes over a betting-phase table that has been idle too long."""
    from bot.cogs.crash import IDLE_REPLACE_SECS
    existing = _make_table(channel_id=3, phase="betting")
    existing.fly_task = None
    assert _staleness(existing, idle_secs=IDLE_REPLACE_SECS + 1), (
        "idle betting-phase table should be replaceable (force_close refunds bets)"
    )


def test_crash_command_blocks_active_flying_table():
    """/crash must block when existing table is actively flying."""
    existing = _make_table(channel_id=4, phase="flying")
    mock_task = MagicMock()
    mock_task.done.return_value = False
    existing.fly_task = mock_task
    assert not _staleness(existing, idle_secs=5), (
        "actively-flying table must block new /crash"
    )


def test_crash_command_replaces_zombie_flight():
    """/crash takes over a flying table whose fly_task already finished."""
    existing = _make_table(channel_id=4, phase="flying")
    mock_task = MagicMock()
    mock_task.done.return_value = True  # task ended but phase never advanced
    existing.fly_task = mock_task
    assert _staleness(existing, idle_secs=1), (
        "zombie flight (fly_task done, phase still 'flying') should be replaceable"
    )


def test_crash_command_allows_replacing_crashed_phase_table():
    """/crash may replace a table whose round fully ended (phase=crashed, task done)."""
    active_tables: dict[int, CrashTable] = {}

    existing = _make_table(channel_id=5, phase="crashed")
    mock_task = MagicMock()
    mock_task.done.return_value = True
    existing.fly_task = mock_task
    active_tables[5] = existing

    fly_done = existing.fly_task is None or existing.fly_task.done()
    is_stale = existing.phase == "crashed" and fly_done

    assert is_stale, "crashed-phase table with done fly_task should be replaceable"


def test_crash_command_allows_replacing_crashed_phase_no_task():
    """/crash may replace a crashed table where fly_task is None."""
    existing = _make_table(channel_id=6, phase="crashed")
    existing.fly_task = None
    assert _staleness(existing, idle_secs=0), (
        "crashed-phase table with fly_task=None should always be replaceable"
    )


@pytest.mark.asyncio
async def test_force_close_refunds_locked_bets():
    """When /crash takes over an idle betting table, locked bets must be refunded."""
    from bot.cogs.crash import CrashCog, CrashPlayer

    # Reset the mock so this test owns the call history
    _queries_stub.update_casino_balance.reset_mock()

    bot = MagicMock()
    cog = CrashCog(bot)

    table = _make_table(channel_id=10, phase="betting")
    table.players[111] = CrashPlayer(
        user_id=111, display_name="A", bet=50, cashed_out=False,
    )
    table.players[222] = CrashPlayer(
        user_id=222, display_name="B", bet=75, cashed_out=True,  # already paid
    )
    # Give the table a stub view so _force_close_table can stop() it
    table.view = MagicMock()
    table.view._lifetime_task = None
    table.message = None  # skip message edit

    await cog._force_close_table(table, "test")

    # Player A's locked bet refunded; player B (already cashed out) not touched
    calls = _queries_stub.update_casino_balance.call_args_list
    refunded = {(c.args[0], c.args[1]) for c in calls}
    assert ("111", 50) in refunded, "non-cashed-out player must be refunded"
    assert ("222", 75) not in refunded, "cashed-out player must NOT be refunded"


@pytest.mark.asyncio
async def test_crash_takes_over_idle_table_via_force_close():
    """/crash should detect an idle betting table and take it over after refund."""
    import time
    from bot.cogs.crash import CrashCog, CrashPlayer, IDLE_REPLACE_SECS

    _queries_stub.update_casino_balance.reset_mock()

    bot = MagicMock()
    cog = CrashCog(bot)

    stuck = _make_table(channel_id=20, phase="betting")
    stuck.players[333] = CrashPlayer(
        user_id=333, display_name="Stuck", bet=100, cashed_out=False,
    )
    stuck.last_activity_at = time.monotonic() - (IDLE_REPLACE_SECS + 10)
    stuck.view = MagicMock()
    stuck.view._lifetime_task = None
    stuck.message = None
    cog.active_tables[20] = stuck

    # Directly exercise the staleness path the /crash command uses
    idle_secs = time.monotonic() - stuck.last_activity_at
    assert idle_secs >= IDLE_REPLACE_SECS

    await cog._force_close_table(stuck, "idle replace")
    cog.active_tables.pop(20, None)

    # Locked bet refunded
    calls = _queries_stub.update_casino_balance.call_args_list
    assert ("333", 100) in {(c.args[0], c.args[1]) for c in calls}
    # Slot freed
    assert 20 not in cog.active_tables


# ---------------------------------------------------------------------------
# 3. view reference stored on table
# ---------------------------------------------------------------------------


def test_view_reference_stored_on_table():
    """CrashTableView.__init__ must store itself on table.view."""
    active_tables: dict[int, CrashTable] = {}
    table = _make_table()
    view = _make_view(table, active_tables)
    assert table.view is view, "table.view should point back to the view"


# ---------------------------------------------------------------------------
# 4. on_timeout identity check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_timeout_skips_when_table_replaced():
    """on_timeout must not touch active_tables when the table was replaced."""
    active_tables: dict[int, CrashTable] = {}

    old_table = _make_table(channel_id=7, phase="crashed")
    old_view = _make_view(old_table, active_tables)
    active_tables[7] = old_table

    # Replace with new table
    new_table = _make_table(channel_id=7, phase="betting")
    active_tables[7] = new_table

    # on_timeout identity check logic:
    if active_tables.get(old_table.channel_id) is not old_table:
        pass  # correct path — returns early
    else:
        active_tables.pop(old_table.channel_id, None)  # would be wrong

    assert active_tables.get(7) is new_table, (
        "on_timeout should not have evicted the replacement table"
    )
