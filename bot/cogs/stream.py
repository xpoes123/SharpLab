"""Stream command — ephemeral topstreamer.info link for a game."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.odds import _fmt_game_time
from db import queries


async def _stream_game_autocomplete(_interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    games = await queries.get_streamable_games(current, sport="nba")
    return [
        app_commands.Choice(
            name=f"{g.away_team} @ {g.home_team} — {_fmt_game_time(g.start_time_utc_iso)}"[:100],
            value=g.game_id,
        )
        for g in games
    ]


class StreamCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="stream", description="Get a stream link for an NBA game")
    @app_commands.describe(game="Select a game")
    @app_commands.autocomplete(game=_stream_game_autocomplete)
    async def stream(self, interaction: discord.Interaction, game: str) -> None:
        target = await queries.get_game_by_id(game)
        if target is None:
            await interaction.response.send_message("Game not found.", ephemeral=True)
            return
        team_slug = target.home_team.split()[-1].lower()
        url = f"https://topstreamer.info/nba/{team_slug}"
        await interaction.response.send_message(
            f"**{target.away_team} @ {target.home_team}**\n{url}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StreamCog(bot))
