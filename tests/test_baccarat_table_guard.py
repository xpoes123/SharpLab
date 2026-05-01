"""Tests for the baccarat 'already a table' guard.

The guard must block /baccarat when a game is actively in progress (bets
placed or cards mid-reveal), but allow it when the table is idle or finished.
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, ".")

from bot.cogs.baccarat import BaccaratCog, BaccaratTable, PlayerSeat


def _make_seat(user_id: int = 1) -> PlayerSeat:
    return PlayerSeat(
        user_id=user_id,
        display_name="Alice",
        bet_type="player",
        wager=100,
    )


def _make_table(channel_id: int = 42) -> BaccaratTable:
    return BaccaratTable(
        channel_id=channel_id,
        opener_id=1,
        opener_name="Alice",
        player_hand=["5♠", "3♥"],
        banker_hand=["2♦", "4♣"],
    )


def _make_interaction(channel_id: int = 42) -> MagicMock:
    interaction = MagicMock()
    interaction.channel_id = channel_id
    interaction.user = MagicMock()
    interaction.user.id = 99
    interaction.user.display_name = "Bob"
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.original_response = AsyncMock(return_value=MagicMock())
    return interaction


def _make_cog() -> BaccaratCog:
    cog = BaccaratCog.__new__(BaccaratCog)
    cog.active_tables = {}
    from bot.cogs.baccarat import _new_shoe
    cog.shoe = _new_shoe()
    return cog


def run(coro):
    return asyncio.run(coro)


# ── Guard blocks active games ────────────────────────────────────────────────

class TestBaccaratTableGuard:
    """The /baccarat command must protect in-progress games."""

    def test_blocks_when_bets_placed(self):
        """If players have placed bets, /baccarat should show 'already a table'."""
        cog = _make_cog()
        table = _make_table()
        table.players[1] = _make_seat(user_id=1)
        cog.active_tables[42] = table

        interaction = _make_interaction(channel_id=42)

        async def _run():
            with patch("bot.cogs.baccarat.queries") as mock_q:
                mock_q.get_or_create_casino_wallet = AsyncMock(return_value=1000)
                await cog.baccarat.callback(cog, interaction)

        run(_run())

        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        msg = call_kwargs.args[0] if call_kwargs.args else ""
        assert "already" in msg.lower()
        assert call_kwargs.kwargs.get("ephemeral") is True
        # Table must NOT be replaced
        assert cog.active_tables[42] is table

    def test_blocks_when_dealing_in_progress(self):
        """If dealt=True and not all cards revealed, /baccarat should block."""
        cog = _make_cog()
        table = _make_table()
        table.dealt = True
        table.player_revealed = 1  # mid-reveal
        table.banker_revealed = 0
        table.players[1] = _make_seat(user_id=1)
        cog.active_tables[42] = table

        interaction = _make_interaction(channel_id=42)

        async def _run():
            with patch("bot.cogs.baccarat.queries") as mock_q:
                mock_q.get_or_create_casino_wallet = AsyncMock(return_value=1000)
                await cog.baccarat.callback(cog, interaction)

        run(_run())

        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        msg = call_kwargs.args[0] if call_kwargs.args else ""
        assert "already" in msg.lower()
        assert call_kwargs.kwargs.get("ephemeral") is True

    # ── Guard allows idle / finished tables to be replaced ──────────────────

    def test_allows_when_table_idle_no_bets(self):
        """An idle table (no bets, not dealt) can be replaced by /baccarat."""
        cog = _make_cog()
        table = _make_table()
        # No players, not dealt → idle
        assert not table.players
        assert not table.dealt
        cog.active_tables[42] = table

        interaction = _make_interaction(channel_id=42)

        async def _run():
            with patch("bot.cogs.baccarat.queries") as mock_q:
                mock_q.get_or_create_casino_wallet = AsyncMock(return_value=1000)
                await cog.baccarat.callback(cog, interaction)

        run(_run())

        # send_message was called to send the new table embed (not an error)
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        # Should NOT be ephemeral (ephemeral=True only for error messages)
        assert not call_kwargs.kwargs.get("ephemeral", False)
        # Old stale table replaced by new one
        assert cog.active_tables[42] is not table

    def test_allows_when_hand_fully_revealed(self):
        """A finished hand (all_revealed=True) can be replaced even with players still seated.

        _finish() does NOT clear players — they persist until "New Hand" is clicked.
        The guard must allow /baccarat once all cards are revealed.
        """
        cog = _make_cog()
        table = _make_table()
        table.dealt = True
        table.player_revealed = len(table.player_hand)  # all player cards shown
        table.banker_revealed = len(table.banker_hand)  # all banker cards shown
        # Players still seated — this is the real post-_finish() state
        table.players[1] = _make_seat(user_id=1)
        assert table.all_revealed  # confirm the state is truly finished
        cog.active_tables[42] = table

        interaction = _make_interaction(channel_id=42)

        async def _run():
            with patch("bot.cogs.baccarat.queries") as mock_q:
                mock_q.get_or_create_casino_wallet = AsyncMock(return_value=1000)
                await cog.baccarat.callback(cog, interaction)

        run(_run())

        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert not call_kwargs.kwargs.get("ephemeral", False)
        assert cog.active_tables[42] is not table

    def test_no_table_creates_fresh(self):
        """With no existing table, /baccarat always creates a fresh one."""
        cog = _make_cog()
        assert not cog.active_tables

        interaction = _make_interaction(channel_id=42)

        async def _run():
            with patch("bot.cogs.baccarat.queries") as mock_q:
                mock_q.get_or_create_casino_wallet = AsyncMock(return_value=1000)
                await cog.baccarat.callback(cog, interaction)

        run(_run())

        assert 42 in cog.active_tables
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert not call_kwargs.kwargs.get("ephemeral", False)
