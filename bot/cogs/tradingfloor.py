"""Trading Floor cog — web-based market simulation game.

Creates a game room on the web server, players join via Discord and trade
4 correlated stocks in their browser with live WebSocket updates.
"""

import os

import discord
import httpx
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs._elo_helpers import update_elo_multiplayer
from db import queries

WEB_API_BASE = os.environ.get("WEB_API_BASE", "https://djiang.xyz")
WEB_API_SECRET = os.environ.get("WEB_API_SECRET", "dev-secret")


class TradingFloorCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._pending_web_rooms: dict[str, int] = {}

    async def cog_load(self) -> None:
        self._poll_web_results.start()

    async def cog_unload(self) -> None:
        self._poll_web_results.cancel()

    @app_commands.command(
        name="tradingfloor",
        description="Open a Trading Floor game (plays in your browser)",
    )
    async def tradingfloor(self, interaction: discord.Interaction) -> None:
        uid = str(interaction.user.id)
        channel_id = str(interaction.channel_id)
        await queries.get_or_create_casino_wallet(uid)

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{WEB_API_BASE}/api/v1/tradingfloor/rooms",
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
            title="\U0001f4c8 Trading Floor",
            description=(
                "Trade 4 correlated stocks over 8 rounds — plays in your browser!\n\n"
                "\U0001f4bb **CHIP** & \U0001f4f1 **SOFT** (Tech sector)\n"
                "\U0001f6e2\ufe0f **OIL** & \u2600\ufe0f **SOLAR** (Energy sector)\n\n"
                "Your trades move prices. NPCs add noise. Insider tips give some players an edge.\n"
                "Highest portfolio value at the end wins the pot.\n\n"
                "Click **Join** below to enter your bet and get your game link."
            ),
            colour=discord.Colour.gold(),
        )
        embed.set_footer(text=f"Room {room_id} \u2022 2-8 players \u2022 8 rounds \u2022 45s per round")

        view = TFWebLobbyView(room_id)
        await interaction.response.send_message(embed=embed, view=view)

    @tasks.loop(seconds=10)
    async def _poll_web_results(self) -> None:
        for room_id in list(self._pending_web_rooms):
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(
                        f"{WEB_API_BASE}/api/v1/tradingfloor/rooms/{room_id}/result",
                    )
                if resp.status_code != 200:
                    continue

                result = resp.json()
                channel_id = self._pending_web_rooms.pop(room_id)
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    continue

                embed = discord.Embed(
                    title="\U0001f4c8 Trading Floor \u2014 Results",
                    colour=discord.Colour.green(),
                )

                # Stock summary
                stocks = result.get("stocks", {})
                stock_lines = []
                for ticker, s in stocks.items():
                    ret = s.get("return", 0)
                    arrow = "\U0001f4c8" if ret > 0 else "\U0001f4c9" if ret < 0 else "\u27a1\ufe0f"
                    stock_lines.append(
                        f"{s['emoji']} **{ticker}** {s['final_price']:.1f}c ({arrow} {ret:+.1f}%)"
                    )
                embed.add_field(name="Stocks", value="\n".join(stock_lines), inline=False)

                # Player results
                medals = ["\U0001f947", "\U0001f948", "\U0001f949"]
                lines = []
                for i, r in enumerate(result.get("results", [])):
                    badge = medals[i] if i < 3 else f"`{i+1}.`"
                    net = r["net"]
                    sign = "+" if net > 0 else ""
                    pnl = r["pnl"]
                    pnl_sign = "+" if pnl > 0 else ""
                    lines.append(
                        f"{badge} **{r['display_name']}** \u2014 "
                        f"{r['final_cash']:,}c ({pnl_sign}{pnl:,} P&L) \u2014 "
                        f"{r['wager']}c \u2192 {r['payout']}c ({sign}{net}c)"
                    )
                embed.add_field(name="Standings", value="\n".join(lines), inline=False)

                embed.set_footer(
                    text=f"Room {room_id} \u2022 {result.get('total_trades', 0)} trades"
                )
                await channel.send(embed=embed)

                finish = [int(r["discord_user"]) for r in result.get("results", []) if r.get("discord_user")]
                if len(finish) >= 2:
                    try:
                        await update_elo_multiplayer(finish, "tradingfloor", "tradingfloor")
                    except Exception:
                        pass
            except Exception:
                pass

    @_poll_web_results.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()


class TFJoinModal(ui.Modal, title="Join Trading Floor"):
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
            await interaction.response.send_message(
                f"Insufficient balance (you have {bal or 0}c).", ephemeral=True,
            )
            return

        await queries.update_casino_balance(uid, -amt)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{WEB_API_BASE}/api/v1/tradingfloor/rooms/{self.room_id}/tokens",
                    json={
                        "discord_user": uid,
                        "display_name": interaction.user.display_name,
                        "wager": amt,
                    },
                    headers={"X-Api-Key": WEB_API_SECRET},
                )
            if resp.status_code != 200:
                await queries.update_casino_balance(uid, amt)
                detail = resp.json().get("detail", "Unknown error")
                await interaction.response.send_message(
                    f"Failed to join: {detail}", ephemeral=True,
                )
                return
            url = resp.json()["url"]
            await interaction.response.send_message(
                f"\U0001f517 **[Click here to play]({url})**\nYour {amt}c bet is locked in.",
                ephemeral=True,
            )
        except Exception:
            await queries.update_casino_balance(uid, amt)
            await interaction.response.send_message(
                "Failed to connect to game server. Bet refunded.", ephemeral=True,
            )


class TFWebLobbyView(ui.View):
    def __init__(self, room_id: str) -> None:
        super().__init__(timeout=1800)
        self.room_id = room_id

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f4dd")
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.send_modal(TFJoinModal(self.room_id))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TradingFloorCog(bot))
