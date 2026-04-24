"""Casino cog — multiplayer /quizbowl bonus rounds.

Uses the qbreader.org API to fetch 3-part quiz bowl bonus questions.
Players type answers in a dedicated thread; the API's check-answer
endpoint handles fuzzy matching.  Game runs until the host ends it.
"""

import asyncio
import html
import re
import time
from collections import deque
from dataclasses import dataclass, field
from itertools import groupby

import discord
import httpx
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 1
PART_TIME = 10  # seconds per bonus part
BETWEEN_PARTS_DELAY = 3  # seconds between parts
BETWEEN_BONUS_DELAY = 4  # seconds between bonuses
BATCH_SIZE = 5  # bonuses fetched per API call
QB_API = "https://www.qbreader.org/api"

MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]

# Paytable: fraction of prize pool by finishing position, keyed by player count
PAYTABLE: dict[int, list[float]] = {
    1: [1.0],
    2: [1.0],
    3: [0.70, 0.30],
    4: [0.55, 0.30, 0.15],
    5: [0.45, 0.25, 0.18, 0.12],
    6: [0.40, 0.24, 0.16, 0.12, 0.08],
    7: [0.36, 0.22, 0.16, 0.12, 0.08, 0.06],
    8: [0.33, 0.21, 0.16, 0.12, 0.08, 0.06, 0.04],
}

# qbreader category names (must be title-cased for the API)
QB_CATEGORIES: list[tuple[str, str, str]] = [
    # (value, label, emoji)
    ("all", "All Categories", "\U0001f3b2"),
    ("Literature", "Literature", "\U0001f4da"),
    ("History", "History", "\U0001f3db\ufe0f"),
    ("Science", "Science", "\U0001f52c"),
    ("Fine Arts", "Fine Arts", "\U0001f3a8"),
    ("Religion", "Religion", "\u2626\ufe0f"),
    ("Mythology", "Mythology", "\u26a1"),
    ("Philosophy", "Philosophy", "\U0001f914"),
    ("Social Science", "Social Science", "\U0001f4ca"),
    ("Current Events", "Current Events", "\U0001f4f0"),
    ("Geography", "Geography", "\U0001f30d"),
    ("Other Academic", "Other Academic", "\U0001f393"),
    ("Trash", "Trash (Pop Culture)", "\U0001f4fa"),
]

# qbreader difficulty levels
QB_DIFFICULTIES: list[tuple[str, str, str, list[int]]] = [
    # (value, label, emoji, difficulty numbers)
    ("all", "All Difficulties", "\U0001f3b2", []),
    ("ms", "Middle School", "\U0001f4d7", [1, 2, 3]),
    ("hs", "High School", "\U0001f4d8", [4, 5, 6]),
    ("college", "College", "\U0001f4d5", [7, 8, 9]),
]

# ── qbreader API helpers ─────────────────────────────────────────────────────


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    clean = re.sub(r"<[^>]+>", "", text)
    return html.unescape(clean).strip()


async def _fetch_bonuses(
    client: httpx.AsyncClient,
    category: str,
    difficulty: str = "all",
    count: int = BATCH_SIZE,
) -> list[dict]:
    """Fetch random 3-part bonuses from qbreader."""
    params: dict[str, str | int | bool] = {
        "number": count,
        "threePartBonuses": "true",
    }
    if category != "all":
        params["categories"] = category
    # Map difficulty label to qbreader difficulty numbers
    diff_nums = next(
        (d for v, _, _, d in QB_DIFFICULTIES if v == difficulty), [],
    )
    if diff_nums:
        params["difficulties"] = ",".join(str(d) for d in diff_nums)
    resp = await client.get(f"{QB_API}/random-bonus", params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("bonuses", [])


async def _check_answer(
    client: httpx.AsyncClient,
    answerline: str,
    given_answer: str,
) -> str:
    """Check an answer against qbreader's answer checker.

    Returns 'accept', 'reject', or 'prompt'.
    """
    params = {"answerline": answerline, "givenAnswer": given_answer}
    resp = await client.get(f"{QB_API}/check-answer", params=params, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    return data.get("directive", "reject")


# ── Payout helpers ───────────────────────────────────────────────────────────


def _compute_payouts(
    players: dict[int, "QBPlayer"], prize_pool: int, n_players: int,
) -> dict[int, int]:
    """Compute per-player payouts using the paytable.

    Players ranked by score. Ties split combined shares for occupied positions.
    Unused paid positions roll up to first place.
    """
    pct_table = PAYTABLE.get(n_players, PAYTABLE[8])

    in_money = sorted(
        [p for p in players.values() if p.score > 0],
        key=lambda p: p.score,
        reverse=True,
    )

    payouts: dict[int, int] = {uid: 0 for uid in players}

    if not in_money:
        return payouts

    paid_positions = len(pct_table)
    pos = 0
    for _score, group_iter in groupby(in_money, key=lambda p: p.score):
        group = list(group_iter)
        if pos >= paid_positions:
            break
        end = min(pos + len(group), paid_positions)
        combined_share = sum(pct_table[pos:end])
        per_player = int(prize_pool * combined_share / len(group))
        for p in group:
            payouts[p.user_id] = per_player
        pos += len(group)

    total_paid = sum(payouts.values())
    leftover = prize_pool - total_paid
    if leftover > 0 and in_money:
        top_score = in_money[0].score
        top_group = [p for p in in_money if p.score == top_score]
        extra = leftover // len(top_group)
        for p in top_group:
            payouts[p.user_id] += extra

    return payouts


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class QBPlayer:
    user_id: int
    display_name: str
    bet: int
    score: int = 0


@dataclass
class QBTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | between_parts | between_bonuses | closed
    players: dict[int, QBPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    thread: discord.Thread | None = None
    category: str = "all"
    difficulty: str = "all"
    # Current bonus state
    bonus_queue: deque[dict] = field(default_factory=deque)
    bonus_num: int = 0
    current_bonus: dict | None = None
    current_part_idx: int = 0
    part_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    part_winner: int | None = None
    part_start_time: float = 0.0
    # Per-bonus part scores: part_idx -> (user_id, display_name)
    bonus_part_results: dict[int, tuple[int, str]] = field(default_factory=dict)
    game_task: asyncio.Task | None = field(default=None, repr=False)
    stop_requested: bool = False
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    total_parts_played: int = 0


# ── Embeds ───────────────────────────────────────────────────────────────────


def _cat_label(cat: str) -> str:
    for val, label, _ in QB_CATEGORIES:
        if val == cat:
            return label
    return cat


def _diff_label(diff: str) -> str:
    for val, label, _, _ in QB_DIFFICULTIES:
        if val == diff:
            return label
    return diff


def _scoreboard(table: QBTable) -> str:
    sorted_players = sorted(
        table.players.values(), key=lambda p: p.score, reverse=True,
    )
    lines: list[str] = []
    for i, p in enumerate(sorted_players):
        prefix = MEDALS[i] if i < len(MEDALS) and p.score > 0 else "\u25aa\ufe0f"
        lines.append(f"{prefix} **{p.display_name}** \u2014 {p.score} pts")
    return "\n".join(lines) if lines else "No scores yet"


def _betting_embed(table: QBTable) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values())
    n = len(table.players)
    cat = _cat_label(table.category)

    diff = _diff_label(table.difficulty)

    embed = discord.Embed(
        title="\U0001f9e0 Quiz Bowl",
        description=(
            f"**Category:** {cat} \u2502 **Difficulty:** {diff}\n"
            "3-part bonus questions. First correct answer per part wins 10 pts.\n"
            "Game runs until the host ends it. Type answers in the game thread!"
        ),
        colour=discord.Colour.blue(),
    )

    if pot:
        embed.add_field(name="Pot", value=f"{pot}c", inline=True)

    if n >= MIN_PLAYERS:
        pt = PAYTABLE.get(n, PAYTABLE[8])
        pt_parts = [
            f"{MEDALS[i] if i < 3 else chr(0x25aa) + chr(0xfe0f)} {int(s * 100)}%"
            for i, s in enumerate(pt)
        ]
        embed.add_field(name="Paytable", value=" | ".join(pt_parts), inline=True)

    if table.players:
        lines = [
            f"\U0001f9e0 **{p.display_name}** \u2014 {p.bet}c"
            for p in table.players.values()
        ]
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet \u2014 click Join!*",
            inline=False,
        )
    embed.set_footer(text=f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} players")
    return embed


def _part_embed(table: QBTable) -> discord.Embed:
    bonus = table.current_bonus
    part_idx = table.current_part_idx
    leadin = _strip_html(bonus["leadin"])
    part_text = _strip_html(bonus["parts"][part_idx])
    cat = bonus.get("category", "")
    subcat = bonus.get("subcategory", "")
    cat_line = f"{cat}" + (f" / {subcat}" if subcat else "")

    embed = discord.Embed(
        title=f"\U0001f9e0 Bonus {table.bonus_num} \u2014 Part {part_idx + 1}/3",
        colour=discord.Colour.gold(),
    )
    embed.description = (
        f"*{leadin}*\n\n"
        f"**{part_text}**\n\n"
        "Type your answer below!"
    )
    embed.add_field(name="\u23f1\ufe0f Time", value=f"{PART_TIME}s", inline=True)
    embed.add_field(name="Category", value=cat_line, inline=True)
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    return embed


def _part_result_embed(
    table: QBTable,
    winner_name: str | None,
    answer: str,
    solve_time: float | None,
) -> discord.Embed:
    part_idx = table.current_part_idx
    embed = discord.Embed(
        title=f"\U0001f9e0 Bonus {table.bonus_num} \u2014 Part {part_idx + 1}/3",
        colour=discord.Colour.green() if winner_name else discord.Colour.dark_grey(),
    )
    if winner_name and solve_time is not None:
        embed.description = (
            f"\u2705 **{winner_name}** got it in **{solve_time:.1f}s**!\n\n"
            f"Answer: **{answer}**"
        )
    else:
        embed.description = (
            f"Time's up! Nobody got it.\n\n"
            f"Answer: **{answer}**"
        )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    return embed


def _bonus_summary_embed(table: QBTable) -> discord.Embed:
    bonus = table.current_bonus
    leadin = _strip_html(bonus["leadin"])
    embed = discord.Embed(
        title=f"\U0001f9e0 Bonus {table.bonus_num} \u2014 Summary",
        colour=discord.Colour.teal(),
    )
    lines: list[str] = []
    for i in range(3):
        answer = _strip_html(bonus["answers_sanitized"][i])
        result = table.bonus_part_results.get(i)
        if result:
            _, name = result
            lines.append(f"**Part {i+1}:** {answer} \u2014 \u2705 {name}")
        else:
            lines.append(f"**Part {i+1}:** {answer} \u2014 \u274c No one")
    embed.description = f"*{leadin}*\n\n" + "\n".join(lines)
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(text="Next bonus coming up\u2026")
    return embed


def _final_embed(
    table: QBTable,
    *,
    payouts: dict[int, int],
    balances: dict[int, int],
) -> discord.Embed:
    max_score = max((p.score for p in table.players.values()), default=0)
    is_refund = max_score == 0

    embed = discord.Embed(
        title="\U0001f9e0 Quiz Bowl \u2014 Results",
        colour=discord.Colour.gold() if not is_refund else discord.Colour.dark_grey(),
    )

    if is_refund:
        embed.description = "No points scored \u2014 all bets refunded!"
    else:
        sorted_p = sorted(
            table.players.values(), key=lambda p: p.score, reverse=True,
        )
        winner = sorted_p[0]
        embed.description = (
            f"\U0001f3c6 **{winner.display_name}** wins with "
            f"**{winner.score}** points!"
        )

    sorted_players = sorted(
        table.players.values(), key=lambda p: p.score, reverse=True,
    )
    lines: list[str] = []
    for i, p in enumerate(sorted_players):
        payout = payouts.get(p.user_id, 0)
        bal = balances.get(p.user_id, 0)
        net = payout - p.bet
        sign = "+" if net >= 0 else ""
        medal = MEDALS[i] if i < len(MEDALS) and p.score > 0 else "\u25aa\ufe0f"
        lines.append(
            f"{medal} **{p.display_name}** ({p.score} pts) \u2014 "
            f"{p.bet}c \u2192 {payout}c "
            f"(**{sign}{net}c**) \u2014 bal: {bal}c"
        )
    embed.add_field(name="Results", value="\n".join(lines), inline=False)

    if not is_refund:
        n = len(table.players)
        pt = PAYTABLE.get(n, PAYTABLE[8])
        pt_parts = [
            f"{MEDALS[i] if i < 3 else chr(0x25aa) + chr(0xfe0f)} {int(s * 100)}%"
            for i, s in enumerate(pt)
        ]
        embed.add_field(
            name=f"Paytable ({n} players)",
            value=" | ".join(pt_parts),
            inline=True,
        )

    embed.add_field(
        name="Bonuses Played", value=str(table.bonus_num), inline=True,
    )
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinQBModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(
        self, table: QBTable, view: "QBLobbyView", balance: int,
    ) -> None:
        super().__init__(title="Join Quiz Bowl")
        self.table = table
        self.lobby_view = view
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
                "You're already in this game!", ephemeral=True,
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

        self.table.players[uid] = QBPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
        )

        self.lobby_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.lobby_view,
        )


# ── "End Game" view posted inside the thread ─────────────────────────────────


class EndGameView(ui.View):
    """Persistent view in the thread so the host can stop the game."""

    def __init__(self, table: QBTable) -> None:
        super().__init__(timeout=None)
        self.table = table

    @ui.button(
        label="End Game", style=discord.ButtonStyle.danger,
        emoji="\u23f9\ufe0f", row=0,
    )
    async def end_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can end the game!", ephemeral=True,
            )
            return
        if self.table.stop_requested:
            await interaction.response.send_message(
                "Already ending\u2026", ephemeral=True,
            )
            return
        self.table.stop_requested = True
        button.disabled = True
        button.label = "Ending\u2026"
        await interaction.response.edit_message(view=self)


class SkipPartView(ui.View):
    """Attached to each part question so players can skip."""

    def __init__(self, table: QBTable) -> None:
        super().__init__(timeout=PART_TIME + 5)
        self.table = table

    @ui.button(
        label="Skip", style=discord.ButtonStyle.secondary,
        emoji="\u23ed\ufe0f", row=0,
    )
    async def skip_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if self.table.part_winner is not None:
            await interaction.response.send_message(
                "Already answered!", ephemeral=True,
            )
            return
        # Skip — trigger timeout without a winner
        button.disabled = True
        button.label = "Skipped"
        await interaction.response.edit_message(view=self)
        self.table.part_solved.set()


# ── Lobby View (main channel) ───────────────────────────────────────────────


_CATEGORY_OPTIONS = [
    discord.SelectOption(label=label, value=value, emoji=emoji, default=(value == "all"))
    for value, label, emoji in QB_CATEGORIES
]

_DIFFICULTY_OPTIONS = [
    discord.SelectOption(label=label, value=value, emoji=emoji, default=(value == "all"))
    for value, label, emoji, _ in QB_DIFFICULTIES
]


class QBLobbyView(ui.View):
    def __init__(
        self, table: QBTable, active_tables: dict[int, QBTable],
    ) -> None:
        super().__init__(timeout=900)  # 15 min lobby timeout
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        betting = self.table.phase == "betting"
        self.start_btn.disabled = (
            not betting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = not betting
        self.close_btn.disabled = self.table.phase == "playing"
        self.category_select.disabled = not betting
        self.difficulty_select.disabled = not betting

    # ── Row 0: Betting ──────────────────────────────────────────────────

    @ui.button(
        label="Start", style=discord.ButtonStyle.success,
        emoji="\u25b6\ufe0f", row=0,
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
                f"Need at least {MIN_PLAYERS} player(s)!", ephemeral=True,
            )
            return
        await self._start_game(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary,
        emoji="\U0001f9e0", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Game in progress! Wait for the next one.", ephemeral=True,
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
            JoinQBModal(self.table, self, bal),
        )

    @ui.button(
        label="Re-bet", style=discord.ButtonStyle.primary,
        emoji="\U0001f504", row=0,
    )
    async def rebet_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Game in progress!", ephemeral=True,
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
        self.table.players[uid] = QBPlayer(
            user_id=uid, display_name=name, bet=amt,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Leave", style=discord.ButtonStyle.secondary,
        emoji="\U0001f6aa", row=0,
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
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Can't leave during a game!", ephemeral=True,
            )
            return
        await queries.update_casino_balance(str(uid), player.bet)
        del self.table.players[uid]
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Row 1: Close ────────────────────────────────────────────────────

    @ui.button(
        label="Close Table", style=discord.ButtonStyle.danger,
        emoji="\u2716\ufe0f", row=1,
    )
    async def close_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can close the table!", ephemeral=True,
            )
            return
        if self.table.phase == "playing":
            await interaction.response.send_message(
                "Can't close while playing! Use the End Game button in the thread.",
                ephemeral=True,
            )
            return
        await self._close_table(interaction)

    # ── Row 2: Category select ──────────────────────────────────────────

    @ui.select(
        placeholder="Category: All Categories",
        options=_CATEGORY_OPTIONS,
        row=2,
    )
    async def category_select(
        self, interaction: discord.Interaction, select: ui.Select,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can change the category!", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Can't change category once started!", ephemeral=True,
            )
            return
        self.table.category = select.values[0]
        chosen = next(
            (o for o in _CATEGORY_OPTIONS if o.value == self.table.category), None,
        )
        select.placeholder = f"Category: {chosen.label}" if chosen else "Category"
        for opt in select.options:
            opt.default = opt.value == self.table.category
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Row 3: Difficulty select ────────────────────────────────────────

    @ui.select(
        placeholder="Difficulty: All Difficulties",
        options=_DIFFICULTY_OPTIONS,
        row=3,
    )
    async def difficulty_select(
        self, interaction: discord.Interaction, select: ui.Select,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can change the difficulty!", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Can't change difficulty once started!", ephemeral=True,
            )
            return
        self.table.difficulty = select.values[0]
        chosen = next(
            (o for o in _DIFFICULTY_OPTIONS if o.value == self.table.difficulty), None,
        )
        select.placeholder = f"Difficulty: {chosen.label}" if chosen else "Difficulty"
        for opt in select.options:
            opt.default = opt.value == self.table.difficulty
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Game start ──────────────────────────────────────────────────────

    async def _start_game(self, interaction: discord.Interaction) -> None:
        table = self.table

        # Save last bets for re-bet
        for uid, p in table.players.items():
            table.last_bets[uid] = (p.display_name, p.bet)

        table.phase = "playing"
        self._update_buttons()

        # Update lobby embed to show "in progress"
        embed = discord.Embed(
            title="\U0001f9e0 Quiz Bowl \u2014 In Progress",
            description="Game is running in the thread below!",
            colour=discord.Colour.gold(),
        )
        players_text = ", ".join(p.display_name for p in table.players.values())
        embed.add_field(name="Players", value=players_text, inline=False)
        pot = sum(p.bet for p in table.players.values())
        embed.add_field(name="Pot", value=f"{pot}c", inline=True)
        embed.add_field(name="Category", value=_cat_label(table.category), inline=True)
        await interaction.response.edit_message(embed=embed, view=self)

        # Create thread
        msg = await interaction.original_response()
        thread = await msg.create_thread(
            name=f"Quiz Bowl \u2014 {_cat_label(table.category)}",
        )
        table.thread = thread

        # Post the "End Game" button in the thread
        end_view = EndGameView(table)
        await thread.send(
            "\U0001f3c1 **Quiz Bowl started!** Answer questions below. "
            "Host can end the game at any time.",
            view=end_view,
        )

        # Launch the game loop
        table.game_task = asyncio.create_task(self._game_loop())

    async def _game_loop(self) -> None:
        table = self.table
        try:
            async with httpx.AsyncClient() as client:
                while not table.stop_requested:
                    # Refill queue if empty
                    if not table.bonus_queue:
                        try:
                            bonuses = await _fetch_bonuses(
                                client, table.category, table.difficulty,
                            )
                            table.bonus_queue.extend(bonuses)
                        except Exception:
                            # API error — wait and retry
                            if table.thread:
                                await table.thread.send(
                                    "\u26a0\ufe0f Failed to fetch questions. Retrying\u2026"
                                )
                            await asyncio.sleep(3)
                            continue

                    if not table.bonus_queue:
                        if table.thread:
                            await table.thread.send(
                                "\u26a0\ufe0f No bonuses available for this category. Ending game."
                            )
                        break

                    # Pop next bonus
                    bonus = table.bonus_queue.popleft()
                    table.current_bonus = bonus
                    table.bonus_num += 1
                    table.bonus_part_results = {}

                    # Play 3 parts
                    for part_idx in range(3):
                        if table.stop_requested:
                            break

                        table.current_part_idx = part_idx
                        table.part_solved.clear()
                        table.part_winner = None
                        table.phase = "playing"
                        table.part_start_time = time.monotonic()

                        # Post the question with Skip button
                        skip_view: SkipPartView | None = None
                        if table.thread:
                            skip_view = SkipPartView(table)
                            await table.thread.send(
                                embed=_part_embed(table), view=skip_view,
                            )

                        # Wait for answer or timeout
                        try:
                            await asyncio.wait_for(
                                table.part_solved.wait(), timeout=PART_TIME,
                            )
                        except asyncio.TimeoutError:
                            pass

                        # Disable the skip button
                        if skip_view is not None:
                            skip_view.stop()

                        # Show result
                        answer = _strip_html(bonus["answers_sanitized"][part_idx])
                        if table.part_winner is not None:
                            winner = table.players[table.part_winner]
                            solve_time = time.monotonic() - table.part_start_time
                            table.bonus_part_results[part_idx] = (
                                winner.user_id, winner.display_name,
                            )
                            result_embed = _part_result_embed(
                                table, winner.display_name, answer, solve_time,
                            )
                        else:
                            result_embed = _part_result_embed(
                                table, None, answer, None,
                            )

                        table.phase = "between_parts"
                        if table.thread:
                            await table.thread.send(embed=result_embed)

                        table.total_parts_played += 1

                        # Delay between parts (shorter than between bonuses)
                        if part_idx < 2 and not table.stop_requested:
                            await asyncio.sleep(BETWEEN_PARTS_DELAY)

                    # Post bonus summary
                    if table.thread and not table.stop_requested:
                        table.phase = "between_bonuses"
                        await table.thread.send(embed=_bonus_summary_embed(table))
                        await asyncio.sleep(BETWEEN_BONUS_DELAY)

            # Game ended — compute payouts
            await self._end_game()

        except asyncio.CancelledError:
            pass
        except Exception:
            table.phase = "closed"
            self.active_tables.pop(table.channel_id, None)

    async def _compute_and_apply_payouts(
        self,
    ) -> tuple[dict[int, int], dict[int, int]]:
        table = self.table
        n_players = len(table.players)
        pot = sum(p.bet for p in table.players.values())
        max_score = max((p.score for p in table.players.values()), default=0)

        if max_score == 0:
            payouts = {uid: p.bet for uid, p in table.players.items()}
            for uid, refund in payouts.items():
                try:
                    await queries.update_casino_balance(str(uid), refund)
                except Exception:
                    pass
        else:
            payouts = _compute_payouts(table.players, pot, n_players)
            for uid, payout in payouts.items():
                if payout > 0:
                    try:
                        await queries.update_casino_balance(str(uid), payout)
                    except Exception:
                        pass

        balances: dict[int, int] = {}
        for uid in table.players:
            bal = await queries.get_casino_balance(str(uid))
            balances[uid] = bal or 0

        for uid, p in table.players.items():
            payout = payouts.get(uid, 0)
            await queries.log_casino_result(str(uid), "quizbowl", p.bet, payout)

        return payouts, balances

    async def _end_game(self) -> None:
        table = self.table
        table.phase = "closed"

        payouts, balances = await self._compute_and_apply_payouts()
        embed = _final_embed(table, payouts=payouts, balances=balances)

        # Post final results in thread
        if table.thread:
            await table.thread.send(embed=embed)
            try:
                await table.thread.edit(archived=True)
            except discord.HTTPException:
                pass

        # Update lobby message
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)

        if table.message:
            try:
                await table.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    async def _close_table(self, interaction: discord.Interaction) -> None:
        table = self.table

        for p in table.players.values():
            try:
                await queries.update_casino_balance(str(p.user_id), p.bet)
            except Exception:
                pass

        embed = discord.Embed(
            title="\U0001f9e0 Quiz Bowl \u2014 Closed",
            description="Table closed. All bets refunded.",
            colour=discord.Colour.dark_grey(),
        )
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table

        if table.game_task and not table.game_task.done():
            table.game_task.cancel()

        if table.phase == "closed":
            return

        # If game was in progress with scores, pay out based on current scores
        if table.total_parts_played > 0:
            table.phase = "closed"
            payouts, balances = await self._compute_and_apply_payouts()
            embed = _final_embed(table, payouts=payouts, balances=balances)
            embed.title = "\U0001f9e0 Quiz Bowl \u2014 Timed Out"

            if table.thread:
                try:
                    await table.thread.send(embed=embed)
                    await table.thread.edit(archived=True)
                except discord.HTTPException:
                    pass
        else:
            # No parts played — full refund
            for p in table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass

        table.phase = "closed"
        self.active_tables.pop(table.channel_id, None)

        if table.message:
            try:
                embed = discord.Embed(
                    title="\U0001f9e0 Quiz Bowl \u2014 Timed Out",
                    description="Table timed out. All bets refunded."
                    if table.total_parts_played == 0
                    else "Table timed out. Scores settled.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class QuizBowlCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, QBTable] = {}
        self._http = httpx.AsyncClient()

    async def cog_unload(self) -> None:
        await self._http.aclose()

    @app_commands.command(
        name="quizbowl",
        description="Open a Quiz Bowl table (multiplayer bonus rounds)",
    )
    async def quizbowl(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a quiz bowl table in this channel!",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = QBTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = QBLobbyView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message) -> None:
        """Listen for chat answers in quiz bowl threads."""
        if message.author.bot:
            return

        # Find the table whose thread matches this channel
        table: QBTable | None = None
        for t in self.active_tables.values():
            if t.thread and t.thread.id == message.channel.id:
                table = t
                break

        if table is None or table.phase != "playing":
            return

        uid = message.author.id
        if uid not in table.players:
            return

        if table.part_winner is not None:
            return

        guess = message.content.strip()
        if len(guess) < 2:
            return

        # Check answer via qbreader API
        bonus = table.current_bonus
        if bonus is None:
            return

        answerline = bonus["answers"][table.current_part_idx]

        try:
            directive = await _check_answer(self._http, answerline, guess)
        except Exception:
            # API error — silently skip
            return

        if directive == "accept":
            player = table.players[uid]
            player.score += 10
            table.part_winner = uid
            try:
                await message.add_reaction("\u2705")
            except discord.HTTPException:
                pass
            table.part_solved.set()
        else:
            try:
                await message.add_reaction("\u274c")
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QuizBowlCog(bot))
