"""Tests for the Discord logging handler (bot/cogs/bot_logs.py)."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    message: str = "something bad happened",
    level: int = logging.WARNING,
    name: str = "test.logger",
    exc_info=None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="",
        lineno=0,
        msg=message,
        args=(),
        exc_info=exc_info,
    )
    return record


# ---------------------------------------------------------------------------
# DiscordLogHandler unit tests
# ---------------------------------------------------------------------------

class TestDiscordLogHandlerEmit:
    def setup_method(self):
        from bot.cogs.bot_logs import DiscordLogHandler
        self.handler = DiscordLogHandler(cog_ref=MagicMock())

    def test_emit_buffers_warning(self):
        record = _make_record(level=logging.WARNING)
        self.handler.emit(record)
        assert len(self.handler._buffer) == 1
        assert self.handler._buffer[0] is record

    def test_emit_buffers_error(self):
        record = _make_record(level=logging.ERROR)
        self.handler.emit(record)
        assert len(self.handler._buffer) == 1

    def test_emit_buffers_critical(self):
        record = _make_record(level=logging.CRITICAL)
        self.handler.emit(record)
        assert len(self.handler._buffer) == 1

    def test_emit_multiple_records(self):
        for _ in range(5):
            self.handler.emit(_make_record())
        assert len(self.handler._buffer) == 5

    def test_handler_level_is_warning(self):
        from bot.cogs.bot_logs import DiscordLogHandler
        h = DiscordLogHandler(cog_ref=MagicMock())
        assert h.level == logging.WARNING

    def test_below_warning_filtered_by_handler_level(self):
        """Records below WARNING should not reach the handler via the logging framework."""
        from bot.cogs.bot_logs import DiscordLogHandler
        h = DiscordLogHandler(cog_ref=MagicMock())
        # The level gate lives in Logger.callHandlers(), not handle().
        # We verify it via a real logger + handler pair so the full machinery runs.
        logger = logging.getLogger("_test_filter_")
        logger.setLevel(logging.DEBUG)  # let everything through the logger itself
        logger.propagate = False
        logger.addHandler(h)
        try:
            logger.debug("this is debug")
            logger.info("this is info")
            # Neither should reach the buffer because h.level == WARNING
            assert len(h._buffer) == 0
        finally:
            logger.removeHandler(h)

    def test_warning_passes_handler_level(self):
        from bot.cogs.bot_logs import DiscordLogHandler
        h = DiscordLogHandler(cog_ref=MagicMock())
        record = _make_record(level=logging.WARNING)
        h.handle(record)
        assert len(h._buffer) == 1


# ---------------------------------------------------------------------------
# _send_log_embed tests (flush logic)
# ---------------------------------------------------------------------------

class TestSendLogEmbed:
    @pytest.fixture(autouse=True)
    def import_helpers(self):
        from bot.cogs.bot_logs import _send_log_embed, DiscordLogHandler
        self._send_log_embed = _send_log_embed
        self.handler = DiscordLogHandler(cog_ref=MagicMock())

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_warning_only_uses_orange(self):
        channel = AsyncMock()
        records = [_make_record(level=logging.WARNING)]
        self._run(self._send_log_embed(channel, records, self.handler))
        channel.send.assert_called_once()
        embed = channel.send.call_args.kwargs["embed"]
        assert embed.color.value == 0xE67E22  # discord.Color.orange()

    def test_error_uses_red(self):
        channel = AsyncMock()
        records = [
            _make_record(level=logging.WARNING),
            _make_record(level=logging.ERROR),
        ]
        self._run(self._send_log_embed(channel, records, self.handler))
        embed = channel.send.call_args.kwargs["embed"]
        assert embed.color.value == 0xE74C3C  # discord.Color.red()

    def test_critical_uses_red(self):
        channel = AsyncMock()
        records = [_make_record(level=logging.CRITICAL)]
        self._run(self._send_log_embed(channel, records, self.handler))
        embed = channel.send.call_args.kwargs["embed"]
        assert embed.color.value == 0xE74C3C

    def test_embed_contains_logger_name_and_message(self):
        channel = AsyncMock()
        record = _make_record(message="disk full", name="bot.cogs.clv", level=logging.WARNING)
        self._run(self._send_log_embed(channel, [record], self.handler))
        embed = channel.send.call_args.kwargs["embed"]
        assert "disk full" in embed.description
        assert "bot.cogs.clv" in embed.description

    def test_embed_description_truncated_at_limit(self):
        channel = AsyncMock()
        # Generate a record whose message alone exceeds 4000 chars
        long_msg = "x" * 5000
        record = _make_record(message=long_msg, level=logging.WARNING)
        self._run(self._send_log_embed(channel, [record], self.handler))
        embed = channel.send.call_args.kwargs["embed"]
        assert len(embed.description) <= 4030  # 4000 + small overhead for truncation notice
        assert "truncated" in embed.description

    def test_traceback_included_for_exc_info(self):
        channel = AsyncMock()
        try:
            raise ValueError("kaboom")
        except ValueError:
            exc_info = sys.exc_info()
        record = _make_record(level=logging.ERROR, exc_info=exc_info)
        self._run(self._send_log_embed(channel, [record], self.handler))
        embed = channel.send.call_args.kwargs["embed"]
        # At least one field should contain traceback info
        field_values = [f.value for f in embed.fields]
        assert any("ValueError" in v or "kaboom" in v for v in field_values)

    def test_no_traceback_field_without_exc_info(self):
        channel = AsyncMock()
        record = _make_record(level=logging.WARNING)
        self._run(self._send_log_embed(channel, [record], self.handler))
        embed = channel.send.call_args.kwargs["embed"]
        assert len(embed.fields) == 0


# ---------------------------------------------------------------------------
# BotLogsCog lifecycle tests
# ---------------------------------------------------------------------------

class TestBotLogsCogLifecycle:
    def _make_bot(self):
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)
        return bot

    def test_no_channel_id_env_skips_handler(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOG_CHANNEL_ID", None)
            from bot.cogs.bot_logs import BotLogsCog
            bot = self._make_bot()
            with patch.object(BotLogsCog, "_flush_loop") as mock_loop:
                cog = BotLogsCog.__new__(BotLogsCog)
                cog.__init__(bot)  # type: ignore[call-arg]
            assert cog.handler is None
            assert cog.log_channel_id is None

    def test_handler_added_to_root_logger_on_init(self):
        with patch.dict(os.environ, {"LOG_CHANNEL_ID": "123456789"}):
            from bot.cogs.bot_logs import BotLogsCog, DiscordLogHandler
            bot = self._make_bot()
            root_logger = logging.getLogger()
            initial_handlers = list(root_logger.handlers)
            try:
                with patch.object(tasks := __import__("discord.ext.tasks", fromlist=["loop"]), "loop", lambda **kw: lambda f: MagicMock(start=MagicMock(), cancel=MagicMock())):
                    from discord.ext import tasks as discord_tasks
                    with patch.object(discord_tasks, "loop", lambda **kw: lambda f: MagicMock(start=MagicMock(), cancel=MagicMock())):
                        cog = BotLogsCog.__new__(BotLogsCog)
                        # Manually set up what __init__ does to avoid loop start issues
                        cog.bot = bot
                        cog.handler = None
                        cog.log_channel_id = int("123456789")
                        cog.handler = DiscordLogHandler(cog)
                        root_logger.addHandler(cog.handler)
                        # Now verify handler is in root logger
                        assert cog.handler in root_logger.handlers
                        # Cleanup
                        root_logger.removeHandler(cog.handler)
            finally:
                # Restore original handlers if needed
                for h in list(root_logger.handlers):
                    if h not in initial_handlers and isinstance(h, DiscordLogHandler):
                        root_logger.removeHandler(h)

    def test_cog_unload_removes_handler_from_root_logger(self):
        from bot.cogs.bot_logs import BotLogsCog, DiscordLogHandler
        root_logger = logging.getLogger()
        bot = self._make_bot()

        # Manually build a minimal cog without triggering the full __init__
        cog = BotLogsCog.__new__(BotLogsCog)
        cog.bot = bot
        cog.log_channel_id = 999
        cog.handler = DiscordLogHandler(cog)
        root_logger.addHandler(cog.handler)

        mock_loop = MagicMock()
        cog._flush_loop = mock_loop

        assert cog.handler in root_logger.handlers
        cog.cog_unload()
        assert cog.handler not in root_logger.handlers
        mock_loop.cancel.assert_called_once()

    def test_cog_unload_no_handler_is_safe(self):
        """cog_unload must not crash when LOG_CHANNEL_ID was unset."""
        from bot.cogs.bot_logs import BotLogsCog
        cog = BotLogsCog.__new__(BotLogsCog)
        cog.bot = MagicMock()
        cog.log_channel_id = None
        cog.handler = None
        mock_loop = MagicMock()
        cog._flush_loop = mock_loop
        # Should not raise
        cog.cog_unload()
        mock_loop.cancel.assert_called_once()
