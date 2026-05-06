"""Global error handler — catches all unhandled exceptions, logs to DB, and sends clean user messages."""
from __future__ import annotations

import hashlib
import logging
import traceback
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db import queries

log = logging.getLogger(__name__)


def _compute_signature(error_type: str, command: str | None, tb: str) -> str:
    """Hash of error type + command + last frame of traceback for dedup."""
    lines = tb.strip().splitlines()
    last_frame = lines[-1] if lines else ""
    raw = f"{error_type}:{command or ''}:{last_frame}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _classify_severity(error: Exception, command: str | None) -> str:
    """Triage severity: high for crashes/unhandled, medium for command failures, low for user input."""
    if isinstance(
        error,
        (
            app_commands.CheckFailure,
            app_commands.MissingPermissions,
            commands.CheckFailure,
            commands.MissingPermissions,
            commands.BadArgument,
            commands.MissingRequiredArgument,
            app_commands.TransformerError,
        ),
    ):
        return "low"
    if isinstance(error, (discord.NotFound, discord.Forbidden)):
        return "medium"
    return "high"


class ErrorHandlerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.on_error = self.on_app_command_error

    async def _log_error(
        self,
        error: Exception,
        command_name: str | None,
        user_id: int | None,
        guild_id: int | None,
        channel_id: int | None,
    ) -> dict | None:
        """Core error logging + dedup logic."""
        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        error_type = type(error).__qualname__
        severity = _classify_severity(error, command_name)

        # Skip logging low-severity (user input errors)
        if severity == "low":
            return None

        signature = _compute_signature(error_type, command_name, tb)
        now = datetime.now(tz=timezone.utc).isoformat()

        # Dedup: check for recent error with same signature
        existing = await queries.find_recent_error_by_signature(signature)
        if existing:
            await queries.increment_error_occurrence(existing["id"], now)
            if existing["resolved"]:
                await queries.reopen_error(existing["id"])
                return {
                    "action": "reopened",
                    "error_id": existing["id"],
                    "severity": severity,
                    "reopen_count": existing["reopen_count"] + 1,
                }
            return {"action": "deduped", "error_id": existing["id"], "severity": severity}

        error_id = await queries.insert_error_log(
            timestamp=now,
            error_type=error_type,
            command=command_name,
            user_id=str(user_id) if user_id else None,
            guild_id=str(guild_id) if guild_id else None,
            channel_id=str(channel_id) if channel_id else None,
            stack_trace=tb,
            severity=severity,
            error_signature=signature,
        )
        return {"action": "created", "error_id": error_id, "severity": severity}

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError,
    ) -> None:
        # Unwrap CommandInvokeError to get the original
        original = error.original if isinstance(error, app_commands.CommandInvokeError) else error

        command_name = interaction.command.qualified_name if interaction.command else "unknown"
        user_id = interaction.user.id
        guild_id = interaction.guild_id
        channel_id = interaction.channel_id

        # Send user-friendly message
        try:
            msg = "Something went wrong. The error has been logged."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass  # best-effort

        # Log to DB
        try:
            await self._log_error(original, command_name, user_id, guild_id, channel_id)
        except Exception:
            log.exception("Failed to log error to DB")
            return

        # Also emit to standard logging so bot_logs.py Discord handler picks it up
        log.error(f"Command /{command_name} failed: {original}", exc_info=original)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """Handle prefix command errors."""
        original = error.original if isinstance(error, commands.CommandInvokeError) else error
        command_name = ctx.command.qualified_name if ctx.command else "unknown"

        try:
            await self._log_error(
                original,
                command_name,
                ctx.author.id,
                ctx.guild.id if ctx.guild else None,
                ctx.channel.id,
            )
        except Exception:
            log.exception("Failed to log error to DB")

        log.error(f"Command !{command_name} failed: {original}", exc_info=original)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ErrorHandlerCog(bot))
