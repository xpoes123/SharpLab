"""Bingo cog — web-based multiplayer bingo game.

Creates a game room on the web server, players join via Discord and watch
their cards auto-mark in the browser with pattern tracking.
"""

import os

import discord
import httpx
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs._elo_helpers import fmt_elo_change, update_elo_multiplayer
from shared.bingo_logic import MAX_CARDS
import logging

log = logging.getLogger(__name__)
WEB_API_BASE = os.environ.get("WEB_API_BASE", "https://sharplab.djiang.xyz")
WEB_API_SECRET = os.environ.get("WEB_API_SECRET", "dev-secret")


class BingoCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._pending_web_rooms: dict[str, int] = {}
        self._room_players: dict[str, dict[str, str]] = {}

    async def cog_load(self) -> None:
        self._poll_web_results.start()

    async def cog_unload(self) -> None:
        self._poll_web_results.cancel()

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
        self._room_players[room_id] = {}

        embed = discord.Embed(
            title="\U0001f3b1 Bingo",
            description=(
                f"Pattern: **{pattern}**\n\n"
                "Click **Join** to get your game link."
            ),
            colour=discord.Colour.blue(),
        )
        embed.set_footer(text=f"Room {room_id}")

        view = BingoWebLobbyView(room_id, self._room_players[room_id])
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
                player_map = self._room_players.pop(room_id, {})
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    continue

                winners = result.get("winners", [])
                raw_results = result.get("results", [])

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
                for i, r in enumerate(raw_results):
                    badge = medals[i] if i < 3 else f"`{i+1}.`"
                    embed.description += (
                        f"{badge} **{r['display_name']}** \u2014 {r['num_cards']} cards\n"
                    )
                embed.set_footer(text=f"Room {room_id}")

                # ELO update
                finish_order: list[int] = []
                for r in raw_results:
                    uid_str = player_map.get(r.get("display_name", ""))
                    if uid_str:
                        try:
                            finish_order.append(int(uid_str))
                        except ValueError:
                            pass

                elo_changes: dict[int, tuple[float, float]] = {}
                if len(finish_order) >= 2:
                    try:
                        elo_changes = await update_elo_multiplayer(finish_order, "bingo", "bingo")
                    except Exception:
                        log.exception("Unhandled error in bingo.py")

                if elo_changes:
                    elo_lines: list[str] = []
                    for r in raw_results:
                        uid_str = player_map.get(r.get("display_name", ""))
                        if uid_str:
                            uid = int(uid_str)
                            if uid in elo_changes:
                                old, new = elo_changes[uid]
                                elo_lines.append(f"**{r['display_name']}**: {fmt_elo_change(old, new)}")
                    if elo_lines:
                        embed.add_field(name="\U0001f4c8 ELO", value="\n".join(elo_lines), inline=False)

                await channel.send(embed=embed)
            except Exception:
                log.exception("Unhandled error in bingo.py")

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

    def __init__(self, room_id: str, player_registry: dict[str, str]) -> None:
        super().__init__()
        self.room_id = room_id
        self._player_registry = player_registry

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
            self._player_registry[interaction.user.display_name] = str(interaction.user.id)
            await interaction.response.send_message(
                f"\U0001f517 **[Click here to play]({url})**\n{num} card{'s' if num > 1 else ''} locked in.",
                ephemeral=True,
            )
        except Exception:
            await interaction.response.send_message("Failed to connect.", ephemeral=True)


class BingoWebLobbyView(ui.View):
    def __init__(self, room_id: str, player_registry: dict[str, str]) -> None:
        super().__init__(timeout=1800)
        self.room_id = room_id
        self._player_registry = player_registry

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f4dd")
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.send_modal(BingoJoinModal(self.room_id, self._player_registry))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BingoCog(bot))
