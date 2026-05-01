"""Discord logging handler — routes WARNING+ structured logs to a dedicated #bot-logs channel."""
from __future__ import annotations

import asyncio
import logging
import os
import traceback
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

_EMBED_DESC_LIMIT = 4000
_TRACEBACK_LIMIT = 1000


class DiscordLogHandler(logging.Handler):
    """Buffers log records and lets the flush loop drain them into Discord."""

    def __init__(self, cog_ref: "BotLogsCog") -> None:
        super().__init__(level=logging.WARNING)
        self._cog = cog_ref
        self._buffer: list[logging.LogRecord] = []
        # asyncio.Lock is only used in the async flush path; emit() uses
        # CPython's atomic list.append() to stay thread-safe without blocking.
        self._lock = asyncio.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        # list.append is atomic in CPython — safe to call from any thread.
        self._buffer.append(record)


class BotLogsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.handler: DiscordLogHandler | None = None
        self.log_channel_id: int | None = None

        _channel_id_str = os.getenv("LOG_CHANNEL_ID")
        if not _channel_id_str:
            log.warning("LOG_CHANNEL_ID environment variable is not set — bot log forwarding disabled")
            return

        self.log_channel_id = int(_channel_id_str)
        self.handler = DiscordLogHandler(self)
        logging.getLogger().addHandler(self.handler)
        self._flush_loop.start()

    def cog_unload(self) -> None:
        self._flush_loop.cancel()
        if self.handler is not None:
            logging.getLogger().removeHandler(self.handler)

    @tasks.loop(seconds=10)
    async def _flush_loop(self) -> None:
        if self.handler is None or not self.handler._buffer:
            return

        # Atomically swap out the buffer so emit() can keep writing during flush.
        records, self.handler._buffer = self.handler._buffer, []

        channel = self.bot.get_channel(self.log_channel_id)
        if channel is None:
            return

        try:
            await _send_log_embed(channel, records, self.handler)
        except Exception:
            # Never log from inside the log handler — that way lies infinite recursion.
            pass

    @_flush_loop.before_loop
    async def _before_flush(self) -> None:
        await self.bot.wait_until_ready()


def _level_label(levelno: int) -> str:
    return logging.getLevelName(levelno)


def _format_record(record: logging.LogRecord) -> str:
    ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%H:%M:%S")
    label = _level_label(record.levelno)
    return f"`{ts}` **[{label}]** `{record.name}`: {record.getMessage()}"


async def _send_log_embed(
    channel: discord.abc.Messageable,
    records: list[logging.LogRecord],
    handler: DiscordLogHandler,
) -> None:
    """Build and send an embed for a batch of log records."""
    has_error = any(r.levelno >= logging.ERROR for r in records)
    color = discord.Color.red() if has_error else discord.Color.orange()

    # Build description lines
    lines = [_format_record(r) for r in records]
    description = "\n".join(lines)
    truncated = False
    if len(description) > _EMBED_DESC_LIMIT:
        description = description[:_EMBED_DESC_LIMIT]
        truncated = True
    if truncated:
        description += "\n... (truncated)"

    embed = discord.Embed(color=color, description=description)
    embed.set_author(name="Bot Logs")

    # Attach traceback for the first record that has exc_info
    for record in records:
        if record.exc_info:
            try:
                tb_lines = traceback.format_exception(*record.exc_info)
                tb_text = "".join(tb_lines)
                if len(tb_text) > _TRACEBACK_LIMIT:
                    tb_text = tb_text[:_TRACEBACK_LIMIT] + "\n... (truncated)"
                embed.add_field(
                    name="Traceback",
                    value=f"```\n{tb_text}\n```",
                    inline=False,
                )
            except Exception:
                pass
            break  # only show one traceback per batch

    await channel.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BotLogsCog(bot))
