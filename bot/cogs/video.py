"""/video — link to the phone video shrinker at /vid."""
import discord
from discord import app_commands
from discord.ext import commands


@app_commands.command(name="video", description="Shrink a video under 5 MB and post it here")
async def video(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "📹 **Video shrinker** — upload from your phone, get it under 5 MB, then download or send it to a channel:\n"
        "https://sharplab.djiang.xyz/vid",
        ephemeral=True,
    )


async def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(video)
