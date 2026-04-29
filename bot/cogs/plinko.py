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

# Multiplier tables per risk level (symmetric, fair EV = 1.0).
MULTIPLIERS: dict[str, list[float]] = {
    "low": [5.6, 2.1, 1.1, 1.0, 0.54, 1.0, 1.1, 2.1, 5.6],
    "medium": [13, 3, 1.3, 0.7, 0.44, 0.7, 1.3, 3, 13],
    "high": [29, 4, 1.5, 0.3, 0.24, 0.3, 1.5, 4, 29],
}

RISK_EMOJI: dict[str, str] = {"low": "🟢", "medium": "🟡", "high": "🔴"}
RISK_LABEL: dict[str, str] = {"low": "Low", "medium": "Med", "high": "High"}
RISK_STYLE: dict[str, discord.ButtonStyle] = {
    "low": discord.ButtonStyle.success,
    "medium": discord.ButtonStyle.primary,
    "high": discord.ButtonStyle.danger,
}
RISK_CYCLE: dict[str, str] = {"low": "medium", "medium": "high", "high": "low"}

# Animation: show ball at these rows, then final (ROWS=8)
ANIM_ROWS = [2, 4, 6]
ANIM_DELAY = 0.6  # seconds between frames


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


def _render_board(
    path: list[int], show_row: int, *, trail: bool = False,
) -> str:
    """Render the peg board as monospace text.

    show_row: which row the ball is currently at (0-8).
    trail: if True, show the full path from top to show_row.
    """
    lines: list[str] = []
    for row in range(ROWS + 1):
        n_pos = row + 1
        leading = ROWS - row
        cells = ["."] * n_pos

        pos = sum(path[:row])
        if row == show_row:
            cells[pos] = "@"
        elif trail and row < show_row:
            cells[pos] = "*"

        lines.append(" " * leading + " ".join(cells))
    return "\n".join(lines)


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class PlinkoPlayer:
    user_id: int
    display_name: str
    bet: int
    payout: int = 0


@dataclass
class PlinkoTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | dropping | finished
    risk: str = "low"  # table-wide risk level set by host
    players: dict[int, PlinkoPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    # Single ball per round
    bucket: int = -1
    multiplier: float = 0.0
    path: list[int] = field(default_factory=list)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: PlinkoTable) -> discord.Embed:
    emoji = RISK_EMOJI[table.risk]
    embed = discord.Embed(
        title=f"Plinko — Place Your Bets (Round {table.round_num})",
        description=f"Risk: {emoji} **{RISK_LABEL[table.risk]}** — one ball, everyone rides!",
        colour=discord.Colour.blurple(),
    )

    mults = MULTIPLIERS[table.risk]
    vals = " · ".join(str(m) for m in mults)
    embed.add_field(name="Buckets", value=f"`{vals}`", inline=False)

    if table.players:
        plines = [
            f"💰 **{p.display_name}** — {p.bet}c"
            for p in table.players.values()
        ]
        embed.add_field(name="Players", value="\n".join(plines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet — click Join!*",
            inline=False,
        )

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _anim_embed(table: PlinkoTable, board: str) -> discord.Embed:
    emoji = RISK_EMOJI[table.risk]
    embed = discord.Embed(
        title=f"Plinko — Round {table.round_num} {emoji}",
        description=f"```\n{board}\n```",
        colour=discord.Colour.gold(),
    )
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _results_embed(
    table: PlinkoTable,
    *,
    board: str | None = None,
    balances: dict[int, int] | None = None,
) -> discord.Embed:
    emoji = RISK_EMOJI[table.risk]
    celebration = ""
    if table.multiplier >= 10:
        celebration = " 🎉"
    elif table.multiplier >= 5:
        celebration = " 🎊"

    desc_parts: list[str] = []
    if board:
        desc_parts.append(f"```\n{board}\n```")
    desc_parts.append(f"{emoji} Landed on **{table.multiplier}x**{celebration}")

    embed = discord.Embed(
        title=f"Plinko — Round {table.round_num} Complete",
        description="\n".join(desc_parts),
        colour=discord.Colour.dark_green(),
    )

    lines: list[str] = []
    for p in table.players.values():
        bal = balances.get(p.user_id, 0) if balances else 0
        net = p.payout - p.bet
        sign = "+" if net >= 0 else ""
        lines.append(
            f"**{p.display_name}** — {p.bet}c → "
            f"**{p.payout}c** ({sign}{net}c) — bal: {bal}c"
        )

    embed.add_field(name="Results", value="\n".join(lines), inline=False)
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

    def __init__(self, table: PlinkoTable, view: "PlinkoTableView", balance: int) -> None:
        super().__init__(title="Join Plinko")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 100 (bal: {balance}c)"

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
        self._sync_risk_btn()
        self._update_buttons()

    def _sync_risk_btn(self) -> None:
        """Update the risk toggle button to reflect the current risk level."""
        risk = self.table.risk
        self.risk_btn.label = f"Risk: {RISK_LABEL[risk]}"
        self.risk_btn.emoji = RISK_EMOJI[risk]
        self.risk_btn.style = RISK_STYLE[risk]

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        finished = phase == "finished"

        self.drop_btn.disabled = not betting or not self.table.players
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = not betting

        self.risk_btn.disabled = not betting
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
        if self.table.phase != "betting" or not self.table.players:
            return
        await self._drop(interaction)

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="💰", row=0)
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
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
        bal = await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(JoinPlinkoModal(self.table, self, bal))

    @ui.button(label="Re-bet", style=discord.ButtonStyle.primary, emoji="🔄", row=0)
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
                "No previous bet — use Join.", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message(
                "Table is full!", ephemeral=True,
            )
            return
        name, amt = last
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
            user_id=uid, display_name=name, bet=amt,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

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

    @ui.button(label="Risk: Low", style=discord.ButtonStyle.success, emoji="🟢", row=1)
    async def risk_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can change risk!", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            return
        self.table.risk = RISK_CYCLE[self.table.risk]
        self._sync_risk_btn()
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
            return
        self._start_new_round()
        self._sync_risk_btn()
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
                "Only the host can close the table!", ephemeral=True,
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

    async def _drop(self, interaction: discord.Interaction) -> None:
        table = self.table

        # Single ball for the whole table
        bucket, path = _drop_ball()
        table.bucket = bucket
        table.path = path
        table.multiplier = MULTIPLIERS[table.risk][bucket]

        # Everyone gets the same multiplier
        for p in table.players.values():
            p.payout = math.floor(p.bet * table.multiplier)

        table.phase = "dropping"
        self._update_buttons()

        # Frame 1: ball near top of board
        board = _render_board(path, ANIM_ROWS[0])
        await interaction.response.edit_message(
            embed=_anim_embed(table, board), view=self,
        )

        # Intermediate animation frames
        for row in ANIM_ROWS[1:]:
            await asyncio.sleep(ANIM_DELAY)
            board = _render_board(path, row)
            if table.message:
                try:
                    await table.message.edit(
                        embed=_anim_embed(table, board), view=self,
                    )
                except discord.HTTPException:
                    pass

        # Credit payouts
        for p in table.players.values():
            if p.payout > 0:
                await queries.update_casino_balance(str(p.user_id), p.payout)
            await queries.log_casino_result(str(p.user_id), "plinko", p.bet, p.payout)

        # Save last bets for re-bet
        for p in table.players.values():
            table.last_bets[p.user_id] = (p.display_name, p.bet)

        # Final frame: full trail + results
        await asyncio.sleep(ANIM_DELAY)
        table.phase = "finished"

        balances: dict[int, int] = {}
        for p in table.players.values():
            bal = await queries.get_casino_balance(str(p.user_id))
            balances[p.user_id] = bal or 0

        final_board = _render_board(path, ROWS, trail=True)
        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(
                    embed=_results_embed(
                        table, board=final_board, balances=balances,
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _start_new_round(self) -> None:
        self.table.players.clear()
        self.table.phase = "betting"
        self.table.round_num += 1
        self.table.bucket = -1
        self.table.multiplier = 0.0
        self.table.path.clear()

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
            existing = self.active_tables[channel_id]
            if getattr(existing, "phase", None) == "closed":
                del self.active_tables[channel_id]
            else:
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
