"""Casino cog — multiplayer /horserace game."""

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 10
MIN_PLAYERS = 1
TICK_INTERVAL = 1.5  # seconds between race ticks
TRACK_LENGTH = 20  # positions to finish line

HORSES: list[tuple[str, str]] = [
    ("Thunderbolt", "\U0001f534"),  # 🔴
    ("Lightning", "\U0001f7e2"),    # 🟢
    ("Storm", "\U0001f535"),        # 🔵
    ("Blaze", "\U0001f7e1"),        # 🟡
    ("Shadow", "\U0001f7e3"),       # 🟣
    ("Phoenix", "\U0001f7e0"),      # 🟠
]

NUM_HORSES = len(HORSES)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _generate_horse_probs() -> list[float]:
    """Generate random win probabilities for each horse.

    Uses a Dirichlet-like distribution so some horses are favorites
    and others are longshots, with enough variance to keep it interesting.
    """
    raw = [random.gammavariate(1.5, 1) for _ in range(NUM_HORSES)]
    total = sum(raw)
    return [r / total for r in raw]


def _horse_label(index: int) -> str:
    """Return e.g. '🔴 Thunderbolt' for a horse index."""
    name, emoji = HORSES[index]
    return f"{emoji} {name}"


def _horse_short(index: int) -> str:
    """Return just the emoji for compact display."""
    return HORSES[index][1]


def _render_track(
    positions: list[int], tick: int, probs: list[float] | None = None,
) -> str:
    """Render the race track as monospace text for a Discord code block."""
    lines: list[str] = [f"\U0001f3c7 Horse Race \u2014 Tick {tick}"]
    lines.append("")
    for i, (name, _emoji) in enumerate(HORSES):
        pos = min(positions[i], TRACK_LENGTH)
        pct = int(pos / TRACK_LENGTH * 100)
        filled = pos
        empty = TRACK_LENGTH - pos
        bar = "\u2588" * filled + "\u2591" * empty
        # Use the horse number since emoji won't render in code blocks
        if probs:
            prob_tag = f"{probs[i] * 100:>2.0f}%"
            lines.append(f"  #{i + 1} {prob_tag} {name:<12s} {bar} {pct:>3d}%")
        else:
            lines.append(f"  #{i + 1} {name:<12s} {bar} {pct:>3d}%")
    return "```\n" + "\n".join(lines) + "\n```"


def _pool_multipliers(players: dict[int, "HorseRacePlayer"]) -> dict[int, float | None]:
    """Compute current pari-mutuel payout multiplier for each horse.

    Returns horse_index -> float multiplier, or None if no bets on that horse.
    Multiplier = total_pool / bets_on_horse.
    """
    total = sum(p.bet for p in players.values())
    if total == 0:
        return {i: None for i in range(NUM_HORSES)}

    per_horse: dict[int, int] = {i: 0 for i in range(NUM_HORSES)}
    for p in players.values():
        per_horse[p.horse_index] += p.bet

    return {
        i: (total / per_horse[i] if per_horse[i] > 0 else None)
        for i in range(NUM_HORSES)
    }


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class HorseRacePlayer:
    user_id: int
    display_name: str
    bet: int
    horse_index: int  # 0-5
    payout: int = 0
    won: bool = False


@dataclass
class HorseRaceTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | racing | finished
    players: dict[int, HorseRacePlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    last_bets: dict[int, tuple[str, int, int]] = field(default_factory=dict)
    # Race-specific
    horse_probs: list[float] = field(default_factory=_generate_horse_probs)
    horse_positions: list[int] = field(default_factory=lambda: [0] * NUM_HORSES)
    tick: int = 0
    winners: list[int] = field(default_factory=list)  # winning horse indices
    race_task: asyncio.Task | None = field(default=None, repr=False)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _fair_multiplier(prob: float) -> float:
    """Return the probability-based fair-odds multiplier (1/prob).

    This is what a solo bettor would get. In pari-mutuel play, the actual
    payout depends on how many others bet the same horse.
    """
    return 1 / prob


def _betting_embed(table: HorseRaceTable) -> discord.Embed:
    total_wagered = sum(p.bet for p in table.players.values())
    pool_mults = _pool_multipliers(table.players)

    embed = discord.Embed(
        title=f"\U0001f3c7 Horse Race \u2014 Place Your Bets (Round {table.round_num})",
        description=(
            "**Pari-mutuel pools** \u2014 winners split the entire pot based on how much "
            "they bet. The more players who pick the same horse, the smaller each winner's "
            "cut. Backing a crowded favourite can pay less than its odds suggest; a "
            "contrarian longshot that lands pays out the whole pool. "
            "Picks are hidden until the race starts."
        ),
        colour=discord.Colour.blurple(),
    )

    if total_wagered:
        embed.add_field(name="Pool", value=f"{total_wagered}c", inline=True)

    # Horse list: win%, fair solo multiplier, live pool multiplier + value signal
    horse_lines: list[str] = []
    for i in range(NUM_HORSES):
        name, emoji = HORSES[i]
        pct = table.horse_probs[i] * 100
        fair = _fair_multiplier(table.horse_probs[i])
        pool_m = pool_mults[i]

        if pool_m is None:
            pool_str = "---"
            signal = ""
        elif pool_m >= fair * 1.1:
            pool_str = f"**{pool_m:.1f}x**"
            signal = " \u2705"   # underbet — value
        elif pool_m <= fair * 0.9:
            pool_str = f"**{pool_m:.1f}x**"
            signal = " \u26a0\ufe0f"  # overbet — crowded
        else:
            pool_str = f"**{pool_m:.1f}x**"
            signal = ""

        horse_lines.append(
            f"{emoji} **#{i + 1} {name}** \u2014 {pct:.0f}% win "
            f"\u00b7 Solo: {fair:.1f}x \u00b7 Pool now: {pool_str}{signal}"
        )

    embed.add_field(
        name="Horses (Win% \u00b7 Solo odds \u00b7 Live pool payout)",
        value="\n".join(horse_lines),
        inline=False,
    )

    # Show who joined + bet amount, but NOT which horse (hidden until race)
    if table.players:
        player_lines = [
            f"\U0001f3b0 **{p.display_name}** \u2014 {p.bet}c"
            for p in table.players.values()
        ]
        embed.add_field(name="Players", value="\n".join(player_lines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet \u2014 click Join!*",
            inline=False,
        )

    embed.add_field(
        name="\u2139\ufe0f How payouts work",
        value=(
            "\u2022 \u2705 = underbet relative to win chance (pool odds > fair odds)\n"
            "\u2022 \u26a0\ufe0f = overbet / crowded (pool odds < fair odds)\n"
            "\u2022 Pool now updates live as players join\n"
            "\u2022 Actual payout locks in when the race starts"
        ),
        inline=False,
    )

    embed.set_footer(
        text=f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} players \u2502 Horse 1-6 in modal",
    )
    return embed


def _racing_embed(table: HorseRaceTable) -> discord.Embed:
    embed = discord.Embed(
        title=f"\U0001f3c7 Horse Race \u2014 Round {table.round_num}",
        colour=discord.Colour.gold(),
    )
    embed.description = _render_track(table.horse_positions, table.tick, table.horse_probs)

    # Show bets by horse (compact)
    bet_lines: list[str] = []
    for i in range(NUM_HORSES):
        bettors = [
            f"{p.display_name} ({p.bet}c)"
            for p in table.players.values()
            if p.horse_index == i
        ]
        if bettors:
            bet_lines.append(f"{_horse_short(i)} {HORSES[i][0]}: {', '.join(bettors)}")
    if bet_lines:
        embed.add_field(name="Bets", value="\n".join(bet_lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _finished_embed(
    table: HorseRaceTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    # Winner horse(s)
    winning_names = [_horse_label(i) for i in table.winners]
    if len(winning_names) == 1:
        winner_text = f"{winning_names[0]} crosses the finish line!"
    else:
        winner_text = f"{' & '.join(winning_names)} cross the finish line together!"

    embed = discord.Embed(
        title=f"\U0001f3c7 Horse Race \u2014 Finish! (Round {table.round_num})",
        description=f"\U0001f3c6 **{winner_text}**",
        colour=discord.Colour.gold(),
    )

    # Show final track
    embed.add_field(
        name="Final Track",
        value=_render_track(table.horse_positions, table.tick, table.horse_probs),
        inline=False,
    )

    # Results per player
    lines: list[str] = []
    for p in table.players.values():
        bal = balances.get(p.user_id, 0) if balances else 0
        net = p.payout - p.bet
        sign = "+" if net >= 0 else ""
        horse = _horse_label(p.horse_index)
        if p.won:
            actual_mult = p.payout / p.bet if p.bet > 0 else 1.0
            lines.append(
                f"\U0001f3c6 **{p.display_name}** ({horse}) \u2014 "
                f"{p.bet}c \u2192 {p.payout}c (**{sign}{net}c**, {actual_mult:.2f}x) \u2014 bal: {bal}c"
            )
        else:
            lines.append(
                f"\u274c **{p.display_name}** ({horse}) \u2014 "
                f"{p.bet}c \u2192 0c (**-{p.bet}c**) \u2014 bal: {bal}c"
            )
    if lines:
        embed.add_field(name="Results", value="\n".join(lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modal ────────────────────────────────────────────────────────────────────


class JoinRaceModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )
    horse_num = ui.TextInput(
        label="Horse number (1-6)",
        placeholder="1=Thunderbolt 2=Lightning 3=Storm 4=Blaze 5=Shadow 6=Phoenix",
        required=True,
        max_length=1,
    )

    def __init__(
        self, table: HorseRaceTable, view: "HorseRaceTableView", balance: int,
    ) -> None:
        super().__init__(title="Join Horse Race")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 100 (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Validate bet amount
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message(
                "Enter a whole number for bet.", ephemeral=True,
            )
            return
        if amt < 1:
            await interaction.response.send_message(
                "Must be at least 1 coin.", ephemeral=True,
            )
            return
        # Validate horse selection
        try:
            horse_idx = int(self.horse_num.value) - 1
        except ValueError:
            await interaction.response.send_message(
                "Enter a number 1-6 for your horse.", ephemeral=True,
            )
            return
        if horse_idx < 0 or horse_idx >= NUM_HORSES:
            await interaction.response.send_message(
                "Horse number must be 1-6.", ephemeral=True,
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

        self.table.players[uid] = HorseRacePlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
            horse_index=horse_idx,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


# ── View ─────────────────────────────────────────────────────────────────────


class HorseRaceTableView(ui.View):
    def __init__(
        self, table: HorseRaceTable, active_tables: dict[int, HorseRaceTable],
    ) -> None:
        super().__init__(timeout=300)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        racing = phase == "racing"
        finished = phase == "finished"

        # Row 0
        self.start_btn.disabled = (
            not betting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = racing

        # Row 1
        self.new_round_btn.disabled = not finished
        self.close_btn.disabled = racing

    # ── Row 0 ────────────────────────────────────────────────────────────────

    @ui.button(
        label="Start", style=discord.ButtonStyle.success, emoji="\u25b6\ufe0f", row=0,
    )
    async def start_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can start!", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Already started!", ephemeral=True,
            )
            return
        if len(self.table.players) < MIN_PLAYERS:
            await interaction.response.send_message(
                f"Need at least {MIN_PLAYERS} players!", ephemeral=True,
            )
            return
        await self._start_race(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f3c7", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Race in progress! Wait for the next round.", ephemeral=True,
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
        await interaction.response.send_modal(
            JoinRaceModal(self.table, self, bal),
        )

    @ui.button(
        label="Re-bet", style=discord.ButtonStyle.primary, emoji="\U0001f504", row=0,
    )
    async def rebet_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Race in progress!", ephemeral=True,
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
                "No previous bet \u2014 use Join instead.", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message(
                "Table is full!", ephemeral=True,
            )
            return
        name, amt, horse_idx = last
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for {amt}c re-bet! (have {bal}c)",
                ephemeral=True,
            )
            return
        self.table.players[uid] = HorseRacePlayer(
            user_id=uid, display_name=name, bet=amt, horse_index=horse_idx,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=0,
    )
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
        if self.table.phase == "racing":
            await interaction.response.send_message(
                "Can't leave mid-race!", ephemeral=True,
            )
            return
        if self.table.phase == "betting":
            await queries.update_casino_balance(str(uid), player.bet)
            del self.table.players[uid]
            self._update_buttons()
            await interaction.response.edit_message(
                embed=_betting_embed(self.table), view=self,
            )
            return
        await interaction.response.send_message(
            "Round is over. Wait for New Round or close.", ephemeral=True,
        )

    # ── Row 1 ────────────────────────────────────────────────────────────────

    @ui.button(
        label="New Round", style=discord.ButtonStyle.success, emoji="\u25b6\ufe0f",
        row=1,
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
        label="Close Table", style=discord.ButtonStyle.danger, emoji="\u2716\ufe0f",
        row=1,
    )
    async def close_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can close the table!", ephemeral=True,
            )
            return
        if self.table.phase == "racing":
            await interaction.response.send_message(
                "Can't close mid-race!", ephemeral=True,
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

    async def _start_race(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "racing"
        table.horse_positions = [0] * NUM_HORSES
        table.tick = 0
        table.winners = []

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_racing_embed(table), view=self,
        )
        table.race_task = asyncio.create_task(self._race_loop())

    async def _race_loop(self) -> None:
        table = self.table
        try:
            while True:
                await asyncio.sleep(TICK_INTERVAL)
                table.tick += 1

                # Advance each horse — weighted by win probability
                for i in range(NUM_HORSES):
                    # speed factor: prob * NUM_HORSES → 1.0 for average horse
                    s = table.horse_probs[i] * NUM_HORSES
                    w0 = max(0.1, 2.0 - s)  # weight for 0 steps (high for longshots)
                    w3 = max(0.1, s)          # weight for 3 steps (high for favorites)
                    weights = [w0, 1.0, 1.0, w3]
                    table.horse_positions[i] += random.choices(
                        [0, 1, 2, 3], weights=weights,
                    )[0]

                # Check for finishers
                finishers = [
                    i for i in range(NUM_HORSES)
                    if table.horse_positions[i] >= TRACK_LENGTH
                ]

                if finishers:
                    table.winners = finishers
                    await self._resolve_win()
                    return

                # Update display
                if table.message:
                    try:
                        await table.message.edit(
                            embed=_racing_embed(table), view=self,
                        )
                    except discord.HTTPException:
                        pass

        except asyncio.CancelledError:
            pass
        except Exception:
            if table.phase == "racing":
                table.phase = "finished"
                await self._refund_all()

    async def _resolve_win(self) -> None:
        table = self.table
        table.phase = "finished"

        # Pari-mutuel payout: winners split the total pool proportional to their bet.
        # For ties, the denominator is the combined pool of all winning horses so
        # total payouts never exceed total_pool (zero-sum regardless of tie count).
        # payout_i = bet_i × (total_pool / winning_pool)
        total_pool = sum(p.bet for p in table.players.values())
        horse_pool: dict[int, int] = {i: 0 for i in range(NUM_HORSES)}
        for p in table.players.values():
            horse_pool[p.horse_index] += p.bet

        winning_pool = sum(horse_pool[w] for w in table.winners if horse_pool[w] > 0)

        for p in table.players.values():
            if p.horse_index in table.winners:
                p.won = True
                p.payout = int(p.bet * total_pool / winning_pool) if winning_pool > 0 else p.bet

        # Credit winners and log results
        balances: dict[int, int] = {}
        for uid, player in table.players.items():
            if player.won and player.payout > 0:
                balances[uid] = await queries.update_casino_balance(
                    str(uid), player.payout,
                )
            else:
                bal = await queries.get_casino_balance(str(uid))
                balances[uid] = bal or 0
            # Log casino result for every player
            await queries.log_casino_result(
                str(uid), "horserace", player.bet, player.payout,
            )

        # Save last bets (name, amount, horse_index)
        for uid, player in table.players.items():
            table.last_bets[uid] = (
                player.display_name, player.bet, player.horse_index,
            )

        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(
                    embed=_finished_embed(table, balances=balances), view=self,
                )
            except discord.HTTPException:
                pass

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _start_new_round(self) -> None:
        table = self.table
        table.players.clear()
        table.phase = "betting"
        table.round_num += 1
        table.horse_probs = _generate_horse_probs()
        table.horse_positions = [0] * NUM_HORSES
        table.tick = 0
        table.winners.clear()
        table.race_task = None

    async def _refund_all(self) -> None:
        for p in self.table.players.values():
            try:
                await queries.update_casino_balance(str(p.user_id), p.bet)
            except Exception:
                pass

    async def _close(
        self, interaction: discord.Interaction, reason: str,
    ) -> None:
        embed = discord.Embed(
            title="\U0001f3c7 Horse Race Table \u2014 Closed",
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

        if table.race_task and not table.race_task.done():
            table.race_task.cancel()

        if table.phase == "finished":
            self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    embed = discord.Embed(
                        title="\U0001f3c7 Horse Race Table \u2014 Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    pass
            return

        # Betting or racing — refund all
        await self._refund_all()
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="\U0001f3c7 Horse Race Table \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class HorseRaceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, HorseRaceTable] = {}

    @app_commands.command(
        name="horserace", description="Open a Horse Race table (multiplayer)",
    )
    async def horserace(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a Horse Race table in this channel!",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = HorseRaceTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = HorseRaceTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HorseRaceCog(bot))
