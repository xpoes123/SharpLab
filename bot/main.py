"""SharpLab Discord bot — entrypoint."""
import asyncio
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from db import schema, queries
from shared.log_config import setup_logging

load_dotenv()
setup_logging()

log = logging.getLogger(__name__)

# Game commands that must not be started inside threads (they create their own threads)
_THREAD_BLOCKED_COMMANDS: set[str] = {
    "blackjack", "baccarat", "paigow", "uth", "videopoker", "hilo",
    "roulette", "craps", "crapless", "crash", "plinko", "slots", "bingo",
    "horserace", "stockmarket", "stockguess", "math24", "countdown",
    "mastermind", "geography", "wordle", "nba-trivia", "nfl-trivia",
    "sudoku", "tictactoe", "rps", "figgie", "mathsprint", "pokemon",
    "quizbowl", "valorant", "solitaire-chess", "nba", "minesweeper",
    "sequence", "prisoner", "indian-poker", "battleship",
    "nbasim", "nflsim", "mlbsim", "tennissim", "soccersim",
    "penalties", "liarsdice",
    "soccersim-tournament",
}


class SharpTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            isinstance(interaction.channel, discord.Thread)
            and interaction.command is not None
        ):
            cmd = interaction.command
            cmd_name = getattr(cmd, "name", None)
            # For group subcommands (e.g. /crapless play), cmd.name is "play"
            # but the blocked set contains "crapless", so also check the parent.
            parent_name = getattr(getattr(cmd, "parent", None), "name", None)
            if cmd_name in _THREAD_BLOCKED_COMMANDS or parent_name in _THREAD_BLOCKED_COMMANDS:
                await interaction.response.send_message(
                    "Games can't be started in threads! Use a regular channel.",
                    ephemeral=True,
                )
                return False
        return True

COGS = [
    "bot.cogs.utils",
    "bot.cogs.odds",
    "bot.cogs.bets",
    "bot.cogs.markets",
    "bot.cogs.clv",
    "bot.cogs.injuries",
    "bot.cogs.bot_logs",
    "bot.cogs.error_handler",
    "bot.cogs.history",
    "bot.cogs.rosters",
    "bot.cogs.prefix",
    "bot.cogs.casino",
    "bot.cogs.trading",
    "bot.cogs.baccarat",
    "bot.cogs.craps",
    "bot.cogs.crapless",
    "bot.cogs.paigow",
    "bot.cogs.uth",
    "bot.cogs.crash",
    "bot.cogs.roulette",
    "bot.cogs.videopoker",
    "bot.cogs.plinko",
    "bot.cogs.hilo",
    "bot.cogs.bingo",
    "bot.cogs.slots",
    "bot.cogs.horserace",
    "bot.cogs.liarsdice",
    "bot.cogs.stockmarket",
    "bot.cogs.stockguess",
    "bot.cogs.stock",
    "bot.cogs.math24",
    "bot.cogs.countdown",
    "bot.cogs.mastermind",
    "bot.cogs.nbasim",
    "bot.cogs.nflsim",
    "bot.cogs.mlbsim",
    "bot.cogs.tennissim",
    "bot.cogs.soccersim",
    "bot.cogs.penalties",
    "bot.cogs.geography",
    "bot.cogs.wordle",
    "bot.cogs.roster",
    "bot.cogs.sudoku",
    "bot.cogs.tictactoe",
    "bot.cogs.rps",
    "bot.cogs.progression",
    "bot.cogs.challenges",
    "bot.cogs.duels",
    "bot.cogs.tournaments",
    "bot.cogs.figgie",
    "bot.cogs.mathsprint",
    "bot.cogs.pokemon",
    "bot.cogs.ratings",
    "bot.cogs.tradingfloor",
    "bot.cogs.quizbowl",
    "bot.cogs.valorant",
    "bot.cogs.solitairechess",
    "bot.cogs.nbaguess",
    "bot.cogs.minesweeper",
    "bot.cogs.sequence",
    "bot.cogs.prisoner",
    "bot.cogs.indianpoker",
    "bot.cogs.battleship",
    "bot.cogs.blotto",
    "bot.cogs.stream",
    "bot.cogs.game_menu",
]

GUILD_IDS = [int(g.strip()) for g in os.environ["DISCORD_GUILD_ID"].split(",") if g.strip()]

intents = discord.Intents.default()
intents.message_content = True  # required for prefix commands (privileged intent)


class SharpBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents, tree_cls=SharpTree)

    async def setup_hook(self) -> None:
        await schema.init_db()
        # Clean up games left over from previous bot session
        duels = await queries.cleanup_stale_duels()
        tournaments = await queries.cleanup_stale_tournaments()
        sessions = await queries.cleanup_stale_game_sessions()
        total = duels + tournaments + sessions
        if total:
            log.info(f"Startup cleanup: {duels} duels, {tournaments} tournaments, {sessions} web sessions expired+refunded.")
        for cog in COGS:
            try:
                await self.load_extension(cog)
            except Exception as e:
                log.error(f"Failed to load cog {cog}", exc_info=e)
        for gid in GUILD_IDS:
            guild = discord.Object(id=gid)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        log.info(f"Slash commands synced to {len(GUILD_IDS)} guild(s).")

    async def on_ready(self) -> None:
        log.info(f"Logged in as {self.user} (id={self.user.id})")


async def main() -> None:
    async with SharpBot() as bot:
        await bot.start(os.environ["DISCORD_BOT_TOKEN"])


if __name__ == "__main__":
    asyncio.run(main())
