"""Figgie cog — web-based multiplayer trading card game.

Creates a game room on the web server, players join via Discord and trade
in their browser with a live order book + WebSocket multiplayer.
"""

import os

import discord
import httpx
from discord import app_commands, ui
from discord.ext import commands, tasks

from db import queries

WEB_API_BASE = os.environ.get("WEB_API_BASE", "https://djiang.xyz")
WEB_API_SECRET = os.environ.get("WEB_API_SECRET", "dev-secret")


class FiggieCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._pending_web_rooms: dict[str, int] = {}

    async def cog_load(self) -> None:
        self._poll_web_results.start()

    async def cog_unload(self) -> None:
        self._poll_web_results.cancel()

    @app_commands.command(
        name="figgie",
        description="Open a Figgie trading game (plays in your browser)",
    )
    async def figgie(self, interaction: discord.Interaction) -> None:
        uid = str(interaction.user.id)
        channel_id = str(interaction.channel_id)
        await queries.get_or_create_casino_wallet(uid)

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{WEB_API_BASE}/api/v1/figgie/rooms",
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
            title="\U0001f0cf Figgie",
            description=(
                "Jane Street's trading card game — play in your browser!\n"
                "40 cards, 4 suits, hidden goal suit worth 10 pts/card.\n"
                "Trade via a live order book. Highest score wins the pot.\n\n"
                "Click **Join** below to enter your bet and get your game link."
            ),
            colour=discord.Colour.dark_teal(),
        )
        embed.set_footer(text=f"Room {room_id} \u2022 3-5 players \u2022 6 rounds")

        view = FiggieWebLobbyView(room_id)
        await interaction.response.send_message(embed=embed, view=view)

    @tasks.loop(seconds=10)
    async def _poll_web_results(self) -> None:
        for room_id in list(self._pending_web_rooms):
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(
                        f"{WEB_API_BASE}/api/v1/figgie/rooms/{room_id}/result",
                    )
                if resp.status_code != 200:
                    continue

                result = resp.json()
                channel_id = self._pending_web_rooms.pop(room_id)
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    continue

                embed = discord.Embed(
                    title="\U0001f0cf Figgie \u2014 Results",
                    colour=discord.Colour.green(),
                )
                goal = result.get("goal_suit", "")
                goal_name = result.get("goal_suit_name", "")
                lines = [f"**Goal suit: {goal} {goal_name}** (10 pts/card)\n"]
                medals = ["\U0001f947", "\U0001f948", "\U0001f949"]
                for i, r in enumerate(result.get("results", [])):
                    badge = medals[i] if i < 3 else f"`{i+1}.`"
                    net = r["net"]
                    sign = "+" if net > 0 else ""
                    lines.append(
                        f"{badge} **{r['display_name']}** \u2014 "
                        f"Score {r['score']} ({r['chips']} chips + {r['goal_cards']}\u00d710) \u2014 "
                        f"{r['wager']}c \u2192 {r['payout']}c ({sign}{net}c)"
                    )
                embed.description = "\n".join(lines)
                embed.set_footer(
                    text=f"Room {room_id} \u2022 {result.get('total_trades', 0)} trades"
                )
                await channel.send(embed=embed)
            except Exception:
                pass

    @_poll_web_results.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()


class FiggieJoinModal(ui.Modal, title="Join Figgie"):
    amount = ui.TextInput(label="Bet amount (coins)", placeholder="e.g. 100", min_length=1, max_length=10)

    def __init__(self, room_id: str) -> None:
        super().__init__()
        self.room_id = room_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Invalid amount.", ephemeral=True)
            return
        if amt <= 0:
            await interaction.response.send_message("Bet must be positive.", ephemeral=True)
            return

        uid = str(interaction.user.id)
        bal = await queries.get_casino_balance(uid)
        if bal is None or bal < amt:
            await interaction.response.send_message(f"Insufficient balance (you have {bal or 0}c).", ephemeral=True)
            return

        await queries.update_casino_balance(uid, -amt)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{WEB_API_BASE}/api/v1/figgie/rooms/{self.room_id}/tokens",
                    json={"discord_user": uid, "display_name": interaction.user.display_name, "wager": amt},
                    headers={"X-Api-Key": WEB_API_SECRET},
                )
            if resp.status_code != 200:
                await queries.update_casino_balance(uid, amt)
                detail = resp.json().get("detail", "Unknown error")
                await interaction.response.send_message(f"Failed to join: {detail}", ephemeral=True)
                return
            url = resp.json()["url"]
            await interaction.response.send_message(
                f"\U0001f517 **[Click here to play]({url})**\nYour {amt}c bet is locked in.",
                ephemeral=True,
            )
        except Exception:
            await queries.update_casino_balance(uid, amt)
            await interaction.response.send_message("Failed to connect to game server. Bet refunded.", ephemeral=True)


class FiggieWebLobbyView(ui.View):
    def __init__(self, room_id: str) -> None:
        super().__init__(timeout=1800)
        self.room_id = room_id

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f4dd")
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.send_modal(FiggieJoinModal(self.room_id))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FiggieCog(bot))
