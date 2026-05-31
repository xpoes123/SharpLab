"""Clue Master — one player sees the secret and gives clues; others guess in thread.

Generic over categories (NBA by default). Each round a new clue master is
chosen; they reveal the answer to themselves via an ephemeral button and
type clues in the thread. First correct guess scores; clue master earns a
small bonus when their player solves it.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field

import discord
from discord import ui
from discord.ext import commands, tasks

from bot.cogs._elo_helpers import fmt_elo_change, update_elo_multiplayer
from bot.cogs._party_categories import (
    CATEGORIES, DEFAULT_CATEGORY, category_options, check_answer, get_category,
    refresh_science_answers,
)

log = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

ROUND_TIME = 90
ROUND_DELAY = 4
MIN_PLAYERS = 2  # need at least clue master + 1 guesser
MAX_PLAYERS = 8
DEFAULT_ROUNDS_PER_PLAYER = 2  # each player is clue master this many times
MAX_ROUNDS_CAP = 24  # safety ceiling on total rounds (e.g. 8 players × 3)
INACTIVITY_ROUNDS = 3
IDLE_REPLACEABLE_SECS = 90  # mirrors crash.py — replace stuck tables after 90s idle

GUESSER_POINTS = 3
CLUEMASTER_BONUS = 1

MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class CMPlayer:
    user_id: int
    display_name: str
    score: int = 0


@dataclass
class CMTable:
    channel_id: int
    host_id: int
    host_name: str
    category_key: str = DEFAULT_CATEGORY
    rounds_per_player: int = DEFAULT_ROUNDS_PER_PLAYER
    total_rounds: int = 0  # computed at game start: rounds_per_player × player count
    phase: str = "betting"  # betting | playing | between_rounds | closed
    players: dict[int, CMPlayer] = field(default_factory=dict)
    player_order: list[int] = field(default_factory=list)
    message: discord.Message | None = None
    thread: discord.Thread | None = None
    round_num: int = 0
    cluemaster_id: int | None = None
    current_item: tuple[str, list[str]] | None = None
    used_names: set[str] = field(default_factory=set)
    round_start_time: float = 0.0
    round_winner: int | None = None
    round_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    round_messages: list[discord.Message] = field(default_factory=list)
    game_task: asyncio.Task | None = field(default=None, repr=False)
    stop_requested: bool = False
    last_activity_at: float = field(default_factory=time.monotonic)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _compute_total_rounds(rounds_per_player: int, n_players: int) -> int:
    """Total rounds = each player is clue master rounds_per_player times,
    capped at MAX_ROUNDS_CAP so big tables don't run forever."""
    return min(MAX_ROUNDS_CAP, max(1, rounds_per_player * n_players))


def _pick_item(category_key: str, used: set[str]) -> tuple[str, list[str]]:
    _label, _emoji, items = get_category(category_key)
    pool = [it for it in items if it[0] not in used]
    if not pool:
        used.clear()
        pool = list(items)
    item = random.choice(pool)
    used.add(item[0])
    return item


def _scoreboard(table: CMTable) -> str:
    sorted_players = sorted(table.players.values(), key=lambda p: p.score, reverse=True)
    lines: list[str] = []
    for i, p in enumerate(sorted_players):
        prefix = MEDALS[i] if i < len(MEDALS) and p.score > 0 else "▪️"
        cm_tag = " \U0001f3af" if p.user_id == table.cluemaster_id else ""
        lines.append(f"{prefix} **{p.display_name}**{cm_tag} — {p.score} pts")
    return "\n".join(lines) or "*No players*"


def _category_label(key: str) -> str:
    label, emoji, _ = get_category(key)
    return f"{emoji} {label}"


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: CMTable) -> discord.Embed:
    n_players = len(table.players)
    total = table.rounds_per_player * n_players if n_players else table.rounds_per_player
    rounds_line = (
        f"**Rounds:** {table.rounds_per_player} per player"
        + (f" ({total} total)" if n_players else "")
    )
    embed = discord.Embed(
        title="\U0001f3af Clue Master",
        description=(
            f"**Category:** {_category_label(table.category_key)}\n"
            f"{rounds_line}\n\n"
            "Each round a player becomes the **Clue Master** and sees the "
            "answer privately. They type clues in the thread; everyone else "
            "races to guess. First correct guess scores 3 pts; clue master "
            "earns 1 pt when their player solves it."
        ),
        colour=discord.Colour.blurple(),
    )
    if table.players:
        lines = [
            f"\U0001f464 **{p.display_name}**"
            + (f" ({p.score} pts)" if p.score > 0 else "")
            for p in table.players.values()
        ]
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Players", value="*No players yet — click Join!*", inline=False)
    embed.set_footer(text=f"Host: {table.host_name} ┃ Min {MIN_PLAYERS} players")
    return embed


def _playing_embed(table: CMTable, remaining: int | None = None) -> discord.Embed:
    cm = table.players.get(table.cluemaster_id) if table.cluemaster_id else None
    cm_name = cm.display_name if cm else "?"
    secs = remaining if remaining is not None else ROUND_TIME
    embed = discord.Embed(
        title=f"\U0001f3af Clue Master — Round {table.round_num}/{table.total_rounds}",
        description=(
            f"**Clue Master:** {cm_name}\n"
            f"**Category:** {_category_label(table.category_key)}\n\n"
            f"\U0001f464 **Guessers:** type the answer in chat.\n"
            f"\U0001f3af **Clue Master:** type clues in chat (no proper nouns / no spelling out the name)."
        ),
        colour=discord.Colour.dark_blue(),
    )
    embed.add_field(name="⏱️ Time", value=f"**{secs}s**", inline=True)
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _round_result_embed(table: CMTable, solved: bool) -> discord.Embed:
    item = table.current_item or ("?", [])
    name = item[0]
    is_last = table.round_num >= table.total_rounds
    if solved and table.round_winner is not None:
        winner = table.players[table.round_winner]
        cm = table.players.get(table.cluemaster_id) if table.cluemaster_id else None
        cm_name = cm.display_name if cm else "?"
        embed = discord.Embed(
            title=f"\U0001f3af Round {table.round_num} ✅",
            description=(
                f"\U0001f3c6 **{winner.display_name}** got it for **{GUESSER_POINTS} pts**!\n"
                f"\U0001f3af Clue Master **{cm_name}** earns **{CLUEMASTER_BONUS} pt**.\n\n"
                f"It was **{name}**."
            ),
            colour=discord.Colour.green(),
        )
    else:
        embed = discord.Embed(
            title=f"\U0001f3af Round {table.round_num} (Time's Up!)",
            description=f"Nobody got it!\n\nIt was **{name}**.",
            colour=discord.Colour.dark_grey(),
        )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(
        text="Next round in a few seconds…" if not is_last else "Final round — calculating results…",
    )
    return embed


def _final_embed(
    table: CMTable, elo_changes: dict[int, tuple[float, float]] | None = None,
) -> discord.Embed:
    sorted_players = sorted(table.players.values(), key=lambda p: p.score, reverse=True)
    max_score = sorted_players[0].score if sorted_players else 0
    no_winner = max_score == 0

    embed = discord.Embed(
        title="\U0001f3af Clue Master — Results",
        colour=discord.Colour.gold() if not no_winner else discord.Colour.dark_grey(),
    )
    if no_winner:
        embed.description = "No rounds were won — game over!"
    else:
        winner = sorted_players[0]
        embed.description = (
            f"\U0001f3c6 **{winner.display_name}** wins with "
            f"**{winner.score}** point{'s' if winner.score != 1 else ''}!"
        )
    lines: list[str] = []
    for i, p in enumerate(sorted_players):
        medal = MEDALS[i] if i < len(MEDALS) and p.score > 0 else "▪️"
        lines.append(f"{medal} **{p.display_name}** — {p.score} pts")
    embed.add_field(name="Results", value="\n".join(lines), inline=False)
    embed.add_field(name="Rounds Played", value=str(table.round_num), inline=True)
    if elo_changes:
        elo_lines = [
            f"**{p.display_name}**: {fmt_elo_change(*elo_changes[p.user_id])}"
            for p in sorted_players if p.user_id in elo_changes
        ]
        if elo_lines:
            embed.add_field(name="\U0001f4c8 ELO", value="\n".join(elo_lines), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Selectors ────────────────────────────────────────────────────────────────


_ROUNDS_OPTIONS = [
    discord.SelectOption(label="1 round each", value="1", description="Each player is clue master once"),
    discord.SelectOption(label="2 rounds each", value="2", default=True, description="Each player is clue master twice"),
    discord.SelectOption(label="3 rounds each", value="3", description="Each player is clue master 3×"),
    discord.SelectOption(label="4 rounds each", value="4", description="Each player is clue master 4×"),
]


def _category_select_options(default_key: str) -> list[discord.SelectOption]:
    return [
        discord.SelectOption(label=label, value=value, emoji=emoji, default=is_def)
        for label, value, emoji, is_def in category_options(default_key)
    ]


# ── Reveal-answer view (per round) ───────────────────────────────────────────


class RevealAnswerView(ui.View):
    """Posted in-thread each round; only the clue master sees the answer."""

    def __init__(self, table: CMTable) -> None:
        super().__init__(timeout=ROUND_TIME + 30)
        self.table = table

    @ui.button(
        label="View Answer (Clue Master)", style=discord.ButtonStyle.primary,
        emoji="\U0001f3af", row=0,
    )
    async def reveal_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.cluemaster_id:
            await interaction.response.send_message(
                "You're not the clue master this round.", ephemeral=True,
            )
            return
        if self.table.current_item is None or self.table.phase != "playing":
            await interaction.response.send_message(
                "The round isn't active.", ephemeral=True,
            )
            return
        name, alts = self.table.current_item
        alt_text = f"\nAlso accepted: {', '.join(alts)}" if alts else ""
        await interaction.response.send_message(
            f"\U0001f3af **The answer is:** **{name}**{alt_text}\n\n"
            "Type clues in the thread. Don't say the name or spell it out.",
            ephemeral=True,
        )


# ── End-game button (always available in thread) ─────────────────────────────


class EndGameView(ui.View):
    def __init__(self, table: CMTable) -> None:
        super().__init__(timeout=None)
        self.table = table

    @ui.button(
        label="End Game", style=discord.ButtonStyle.danger,
        emoji="⏹️", row=0,
    )
    async def end_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase == "closed":
            await interaction.response.send_message(
                "The game has already ended.", ephemeral=True,
            )
            return
        if (
            interaction.user.id != self.table.host_id
            and interaction.user.id not in self.table.players
        ):
            await interaction.response.send_message(
                "Only players can end the game!", ephemeral=True,
            )
            return
        if self.table.stop_requested:
            await interaction.response.send_message(
                "Already ending…", ephemeral=True,
            )
            return
        self.table.stop_requested = True
        self.table.round_solved.set()
        self.table.last_activity_at = time.monotonic()
        button.disabled = True
        button.label = "Ending…"
        await interaction.response.edit_message(view=self)


# ── Lobby view ───────────────────────────────────────────────────────────────


class CluemasterView(ui.View):
    def __init__(self, table: CMTable, active_tables: dict[int, CMTable]) -> None:
        super().__init__(timeout=900)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        self.table.last_activity_at = time.monotonic()
        return True

    def _update_buttons(self) -> None:
        betting = self.table.phase == "betting"
        racing = self.table.phase in ("playing", "between_rounds")
        self.start_btn.disabled = not betting or len(self.table.players) < MIN_PLAYERS
        self.join_btn.disabled = not betting
        self.leave_btn.disabled = not betting
        self.close_btn.disabled = racing
        self.rounds_select.disabled = not betting
        self.category_select.disabled = not betting

    @ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶️", row=0)
    async def start_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message("Only the host can start!", ephemeral=True)
            return
        if self.table.phase != "betting":
            await interaction.response.send_message("Already started!", ephemeral=True)
            return
        if len(self.table.players) < MIN_PLAYERS:
            await interaction.response.send_message(
                f"Need at least {MIN_PLAYERS} players!", ephemeral=True,
            )
            return
        await self._start_game(interaction)

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f3af", row=0)
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Game in progress!", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        self.table.players[uid] = CMPlayer(
            user_id=uid, display_name=interaction.user.display_name,
        )
        self._update_buttons()
        await interaction.response.edit_message(embed=_betting_embed(self.table), view=self)

    @ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=0)
    async def leave_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if self.table.phase != "betting":
            await interaction.response.send_message("Can't leave mid-game!", ephemeral=True)
            return
        if uid not in self.table.players:
            await interaction.response.send_message("You're not in!", ephemeral=True)
            return
        del self.table.players[uid]
        self._update_buttons()
        await interaction.response.edit_message(embed=_betting_embed(self.table), view=self)

    @ui.button(label="Close Table", style=discord.ButtonStyle.danger, emoji="✖️", row=1)
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can close!", ephemeral=True,
            )
            return
        if self.table.phase in ("playing", "between_rounds"):
            await interaction.response.send_message(
                "Can't close mid-game!", ephemeral=True,
            )
            return
        self.table.phase = "closed"
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(self.table.channel_id, None)
        embed = discord.Embed(
            title="\U0001f3af Clue Master — Closed",
            description="Table closed.",
            colour=discord.Colour.dark_grey(),
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @ui.select(placeholder="Rounds each: 2", options=_ROUNDS_OPTIONS, row=2)
    async def rounds_select(self, interaction: discord.Interaction, select: ui.Select) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message("Only the host can change!", ephemeral=True)
            return
        if self.table.phase != "betting":
            await interaction.response.send_message("Can't change mid-game!", ephemeral=True)
            return
        val = int(select.values[0])
        self.table.rounds_per_player = val
        select.placeholder = f"Rounds each: {val}"
        for opt in select.options:
            opt.default = opt.value == str(val)
        await interaction.response.edit_message(embed=_betting_embed(self.table), view=self)

    @ui.select(placeholder="Category: NBA Players", options=_category_select_options(DEFAULT_CATEGORY), row=3)
    async def category_select(self, interaction: discord.Interaction, select: ui.Select) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message("Only the host can change!", ephemeral=True)
            return
        if self.table.phase != "betting":
            await interaction.response.send_message("Can't change mid-game!", ephemeral=True)
            return
        key = select.values[0]
        self.table.category_key = key
        label, _emoji, _items = get_category(key)
        select.placeholder = f"Category: {label}"
        for opt in select.options:
            opt.default = opt.value == key
        await interaction.response.edit_message(embed=_betting_embed(self.table), view=self)

    # ── Game flow ────────────────────────────────────────────────────────

    async def _start_game(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "playing"
        table.player_order = list(table.players.keys())
        random.shuffle(table.player_order)
        # Total rounds scale with the table: each player is clue master
        # rounds_per_player times, capped for sanity.
        table.total_rounds = _compute_total_rounds(
            table.rounds_per_player, len(table.player_order),
        )
        self._update_buttons()

        in_progress = discord.Embed(
            title="\U0001f3af Clue Master — In Progress",
            description="Game running in the thread below!",
            colour=discord.Colour.blurple(),
        )
        in_progress.add_field(
            name="Players",
            value=", ".join(p.display_name for p in table.players.values()),
            inline=False,
        )
        await interaction.response.edit_message(embed=in_progress, view=self)
        msg = await interaction.original_response()
        thread = await msg.create_thread(name=f"Clue Master — {table.host_name}")
        table.thread = thread
        await thread.send(
            "\U0001f3c1 **Clue Master started!** Each round, the clue master "
            "clicks the button to see the secret, then types clues here. "
            "Everyone else types guesses.",
            view=EndGameView(table),
        )
        table.game_task = asyncio.create_task(self._run_game())

    async def _run_game(self) -> None:
        table = self.table
        try:
            consecutive_unanswered = 0
            for rnd in range(1, table.total_rounds + 1):
                if table.stop_requested:
                    break
                cm_uid = table.player_order[(rnd - 1) % len(table.player_order)]
                table.cluemaster_id = cm_uid
                table.current_item = _pick_item(table.category_key, table.used_names)
                table.round_num = rnd
                table.round_winner = None
                table.round_solved.clear()
                table.round_messages.clear()
                table.phase = "playing"
                table.round_start_time = time.monotonic()
                table.last_activity_at = time.monotonic()

                cm_player = table.players[cm_uid]
                if table.thread:
                    try:
                        await table.thread.send(
                            f"\U0001f3af **Round {rnd}/{table.total_rounds}** — "
                            f"**{cm_player.display_name}** is the clue master. "
                            f"Click below to see the secret, then drop clues!",
                            view=RevealAnswerView(table),
                        )
                        table.message = await table.thread.send(embed=_playing_embed(table))
                    except discord.HTTPException:
                        log.exception("cluemaster: failed to post round start")

                solved = await self._wait_for_solve_or_timeout()
                if table.stop_requested:
                    break

                if table.message:
                    try:
                        await table.message.edit(
                            embed=_round_result_embed(table, solved),
                        )
                    except discord.HTTPException:
                        pass

                if solved:
                    consecutive_unanswered = 0
                else:
                    consecutive_unanswered += 1
                if consecutive_unanswered >= INACTIVITY_ROUNDS:
                    if table.thread:
                        try:
                            await table.thread.send(
                                f"⏸️ No one solved {INACTIVITY_ROUNDS} rounds in a row — ending."
                            )
                        except discord.HTTPException:
                            pass
                    break

                if rnd >= table.total_rounds:
                    break

                await self._clear_round_messages()
                table.phase = "between_rounds"
                await asyncio.sleep(ROUND_DELAY)

            await self._clear_round_messages()
            if table.stop_requested and table.thread:
                try:
                    await table.thread.send("⏹️ Game ended early.")
                except discord.HTTPException:
                    pass
            await self._end_game()
        except asyncio.CancelledError:
            await self._safe_close()
        except Exception:
            log.exception("cluemaster: unhandled error in game loop")
            await self._safe_close()

    async def _wait_for_solve_or_timeout(self) -> bool:
        table = self.table
        deadline = table.round_start_time + ROUND_TIME
        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                return table.round_winner is not None
            try:
                await asyncio.wait_for(table.round_solved.wait(), timeout=min(5.0, remaining))
                return True
            except asyncio.TimeoutError:
                if table.round_winner is not None:
                    return True
                secs_left = max(0, int(deadline - time.monotonic()))
                if secs_left > 0 and table.message:
                    try:
                        await table.message.edit(embed=_playing_embed(table, remaining=secs_left))
                    except discord.HTTPException:
                        pass

    async def _clear_round_messages(self) -> None:
        msgs = list(self.table.round_messages)
        self.table.round_messages.clear()
        for m in msgs:
            try:
                await m.delete()
            except discord.HTTPException:
                pass

    async def _end_game(self) -> None:
        table = self.table
        table.phase = "closed"
        elo_changes: dict[int, tuple[float, float]] = {}
        max_score = max((p.score for p in table.players.values()), default=0)
        if max_score > 0 and len(table.players) >= 2:
            sorted_players = sorted(
                table.players.values(), key=lambda p: p.score, reverse=True,
            )
            finish_order = [p.user_id for p in sorted_players]
            try:
                elo_changes = await update_elo_multiplayer(
                    finish_order, "cluemaster", "cluemaster",
                    scores={p.user_id: p.score for p in sorted_players},
                )
            except Exception:
                log.exception("cluemaster: ELO update failed")
        embed = _final_embed(table, elo_changes)
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)
        if table.thread:
            try:
                await table.thread.send(embed=embed)
            except discord.HTTPException:
                pass
            try:
                await table.thread.edit(archived=True)
            except discord.HTTPException:
                pass

    async def _safe_close(self) -> None:
        table = self.table
        table.phase = "closed"
        self.active_tables.pop(table.channel_id, None)
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        if table.thread:
            try:
                await table.thread.send("⚠️ Game closed unexpectedly.")
            except Exception:
                pass
            try:
                await table.thread.edit(archived=True)
            except Exception:
                pass

    async def on_timeout(self) -> None:
        table = self.table
        if table.phase == "closed":
            return
        if table.game_task and not table.game_task.done():
            table.game_task.cancel()
        await self._safe_close()


# ── Cog ──────────────────────────────────────────────────────────────────────


class CluemasterCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, CMTable] = {}

    async def cog_load(self) -> None:
        # Populate the QBReader science cache shared by cluemaster + imposter.
        # First iteration runs immediately, then every 6 hours.
        self._refresh_science.start()

    async def cog_unload(self) -> None:
        self._refresh_science.cancel()

    @tasks.loop(hours=6)
    async def _refresh_science(self) -> None:
        n = await refresh_science_answers()
        log.info("cluemaster: QBReader science cache holds %d answers", n)

    @_refresh_science.before_loop
    async def _before_refresh_science(self) -> None:
        await self.bot.wait_until_ready()

    def _is_replaceable(self, table: CMTable) -> tuple[bool, int]:
        """Mirror crash.py: replaceable if closed, or task dead, or idle 90s+."""
        if table.phase == "closed":
            return True, 0
        if table.game_task is not None and table.game_task.done():
            return True, 0
        idle = time.monotonic() - table.last_activity_at
        if idle >= IDLE_REPLACEABLE_SECS:
            return True, 0
        return False, max(0, int(IDLE_REPLACEABLE_SECS - idle))

    async def _force_close_table(self, table: CMTable) -> None:
        table.stop_requested = True
        table.phase = "closed"
        if table.game_task and not table.game_task.done():
            table.game_task.cancel()
        self.active_tables.pop(table.channel_id, None)
        if table.thread:
            try:
                await table.thread.edit(archived=True)
            except Exception:
                pass

    async def cluemaster(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            replaceable, secs_left = self._is_replaceable(existing)
            if not replaceable:
                await interaction.response.send_message(
                    f"There's already a Clue Master game in this channel "
                    f"(idle {secs_left}s left until replaceable).",
                    ephemeral=True,
                )
                return
            await self._force_close_table(existing)

        table = CMTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        view = CluemasterView(table, self.active_tables)
        embed = _betting_embed(table)
        try:
            await interaction.response.send_message(embed=embed, view=view)
        except discord.NotFound:
            return
        self.active_tables[channel_id] = table
        table.message = await interaction.original_response()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        # Find a table whose thread matches this channel
        table: CMTable | None = None
        for t in self.active_tables.values():
            if t.thread is not None and t.thread.id == message.channel.id:
                table = t
                break
        if table is None or table.phase != "playing":
            return
        uid = message.author.id
        if uid not in table.players:
            return
        text = message.content.strip()
        if not text:
            return
        # Clue master messages stay (they're the clues) — never count as guesses.
        if uid == table.cluemaster_id:
            return
        if table.round_winner is not None:
            return
        if len(text) < 3:
            return
        alpha_chars = sum(1 for c in text if c.isalpha())
        if alpha_chars < len(text) * 0.5:
            return
        table.last_activity_at = time.monotonic()
        table.round_messages.append(message)
        if table.current_item and check_answer(text, table.current_item):
            cm_player = table.players.get(table.cluemaster_id) if table.cluemaster_id else None
            guesser = table.players[uid]
            guesser.score += GUESSER_POINTS
            if cm_player:
                cm_player.score += CLUEMASTER_BONUS
            table.round_winner = uid
            try:
                await message.add_reaction("✅")
            except discord.HTTPException:
                pass
            table.round_solved.set()
        else:
            try:
                await message.add_reaction("❌")
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CluemasterCog(bot))
