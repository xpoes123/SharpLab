"""Roster / injury report commands — /rosters."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

import discord
from discord import app_commands
from discord.ext import commands

from db import queries
import logging

log = logging.getLogger(__name__)
_STATUS_ICON = {
    "Out":          "🔴",
    "Doubtful":     "🟠",
    "Questionable": "🟡",
    "Day-To-Day":   "🟡",
    "Probable":     "🟢",
}

_STATUS_COLOR = {
    "Out":          0xE74C3C,
    "Doubtful":     0xE67E22,
    "Questionable": 0xF1C40F,
    "Day-To-Day":   0xF1C40F,
    "Probable":     0x2ECC71,
}

# Severity order for picking the embed color (worst = highest index wins)
_STATUS_ORDER = ["Probable", "Day-To-Day", "Questionable", "Doubtful", "Out"]


def _fmt_game_time(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(_ET)
    h = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%a %b')} {dt.day}, {h}:{dt.strftime('%M')} {ampm} {dt.strftime('%Z')}"


async def game_autocomplete(
    _interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    games = await queries.get_upcoming_games(current, sport="nba")
    return [
        app_commands.Choice(
            name=f"{g.away_team} @ {g.home_team} — {_fmt_game_time(g.start_time_utc_iso)}"[:100],
            value=g.game_id,
        )
        for g in games
    ]


def _fmt_updated(utc_iso: str) -> str:
    dt = datetime.fromisoformat(utc_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(_ET)
    h = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%b %d')} {h}:{dt.strftime('%M')} {ampm} {dt.strftime('%Z')}"


def _fmt_team_injuries(team: str, alerts: list) -> str:
    if not alerts:
        return f"**{team}**\nNo injuries reported."
    lines = [f"**{team}**"]
    for a in alerts:
        icon = _STATUS_ICON.get(a.status, "⚪")
        detail = f" — {a.detail}" if a.detail else ""
        lines.append(f"{icon} **{a.player_name}** ({a.status}){detail}")
    return "\n".join(lines)


def _worst_color(home_alerts: list, away_alerts: list) -> int:
    all_statuses = [a.status for a in home_alerts + away_alerts]
    worst_idx = max(
        (_STATUS_ORDER.index(s) for s in all_statuses if s in _STATUS_ORDER),
        default=-1,
    )
    if worst_idx == -1:
        return 0x5865F2
    return _STATUS_COLOR.get(_STATUS_ORDER[worst_idx], 0x5865F2)


class RostersCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="rosters", description="Injury report for today's NBA games")
    @app_commands.describe(game="Select a game")
    @app_commands.autocomplete(game=game_autocomplete)
    async def rosters(self, interaction: discord.Interaction, game: str) -> None:
        await interaction.response.defer()

        game_obj = await queries.get_game_by_id(game)
        if game_obj is None:
            await interaction.followup.send("Game not found.")
            return

        home_alerts, away_alerts = await asyncio.gather(
            queries.get_injuries_for_team(game_obj.home_team),
            queries.get_injuries_for_team(game_obj.away_team),
        )

        home_section = _fmt_team_injuries(game_obj.home_team, home_alerts)
        away_section = _fmt_team_injuries(game_obj.away_team, away_alerts)
        description = f"{away_section}\n\n{home_section}"

        color = _worst_color(home_alerts, away_alerts)

        # Footer: most recent updated_at across all alerts
        all_alerts = home_alerts + away_alerts
        footer_text = "ESPN"
        if all_alerts:
            most_recent = max(all_alerts, key=lambda a: a.updated_at_utc_iso)
            footer_text = f"ESPN • last updated {_fmt_updated(most_recent.updated_at_utc_iso)}"

        embed = discord.Embed(
            title=f"Injury Report — {game_obj.away_team} @ {game_obj.home_team}",
            description=description,
            color=color,
        )
        embed.set_footer(text=footer_text)
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RostersCog(bot))
