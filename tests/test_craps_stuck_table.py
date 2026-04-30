"""Tests for craps stuck-table fixes: exception safety and phase consistency."""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

from bot.cogs.craps import CrapsTable, Phase


def run(coro):
    return asyncio.run(coro)


# ── on_timeout sets phase ────────────────────────────────────────────────────

class TestOnTimeoutPhase:
    """on_timeout must mark table.phase = FINISHED so state is consistent."""

    def _make_table(self) -> CrapsTable:
        return CrapsTable(channel_id=1, shooter_id=42, shooter_name="Alice")

    def test_phase_set_to_finished_by_timeout(self):
        """on_timeout should mark the table as FINISHED regardless of DB results."""
        from unittest.mock import AsyncMock, patch, MagicMock
        from bot.cogs.craps import CrapsTableView

        table = self._make_table()
        active_tables: dict[int, CrapsTable] = {1: table}
        view = CrapsTableView(table, active_tables)

        # on_timeout calls update_casino_balance (no players → no calls) then pops
        async def _run():
            with patch("bot.cogs.craps.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock()
                table.message = None
                await view.on_timeout()

        run(_run())
        assert table.phase == Phase.FINISHED

    def test_timeout_removes_table_from_active(self):
        from unittest.mock import AsyncMock, patch
        from bot.cogs.craps import CrapsTableView

        table = self._make_table()
        active_tables: dict[int, CrapsTable] = {1: table}
        view = CrapsTableView(table, active_tables)

        async def _run():
            with patch("bot.cogs.craps.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock()
                table.message = None
                await view.on_timeout()

        run(_run())
        assert 1 not in active_tables

    def test_timeout_noop_if_already_finished(self):
        """If phase is already FINISHED, on_timeout should return immediately."""
        from unittest.mock import AsyncMock, patch
        from bot.cogs.craps import CrapsTableView

        table = self._make_table()
        table.phase = Phase.FINISHED
        active_tables: dict[int, CrapsTable] = {}  # already cleaned up
        view = CrapsTableView(table, active_tables)

        called = []

        async def _run():
            with patch("bot.cogs.craps.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock(side_effect=lambda *a, **kw: called.append(1))
                await view.on_timeout()

        run(_run())
        assert not called, "Should not touch DB if already FINISHED"


# ── _finish cleanup order ────────────────────────────────────────────────────

class TestFinishCleanupOrder:
    """_finish must clean up active_tables and stop the view BEFORE DB calls,
    so a DB failure cannot leave the table stuck."""

    def _make_table(self) -> CrapsTable:
        return CrapsTable(channel_id=7, shooter_id=99, shooter_name="Bob")

    def test_active_table_removed_even_if_db_fails(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from bot.cogs.craps import CrapsTableView, PlayerBets, BetType, Phase

        table = self._make_table()
        table.phase = Phase.FINISHED
        table.outcome = "Natural!"
        player = PlayerBets(user_id=99, display_name="Bob", bet_type=BetType.PASS_LINE, bet=50, coins_in=50, payout=100)
        table.players[99] = player
        active_tables: dict[int, CrapsTable] = {7: table}
        view = CrapsTableView(table, active_tables)

        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        async def _run():
            with patch("bot.cogs.craps.queries") as mock_q:
                # Simulate DB failure
                mock_q.update_casino_balance = AsyncMock(side_effect=RuntimeError("DB down"))
                mock_q.get_casino_balance = AsyncMock(return_value=0)
                mock_q.log_casino_result = AsyncMock()
                # Should not raise — errors are caught per-player
                await view._finish(interaction, followup=False)

        run(_run())
        # Table must be removed from active_tables despite DB failure
        assert 7 not in active_tables

    def test_finish_logs_error_on_db_failure(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from bot.cogs.craps import CrapsTableView, PlayerBets, BetType, Phase
        import logging

        table = self._make_table()
        table.phase = Phase.FINISHED
        table.outcome = "Seven-out!"
        player = PlayerBets(user_id=99, display_name="Bob", bet_type=BetType.PASS_LINE, bet=50, coins_in=50, payout=0)
        table.players[99] = player
        active_tables: dict[int, CrapsTable] = {7: table}
        view = CrapsTableView(table, active_tables)

        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()

        logged = []

        async def _run():
            with patch("bot.cogs.craps.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock(side_effect=RuntimeError("DB error"))
                mock_q.get_casino_balance = AsyncMock(side_effect=RuntimeError("DB error"))
                mock_q.log_casino_result = AsyncMock()
                with patch("bot.cogs.craps.log") as mock_log:
                    mock_log.exception = lambda msg, *a, **kw: logged.append(msg)
                    await view._finish(interaction, followup=False)

        run(_run())
        assert any("craps" in m for m in logged), "Should log the DB failure"


# ── /craps close command ──────────────────────────────────────────────────────

class TestCrapsCloseCommand:
    """Force-close command should refund players and remove table from active_tables."""

    def _make_table(self) -> CrapsTable:
        return CrapsTable(channel_id=5, shooter_id=10, shooter_name="Charlie")

    def test_close_removes_table(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from bot.cogs.craps import CrapsCog, Phase

        table = self._make_table()
        cog = CrapsCog.__new__(CrapsCog)
        cog.active_tables = {5: table}

        interaction = MagicMock()
        interaction.channel_id = 5
        interaction.user = MagicMock()
        interaction.user.display_name = "Admin"
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        async def _run():
            with patch("bot.cogs.craps.queries") as mock_q:
                mock_q.update_casino_balance = AsyncMock(return_value=1000)
                table.message = None
                await cog.craps_close.callback(cog, interaction)

        run(_run())
        assert 5 not in cog.active_tables
        assert table.phase == Phase.FINISHED

    def test_close_no_table_in_channel(self):
        from unittest.mock import AsyncMock, MagicMock
        from bot.cogs.craps import CrapsCog

        cog = CrapsCog.__new__(CrapsCog)
        cog.active_tables = {}

        interaction = MagicMock()
        interaction.channel_id = 99
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        run(cog.craps_close.callback(cog, interaction))
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
