"""Stream command — ephemeral topstreamer.info link for a game."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.odds import game_autocomplete
from db import queries


class StreamCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="stream", description="Get a stream link for an NBA game")
    @app_commands.describe(game="Select a game")
    @app_commands.autocomplete(game=game_autocomplete)
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
