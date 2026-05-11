"""Global error handler — catches all unhandled exceptions, logs to DB, and sends clean user messages."""
from __future__ import annotations

import hashlib
import logging
import os
import traceback
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.error_forward import forward_to_sentinel
from db import queries

log = logging.getLogger(__name__)

SEVERITY_COLORS = {
    "high": discord.Colour.red(),
    "medium": discord.Colour.orange(),
    "low": discord.Colour.greyple(),
}


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
        self.alert_channel_id = int(os.getenv("ERROR_ALERT_CHANNEL_ID", "0")) or None
        self.log_thread_id = int(os.getenv("ERROR_LOG_THREAD_ID", "0")) or None
        self._log_thread: discord.Thread | None = None

    async def _get_or_create_log_thread(self, channel: discord.TextChannel) -> discord.Thread | None:
        """Get existing log thread or create one in the alert channel."""
        if self._log_thread is not None:
            return self._log_thread

        # Try fetching existing thread by stored ID
        if self.log_thread_id:
            try:
                thread = channel.guild.get_thread(self.log_thread_id)
                if thread is None:
                    thread = await channel.guild.fetch_channel(self.log_thread_id)
                self._log_thread = thread
                return thread
            except (discord.NotFound, discord.HTTPException):
                log.warning("Configured ERROR_LOG_THREAD_ID %s not found, creating new thread", self.log_thread_id)

        # Create a new thread
        try:
            thread = await channel.create_thread(
                name="Error Log Mirror",
                type=discord.ChannelType.public_thread,
                reason="Persistent error log mirror for Sentinel",
            )
            self._log_thread = thread
            self.log_thread_id = thread.id
            log.info("Created error log thread: %s (ID: %s)", thread.name, thread.id)
            return thread
        except discord.HTTPException:
            log.exception("Failed to create error log thread")
            return None

    async def _send_alert(
        self,
        result: dict,
        error: Exception,
        command_name: str | None,
        severity: str,
    ) -> None:
        """Send or update an alert embed in the alert channel."""
        if not self.alert_channel_id:
            return

        channel = self.bot.get_channel(self.alert_channel_id)
        if channel is None:
            log.warning("Alert channel %s not found", self.alert_channel_id)
            return

        error_id = result["error_id"]
        error_type = type(error).__qualname__
        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        # Truncate traceback for embed (max ~1024 chars for field value)
        tb_display = tb[-1000:] if len(tb) > 1000 else tb

        action = result["action"]

        if action == "created":
            embed = discord.Embed(
                title=f"{'🔴' if severity == 'high' else '🟠'} New Error — {error_type}",
                colour=SEVERITY_COLORS.get(severity, discord.Colour.greyple()),
                timestamp=datetime.now(tz=timezone.utc),
            )
            embed.add_field(name="Command", value=f"`/{command_name}`" if command_name else "N/A", inline=True)
            embed.add_field(name="Severity", value=severity.upper(), inline=True)
            embed.add_field(name="Error ID", value=f"`#{error_id}`", inline=True)
            embed.add_field(name="Traceback", value=f"```\n{tb_display}\n```", inline=False)
            embed.set_footer(text=f"Error #{error_id} • 1 occurrence")

            msg = await channel.send(embed=embed)
            # Store message ID as ticket_id for future edits
            await queries.update_error_ticket_id(error_id, str(msg.id))
            # Update footer with jump link
            embed.set_footer(text=f"Error #{error_id} • 1 occurrence • {msg.jump_url}")
            await msg.edit(embed=embed)

        elif action == "deduped":
            # ticket_id and occurrence_count are threaded through from _log_error
            ticket_id = result.get("ticket_id")
            occurrence_count = result.get("occurrence_count", 1)

            if ticket_id:
                try:
                    orig_msg = await channel.fetch_message(int(ticket_id))
                    if orig_msg.embeds:
                        embed = orig_msg.embeds[0].copy()
                        embed.set_footer(text=f"Error #{error_id} • {occurrence_count} occurrences • {orig_msg.jump_url}")
                        embed.timestamp = datetime.now(tz=timezone.utc)
                        await orig_msg.edit(embed=embed)
                        return
                except (discord.NotFound, discord.HTTPException, ValueError):
                    pass

            # Fallback: send a short update note
            await channel.send(
                f"⚠️ Error `#{error_id}` (`{error_type}` in `/{command_name}`) occurred again — "
                f"**{occurrence_count}** total occurrences"
            )

        elif action == "reopened":
            reopen_count = result.get("reopen_count", 1)
            embed = discord.Embed(
                title=f"🚨 FIX VERIFICATION FAILED — {error_type}",
                colour=discord.Colour.dark_red(),
                description=(
                    f"Error `#{error_id}` was marked as resolved but has recurred.\n"
                    f"This error has been reopened **{reopen_count}** time(s)."
                ),
                timestamp=datetime.now(tz=timezone.utc),
            )
            embed.add_field(name="Command", value=f"`/{command_name}`" if command_name else "N/A", inline=True)
            embed.add_field(name="Severity", value=severity.upper(), inline=True)
            embed.add_field(name="Error ID", value=f"`#{error_id}`", inline=True)
            embed.add_field(name="Traceback", value=f"```\n{tb_display}\n```", inline=False)
            embed.set_footer(text=f"Error #{error_id} • Reopened {reopen_count}x")

            msg = await channel.send(embed=embed)
            await queries.update_error_ticket_id(error_id, str(msg.id))

    async def _mirror_to_thread(
        self,
        result: dict,
        error: Exception,
        command_name: str | None,
        severity: str,
    ) -> None:
        """Post a compact log entry in the error log thread."""
        if not self.alert_channel_id:
            return

        channel = self.bot.get_channel(self.alert_channel_id)
        if channel is None:
            return

        thread = await self._get_or_create_log_thread(channel)
        if thread is None:
            return

        error_id = result["error_id"]
        error_type = type(error).__qualname__
        action = result["action"]
        now = discord.utils.format_dt(datetime.now(tz=timezone.utc), style="T")

        if action == "created":
            line = f"{now} | **NEW** `#{error_id}` [{severity.upper()}] `{error_type}` in `/{command_name}`"
        elif action == "deduped":
            line = f"{now} | **DUP** `#{error_id}` [{severity.upper()}] `{error_type}` in `/{command_name}`"
        elif action == "reopened":
            reopen_count = result.get("reopen_count", 1)
            line = f"{now} | **REOPENED** `#{error_id}` [{severity.upper()}] `{error_type}` in `/{command_name}` (reopen #{reopen_count})"
        else:
            return

        await thread.send(line)

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
                result = {
                    "action": "reopened",
                    "error_id": existing["id"],
                    "severity": severity,
                    "reopen_count": existing["reopen_count"] + 1,
                }
            else:
                result = {
                    "action": "deduped",
                    "error_id": existing["id"],
                    "severity": severity,
                    "occurrence_count": existing["occurrence_count"] + 1,
                    "ticket_id": existing.get("ticket_id"),
                }
        else:
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
            result = {"action": "created", "error_id": error_id, "severity": severity}

        # Alerting — wrapped so failures never block error handling
        try:
            await self._send_alert(result, error, command_name, severity)
        except Exception:
            log.exception("Failed to send error alert")

        try:
            await self._mirror_to_thread(result, error, command_name, severity)
        except Exception:
            log.exception("Failed to mirror error to thread")

        # Forward NEW and REOPENED errors to Sentinel so it can auto-triage.
        # Skip 'deduped' — local dedup is enough; no need to re-notify Sentinel
        # about an error it has already seen.
        if result["action"] in ("created", "reopened"):
            try:
                await forward_to_sentinel(
                    error_id=result["error_id"],
                    exception_type=type(error).__qualname__,
                    message=str(error)[:1000],
                    traceback_text=tb,
                    context=f"command=/{command_name}" if command_name else None,
                )
            except Exception:
                log.exception("Failed to forward error to Sentinel")

        return result

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
