"""Casino cog — multiplayer /crash rocket game."""

import asyncio
import math
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 10
MAX_BET = 5000
TICK_INTERVAL = 1.5  # seconds between multiplier updates
GROWTH_RATE = 0.08  # e^(rate * tick)
HOUSE_EDGE_FACTOR = 0.99  # 1% house edge
AUTO_CASHOUT_MAX = 100.0


# ── Helpers ──────────────────────────────────────────────────────────────────


def _generate_crash_point() -> float:
    """Random crash multiplier with ~1% house edge.

    Distribution:  ~1% instant crash (1.00x), median ~1.98x.
    """
    r = random.random()
    if r == 0:
        r = 1e-10
    return max(1.00, math.floor(100 * HOUSE_EDGE_FACTOR / r) / 100)


def _multiplier_at_tick(tick: int) -> float:
    return round(math.exp(GROWTH_RATE * tick), 2)


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class CrashPlayer:
    user_id: int
    display_name: str
    bet: int
    auto_cashout: float = 0.0  # 0 = disabled
    cashed_out: bool = False
    cashout_mult: float = 0.0
    payout: int = 0


@dataclass
class CrashTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | flying | crashed
    players: dict[int, CrashPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    crash_point: float = 0.0
    current_mult: float = 1.00
    tick: int = 0
    round_num: int = 1
    last_bets: dict[int, tuple[str, int, float]] = field(default_factory=dict)
    fly_task: asyncio.Task | None = field(default=None, repr=False)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: CrashTable) -> discord.Embed:
    embed = discord.Embed(
        title=f"Crash — Place Your Bets (Round {table.round_num})",
        description="Join the table, then the host launches the rocket!",
        colour=discord.Colour.blurple(),
    )
    if table.players:
        lines: list[str] = []
        for p in table.players.values():
            ac = f" (auto: {p.auto_cashout:.2f}x)" if p.auto_cashout > 0 else ""
            lines.append(f"💰 **{p.display_name}** — {p.bet}c{ac}")
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet — click Join!*",
            inline=False,
        )
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _flying_embed(table: CrashTable) -> discord.Embed:
    if table.current_mult < 2.0:
        colour = discord.Colour.green()
    elif table.current_mult < 5.0:
        colour = discord.Colour.gold()
    else:
        colour = discord.Colour.red()

    embed = discord.Embed(
        title=f"Crash — Round {table.round_num}",
        description=f"# 🚀 {table.current_mult:.2f}x",
        colour=colour,
    )

    lines: list[str] = []
    for p in table.players.values():
        if p.cashed_out:
            net = p.payout - p.bet
            lines.append(
                f"✅ **{p.display_name}** — Cashed out at **{p.cashout_mult:.2f}x** "
                f"(+{net}c)"
            )
        else:
            lines.append(f"🚀 **{p.display_name}** — {p.bet}c flying...")
    embed.add_field(name="Players", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _crashed_embed(
    table: CrashTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Crash — Round {table.round_num} Complete",
        description=f"# 💥 Crashed at {table.crash_point:.2f}x",
        colour=discord.Colour.dark_red(),
    )

    lines: list[str] = []
    for p in table.players.values():
        bal = balances.get(p.user_id, 0) if balances else 0
        if p.cashed_out:
            net = p.payout - p.bet
            lines.append(
                f"✅ **{p.display_name}** — {p.cashout_mult:.2f}x "
                f"(**+{net}c**) — bal: {bal}c"
            )
        else:
            lines.append(
                f"💥 **{p.display_name}** — Busted! "
                f"(**-{p.bet}c**) — bal: {bal}c"
            )
    embed.add_field(name="Results", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modal ────────────────────────────────────────────────────────────────────


class JoinCrashModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )
    auto = ui.TextInput(
        label="Auto cash-out multiplier (0 = off)",
        placeholder="e.g. 2.5",
        required=False,
        max_length=10,
        default="0",
    )

    def __init__(self, table: CrashTable, view: "CrashTableView") -> None:
        super().__init__(title="Join Crash")
        self.table = table
        self.table_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Parse bet
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

        # Parse auto-cashout
        auto_co = 0.0
        raw = (self.auto.value or "").strip()
        if raw and raw != "0":
            try:
                auto_co = float(raw)
            except ValueError:
                await interaction.response.send_message(
                    "Auto cash-out must be a number (e.g. 2.5).", ephemeral=True,
                )
                return
            if auto_co < 1.01:
                await interaction.response.send_message(
                    "Auto cash-out must be at least 1.01x.", ephemeral=True,
                )
                return
            if auto_co > AUTO_CASHOUT_MAX:
                await interaction.response.send_message(
                    f"Auto cash-out max is {AUTO_CASHOUT_MAX:.0f}x.", ephemeral=True,
                )
                return

        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in this round!", ephemeral=True,
            )
            return

        # Deduct coins
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        self.table.players[uid] = CrashPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
            auto_cashout=auto_co,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


# ── View ─────────────────────────────────────────────────────────────────────


class CrashTableView(ui.View):
    def __init__(
        self, table: CrashTable, active_tables: dict[int, CrashTable],
    ) -> None:
        super().__init__(timeout=300)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        flying = phase == "flying"
        crashed = phase == "crashed"

        # Row 0
        self.launch_btn.disabled = not betting or not self.table.players
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = flying

        # Row 1
        self.cashout_btn.disabled = not flying

        # Row 2
        self.new_round_btn.disabled = not crashed
        self.close_btn.disabled = flying

    # ── Row 0 ────────────────────────────────────────────────────────────────

    @ui.button(label="Launch", style=discord.ButtonStyle.success, emoji="🚀", row=0)
    async def launch_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can launch!", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Already launched!", ephemeral=True,
            )
            return
        if not self.table.players:
            await interaction.response.send_message(
                "No players yet!", ephemeral=True,
            )
            return
        await self._launch(interaction)

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
        await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(JoinCrashModal(self.table, self))

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
                "No previous bet — use Join instead.", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message(
                "Table is full!", ephemeral=True,
            )
            return
        name, amt, auto_co = last
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for {amt}c re-bet! (have {bal}c)",
                ephemeral=True,
            )
            return
        self.table.players[uid] = CrashPlayer(
            user_id=uid,
            display_name=name,
            bet=amt,
            auto_cashout=auto_co,
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
        if self.table.phase == "flying":
            await interaction.response.send_message(
                "Can't leave while flying! Cash out first.", ephemeral=True,
            )
            return
        # Betting phase — refund
        if self.table.phase == "betting":
            await queries.update_casino_balance(str(uid), player.bet)
            del self.table.players[uid]
            self._update_buttons()
            await interaction.response.edit_message(
                embed=_betting_embed(self.table), view=self,
            )
            return
        # Crashed phase — just acknowledge
        await interaction.response.send_message(
            "Round is over. Wait for New Round or close.", ephemeral=True,
        )

    # ── Row 1 ────────────────────────────────────────────────────────────────

    @ui.button(
        label="Cash Out", style=discord.ButtonStyle.success, emoji="💸", row=1,
    )
    async def cashout_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "flying":
            await interaction.response.send_message(
                "Not flying right now!", ephemeral=True,
            )
            return
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message(
                "You're not in this round!", ephemeral=True,
            )
            return
        if player.cashed_out:
            await interaction.response.send_message(
                "You already cashed out!", ephemeral=True,
            )
            return

        await self._cashout_player(player)
        await interaction.response.edit_message(
            embed=_flying_embed(self.table), view=self,
        )

    # ── Row 2 ────────────────────────────────────────────────────────────────

    @ui.button(
        label="New Round", style=discord.ButtonStyle.success, emoji="▶️", row=2,
    )
    async def new_round_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can start a new round!", ephemeral=True,
            )
            return
        if self.table.phase != "crashed":
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
        label="Close Table", style=discord.ButtonStyle.danger, emoji="✖️", row=2,
    )
    async def close_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can close the table!", ephemeral=True,
            )
            return
        if self.table.phase == "flying":
            await interaction.response.send_message(
                "Can't close mid-flight!", ephemeral=True,
            )
            return
        if self.table.phase == "betting":
            # Refund everyone
            for p in self.table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass
        await self._close(interaction, "Table closed by host.")

    # ── Flight logic ─────────────────────────────────────────────────────────

    async def _launch(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "flying"
        table.crash_point = _generate_crash_point()
        table.current_mult = 1.00
        table.tick = 0

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_flying_embed(table), view=self,
        )

        table.fly_task = asyncio.create_task(self._fly_loop())

    async def _fly_loop(self) -> None:
        table = self.table
        try:
            while True:
                await asyncio.sleep(TICK_INTERVAL)
                table.tick += 1
                table.current_mult = _multiplier_at_tick(table.tick)

                # Auto-cashouts first (player gets their exact target)
                for p in table.players.values():
                    if (
                        not p.cashed_out
                        and p.auto_cashout > 0
                        and table.current_mult >= p.auto_cashout
                    ):
                        await self._cashout_player(
                            p, mult_override=p.auto_cashout,
                        )

                # Check crash
                if table.current_mult >= table.crash_point:
                    table.current_mult = table.crash_point
                    await self._crash()
                    return

                # Update display
                if table.message:
                    try:
                        await table.message.edit(
                            embed=_flying_embed(table), view=self,
                        )
                    except discord.HTTPException:
                        pass  # rate limited — skip this tick

        except asyncio.CancelledError:
            pass
        except Exception:
            # Unexpected error — refund and bail
            if table.phase == "flying":
                table.phase = "crashed"
                await self._refund_active()

    async def _cashout_player(
        self, player: CrashPlayer, *, mult_override: float | None = None,
    ) -> None:
        mult = mult_override or self.table.current_mult
        player.cashed_out = True
        player.cashout_mult = mult
        player.payout = math.floor(player.bet * mult)
        await queries.update_casino_balance(str(player.user_id), player.payout)

    async def _crash(self) -> None:
        table = self.table
        table.phase = "crashed"

        # Save last bets for re-bet
        for p in table.players.values():
            table.last_bets[p.user_id] = (
                p.display_name, p.bet, p.auto_cashout,
            )

        # Gather balances
        balances: dict[int, int] = {}
        for p in table.players.values():
            bal = await queries.get_casino_balance(str(p.user_id))
            balances[p.user_id] = bal or 0

        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(
                    embed=_crashed_embed(table, balances=balances), view=self,
                )
            except discord.HTTPException:
                pass

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _start_new_round(self) -> None:
        table = self.table
        table.players.clear()
        table.phase = "betting"
        table.crash_point = 0.0
        table.current_mult = 1.00
        table.tick = 0
        table.round_num += 1
        table.fly_task = None

    async def _refund_active(self) -> None:
        for p in self.table.players.values():
            if not p.cashed_out:
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass

    async def _close(
        self, interaction: discord.Interaction, reason: str,
    ) -> None:
        embed = discord.Embed(
            title="Crash Table — Closed",
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

        # Cancel fly task if running
        if table.fly_task and not table.fly_task.done():
            table.fly_task.cancel()

        if table.phase == "crashed":
            # Between rounds — just clean up
            self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    embed = discord.Embed(
                        title="Crash Table — Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    pass
            return

        # Betting or flying — refund active players
        await self._refund_active()
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="Crash Table — Timed Out",
                    description="Table timed out. Active bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class CrashCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, CrashTable] = {}

    @app_commands.command(
        name="crash", description="Open a Crash table (multiplayer rocket game)",
    )
    async def crash(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a Crash table in this channel!",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = CrashTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = CrashTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CrashCog(bot))
