"""Coin cooldown ping — background task that notifies users when 8h cooldown expires."""
from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands, tasks

from db import queries

log = logging.getLogger(__name__)

COIN_CHANNEL_ID = int(os.getenv("CLV_CHANNEL_ID", "1485475287054418151"))


class CoinsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.coin_ping_check.start()

    def cog_unload(self) -> None:
        self.coin_ping_check.cancel()

    @tasks.loop(minutes=5)
    async def coin_ping_check(self) -> None:
        """Check for users whose 8h coin cooldown has expired and ping them."""
        channel = self.bot.get_channel(COIN_CHANNEL_ID)
        if channel is None:
            return

        ready_users = await queries.get_users_ready_for_coin_ping()
        for discord_user in ready_users:
            await channel.send(f"<@{discord_user}> Your coins are ready to claim! 🪙")
            await queries.clear_coin_ping(discord_user)
            log.info("[coin_ping] notified user %s", discord_user)

    @coin_ping_check.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CoinsCog(bot))
