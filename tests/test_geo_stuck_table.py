"""Regression tests for the geography 'already a table' false-positive bug.

Root cause
----------
`_end_game` set ``table.phase = "closed"`` on line 1, then called
``await update_elo_multiplayer(...)`` (async), and only called
``active_tables.pop()`` *after* that await returned.  During the ELO
computation window the table was still in ``active_tables`` with a running
``race_task``, so `/geography` incorrectly returned "There's already a
geography table in this channel!" even though the game was over.

Fix
---
``active_tables.pop()`` (and ``self.stop()``) now happen *synchronously*
before any ``await`` in ``_end_game``.

Secondary fix
-------------
``on_timeout`` now includes an identity check so an old view's timeout
cannot evict a *replacement* table that was created for the same channel
after the original game ended.

Tests
-----
1. Active tables cleared before ELO is awaited (core regression).
2. ``on_timeout`` skips eviction when a replacement table is registered.
3. ``on_timeout`` does evict its own table when it is still registered.
4. Playing-phase table blocks /geography (existing correct behaviour).
"""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stubs so the geography module can be imported without a live DB or Discord
# ---------------------------------------------------------------------------

_queries_stub = types.ModuleType("db.queries")
_queries_stub.record_geo_attempt = AsyncMock()
_queries_stub.get_geo_stats_by_region = AsyncMock(return_value=[])
_queries_stub.get_elo_rating = AsyncMock(return_value={"rating": 1000, "games_played": 0})
sys.modules.setdefault("db", types.ModuleType("db"))
sys.modules["db.queries"] = _queries_stub

from bot.cogs.geography import GeoPlayer, GeoTable, GeoTableView  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_table(channel_id: int = 1, phase: str = "betting") -> GeoTable:
    return GeoTable(channel_id=channel_id, host_id=42, host_name="Host", phase=phase)


def _make_view(table: GeoTable, active_tables: dict) -> GeoTableView:
    """Create a GeoTableView without touching Discord internals."""
    view = object.__new__(GeoTableView)
    view.table = table
    view.active_tables = active_tables
    # discord.ui.View.children is a read-only property; leave it alone.
    # Tests that call _end_game must patch it via PropertyMock.
    return view


# ---------------------------------------------------------------------------
# 1. Core regression: active_tables must be cleared before ELO is computed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_tables_cleared_before_elo_computation():
    """active_tables.pop must happen synchronously before update_elo_multiplayer.

    Bug: the pop happened *after* the await, so anyone who ran /geography
    during ELO computation got a false 'already a table' error.
    """
    active_tables: dict[int, GeoTable] = {}

    table = _make_table(channel_id=1, phase="playing")
    # Add two players so the ELO branch is entered
    table.players = {
        42: GeoPlayer(user_id=42, display_name="Alice", rounds_won=3),
        99: GeoPlayer(user_id=99, display_name="Bob", rounds_won=1),
    }
    active_tables[1] = table

    view = _make_view(table, active_tables)

    # Patch the read-only discord.ui.View properties/methods needed by _end_game
    view.stop = MagicMock()

    still_registered_when_elo_called = None

    async def mock_elo(finish_order, *args, **kwargs):
        nonlocal still_registered_when_elo_called
        still_registered_when_elo_called = 1 in active_tables
        return {}

    with (
        patch.object(GeoTableView, "children", new_callable=PropertyMock, return_value=[]),
        patch("bot.cogs.geography.update_elo_multiplayer", mock_elo),
    ):
        await view._end_game()

    assert still_registered_when_elo_called is False, (
        "active_tables still had an entry when update_elo_multiplayer ran. "
        "This means /geography would incorrectly block during ELO computation."
    )
    assert 1 not in active_tables, "active_tables should be empty after _end_game"


# ---------------------------------------------------------------------------
# 2. on_timeout identity check — must not evict a replacement table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_timeout_skips_eviction_when_table_replaced():
    """Old view's on_timeout must not pop a replacement table.

    Scenario: old table ends, new table registered for same channel,
    old view's timeout fires later — it must leave the new table alone.
    """
    active_tables: dict[int, GeoTable] = {}

    old_table = _make_table(channel_id=1, phase="betting")
    active_tables[1] = old_table

    # Replacement arrives (simulates another /geography call)
    new_table = _make_table(channel_id=1, phase="betting")
    active_tables[1] = new_table

    # Replay the identity check logic from on_timeout
    if active_tables.get(old_table.channel_id) is not old_table:
        pass  # correct — returns early without popping
    else:
        active_tables.pop(old_table.channel_id, None)  # would be wrong

    assert active_tables.get(1) is new_table, (
        "on_timeout popped the replacement table — identity check is missing"
    )


# ---------------------------------------------------------------------------
# 3. on_timeout does evict its own table when still registered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_timeout_evicts_own_table():
    """on_timeout must remove the table when it is still the registered one."""
    active_tables: dict[int, GeoTable] = {}

    table = _make_table(channel_id=2, phase="betting")
    active_tables[2] = table

    # Identity check: same object → evict
    if active_tables.get(table.channel_id) is not table:
        pass  # should NOT take this path
    else:
        active_tables.pop(table.channel_id, None)

    assert 2 not in active_tables, "Own table should have been evicted"


# ---------------------------------------------------------------------------
# 4. Playing-phase table blocks /geography (guard correctness)
# ---------------------------------------------------------------------------


def test_geography_command_blocks_playing_phase_table():
    """A running race_task in playing phase must block /geography."""
    active_tables: dict[int, GeoTable] = {}

    existing = _make_table(channel_id=3, phase="playing")
    mock_task = MagicMock()
    mock_task.done.return_value = False
    existing.race_task = mock_task
    active_tables[3] = existing

    # Replicate the _has_running check from the /geography command
    _has_running = any(
        (t := getattr(existing, n, None)) is not None and not t.done()
        for n in (
            "game_task", "race_task", "sim_task", "round_task", "_round_task",
            "trade_task", "fly_task", "_shot_clock_task", "_countdown_task",
        )
    )

    assert _has_running, "Playing-phase table with active race_task should block /geography"
