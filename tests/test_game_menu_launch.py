"""Regression test for /game dispatch: launchers may be plain coroutine methods
OR @app_commands.command-decorated (a non-callable Command object). _launch
must handle both — calling a Command directly raised
'Command' object is not callable."""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock

from discord import app_commands
from discord.ext import commands

sys.path.insert(0, ".")

from bot.cogs import game_menu  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class _PlainCog(commands.Cog):
    def __init__(self):
        self.called_with = None

    async def launch(self, interaction):  # plain coroutine method
        self.called_with = interaction


class _CommandCog(commands.Cog):
    def __init__(self):
        self.called_with = None

    @app_commands.command(name="launch")
    async def launch(self, interaction):  # attribute is an app_commands.Command
        self.called_with = interaction


def _fake_bot(cog):
    bot = MagicMock()
    bot.get_cog.return_value = cog
    return bot


def _patch_dispatch(monkeypatch, cog_name, method_name):
    monkeypatch.setattr(game_menu, "GAME_DISPATCH", {"x": (cog_name, method_name)})


def test_launch_plain_method(monkeypatch):
    cog = _PlainCog()
    _patch_dispatch(monkeypatch, "_PlainCog", "launch")
    interaction = MagicMock()
    _run(game_menu._launch(_fake_bot(cog), interaction, "x"))
    assert cog.called_with is interaction


def test_launch_app_command(monkeypatch):
    cog = _CommandCog()
    # Sanity: the decorated attribute really is a non-callable Command.
    assert isinstance(cog.launch, app_commands.Command)
    _patch_dispatch(monkeypatch, "_CommandCog", "launch")
    interaction = MagicMock()
    _run(game_menu._launch(_fake_bot(cog), interaction, "x"))
    assert cog.called_with is interaction


def test_launch_strips_leaked_autocomplete_label(monkeypatch):
    """Typing a suggestion instead of clicking it sends the full label
    ('x — X — does a thing'), not the value ('x'). _launch must still dispatch."""
    cog = _PlainCog()
    _patch_dispatch(monkeypatch, "_PlainCog", "launch")
    interaction = MagicMock()
    _run(game_menu._launch(_fake_bot(cog), interaction, "x — X — does a thing"))
    assert cog.called_with is interaction


def test_launch_is_case_insensitive(monkeypatch):
    cog = _PlainCog()
    _patch_dispatch(monkeypatch, "_PlainCog", "launch")
    interaction = MagicMock()
    _run(game_menu._launch(_fake_bot(cog), interaction, "X"))
    assert cog.called_with is interaction
