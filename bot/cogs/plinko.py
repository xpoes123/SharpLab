"""Casino cog — multiplayer /plinko ball-drop game."""

import asyncio
import math
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Constants ────────────────────────────────────────────────────────────────

ROWS = 8  # 9 buckets
MAX_PLAYERS = 10
MAX_BET = 5000

# Multiplier tables per risk level (symmetric, ~1% house edge each).
MULTIPLIERS: dict[str, list[float]] = {
    "low": [5.6, 2.1, 1.1, 1.0, 0.5, 1.0, 1.1, 2.1, 5.6],
    "medium": [13, 3, 1.3, 0.7, 0.4, 0.7, 1.3, 3, 13],
    "high": [29, 4, 1.5, 0.3, 0.2, 0.3, 1.5, 4, 29],
}

RISK_EMOJI: dict[str, str] = {"low": "🟢", "medium": "🟡", "high": "🔴"}
RISK_LABEL: dict[str, str] = {"low": "Low", "medium": "Med", "high": "High"}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _drop_ball() -> tuple[int, list[int]]:
    """Drop a ball through 8 rows.  Returns (bucket, path of 0=left / 1=right)."""
    pos = 0
    path: list[int] = []
    for _ in range(ROWS):
        d = random.randint(0, 1)
        pos += d
        path.append(d)
    return pos, path


def _path_arrows(path: list[int]) -> str:
    return "".join("↘" if d else "↙" for d in path)


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class PlinkoPlayer:
    user_id: int
    display_name: str
    bet: int
    risk: str  # "low" | "medium" | "high"
    bucket: int = -1
    multiplier: float = 0.0
    payout: int = 0
    path: list[int] = field(default_factory=list)


@dataclass
class PlinkoTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | dropping | finished
    players: dict[int, PlinkoPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    last_bets: dict[int, tuple[str, int, str]] = field(default_factory=dict)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: PlinkoTable) -> discord.Embed:
    embed = discord.Embed(
        title=f"Plinko — Place Your Bets (Round {table.round_num})",
        description="Pick a risk level to join, then the host drops the balls!",
        colour=discord.Colour.blurple(),
    )

    # Compact multiplier reference
    lines = []
    for risk in ("low", "medium", "high"):
        mults = MULTIPLIERS[risk]
        emoji = RISK_EMOJI[risk]
        vals = " · ".join(str(m) for m in mults)
        lines.append(f"{emoji} **{RISK_LABEL[risk]}:** {vals}")
    embed.add_field(name="Buckets", value="\n".join(lines), inline=False)

    if table.players:
        plines: list[str] = []
        for p in table.players.values():
            emoji = RISK_EMOJI[p.risk]
            plines.append(
                f"{emoji} **{p.display_name}** — {p.bet}c ({RISK_LABEL[p.risk]})"
            )
        embed.add_field(name="Players", value="\n".join(plines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet — pick a risk level!*",
            inline=False,
        )

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _dropping_embed(table: PlinkoTable) -> discord.Embed:
    embed = discord.Embed(
        title=f"Plinko — Round {table.round_num}",
        description="# 🎱 Dropping...",
        colour=discord.Colour.gold(),
    )
    lines = []
    for p in table.players.values():
        emoji = RISK_EMOJI[p.risk]
        lines.append(f"{emoji} **{p.display_name}** — {p.bet}c")
    embed.add_field(name="Players", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _results_embed(
    table: PlinkoTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Plinko — Round {table.round_num} Complete",
        colour=discord.Colour.dark_green(),
    )

    lines: list[str] = []
    for p in table.players.values():
        emoji = RISK_EMOJI[p.risk]
        bal = balances.get(p.user_id, 0) if balances else 0
        net = p.payout - p.bet
        sign = "+" if net >= 0 else ""
        celebration = ""
        if p.multiplier >= 10:
            celebration = " 🎉"
        elif p.multiplier >= 5:
            celebration = " 🎊"
        arrows = _path_arrows(p.path)
        lines.append(
            f"{emoji} **{p.display_name}** ({RISK_LABEL[p.risk]})\n"
            f"{arrows} → **{p.multiplier}x**{celebration}\n"
            f"{p.bet}c → **{p.payout}c** ({sign}{net}c) — bal: {bal}c"
        )

    embed.add_field(name="Results", value="\n\n".join(lines), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modal ────────────────────────────────────────────────────────────────────


class JoinPlinkoModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(
        self, table: PlinkoTable, risk: str, view: "PlinkoTableView",
    ) -> None:
        super().__init__(title=f"Join Plinko — {RISK_LABEL[risk]} Risk")
        self.table = table
        self.risk = risk
        self.table_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message(
                "Enter a whole number.", ephemeral=True,
            )
            return
        if amt < 1:
            await interaction.response.send_message(
                "Must be at least 1 coin.", ephemeral=True,
            )
            return
        if amt > MAX_BET:
            await interaction.response.send_message(
                f"Max bet is {MAX_BET}c.", ephemeral=True,
            )
            return

        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in this round!", ephemeral=True,
            )
            return

        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        self.table.players[uid] = PlinkoPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
            risk=self.risk,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


# ── View ─────────────────────────────────────────────────────────────────────


class PlinkoTableView(ui.View):
    def __init__(
        self, table: PlinkoTable, active_tables: dict[int, PlinkoTable],
    ) -> None:
        super().__init__(timeout=180)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        finished = phase == "finished"

        self.drop_btn.disabled = not betting or not self.table.players
        self.low_btn.disabled = not betting
        self.med_btn.disabled = not betting
        self.high_btn.disabled = not betting
        self.leave_btn.disabled = not betting

        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.new_round_btn.disabled = not finished
        self.close_btn.disabled = phase == "dropping"

    # ── Row 0 ────────────────────────────────────────────────────────────────

    @ui.button(label="Drop", style=discord.ButtonStyle.success, emoji="🎱", row=0)
    async def drop_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can drop!", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Already dropping!", ephemeral=True,
            )
            return
        if not self.table.players:
            await interaction.response.send_message(
                "No players yet!", ephemeral=True,
            )
            return
        await self._drop(interaction)

    @ui.button(label="Low", style=discord.ButtonStyle.success, emoji="🟢", row=0)
    async def low_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._join(interaction, "low")

    @ui.button(label="Medium", style=discord.ButtonStyle.primary, emoji="🟡", row=0)
    async def med_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._join(interaction, "medium")

    @ui.button(label="High", style=discord.ButtonStyle.danger, emoji="🔴", row=0)
    async def high_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await self._join(interaction, "high")

    @ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="🚪", row=0)
    async def leave_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message(
                "You're not at this table.", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Can't leave mid-drop!", ephemeral=True,
            )
            return
        await queries.update_casino_balance(str(uid), player.bet)
        del self.table.players[uid]
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Row 1 ────────────────────────────────────────────────────────────────

    @ui.button(label="Re-bet", style=discord.ButtonStyle.primary, emoji="🔄", row=1)
    async def rebet_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Round in progress!", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in!", ephemeral=True,
            )
            return
        last = self.table.last_bets.get(uid)
        if last is None:
            await interaction.response.send_message(
                "No previous bet — pick a risk level.", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message(
                "Table is full!", ephemeral=True,
            )
            return
        name, amt, risk = last
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for {amt}c re-bet! (have {bal}c)",
                ephemeral=True,
            )
            return
        self.table.players[uid] = PlinkoPlayer(
            user_id=uid, display_name=name, bet=amt, risk=risk,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="New Round", style=discord.ButtonStyle.success, emoji="▶️", row=1,
    )
    async def new_round_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can start a new round!", ephemeral=True,
            )
            return
        if self.table.phase != "finished":
            await interaction.response.send_message(
                "Round still in progress!", ephemeral=True,
            )
            return
        self._start_new_round()
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Close Table", style=discord.ButtonStyle.danger, emoji="✖️", row=1,
    )
    async def close_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can close!", ephemeral=True,
            )
            return
        if self.table.phase == "dropping":
            await interaction.response.send_message(
                "Can't close mid-drop!", ephemeral=True,
            )
            return
        if self.table.phase == "betting":
            for p in self.table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass
        await self._close(interaction, "Table closed by host.")

    # ── Game logic ───────────────────────────────────────────────────────────

    async def _join(self, interaction: discord.Interaction, risk: str) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Round in progress! Wait for the next one.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in!", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message(
                "Table is full!", ephemeral=True,
            )
            return
        await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(
            JoinPlinkoModal(self.table, risk, self),
        )

    async def _drop(self, interaction: discord.Interaction) -> None:
        table = self.table

        # Pre-calculate all drops
        for p in table.players.values():
            bucket, path = _drop_ball()
            p.bucket = bucket
            p.path = path
            p.multiplier = MULTIPLIERS[p.risk][bucket]
            p.payout = math.floor(p.bet * p.multiplier)

        # Frame 1: "Dropping..." animation
        table.phase = "dropping"
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_dropping_embed(table), view=self,
        )

        # Credit payouts
        for p in table.players.values():
            if p.payout > 0:
                await queries.update_casino_balance(str(p.user_id), p.payout)

        # Save last bets
        for p in table.players.values():
            table.last_bets[p.user_id] = (p.display_name, p.bet, p.risk)

        # Dramatic pause
        await asyncio.sleep(1.5)

        # Frame 2: Results
        table.phase = "finished"
        balances: dict[int, int] = {}
        for p in table.players.values():
            bal = await queries.get_casino_balance(str(p.user_id))
            balances[p.user_id] = bal or 0

        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(
                    embed=_results_embed(table, balances=balances), view=self,
                )
            except discord.HTTPException:
                pass

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _start_new_round(self) -> None:
        self.table.players.clear()
        self.table.phase = "betting"
        self.table.round_num += 1

    async def _close(
        self, interaction: discord.Interaction, reason: str,
    ) -> None:
        embed = discord.Embed(
            title="Plinko Table — Closed",
            description=reason,
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(self.table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table
        if table.phase == "finished":
            self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    embed = discord.Embed(
                        title="Plinko Table — Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    pass
            return
        # Betting — refund
        if table.phase == "betting":
            for p in table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="Plinko Table — Timed Out",
                    description="Table timed out. Bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class PlinkoCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, PlinkoTable] = {}

    @app_commands.command(
        name="plinko",
        description="Open a Plinko table (multiplayer ball-drop game)",
    )
    async def plinko(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a Plinko table in this channel!",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = PlinkoTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = PlinkoTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PlinkoCog(bot))
