"""Solitaire Chess cog — web-based puzzle game.

Creates a game room on the web server, players join via Discord and play
in their browser with a clickable 4x4 chess board.
"""

import os

import discord
import httpx
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs._elo_helpers import update_elo_multiplayer

WEB_API_BASE = os.environ.get("WEB_API_BASE", "https://sharplab.djiang.xyz")
WEB_API_SECRET = os.environ.get("WEB_API_SECRET", "dev-secret")

DIFFICULTY_CHOICES = [
    app_commands.Choice(name="Easy \u2014 4 pieces, 3 moves", value="easy"),
    app_commands.Choice(name="Medium \u2014 5 pieces, 4 moves", value="medium"),
    app_commands.Choice(name="Hard \u2014 6 pieces, 5 moves", value="hard"),
    app_commands.Choice(name="Expert \u2014 7 pieces, 6 moves", value="expert"),
]

DIFF_LABELS = {
    "easy": "Easy (4 pieces)",
    "medium": "Medium (5 pieces)",
    "hard": "Hard (6 pieces)",
    "expert": "Expert (7 pieces)",
}


class SolChessCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._pending_web_rooms: dict[str, int] = {}  # room_id -> channel_id

    async def cog_load(self) -> None:
        self._poll_web_results.start()

    async def cog_unload(self) -> None:
        self._poll_web_results.cancel()

    @app_commands.command(
        name="solitaire-chess",
        description="Solitaire Chess puzzle race \u2014 plays in your browser",
    )
    @app_commands.describe(difficulty="Puzzle difficulty (number of pieces)")
    @app_commands.choices(difficulty=DIFFICULTY_CHOICES)
    async def solitaire_chess(
        self,
        interaction: discord.Interaction,
        difficulty: str = "medium",
    ) -> None:
        try:
            uid = str(interaction.user.id)
            channel_id = str(interaction.channel_id)
            if difficulty not in DIFF_LABELS:
                difficulty = "medium"

            # Create room via web API
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.post(
                        f"{WEB_API_BASE}/api/v1/solitairechess/rooms",
                        json={
                            "host_discord_id": uid,
                            "channel_id": channel_id,
                            "host_display_name": interaction.user.display_name,
                            "difficulty": difficulty,
                        },
                        headers={"X-Api-Key": WEB_API_SECRET},
                    )
            except Exception as exc:
                await interaction.response.send_message(
                    f"Failed to connect to game server: {type(exc).__name__}",
                    ephemeral=True,
                )
                return

            if resp.status_code != 200:
                detail = ""
                try:
                    detail = resp.json().get("detail", "")
                except Exception:
                    pass
                await interaction.response.send_message(
                    f"Failed to create game room (HTTP {resp.status_code})"
                    + (f": {detail}" if detail else ""),
                    ephemeral=True,
                )
                return

            room_id = resp.json()["room_id"]
            self._pending_web_rooms[room_id] = interaction.channel_id

            embed = discord.Embed(
                title="\u265e Solitaire Chess",
                description=(
                    "Play in your browser with a clickable board!\n"
                    f"Difficulty: **{DIFF_LABELS[difficulty]}**\n\n"
                    "Click **Join** below to get your game link."
                ),
                colour=discord.Colour.dark_teal(),
            )
            embed.set_footer(text=f"Room {room_id} \u2022 First to 3 wins")

            view = WebSolChessLobbyView(room_id, self.bot)
            await interaction.response.send_message(embed=embed, view=view)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        f"Error: {type(exc).__name__}: {exc}", ephemeral=True,
                    )
            except Exception:
                pass

    # ── Result polling ─────────────────────────────────────────────────────

    @tasks.loop(seconds=10)
    async def _poll_web_results(self) -> None:
        for room_id in list(self._pending_web_rooms):
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(
                        f"{WEB_API_BASE}/api/v1/solitairechess/rooms/{room_id}/result",
                    )
                if resp.status_code != 200:
                    continue

                result = resp.json()
                channel_id = self._pending_web_rooms.pop(room_id)
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    continue

                embed = discord.Embed(
                    title="\u265e Solitaire Chess \u2014 Results",
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
                        await update_elo_multiplayer(finish, "solitairechess", "solitairechess")
                    except Exception:
                        pass
            except Exception:
                pass

    @_poll_web_results.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()


# ── Web Lobby View ────────────────────────────────────────────────────────


class WebSolChessLobbyView(ui.View):
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
                    f"{WEB_API_BASE}/api/v1/solitairechess/rooms/{self.room_id}/tokens",
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
    await bot.add_cog(SolChessCog(bot))
