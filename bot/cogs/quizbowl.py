"""Casino cog — multiplayer /quizbowl bonus rounds.

Uses the qbreader.org API to fetch 3-part quiz bowl bonus questions.
Players type answers in a dedicated thread; the API's check-answer
endpoint handles fuzzy matching.  Game runs until the host ends it.
"""

import asyncio
import difflib
import html
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
import discord
import httpx
from discord import app_commands, ui
from discord.ext import commands

log = logging.getLogger(__name__)

from bot.cogs._elo_helpers import fmt_elo_change, update_elo_multiplayer

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 1
PART_TIME = 10  # seconds per bonus part
BETWEEN_PARTS_DELAY = 3  # seconds between parts
BETWEEN_BONUS_DELAY = 4  # seconds between bonuses
INACTIVITY_ROUNDS = 5  # auto-end after N consecutive unanswered parts
INACTIVITY_SECS = 180  # auto-end after 3 min of no answers
BATCH_SIZE = 5  # bonuses fetched per API call
QB_API = "https://www.qbreader.org/api"

MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]

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


_ARTICLES = re.compile(r"\b(the|a|an)\b")
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")
# Brackets like [accept USSR] or [or Soviet Russia]
_BRACKET_ALTERNATES = re.compile(r"\[(?:accept|or)\s+([^\]]+)\]", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")


def _normalize(s: str) -> str:
    """Lowercase, strip HTML, remove punctuation and English articles."""
    s = _HTML_TAG.sub("", s).lower()
    s = _PUNCT.sub("", s)
    s = _ARTICLES.sub("", s)
    return _WS.sub(" ", s).strip()


def _extract_alternates(answerline: str) -> list[str]:
    """Return main answer plus any [accept X] / [or Y] alternates."""
    main = _BRACKET_ALTERNATES.split(answerline)[0]
    alternates: list[str] = [main]
    for group in _BRACKET_ALTERNATES.findall(answerline):
        # Each group may contain " or " separators inside the bracket
        for part in re.split(r"\s+or\s+", group, flags=re.IGNORECASE):
            alternates.append(part.strip())
    return alternates


def _local_check_answer(answerline: str, given: str) -> bool:
    """Fallback fuzzy answer check used when the qbreader API is unavailable.

    Parses [accept X] / [or Y] alternates and uses difflib for fuzzy matching
    (ratio ≥ 0.75).  Also accepts if either string is a substring of the other
    after normalisation (handles "Elizabeth Stanton" → "Elizabeth Cady Stanton").
    """
    candidates = _extract_alternates(answerline)
    given_norm = _normalize(given)
    if not given_norm:
        return False
    for candidate in candidates:
        cand_norm = _normalize(candidate)
        if not cand_norm:
            continue
        if given_norm == cand_norm:
            return True
        if given_norm in cand_norm or cand_norm in given_norm:
            return True
        ratio = difflib.SequenceMatcher(None, given_norm, cand_norm).ratio()
        if ratio >= 0.75:
            return True
    return False


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class QBPlayer:
    user_id: int
    display_name: str
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
    total_parts_played: int = 0
    last_activity: float = field(default_factory=time.monotonic)


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

    if table.players:
        lines = [
            f"\U0001f9e0 **{p.display_name}**"
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


def _final_embed(table: QBTable, elo_changes: dict[int, tuple[float, float]] | None = None) -> discord.Embed:
    max_score = max((p.score for p in table.players.values()), default=0)
    no_scores = max_score == 0

    embed = discord.Embed(
        title="\U0001f9e0 Quiz Bowl \u2014 Results",
        colour=discord.Colour.gold() if not no_scores else discord.Colour.dark_grey(),
    )

    if no_scores:
        embed.description = "No points scored!"
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
        medal = MEDALS[i] if i < len(MEDALS) and p.score > 0 else "\u25aa\ufe0f"
        lines.append(f"{medal} **{p.display_name}** \u2014 {p.score} pts")
    embed.add_field(name="Scores", value="\n".join(lines), inline=False)

    embed.add_field(
        name="Bonuses Played", value=str(table.bonus_num), inline=True,
    )

    if elo_changes:
        sorted_players = sorted(table.players.values(), key=lambda p: p.score, reverse=True)
        elo_lines: list[str] = []
        for p in sorted_players:
            if p.user_id in elo_changes:
                old, new = elo_changes[p.user_id]
                elo_lines.append(f"**{p.display_name}**: {fmt_elo_change(old, new)}")
        if elo_lines:
            embed.add_field(name="\U0001f4c8 ELO", value="\n".join(elo_lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


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
                "Already ending\u2026", ephemeral=True,
            )
            return
        self.table.stop_requested = True
        self.table.part_solved.set()  # wake up the game loop immediately
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
        self.leave_btn.disabled = not betting
        self.close_btn.disabled = self.table.phase == "closed"
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
        self.table.players[uid] = QBPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
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
        if self.table.phase in ("playing", "between_parts", "between_bonuses"):
            # Game is running — signal stop and let the game loop end gracefully
            if self.table.stop_requested:
                await interaction.response.send_message(
                    "Already ending\u2026", ephemeral=True,
                )
                return
            self.table.stop_requested = True
            self.table.part_solved.set()  # wake up the game loop
            self.close_btn.disabled = True
            self.close_btn.label = "Ending\u2026"
            await interaction.response.edit_message(view=self)
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
            consecutive_unanswered = 0
            consecutive_fetch_errors = 0
            async with httpx.AsyncClient() as client:
                while not table.stop_requested:
                    # Refill queue if empty
                    if not table.bonus_queue:
                        try:
                            bonuses = await _fetch_bonuses(
                                client, table.category, table.difficulty,
                            )
                            table.bonus_queue.extend(bonuses)
                            consecutive_fetch_errors = 0
                        except Exception as e:
                            consecutive_fetch_errors += 1
                            log.warning(
                                "Quizbowl fetch error #%d (channel %s): %s",
                                consecutive_fetch_errors, table.channel_id, e,
                            )
                            if consecutive_fetch_errors >= 5:
                                if table.thread:
                                    await table.thread.send(
                                        "⚠️ Failed to fetch questions after 5 attempts. Ending game."
                                    )
                                break
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

                        # Stop requested while we were waiting — bail immediately
                        if table.stop_requested:
                            break

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

                        # Inactivity: end if nobody answered N parts in a row
                        if table.part_winner is None:
                            consecutive_unanswered += 1
                        else:
                            consecutive_unanswered = 0
                        if consecutive_unanswered >= INACTIVITY_ROUNDS:
                            if table.thread:
                                try:
                                    await table.thread.send(
                                        "\u23f8\ufe0f No one answered for 5 consecutive questions \u2014 ending due to inactivity."
                                    )
                                except discord.HTTPException:
                                    pass
                            break
                        if time.monotonic() - table.last_activity > INACTIVITY_SECS:
                            if table.thread:
                                try:
                                    await table.thread.send(
                                        "\u23f8\ufe0f No activity for 3 minutes \u2014 ending due to inactivity."
                                    )
                                except discord.HTTPException:
                                    pass
                            break

                        # Delay between parts (shorter than between bonuses)
                        if part_idx < 2 and not table.stop_requested:
                            await asyncio.sleep(BETWEEN_PARTS_DELAY)

                    else:
                        # for-loop completed normally (no inactivity break)
                        # Post bonus summary
                        if table.thread and not table.stop_requested:
                            table.phase = "between_bonuses"
                            await table.thread.send(embed=_bonus_summary_embed(table))
                            await asyncio.sleep(BETWEEN_BONUS_DELAY)
                        continue
                    # for-loop exited via inactivity break — propagate to outer while
                    break

            # Game ended
            await self._end_game()

        except asyncio.CancelledError:
            table.phase = "closed"
            self.active_tables.pop(table.channel_id, None)
            if table.thread:
                try:
                    await table.thread.edit(archived=True)
                except Exception:
                    pass
        except Exception as e:
            log.error("Quizbowl game loop crashed (channel %s): %s", table.channel_id, e, exc_info=True)
            table.phase = "closed"
            self.active_tables.pop(table.channel_id, None)
            if table.thread:
                try:
                    await table.thread.edit(archived=True)
                except Exception:
                    pass

    async def _end_game(self) -> None:
        table = self.table
        table.phase = "closed"

        elo_changes: dict[int, tuple[float, float]] = {}
        if len(table.players) >= 2:
            sorted_p = sorted(table.players.values(), key=lambda p: p.score, reverse=True)
            finish_order = [p.user_id for p in sorted_p]
            try:
                elo_changes = await update_elo_multiplayer(finish_order, "quizbowl", "quizbowl")
            except Exception as e:
                log.warning("Failed to update ELO for quizbowl game: %s", e)

        embed = _final_embed(table, elo_changes)

        # Post final results in thread
        if table.thread:
            try:
                await table.thread.send(embed=embed)
            except discord.HTTPException:
                pass
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
        table.phase = "closed"

        embed = discord.Embed(
            title="\U0001f9e0 Quiz Bowl \u2014 Closed",
            description="Table closed.",
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

        # If game was in progress with scores, show final results
        if table.total_parts_played > 0:
            table.phase = "closed"
            embed = _final_embed(table)
            embed.title = "\U0001f9e0 Quiz Bowl \u2014 Timed Out"

            if table.thread:
                try:
                    await table.thread.send(embed=embed)
                    await table.thread.edit(archived=True)
                except discord.HTTPException:
                    pass

        table.phase = "closed"
        self.active_tables.pop(table.channel_id, None)

        if table.message:
            try:
                embed = discord.Embed(
                    title="\U0001f9e0 Quiz Bowl \u2014 Timed Out",
                    description="Table timed out. Scores settled."
                    if table.total_parts_played > 0
                    else "Table timed out.",
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
            existing = self.active_tables[channel_id]
            game_task = getattr(existing, "game_task", None)
            if game_task is not None and not game_task.done():
                # Game loop is actively running — block
                await interaction.response.send_message(
                    "There's already a quiz bowl game running in this channel!",
                    ephemeral=True,
                )
                return
            # No active game loop (idle lobby or finished) — clean up
            del self.active_tables[channel_id]

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

        if table is None:
            return

        # Snapshot mutable state before any await so we can re-validate later.
        # We also use submission_mono to handle the race where asyncio processes
        # the wait_for timeout before dispatching on_message: if the handler fires
        # within 0.5 s after the deadline we still accept the answer.
        submission_mono = time.monotonic()
        captured_part_idx = table.current_part_idx
        captured_bonus_num = table.bonus_num

        if table.phase == "between_parts":
            # Allow a short grace window for late handler dispatch
            if submission_mono - table.part_start_time > PART_TIME + 0.5:
                return
        elif table.phase != "playing":
            return

        uid = message.author.id
        if uid not in table.players:
            return

        if table.part_winner is not None:
            return

        guess = message.content.strip()
        if len(guess) < 2:
            return

        bonus = table.current_bonus
        if bonus is None:
            return

        answerline = bonus["answers"][captured_part_idx]

        try:
            directive = await _check_answer(self._http, answerline, guess)
        except Exception:
            # API unavailable — fall back to local fuzzy matching so a network
            # hiccup doesn't silently discard a correct answer.
            directive = "accept" if _local_check_answer(answerline, guess) else "reject"

        # "prompt" means the answer is close enough to warrant clarification.
        # In Discord there is no back-and-forth, so treat it as correct.
        if directive == "prompt":
            directive = "accept"

        # Re-validate: the async API call may have taken a while.  If the bonus
        # or part advanced while we waited, discard this result entirely.
        if table.bonus_num != captured_bonus_num or table.current_part_idx != captured_part_idx:
            return
        if table.part_winner is not None or captured_part_idx in table.bonus_part_results:
            return

        if directive == "accept":
            player = table.players[uid]
            player.score += 10
            table.part_winner = uid
            table.last_activity = time.monotonic()
            try:
                await message.add_reaction("\u2705")
            except discord.HTTPException:
                pass
            # Only signal the game loop if it is still waiting (phase == "playing").
            # If the timer already expired the event is harmless but we skip it to
            # avoid confusing the next part's wait.
            if table.phase == "playing":
                table.part_solved.set()
        else:
            try:
                await message.add_reaction("\u274c")
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QuizBowlCog(bot))
