"""Roster / injury report commands — /rosters."""
from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db import queries
from shared.models import TEAM_ABBR

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

_ALL_TEAMS = sorted(TEAM_ABBR.keys())


async def team_autocomplete(
    _interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    matches = [t for t in _ALL_TEAMS if current.lower() in t.lower()]
    return [app_commands.Choice(name=t, value=t) for t in matches[:25]]


def _fmt_updated(utc_iso: str) -> str:
    dt = datetime.fromisoformat(utc_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%b %d %H:%M UTC")


class RostersCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="rosters", description="Injury report for an NBA team")
    @app_commands.describe(team="Team name")
    @app_commands.autocomplete(team=team_autocomplete)
    async def rosters(self, interaction: discord.Interaction, team: str) -> None:
        await interaction.response.defer()

        alerts = await queries.get_injuries_for_team(team)

        if not alerts:
            await interaction.followup.send(
                f"No injury report entries on file for **{team}**."
            )
            return

        # Color based on worst status present
        worst = alerts[0].status
        color = _STATUS_COLOR.get(worst, 0x5865F2)

        lines = []
        for a in alerts:
            icon = _STATUS_ICON.get(a.status, "⚪")
            detail = f" — {a.detail}" if a.detail else ""
            lines.append(f"{icon} **{a.player_name}** ({a.status}){detail}")

        updated = _fmt_updated(alerts[0].updated_at_utc_iso)

        embed = discord.Embed(
            title=f"Injury Report — {team}",
            description="\n".join(lines),
            color=color,
        )
        embed.set_footer(text=f"ESPN • last updated {updated}")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RostersCog(bot))
