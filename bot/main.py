"""SharpLab Discord bot — entrypoint."""
import asyncio
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

COGS = [
    "bot.cogs.utils",
    "bot.cogs.odds",
]

intents = discord.Intents.default()


class SharpBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        for cog in COGS:
            await self.load_extension(cog)
        await self.tree.sync()
        print("Slash commands synced.")

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} (id={self.user.id})")


async def main() -> None:
    async with SharpBot() as bot:
        await bot.start(os.environ["DISCORD_BOT_TOKEN"])


if __name__ == "__main__":
    asyncio.run(main())
