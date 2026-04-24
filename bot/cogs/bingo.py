"""Bingo cog — web-based multiplayer bingo game.

Creates a game room on the web server, players join via Discord and watch
their cards auto-mark in the browser with pattern tracking.
"""

import os

import discord
import httpx
from discord import app_commands, ui
from discord.ext import commands, tasks

from shared.bingo_logic import MAX_CARDS

WEB_API_BASE = os.environ.get("WEB_API_BASE", "https://djiang.xyz")
WEB_API_SECRET = os.environ.get("WEB_API_SECRET", "dev-secret")


class BingoCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._pending_web_rooms: dict[str, int] = {}

    async def cog_load(self) -> None:
        self._poll_web_results.start()

    async def cog_unload(self) -> None:
        self._poll_web_results.cancel()

    @app_commands.command(
        name="bingo",
        description="Open a Bingo game (plays in your browser)",
    )
    async def bingo(self, interaction: discord.Interaction) -> None:
        uid = str(interaction.user.id)
        channel_id = str(interaction.channel_id)

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{WEB_API_BASE}/api/v1/bingo/rooms",
                json={
                    "host_discord_id": uid,
                    "channel_id": channel_id,
                    "host_display_name": interaction.user.display_name,
                },
                headers={"X-Api-Key": WEB_API_SECRET},
            )
        if resp.status_code != 200:
            await interaction.response.send_message("Failed to create game room.", ephemeral=True)
            return

        data = resp.json()
        room_id = data["room_id"]
        pattern = data.get("pattern", "")
        self._pending_web_rooms[room_id] = interaction.channel_id

        embed = discord.Embed(
            title="\U0001f3b1 Bingo",
            description=(
                f"Pattern: **{pattern}**\n\n"
                "Click **Join** to get your game link."
            ),
            colour=discord.Colour.blue(),
        )
        embed.set_footer(text=f"Room {room_id}")

        view = BingoWebLobbyView(room_id)
        await interaction.response.send_message(embed=embed, view=view)

    @tasks.loop(seconds=10)
    async def _poll_web_results(self) -> None:
        for room_id in list(self._pending_web_rooms):
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(
                        f"{WEB_API_BASE}/api/v1/bingo/rooms/{room_id}/result",
                    )
                if resp.status_code != 200:
                    continue

                result = resp.json()
                channel_id = self._pending_web_rooms.pop(room_id)
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    continue

                winners = result.get("winners", [])
                embed = discord.Embed(
                    title="\U0001f3b1 Bingo \u2014 Results",
                    colour=discord.Colour.green() if winners else discord.Colour.greyple(),
                )
                if winners:
                    embed.description = f"\U0001f3c6 **{', '.join(winners)}** got BINGO!\n"
                else:
                    embed.description = "No winner this round.\n"

                embed.description += f"Pattern: {result.get('pattern', '')} \u2022 {result.get('numbers_called', 0)} numbers\n\n"

                medals = ["\U0001f947", "\U0001f948", "\U0001f949"]
                for i, r in enumerate(result.get("results", [])):
                    badge = medals[i] if i < 3 else f"`{i+1}.`"
                    embed.description += (
                        f"{badge} **{r['display_name']}** \u2014 {r['num_cards']} cards\n"
                    )
                embed.set_footer(text=f"Room {room_id}")
                await channel.send(embed=embed)
            except Exception:
                pass

    @_poll_web_results.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()


class BingoJoinModal(ui.Modal, title="Join Bingo"):
    cards = ui.TextInput(
        label=f"Number of cards (1-{MAX_CARDS})",
        placeholder="e.g. 3",
        min_length=1,
        max_length=1,
    )

    def __init__(self, room_id: str) -> None:
        super().__init__()
        self.room_id = room_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            num = int(self.cards.value)
        except ValueError:
            await interaction.response.send_message("Invalid number.", ephemeral=True)
            return
        if num < 1 or num > MAX_CARDS:
            await interaction.response.send_message(f"Must be 1-{MAX_CARDS}.", ephemeral=True)
            return

        uid = str(interaction.user.id)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{WEB_API_BASE}/api/v1/bingo/rooms/{self.room_id}/tokens",
                    json={"discord_user": uid, "display_name": interaction.user.display_name, "num_cards": num},
                    headers={"X-Api-Key": WEB_API_SECRET},
                )
            if resp.status_code != 200:
                detail = resp.json().get("detail", "Unknown error")
                await interaction.response.send_message(f"Failed to join: {detail}", ephemeral=True)
                return
            url = resp.json()["url"]
            await interaction.response.send_message(
                f"\U0001f517 **[Click here to play]({url})**\n{num} card{'s' if num > 1 else ''} locked in.",
                ephemeral=True,
            )
        except Exception:
            await interaction.response.send_message("Failed to connect.", ephemeral=True)


class BingoWebLobbyView(ui.View):
    def __init__(self, room_id: str) -> None:
        super().__init__(timeout=1800)
        self.room_id = room_id

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f4dd")
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.send_modal(BingoJoinModal(self.room_id))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BingoCog(bot))
