"""Imposter — everyone but one player knows the secret. Hints, vote, reveal.

Generic over categories (NBA by default). The imposter sees only the
category and must bluff a hint that sounds plausible. After all hints,
players vote on who they think the imposter is. If the imposter gets the
most votes, the players win — otherwise the imposter wins.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field

import discord
from discord import ui
from discord.ext import commands

from bot.cogs._elo_helpers import fmt_elo_change, update_elo_multiplayer
from bot.cogs._party_categories import (
    CATEGORIES, DEFAULT_CATEGORY, category_options, get_category,
)

log = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

MIN_PLAYERS = 3
MAX_PLAYERS = 10
HINT_TIME = 30
VOTE_TIME = 45
ROLE_REVEAL_TIME = 30
IDLE_REPLACEABLE_SECS = 90

PLAYER_WIN_POINTS = 2
IMPOSTER_WIN_POINTS = 3
CORRECT_VOTE_POINTS = 1


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class ImpPlayer:
    user_id: int
    display_name: str
    score: int = 0
    role_seen: bool = False
    hint: str | None = None
    voted_for: int | None = None


@dataclass
class ImpTable:
    channel_id: int
    host_id: int
    host_name: str
    category_key: str = DEFAULT_CATEGORY
    phase: str = "betting"  # betting | reveal | hints | voting | results | closed
    players: dict[int, ImpPlayer] = field(default_factory=dict)
    hint_order: list[int] = field(default_factory=list)
    answer: str | None = None
    imposter_id: int | None = None
    current_hinter: int | None = None
    current_hint_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    all_revealed_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    voting_done_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    message: discord.Message | None = None
    thread: discord.Thread | None = None
    game_task: asyncio.Task | None = field(default=None, repr=False)
    stop_requested: bool = False
    last_activity_at: float = field(default_factory=time.monotonic)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _category_label(key: str) -> str:
    label, emoji, _ = get_category(key)
    return f"{emoji} {label}"


def _pick_secret(category_key: str) -> str:
    _label, _emoji, items = get_category(category_key)
    return random.choice(items)[0]


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: ImpTable) -> discord.Embed:
    embed = discord.Embed(
        title="\U0001f575️ Imposter",
        description=(
            f"**Category:** {_category_label(table.category_key)}\n\n"
            "Everyone except one player will see a secret. The imposter "
            "only knows the category and must bluff. Each player gives a "
            "one-word hint, then votes on who the imposter is."
        ),
        colour=discord.Colour.purple(),
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
    embed.set_footer(text=f"Host: {table.host_name} ┃ {MIN_PLAYERS}-{MAX_PLAYERS} players")
    return embed


def _reveal_embed(table: ImpTable, deadline: float | None = None) -> discord.Embed:
    secs = max(0, int(deadline - time.monotonic())) if deadline else ROLE_REVEAL_TIME
    seen = sum(1 for p in table.players.values() if p.role_seen)
    embed = discord.Embed(
        title="\U0001f575️ Imposter — Role Reveal",
        description=(
            f"**Category:** {_category_label(table.category_key)}\n\n"
            "Click below to see your role privately. "
            "Once everyone has clicked, hints start."
        ),
        colour=discord.Colour.dark_purple(),
    )
    embed.add_field(name="Revealed", value=f"{seen}/{len(table.players)}", inline=True)
    embed.add_field(name="⏱️ Time", value=f"**{secs}s**", inline=True)
    return embed


def _hints_embed(
    table: ImpTable,
    deadline: float | None = None,
) -> discord.Embed:
    secs = max(0, int(deadline - time.monotonic())) if deadline else HINT_TIME
    embed = discord.Embed(
        title="\U0001f575️ Imposter — Hints",
        colour=discord.Colour.dark_purple(),
    )
    cur = table.players.get(table.current_hinter) if table.current_hinter else None
    if cur:
        embed.description = (
            f"⏩ **{cur.display_name}**'s turn to give a hint. "
            f"Type a single short hint in the thread."
        )
    lines: list[str] = []
    for uid in table.hint_order:
        p = table.players[uid]
        if p.hint is not None:
            lines.append(f"✅ **{p.display_name}** — {p.hint}")
        elif uid == table.current_hinter:
            lines.append(f"⏳ **{p.display_name}** — *waiting…*")
        else:
            lines.append(f"▪️ **{p.display_name}** — *queued*")
    embed.add_field(name="Hints", value="\n".join(lines) or "*—*", inline=False)
    embed.add_field(name="⏱️ Hint Timer", value=f"**{secs}s**", inline=True)
    embed.add_field(name="Category", value=_category_label(table.category_key), inline=True)
    return embed


def _voting_embed(table: ImpTable, deadline: float | None = None) -> discord.Embed:
    secs = max(0, int(deadline - time.monotonic())) if deadline else VOTE_TIME
    embed = discord.Embed(
        title="\U0001f575️ Imposter — Vote!",
        description="Pick who you think is the imposter. You can't vote for yourself.",
        colour=discord.Colour.gold(),
    )
    lines: list[str] = []
    for uid in table.hint_order:
        p = table.players[uid]
        marker = "✅" if p.voted_for is not None else "▪️"
        hint = p.hint if p.hint is not None else "—"
        lines.append(f"{marker} **{p.display_name}** — *{hint}*")
    embed.add_field(name="Hints", value="\n".join(lines), inline=False)
    embed.add_field(name="⏱️ Vote Timer", value=f"**{secs}s**", inline=True)
    return embed


def _results_embed(
    table: ImpTable,
    imposter_caught: bool,
    vote_counts: dict[int, int],
    elo_changes: dict[int, tuple[float, float]] | None = None,
) -> discord.Embed:
    imposter = table.players.get(table.imposter_id) if table.imposter_id else None
    title = "🎉 Players Win!" if imposter_caught else "😈 Imposter Wins!"
    desc_lines = [
        f"**Answer:** {table.answer}",
        f"**Imposter:** {imposter.display_name if imposter else '?'}",
    ]
    embed = discord.Embed(
        title=f"\U0001f575️ Imposter — {title}",
        description="\n".join(desc_lines),
        colour=discord.Colour.green() if imposter_caught else discord.Colour.red(),
    )
    # Hints recap
    hint_lines = [
        f"**{table.players[uid].display_name}**"
        + (" \U0001f575️" if uid == table.imposter_id else "")
        + f" — *{table.players[uid].hint or '—'}*"
        for uid in table.hint_order
    ]
    embed.add_field(name="Hints", value="\n".join(hint_lines), inline=False)
    # Votes recap
    vote_lines: list[str] = []
    for uid in table.hint_order:
        p = table.players[uid]
        target_id = p.voted_for
        target_name = (
            table.players[target_id].display_name if target_id in table.players else "—"
        )
        vote_lines.append(f"**{p.display_name}** → {target_name}")
    embed.add_field(name="Votes", value="\n".join(vote_lines) or "*—*", inline=False)
    # Tally
    if vote_counts:
        tally_lines = [
            f"**{table.players[uid].display_name}**: {n}"
            for uid, n in sorted(vote_counts.items(), key=lambda kv: -kv[1])
            if uid in table.players
        ]
        embed.add_field(name="Tally", value="\n".join(tally_lines), inline=False)
    # Scores
    score_lines = [
        f"**{p.display_name}** — {p.score} pts"
        for p in sorted(table.players.values(), key=lambda x: -x.score)
    ]
    embed.add_field(name="Scores (this game)", value="\n".join(score_lines), inline=False)
    if elo_changes:
        elo_lines = [
            f"**{table.players[uid].display_name}**: {fmt_elo_change(old, new)}"
            for uid, (old, new) in elo_changes.items() if uid in table.players
        ]
        if elo_lines:
            embed.add_field(name="\U0001f4c8 ELO", value="\n".join(elo_lines), inline=False)
    return embed


# ── Selectors ────────────────────────────────────────────────────────────────


def _category_select_options(default_key: str) -> list[discord.SelectOption]:
    return [
        discord.SelectOption(label=label, value=value, emoji=emoji, default=is_def)
        for label, value, emoji, is_def in category_options(default_key)
    ]


# ── In-thread role reveal view ───────────────────────────────────────────────


class ImposterRoleRevealView(ui.View):
    def __init__(self, table: ImpTable) -> None:
        super().__init__(timeout=ROLE_REVEAL_TIME + 30)
        self.table = table

    @ui.button(
        label="View My Role", style=discord.ButtonStyle.primary,
        emoji="\U0001f441️", row=0,
    )
    async def reveal_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        uid = interaction.user.id
        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game.", ephemeral=True,
            )
            return
        if self.table.phase not in ("reveal", "hints", "voting"):
            await interaction.response.send_message(
                "The reveal phase is over.", ephemeral=True,
            )
            return
        self.table.players[uid].role_seen = True
        self.table.last_activity_at = time.monotonic()
        if uid == self.table.imposter_id:
            text = (
                f"\U0001f575️ **You are the IMPOSTER.**\n\n"
                f"Category: {_category_label(self.table.category_key)}\n"
                "You don't know the answer. Bluff a plausible hint!"
            )
        else:
            text = (
                f"✅ **The answer is:** **{self.table.answer}**\n\n"
                f"Category: {_category_label(self.table.category_key)}\n"
                "Give a one-word hint. Don't make it too obvious!"
            )
        await interaction.response.send_message(text, ephemeral=True)
        # Wake the game loop if everyone has now seen
        if all(p.role_seen for p in self.table.players.values()):
            self.table.all_revealed_event.set()


# ── Voting view ──────────────────────────────────────────────────────────────


class ImposterVoteView(ui.View):
    def __init__(self, table: ImpTable) -> None:
        super().__init__(timeout=VOTE_TIME + 30)
        self.table = table
        # One button per player (max 10)
        for uid in table.hint_order:
            self.add_item(VoteButton(uid, table.players[uid].display_name))


class VoteButton(ui.Button):
    def __init__(self, target_uid: int, label: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.target_uid = target_uid

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ImposterVoteView = self.view  # type: ignore[assignment]
        table = view.table
        uid = interaction.user.id
        if uid not in table.players:
            await interaction.response.send_message(
                "You're not in this game.", ephemeral=True,
            )
            return
        if table.phase != "voting":
            await interaction.response.send_message(
                "Voting is closed.", ephemeral=True,
            )
            return
        if uid == self.target_uid:
            await interaction.response.send_message(
                "Can't vote for yourself!", ephemeral=True,
            )
            return
        table.players[uid].voted_for = self.target_uid
        table.last_activity_at = time.monotonic()
        target_name = table.players[self.target_uid].display_name
        await interaction.response.send_message(
            f"✅ You voted for **{target_name}**.", ephemeral=True,
        )
        if all(p.voted_for is not None for p in table.players.values()):
            table.voting_done_event.set()


# ── End-game button ──────────────────────────────────────────────────────────


class ImposterEndGameView(ui.View):
    def __init__(self, table: ImpTable) -> None:
        super().__init__(timeout=None)
        self.table = table

    @ui.button(label="End Game", style=discord.ButtonStyle.danger, emoji="⏹️", row=0)
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
        self.table.last_activity_at = time.monotonic()
        # Wake any pending awaits
        self.table.all_revealed_event.set()
        self.table.current_hint_event.set()
        self.table.voting_done_event.set()
        button.disabled = True
        button.label = "Ending…"
        await interaction.response.edit_message(view=self)


# ── Lobby view ───────────────────────────────────────────────────────────────


class ImposterLobbyView(ui.View):
    def __init__(self, table: ImpTable, active_tables: dict[int, ImpTable]) -> None:
        super().__init__(timeout=900)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        self.table.last_activity_at = time.monotonic()
        return True

    def _update_buttons(self) -> None:
        betting = self.table.phase == "betting"
        self.start_btn.disabled = not betting or len(self.table.players) < MIN_PLAYERS
        self.join_btn.disabled = not betting
        self.leave_btn.disabled = not betting
        self.close_btn.disabled = not betting
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

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f575️", row=0)
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
        self.table.players[uid] = ImpPlayer(
            user_id=uid, display_name=interaction.user.display_name,
        )
        self._update_buttons()
        await interaction.response.edit_message(embed=_betting_embed(self.table), view=self)

    @ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=0)
    async def leave_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Can't leave mid-game!", ephemeral=True)
            return
        uid = interaction.user.id
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
        self.table.phase = "closed"
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(self.table.channel_id, None)
        embed = discord.Embed(
            title="\U0001f575️ Imposter — Closed",
            description="Table closed.",
            colour=discord.Colour.dark_grey(),
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @ui.select(placeholder="Category: NBA Players", options=_category_select_options(DEFAULT_CATEGORY), row=2)
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
        table.phase = "reveal"
        table.answer = _pick_secret(table.category_key)
        uids = list(table.players.keys())
        random.shuffle(uids)
        table.imposter_id = uids[0]
        table.hint_order = list(uids)
        random.shuffle(table.hint_order)
        for p in table.players.values():
            p.role_seen = False
            p.hint = None
            p.voted_for = None
        table.all_revealed_event.clear()
        table.voting_done_event.clear()
        self._update_buttons()

        in_progress = discord.Embed(
            title="\U0001f575️ Imposter — In Progress",
            description="Game running in the thread below!",
            colour=discord.Colour.purple(),
        )
        in_progress.add_field(
            name="Players",
            value=", ".join(p.display_name for p in table.players.values()),
            inline=False,
        )
        await interaction.response.edit_message(embed=in_progress, view=self)
        msg = await interaction.original_response()
        thread = await msg.create_thread(name=f"Imposter — {table.host_name}")
        table.thread = thread
        await thread.send(
            "\U0001f3c1 **Imposter started!**", view=ImposterEndGameView(table),
        )
        table.game_task = asyncio.create_task(self._run_game())

    async def _run_game(self) -> None:
        table = self.table
        try:
            # Phase 1: role reveal
            await self._phase_reveal()
            if table.stop_requested:
                return
            # Phase 2: hints in turn order
            await self._phase_hints()
            if table.stop_requested:
                return
            # Phase 3: voting
            await self._phase_voting()
            if table.stop_requested:
                return
            # Phase 4: results
            await self._phase_results()
        except asyncio.CancelledError:
            await self._safe_close()
        except Exception:
            log.exception("imposter: unhandled error in game loop")
            await self._safe_close()

    async def _phase_reveal(self) -> None:
        table = self.table
        if table.thread is None:
            return
        deadline = time.monotonic() + ROLE_REVEAL_TIME
        reveal_view = ImposterRoleRevealView(table)
        msg = await table.thread.send(embed=_reveal_embed(table, deadline), view=reveal_view)
        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0 or table.stop_requested:
                break
            try:
                await asyncio.wait_for(table.all_revealed_event.wait(), timeout=min(5.0, remaining))
                break
            except asyncio.TimeoutError:
                try:
                    await msg.edit(embed=_reveal_embed(table, deadline))
                except discord.HTTPException:
                    pass
        for child in reveal_view.children:
            child.disabled = True  # type: ignore[union-attr]
        try:
            await msg.edit(view=reveal_view)
        except discord.HTTPException:
            pass

    async def _phase_hints(self) -> None:
        table = self.table
        if table.thread is None:
            return
        table.phase = "hints"
        for uid in list(table.hint_order):
            if table.stop_requested:
                return
            table.current_hinter = uid
            table.current_hint_event.clear()
            deadline = time.monotonic() + HINT_TIME
            try:
                hint_msg = await table.thread.send(embed=_hints_embed(table, deadline))
            except discord.HTTPException:
                hint_msg = None
            while True:
                now = time.monotonic()
                remaining = deadline - now
                if remaining <= 0 or table.stop_requested:
                    break
                try:
                    await asyncio.wait_for(
                        table.current_hint_event.wait(),
                        timeout=min(5.0, remaining),
                    )
                    break
                except asyncio.TimeoutError:
                    if hint_msg:
                        try:
                            await hint_msg.edit(embed=_hints_embed(table, deadline))
                        except discord.HTTPException:
                            pass
            if table.players[uid].hint is None:
                table.players[uid].hint = "…"  # auto-skip
                try:
                    await table.thread.send(
                        f"⏰ **{table.players[uid].display_name}** ran out of time — auto-skip.",
                    )
                except discord.HTTPException:
                    pass
            if hint_msg:
                try:
                    await hint_msg.edit(embed=_hints_embed(table, deadline))
                except discord.HTTPException:
                    pass
        table.current_hinter = None

    async def _phase_voting(self) -> None:
        table = self.table
        if table.thread is None:
            return
        table.phase = "voting"
        table.voting_done_event.clear()
        deadline = time.monotonic() + VOTE_TIME
        view = ImposterVoteView(table)
        try:
            vote_msg = await table.thread.send(embed=_voting_embed(table, deadline), view=view)
        except discord.HTTPException:
            return
        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0 or table.stop_requested:
                break
            try:
                await asyncio.wait_for(table.voting_done_event.wait(), timeout=min(5.0, remaining))
                break
            except asyncio.TimeoutError:
                try:
                    await vote_msg.edit(embed=_voting_embed(table, deadline))
                except discord.HTTPException:
                    pass
        for child in view.children:
            child.disabled = True  # type: ignore[union-attr]
        try:
            await vote_msg.edit(view=view)
        except discord.HTTPException:
            pass

    async def _phase_results(self) -> None:
        table = self.table
        table.phase = "results"
        # Tally votes
        vote_counts: dict[int, int] = {}
        for p in table.players.values():
            if p.voted_for is not None:
                vote_counts[p.voted_for] = vote_counts.get(p.voted_for, 0) + 1
        # Determine outcome
        imposter_caught = False
        if vote_counts:
            max_votes = max(vote_counts.values())
            top = [uid for uid, n in vote_counts.items() if n == max_votes]
            # Imposter caught only if they got the unique plurality
            if top == [table.imposter_id]:
                imposter_caught = True
        # Award points
        if imposter_caught:
            for p in table.players.values():
                if p.user_id != table.imposter_id:
                    p.score += PLAYER_WIN_POINTS
                if p.voted_for == table.imposter_id:
                    p.score += CORRECT_VOTE_POINTS
        else:
            if table.imposter_id and table.imposter_id in table.players:
                table.players[table.imposter_id].score += IMPOSTER_WIN_POINTS
        # ELO: rank players by score this game (imposter as a 1-player team if won)
        elo_changes: dict[int, tuple[float, float]] = {}
        if len(table.players) >= 2:
            sorted_players = sorted(
                table.players.values(), key=lambda p: p.score, reverse=True,
            )
            finish_order = [p.user_id for p in sorted_players]
            try:
                elo_changes = await update_elo_multiplayer(
                    finish_order, "imposter", "imposter",
                )
            except Exception:
                log.exception("imposter: ELO update failed")
        embed = _results_embed(table, imposter_caught, vote_counts, elo_changes)
        if table.thread:
            try:
                await table.thread.send(embed=embed)
            except discord.HTTPException:
                pass
        await self._end_game()

    async def _end_game(self) -> None:
        table = self.table
        table.phase = "closed"
        self.active_tables.pop(table.channel_id, None)
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        if table.thread:
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


class ImposterCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, ImpTable] = {}

    def _is_replaceable(self, table: ImpTable) -> tuple[bool, int]:
        if table.phase == "closed":
            return True, 0
        if table.game_task is not None and table.game_task.done():
            return True, 0
        idle = time.monotonic() - table.last_activity_at
        if idle >= IDLE_REPLACEABLE_SECS:
            return True, 0
        return False, max(0, int(IDLE_REPLACEABLE_SECS - idle))

    async def _force_close_table(self, table: ImpTable) -> None:
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

    async def imposter(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            replaceable, secs_left = self._is_replaceable(existing)
            if not replaceable:
                await interaction.response.send_message(
                    f"There's already an Imposter game in this channel "
                    f"(idle {secs_left}s left until replaceable).",
                    ephemeral=True,
                )
                return
            await self._force_close_table(existing)

        table = ImpTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        view = ImposterLobbyView(table, self.active_tables)
        try:
            await interaction.response.send_message(embed=_betting_embed(table), view=view)
        except discord.NotFound:
            return
        self.active_tables[channel_id] = table
        table.message = await interaction.original_response()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        table: ImpTable | None = None
        for t in self.active_tables.values():
            if t.thread is not None and t.thread.id == message.channel.id:
                table = t
                break
        if table is None or table.phase != "hints":
            return
        uid = message.author.id
        if uid != table.current_hinter:
            return
        text = message.content.strip()
        if not text:
            return
        # Take just the first word/short phrase to keep hints terse.
        hint = text.split("\n", 1)[0]
        if len(hint) > 60:
            hint = hint[:60].rstrip() + "…"
        table.players[uid].hint = hint
        table.last_activity_at = time.monotonic()
        table.current_hint_event.set()
        try:
            await message.add_reaction("✅")
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ImposterCog(bot))
