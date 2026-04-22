"""SharpLab Discord bot — entrypoint."""
import asyncio
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from db import schema

load_dotenv()

COGS = [
    "bot.cogs.utils",
    "bot.cogs.odds",
    "bot.cogs.bets",
    "bot.cogs.markets",
    "bot.cogs.clv",
    "bot.cogs.injuries",
    "bot.cogs.history",
    "bot.cogs.rosters",
    "bot.cogs.prefix",
    "bot.cogs.casino",
    "bot.cogs.trading",
    "bot.cogs.baccarat",
    "bot.cogs.craps",
    "bot.cogs.paigow",
]

GUILD_ID = int(os.environ["DISCORD_GUILD_ID"])

intents = discord.Intents.default()
intents.message_content = True  # required for prefix commands (privileged intent)


class SharpBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        await schema.init_db()
        for cog in COGS:
            await self.load_extension(cog)
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        print(f"Slash commands synced to guild {GUILD_ID}.")

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} (id={self.user.id})")


async def main() -> None:
    async with SharpBot() as bot:
        await bot.start(os.environ["DISCORD_BOT_TOKEN"])


if __name__ == "__main__":
    asyncio.run(main())
