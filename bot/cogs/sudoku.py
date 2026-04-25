"""Sudoku Sprint cog — web-based multiplayer game.

Creates a game room on the web server, players join via Discord and play
in their browser with an interactive grid + WebSocket multiplayer.
"""

import os

import discord
import httpx
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs._elo_helpers import update_elo_multiplayer

WEB_API_BASE = os.environ.get("WEB_API_BASE", "https://sharplab.djiang.xyz")
WEB_API_SECRET = os.environ.get("WEB_API_SECRET", "dev-secret")


class SudokuCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._pending_web_rooms: dict[str, int] = {}  # room_id -> channel_id

    async def cog_load(self) -> None:
        self._poll_web_results.start()

    async def cog_unload(self) -> None:
        self._poll_web_results.cancel()

    @app_commands.command(
        name="sudoku",
        description="Open a Sudoku Sprint game (plays in your browser)",
    )
    async def sudoku(self, interaction: discord.Interaction) -> None:
        uid = str(interaction.user.id)
        channel_id = str(interaction.channel_id)

        # Create room via web API
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{WEB_API_BASE}/api/v1/sudoku/rooms",
                json={
                    "host_discord_id": uid,
                    "channel_id": channel_id,
                    "host_display_name": interaction.user.display_name,
                },
                headers={"X-Api-Key": WEB_API_SECRET},
            )
        if resp.status_code != 200:
            await interaction.response.send_message(
                "Failed to create web game room.", ephemeral=True,
            )
            return

        room_id = resp.json()["room_id"]
        self._pending_web_rooms[room_id] = interaction.channel_id

        embed = discord.Embed(
            title="\U0001f9e9 Sudoku Sprint",
            description=(
                "Play in your browser with an interactive grid!\n\n"
                "Click **Join** below to get your game link."
            ),
            colour=discord.Colour.gold(),
        )
        embed.set_footer(text=f"Room {room_id} \u2022 First to 3 wins")

        view = WebSudokuLobbyView(room_id, self.bot)
        await interaction.response.send_message(embed=embed, view=view)

    # ── Result polling ─────────────────────────────────────────────────────

    @tasks.loop(seconds=10)
    async def _poll_web_results(self) -> None:
        for room_id in list(self._pending_web_rooms):
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(
                        f"{WEB_API_BASE}/api/v1/sudoku/rooms/{room_id}/result",
                    )
                if resp.status_code != 200:
                    continue

                result = resp.json()
                channel_id = self._pending_web_rooms.pop(room_id)
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    continue

                embed = discord.Embed(
                    title="\U0001f9e9 Sudoku Sprint \u2014 Results",
                    colour=discord.Colour.green(),
                )
                lines = []
                medals = ["\U0001f947", "\U0001f948", "\U0001f949"]
                for i, r in enumerate(result.get("results", [])):
                    badge = medals[i] if i < 3 else f"`{i+1}.`"
                    lines.append(
                        f"{badge} **{r['display_name']}** \u2014 "
                        f"{r['rounds_won']}W"
                    )
                embed.description = "\n".join(lines) if lines else "No results."
                embed.set_footer(
                    text=f"Room {room_id} \u2022 {result.get('total_rounds', 0)} rounds played"
                )
                await channel.send(embed=embed)

                # ELO update — results already sorted by rank
                finish = [int(r["discord_user"]) for r in result.get("results", []) if r.get("discord_user")]
                if len(finish) >= 2:
                    try:
                        await update_elo_multiplayer(finish, "sudoku", "sudoku")
                    except Exception:
                        pass
            except Exception:
                pass

    @_poll_web_results.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()


# ── Web Sudoku Lobby View ────────────────────────────────────────────────────


class WebSudokuLobbyView(ui.View):
    def __init__(self, room_id: str, bot: commands.Bot) -> None:
        super().__init__(timeout=1800)
        self.room_id = room_id
        self.bot = bot

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f3ae")
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        uid = str(interaction.user.id)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{WEB_API_BASE}/api/v1/sudoku/rooms/{self.room_id}/tokens",
                    json={
                        "discord_user": uid,
                        "display_name": interaction.user.display_name,
                        "wager": 0,
                    },
                    headers={"X-Api-Key": WEB_API_SECRET},
                )
            if resp.status_code != 200:
                detail = resp.json().get("detail", "Unknown error")
                await interaction.response.send_message(
                    f"Failed to join: {detail}", ephemeral=True,
                )
                return
            url = resp.json()["url"]
            await interaction.response.send_message(
                f"\U0001f517 **[Click here to play]({url})**",
                ephemeral=True,
            )
        except Exception:
            await interaction.response.send_message(
                "Failed to connect to game server.", ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SudokuCog(bot))
