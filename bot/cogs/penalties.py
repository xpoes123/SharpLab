"""Casino cog — 1v1 /penalties (Penalty Shootout) duel game."""

import asyncio
import random
import time
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
import logging

log = logging.getLogger(__name__)
# ── Constants ────────────────────────────────────────────────────────────────

SHOT_CLOCK = 30  # seconds per pick
KICKS_PER_PLAYER = 5
RESULT_PAUSE = 3  # seconds to show kick result before advancing

ZONES = ["Top Left", "Top Center", "Top Right", "Bottom Left", "Bottom Center", "Bottom Right"]
ZONE_EMOJI = ["\u2196\ufe0f", "\u2b06\ufe0f", "\u2197\ufe0f", "\u2199\ufe0f", "\u2b07\ufe0f", "\u2198\ufe0f"]
ZONE_SHORT = ["\u2196", "\u2b06", "\u2197", "\u2199", "\u2b07", "\u2198"]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _goal_grid(shoot_zone: int, keeper_zone: int) -> str:
    """Render a 3x2 visual grid showing where the ball went and keeper dove."""
    cells = ["\u2b1c"] * 6  # all empty
    if shoot_zone == keeper_zone:
        cells[shoot_zone] = "\U0001f6d1"  # save
    else:
        cells[shoot_zone] = "\u26bd"  # ball
        cells[keeper_zone] = "\U0001f9e4"  # keeper
    top = " ".join(cells[:3])
    bot = " ".join(cells[3:])
    return f"{top}\n{bot}"


def _kick_history_line(kicks: list[dict], player_id: int, max_kicks: int) -> str:
    """Build a kick history line like: 🟢🟢🔴⬜⬜"""
    parts: list[str] = []
    for k in kicks:
        if k["shooter_id"] != player_id:
            continue
        if k["goal"]:
            parts.append("\U0001f7e2")  # green = goal
        else:
            parts.append("\U0001f534")  # red = saved
    # Fill remaining with grey
    remaining = max_kicks - len(parts)
    parts.extend(["\u2b1c"] * remaining)
    return " ".join(parts)


def _is_decided(scores: dict[int, int], kicks_taken: dict[int, int],
                max_kicks: int, player_ids: list[int]) -> bool:
    """Check if the shootout is mathematically decided."""
    a, b = player_ids
    remaining_a = max_kicks - kicks_taken[a]
    remaining_b = max_kicks - kicks_taken[b]
    if scores[a] + remaining_a < scores[b]:
        return True
    if scores[b] + remaining_b < scores[a]:
        return True
    return False


def _sudden_death_decided(scores: dict[int, int], kicks_taken: dict[int, int],
                          player_ids: list[int]) -> bool:
    """In sudden death, check after both players have kicked in a pair."""
    a, b = player_ids
    # Only check after an even number of SD kicks (both have kicked)
    if kicks_taken[a] != kicks_taken[b]:
        return False
    return scores[a] != scores[b]


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class Kick:
    shooter_id: int
    keeper_id: int
    shoot_zone: int | None = None
    keeper_zone: int | None = None
    goal: bool | None = None


@dataclass
class PKDuel:
    channel_id: int
    challenger_id: int
    opponent_id: int
    challenger_name: str
    opponent_name: str
    bet: int
    phase: str = "pending"  # pending | playing | finished
    kicks: list[Kick] = field(default_factory=list)
    current_kick: int = 0
    scores: dict[int, int] = field(default_factory=dict)
    kicks_taken: dict[int, int] = field(default_factory=dict)
    sudden_death: bool = False
    message: discord.Message | None = None
    shot_clock_expires: float | None = None

    @property
    def player_ids(self) -> list[int]:
        return [self.challenger_id, self.opponent_id]

    def player_name(self, uid: int) -> str:
        if uid == self.challenger_id:
            return self.challenger_name
        return self.opponent_name

    def current_round(self) -> int:
        """1-indexed round number (each round = 2 kicks)."""
        return (self.current_kick // 2) + 1


# ── Embeds ───────────────────────────────────────────────────────────────────


def _pending_embed(duel: PKDuel) -> discord.Embed:
    embed = discord.Embed(
        title="\u26bd Penalty Shootout \u2014 Challenge!",
        description=(
            f"**{duel.challenger_name}** challenges **{duel.opponent_name}** "
            f"to a Penalty Shootout for **{duel.bet}c**!\n\n"
            f"Pot: **{duel.bet * 2}c**\n"
            f"5 rounds of alternating kicks. Tied? Sudden death."
        ),
        colour=discord.Colour.green(),
    )
    embed.set_footer(text=f"Waiting for {duel.opponent_name} to accept or decline")
    return embed


def _playing_embed(duel: PKDuel) -> discord.Embed:
    kick = duel.kicks[duel.current_kick]
    shooter_name = duel.player_name(kick.shooter_id)
    keeper_name = duel.player_name(kick.keeper_id)

    s_c = duel.scores[duel.challenger_id]
    s_o = duel.scores[duel.opponent_id]

    if duel.sudden_death:
        title = f"\u26bd Penalty Shootout \u2014 Sudden Death"
    else:
        title = f"\u26bd Penalty Shootout \u2014 Round {duel.current_round()}/{KICKS_PER_PLAYER}"

    embed = discord.Embed(title=title, colour=discord.Colour.orange())

    # Score line
    embed.description = f"**{duel.challenger_name}** {s_c} - {s_o} **{duel.opponent_name}**"

    # Kick info
    ready_shoot = "\u2705" if kick.shoot_zone is not None else "\u23f3"
    ready_keep = "\u2705" if kick.keeper_zone is not None else "\u23f3"
    embed.description += (
        f"\n\nKick #{duel.current_kick + 1}: "
        f"\u26bd {shooter_name} {ready_shoot}  vs  \U0001f9e4 {keeper_name} {ready_keep}"
    )

    # Shot clock
    if duel.shot_clock_expires:
        ts = int(duel.shot_clock_expires)
        embed.description += f"\n\u23f0 <t:{ts}:R>"

    # Kick history
    kick_log = _kick_history_data(duel)
    max_display = KICKS_PER_PLAYER if not duel.sudden_death else duel.kicks_taken[duel.challenger_id] + 1
    c_line = _kick_history_line(kick_log, duel.challenger_id, max_display)
    o_line = _kick_history_line(kick_log, duel.opponent_id, max_display)
    embed.add_field(
        name="Shootout",
        value=f"{c_line}  **{duel.challenger_name}**\n{o_line}  **{duel.opponent_name}**",
        inline=False,
    )

    return embed


def _result_embed(duel: PKDuel, kick: Kick) -> discord.Embed:
    """Embed shown briefly after a kick resolves."""
    shooter_name = duel.player_name(kick.shooter_id)
    keeper_name = duel.player_name(kick.keeper_id)

    s_c = duel.scores[duel.challenger_id]
    s_o = duel.scores[duel.opponent_id]

    if duel.sudden_death:
        title = f"\u26bd Penalty Shootout \u2014 Sudden Death"
    else:
        title = f"\u26bd Penalty Shootout \u2014 Round {duel.current_round()}/{KICKS_PER_PLAYER}"

    embed = discord.Embed(title=title, colour=discord.Colour.orange())

    # Score
    embed.description = f"**{duel.challenger_name}** {s_c} - {s_o} **{duel.opponent_name}**\n\n"

    # Grid
    grid = _goal_grid(kick.shoot_zone, kick.keeper_zone)
    if kick.goal:
        verdict = f"**GOAL!** \u26bd {shooter_name} scores!"
    else:
        verdict = f"**SAVE!** \U0001f9e4 {keeper_name} stops it!"
    embed.description += f"{grid}\n\n{verdict}"

    # Kick history
    kick_log = _kick_history_data(duel)
    max_display = KICKS_PER_PLAYER if not duel.sudden_death else max(duel.kicks_taken[duel.challenger_id], duel.kicks_taken[duel.opponent_id])
    c_line = _kick_history_line(kick_log, duel.challenger_id, max_display)
    o_line = _kick_history_line(kick_log, duel.opponent_id, max_display)
    embed.add_field(
        name="Shootout",
        value=f"{c_line}  **{duel.challenger_name}**\n{o_line}  **{duel.opponent_name}**",
        inline=False,
    )

    return embed


def _finished_embed(duel: PKDuel, winner_id: int, payout: int,
                    balances: dict[int, int]) -> discord.Embed:
    s_c = duel.scores[duel.challenger_id]
    s_o = duel.scores[duel.opponent_id]
    winner_name = duel.player_name(winner_id)

    embed = discord.Embed(
        title="\u26bd Penalty Shootout \u2014 Final Result",
        description=(
            f"**{duel.challenger_name}** {s_c} - {s_o} **{duel.opponent_name}**\n\n"
            f"\U0001f3c6 **{winner_name}** wins **{payout}c**!"
        ),
        colour=discord.Colour.gold(),
    )

    # Kick history
    kick_log = _kick_history_data(duel)
    max_display = max(duel.kicks_taken[duel.challenger_id], duel.kicks_taken[duel.opponent_id])
    c_line = _kick_history_line(kick_log, duel.challenger_id, max_display)
    o_line = _kick_history_line(kick_log, duel.opponent_id, max_display)
    embed.add_field(
        name="Shootout",
        value=f"{c_line}  **{duel.challenger_name}**\n{o_line}  **{duel.opponent_name}**",
        inline=False,
    )

    # Payouts
    loser_id = duel.opponent_id if winner_id == duel.challenger_id else duel.challenger_id
    loser_name = duel.player_name(loser_id)
    lines = [
        f"\U0001f3c6 **{winner_name}** \u2014 {duel.bet}c \u2192 {payout}c "
        f"(**+{payout - duel.bet}c**) \u2014 bal: {balances.get(winner_id, 0)}c",
        f"\u274c **{loser_name}** \u2014 {duel.bet}c \u2192 0c "
        f"(**-{duel.bet}c**) \u2014 bal: {balances.get(loser_id, 0)}c",
    ]
    embed.add_field(name="Results", value="\n".join(lines), inline=False)

    if duel.sudden_death:
        embed.set_footer(text="Decided in sudden death!")

    return embed


def _kick_history_data(duel: PKDuel) -> list[dict]:
    """Return resolved kicks as simple dicts for history rendering."""
    result: list[dict] = []
    for k in duel.kicks:
        if k.goal is not None:
            result.append({
                "shooter_id": k.shooter_id,
                "goal": k.goal,
            })
    return result


# ── Zone Picker View (ephemeral) ────────────────────────────────────────────


class ZonePickerView(ui.View):
    """Ephemeral 3x2 grid for choosing a zone."""

    def __init__(self, duel_view: "PKDuelView", role: str) -> None:
        super().__init__(timeout=SHOT_CLOCK + 5)
        self.duel_view = duel_view
        self.role = role  # "shoot" or "dive"
        for i in range(6):
            row = 0 if i < 3 else 1
            btn = ui.Button(
                label=ZONES[i],
                emoji=ZONE_EMOJI[i],
                style=discord.ButtonStyle.primary,
                row=row,
                custom_id=f"zone_{i}",
            )
            btn.callback = self._make_callback(i)
            self.add_item(btn)

    def _make_callback(self, zone: int):
        async def callback(interaction: discord.Interaction) -> None:
            await self._pick_zone(interaction, zone)
        return callback

    async def _pick_zone(self, interaction: discord.Interaction, zone: int) -> None:
        # Disable all buttons
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()

        chosen = ZONE_EMOJI[zone]
        if self.role == "shoot":
            await interaction.response.edit_message(
                content=f"\u2705 Locked in! Shooting {chosen} {ZONES[zone]}",
                view=self,
            )
        else:
            await interaction.response.edit_message(
                content=f"\u2705 Locked in! Diving {chosen} {ZONES[zone]}",
                view=self,
            )

        # Register the pick on the duel view
        await self.duel_view._register_pick(self.role, zone)


# ── Main Duel View ──────────────────────────────────────────────────────────


class PKDuelView(ui.View):
    def __init__(self, duel: PKDuel, active_duels: dict[int, "PKDuel"]) -> None:
        super().__init__(timeout=300)
        self.duel = duel
        self.active_duels = active_duels
        self._shot_clock_task: asyncio.Task | None = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        pending = self.duel.phase == "pending"
        playing = self.duel.phase == "playing"
        finished = self.duel.phase == "finished"

        self.accept_btn.disabled = not pending
        self.decline_btn.disabled = not pending
        self.shoot_btn.disabled = not playing
        self.dive_btn.disabled = not playing
        self.score_btn.disabled = not playing

        # Hide pending buttons during play and vice versa
        self.accept_btn.row = 0 if pending else None  # type: ignore[assignment]
        self.decline_btn.row = 0 if pending else None  # type: ignore[assignment]
        self.shoot_btn.row = 0 if playing else None  # type: ignore[assignment]
        self.dive_btn.row = 0 if playing else None  # type: ignore[assignment]
        self.score_btn.row = 0 if playing else None  # type: ignore[assignment]

        if finished:
            for child in self.children:
                child.disabled = True  # type: ignore[union-attr]

    # ── Shot clock ────────────────────────────────────────────────────────

    def _start_shot_clock(self) -> None:
        self._cancel_shot_clock()
        self.duel.shot_clock_expires = time.time() + SHOT_CLOCK
        self._shot_clock_task = asyncio.create_task(self._shot_clock_coro())

    def _cancel_shot_clock(self) -> None:
        if self._shot_clock_task and not self._shot_clock_task.done():
            self._shot_clock_task.cancel()
        self._shot_clock_task = None
        self.duel.shot_clock_expires = None

    async def _shot_clock_coro(self) -> None:
        try:
            await asyncio.sleep(SHOT_CLOCK)
        except asyncio.CancelledError:
            return
        if self.duel.phase != "playing":
            return
        self.duel.shot_clock_expires = None
        await self._handle_timeout()

    async def _handle_timeout(self) -> None:
        """Auto-pick random zones for anyone who hasn't chosen."""
        kick = self.duel.kicks[self.duel.current_kick]
        if kick.shoot_zone is None:
            kick.shoot_zone = random.randint(0, 5)
        if kick.keeper_zone is None:
            kick.keeper_zone = random.randint(0, 5)
        await self._resolve_kick()

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _edit_msg(self, interaction: discord.Interaction | None, **kwargs) -> None:
        if interaction:
            await interaction.response.edit_message(**kwargs)
        elif self.duel.message:
            await self.duel.message.edit(**kwargs)

    # ── Pending phase buttons ─────────────────────────────────────────────

    @ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="\u2705", row=0)
    async def accept_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.duel.opponent_id:
            await interaction.response.send_message("Not your challenge!", ephemeral=True)
            return
        if self.duel.phase != "pending":
            await interaction.response.send_message("Already started!", ephemeral=True)
            return

        # Deduct opponent's coins
        try:
            await queries.update_casino_balance(str(self.duel.opponent_id), -self.duel.bet)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(self.duel.opponent_id))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        await self._start_game(interaction)

    @ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="\u274c", row=0)
    async def decline_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.duel.opponent_id:
            await interaction.response.send_message("Not your challenge!", ephemeral=True)
            return
        if self.duel.phase != "pending":
            await interaction.response.send_message("Already started!", ephemeral=True)
            return

        # Refund challenger
        await queries.update_casino_balance(str(self.duel.challenger_id), self.duel.bet)
        await self._close(interaction, f"{self.duel.opponent_name} declined the challenge.")

    # ── Playing phase buttons ─────────────────────────────────────────────

    @ui.button(label="Shoot", style=discord.ButtonStyle.success, emoji="\u26bd", row=0)
    async def shoot_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.duel.phase != "playing":
            await interaction.response.send_message("No game in progress.", ephemeral=True)
            return
        kick = self.duel.kicks[self.duel.current_kick]
        if interaction.user.id != kick.shooter_id:
            await interaction.response.send_message("You're not the shooter!", ephemeral=True)
            return
        if kick.shoot_zone is not None:
            await interaction.response.send_message("You already picked!", ephemeral=True)
            return

        picker = ZonePickerView(self, "shoot")
        await interaction.response.send_message(
            "\u26bd **Pick where to shoot:**", view=picker, ephemeral=True,
        )

    @ui.button(label="Dive", style=discord.ButtonStyle.primary, emoji="\U0001f9e4", row=0)
    async def dive_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.duel.phase != "playing":
            await interaction.response.send_message("No game in progress.", ephemeral=True)
            return
        kick = self.duel.kicks[self.duel.current_kick]
        if interaction.user.id != kick.keeper_id:
            await interaction.response.send_message("You're not the keeper!", ephemeral=True)
            return
        if kick.keeper_zone is not None:
            await interaction.response.send_message("You already picked!", ephemeral=True)
            return

        picker = ZonePickerView(self, "dive")
        await interaction.response.send_message(
            "\U0001f9e4 **Pick where to dive:**", view=picker, ephemeral=True,
        )

    @ui.button(label="Score", style=discord.ButtonStyle.secondary, emoji="\U0001f4ca", row=0)
    async def score_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        duel = self.duel
        s_c = duel.scores[duel.challenger_id]
        s_o = duel.scores[duel.opponent_id]

        kick_log = _kick_history_data(duel)
        max_display = max(duel.kicks_taken[duel.challenger_id], duel.kicks_taken[duel.opponent_id], 1)
        c_line = _kick_history_line(kick_log, duel.challenger_id, max_display)
        o_line = _kick_history_line(kick_log, duel.opponent_id, max_display)

        text = (
            f"**{duel.challenger_name}** {s_c} - {s_o} **{duel.opponent_name}**\n\n"
            f"{c_line}  {duel.challenger_name}\n"
            f"{o_line}  {duel.opponent_name}"
        )
        await interaction.response.send_message(text, ephemeral=True)

    # ── Game logic ────────────────────────────────────────────────────────

    async def _start_game(self, interaction: discord.Interaction) -> None:
        duel = self.duel
        duel.phase = "playing"
        duel.scores = {duel.challenger_id: 0, duel.opponent_id: 0}
        duel.kicks_taken = {duel.challenger_id: 0, duel.opponent_id: 0}

        # Create first kick
        self._setup_next_kick()

        self._start_shot_clock()
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(duel), view=self,
        )

    def _setup_next_kick(self) -> None:
        """Create and append the next kick based on current_kick index."""
        duel = self.duel
        idx = duel.current_kick
        # Even = challenger shoots, odd = opponent shoots
        if idx % 2 == 0:
            shooter = duel.challenger_id
            keeper = duel.opponent_id
        else:
            shooter = duel.opponent_id
            keeper = duel.challenger_id

        kick = Kick(shooter_id=shooter, keeper_id=keeper)
        if len(duel.kicks) <= idx:
            duel.kicks.append(kick)
        else:
            duel.kicks[idx] = kick

    async def _register_pick(self, role: str, zone: int) -> None:
        """Called by ZonePickerView when a player locks in a zone."""
        duel = self.duel
        if duel.phase != "playing":
            return
        kick = duel.kicks[duel.current_kick]

        if role == "shoot":
            if kick.shoot_zone is not None:
                return  # already picked (race condition guard)
            kick.shoot_zone = zone
        else:
            if kick.keeper_zone is not None:
                return
            kick.keeper_zone = zone

        # Update main embed to show ready indicators
        if kick.shoot_zone is not None and kick.keeper_zone is not None:
            # Both picked — resolve
            self._cancel_shot_clock()
            await self._resolve_kick()
        elif duel.message:
            # One picked, update the embed to show ready status
            try:
                await duel.message.edit(embed=_playing_embed(duel), view=self)
            except discord.HTTPException:
                pass

    async def _resolve_kick(self) -> None:
        """Resolve the current kick and advance."""
        duel = self.duel
        kick = duel.kicks[duel.current_kick]

        # Determine result
        kick.goal = kick.shoot_zone != kick.keeper_zone
        if kick.goal:
            duel.scores[kick.shooter_id] += 1
        duel.kicks_taken[kick.shooter_id] += 1

        # Show result
        result_emb = _result_embed(duel, kick)
        self._update_buttons()
        if duel.message:
            try:
                await duel.message.edit(embed=result_emb, view=self)
            except discord.HTTPException:
                pass

        # Check if game is decided
        if not duel.sudden_death:
            # Standard play
            if _is_decided(duel.scores, duel.kicks_taken, KICKS_PER_PLAYER, duel.player_ids):
                await asyncio.sleep(RESULT_PAUSE)
                await self._finish_game()
                return

            # Check if standard kicks are done
            total_kicks = duel.kicks_taken[duel.challenger_id] + duel.kicks_taken[duel.opponent_id]
            if total_kicks >= KICKS_PER_PLAYER * 2:
                if duel.scores[duel.challenger_id] != duel.scores[duel.opponent_id]:
                    # Someone won
                    await asyncio.sleep(RESULT_PAUSE)
                    await self._finish_game()
                    return
                else:
                    # Tied — enter sudden death
                    duel.sudden_death = True
        else:
            # Sudden death — check after both have kicked in this SD pair
            if _sudden_death_decided(duel.scores, duel.kicks_taken, duel.player_ids):
                await asyncio.sleep(RESULT_PAUSE)
                await self._finish_game()
                return

        # Advance to next kick
        await asyncio.sleep(RESULT_PAUSE)
        duel.current_kick += 1
        self._setup_next_kick()
        self._start_shot_clock()
        self._update_buttons()
        if duel.message:
            try:
                await duel.message.edit(embed=_playing_embed(duel), view=self)
            except discord.HTTPException:
                pass

    async def _finish_game(self) -> None:
        duel = self.duel
        duel.phase = "finished"
        self._cancel_shot_clock()

        # Determine winner
        if duel.scores[duel.challenger_id] > duel.scores[duel.opponent_id]:
            winner_id = duel.challenger_id
        elif duel.scores[duel.opponent_id] > duel.scores[duel.challenger_id]:
            winner_id = duel.opponent_id
        else:
            # True draw (shouldn't happen with SD, but safety: refund both)
            await queries.update_casino_balance(str(duel.challenger_id), duel.bet)
            await queries.update_casino_balance(str(duel.opponent_id), duel.bet)
            await queries.log_casino_result(str(duel.challenger_id), "penalties", duel.bet, duel.bet)
            await queries.log_casino_result(str(duel.opponent_id), "penalties", duel.bet, duel.bet)
            embed = discord.Embed(
                title="\u26bd Penalty Shootout \u2014 Draw!",
                description="Somehow a draw! Both players refunded.",
                colour=discord.Colour.greyple(),
            )
            self._update_buttons()
            self.active_duels.pop(duel.channel_id, None)
            if duel.message:
                await duel.message.edit(embed=embed, view=self)
            self.stop()
            return

        # Payout
        total_pot = duel.bet * 2
        payout = total_pot
        loser_id = duel.opponent_id if winner_id == duel.challenger_id else duel.challenger_id

        balances: dict[int, int] = {}
        balances[winner_id] = await queries.update_casino_balance(str(winner_id), payout)
        bal = await queries.get_casino_balance(str(loser_id))
        balances[loser_id] = bal or 0

        await queries.log_casino_result(str(winner_id), "penalties", duel.bet, payout)
        await queries.log_casino_result(str(loser_id), "penalties", duel.bet, 0)

        embed = _finished_embed(duel, winner_id, payout, balances)
        self._update_buttons()
        self.active_duels.pop(duel.channel_id, None)
        if duel.message:
            await duel.message.edit(embed=embed, view=self)
        self.stop()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def _close(self, interaction: discord.Interaction, reason: str) -> None:
        self._cancel_shot_clock()
        embed = discord.Embed(
            title="\u26bd Penalty Shootout \u2014 Cancelled",
            description=reason,
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_duels.pop(self.duel.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        self._cancel_shot_clock()
        duel = self.duel

        if duel.phase == "pending":
            # Refund challenger
            try:
                await queries.update_casino_balance(str(duel.challenger_id), duel.bet)
            except Exception:
                log.exception("Unhandled error in penalties.py")
            self.active_duels.pop(duel.channel_id, None)
            if duel.message:
                try:
                    embed = discord.Embed(
                        title="\u26bd Penalty Shootout \u2014 Expired",
                        description=f"Challenge expired. {duel.challenger_name}'s coins refunded.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await duel.message.edit(embed=embed, view=None)
                except Exception:
                    log.exception("Unhandled error in penalties.py")
            return

        if duel.phase == "playing":
            # Refund both players (mid-game timeout shouldn't happen with shot clock, but safety)
            try:
                await queries.update_casino_balance(str(duel.challenger_id), duel.bet)
                await queries.update_casino_balance(str(duel.opponent_id), duel.bet)
            except Exception:
                log.exception("Unhandled error in penalties.py")

        self.active_duels.pop(duel.channel_id, None)
        if duel.message:
            try:
                embed = discord.Embed(
                    title="\u26bd Penalty Shootout \u2014 Timed Out",
                    description="Game timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await duel.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("Unhandled error in penalties.py")


# ── Cog ──────────────────────────────────────────────────────────────────────


class PenaltiesCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_duels: dict[int, PKDuel] = {}

    @app_commands.command(
        name="penalties",
        description="Challenge someone to a 1v1 Penalty Shootout!",
    )
    @app_commands.describe(
        opponent="Who to challenge",
        bet="Coin amount to wager (1-500)",
    )
    async def penalties(
        self, interaction: discord.Interaction,
        opponent: discord.User, bet: int,
    ) -> None:
        uid = interaction.user.id
        channel_id = interaction.channel_id

        # Validations
        if opponent.bot:
            await interaction.response.send_message("Can't challenge a bot!", ephemeral=True)
            return
        if opponent.id == uid:
            await interaction.response.send_message("Can't challenge yourself!", ephemeral=True)
            return
        if channel_id in self.active_duels:
            await interaction.response.send_message(
                "There's already a Penalty Shootout in this channel!", ephemeral=True,
            )
            return
        if bet < 1:
            await interaction.response.send_message("Bet must be at least 1c.", ephemeral=True)
            return
        # Deduct challenger's coins
        await queries.get_or_create_casino_wallet(str(uid))
        try:
            await queries.update_casino_balance(str(uid), -bet)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        # Ensure opponent has a wallet
        await queries.get_or_create_casino_wallet(str(opponent.id))

        duel = PKDuel(
            channel_id=channel_id,
            challenger_id=uid,
            opponent_id=opponent.id,
            challenger_name=interaction.user.display_name,
            opponent_name=opponent.display_name,
            bet=bet,
        )
        self.active_duels[channel_id] = duel

        view = PKDuelView(duel, self.active_duels)
        embed = _pending_embed(duel)
        await interaction.response.send_message(
            content=f"<@{opponent.id}>", embed=embed, view=view,
        )
        duel.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PenaltiesCog(bot))
