"""Casino cog — multiplayer /bingo game."""

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
from bot.cogs._pool import compute_side_pot_payouts

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 10
CARD_PRICE = 500
MAX_CARDS = 5
MIN_PLAYERS = 1
CALL_INTERVAL = 2.5  # seconds between number calls

BINGO_RANGES: list[tuple[str, range]] = [
    ("B", range(1, 16)),
    ("I", range(16, 31)),
    ("N", range(31, 46)),
    ("G", range(46, 61)),
    ("O", range(61, 76)),
]
BINGO_LETTERS = ["B", "I", "N", "G", "O"]


# ── Patterns ─────────────────────────────────────────────────────────────────


def _make_target(cells: list[tuple[int, int]]) -> list[list[bool]]:
    """Build a 5x5 boolean grid from a list of (row, col) pairs."""
    grid = [[False] * 5 for _ in range(5)]
    for r, c in cells:
        grid[r][c] = True
    return grid


@dataclass
class BingoPattern:
    name: str
    emoji: str
    description: str
    target: list[list[bool]]  # 5x5, True = cell must be marked to win

    def check(self, card: "BingoCard") -> bool:
        for r in range(5):
            for c in range(5):
                if self.target[r][c] and not card.marked[r][c]:
                    return False
        return True

    def progress(self, card: "BingoCard") -> tuple[int, int]:
        total = marked = 0
        for r in range(5):
            for c in range(5):
                if self.target[r][c]:
                    total += 1
                    if card.marked[r][c]:
                        marked += 1
        return marked, total

    def preview_text(self) -> str:
        lines = ["  B  I  N  G  O"]
        for r in range(5):
            cells = []
            for c in range(5):
                if r == 2 and c == 2:
                    cells.append(" \u2605 ")
                elif self.target[r][c]:
                    cells.append(" \u25a0 ")
                else:
                    cells.append(" \u00b7 ")
            lines.append("".join(cells))
        return "```\n" + "\n".join(lines) + "\n```"


BINGO_PATTERNS: list[BingoPattern] = [
    BingoPattern(
        name="Four Corners",
        emoji="\U0001f4d0",
        description="Mark all four corners",
        target=_make_target([(0, 0), (0, 4), (4, 0), (4, 4)]),
    ),
    BingoPattern(
        name="X",
        emoji="\u274c",
        description="Complete both diagonals",
        target=_make_target([
            (0, 0), (1, 1), (2, 2), (3, 3), (4, 4),
            (0, 4), (1, 3), (3, 1), (4, 0),
        ]),
    ),
    BingoPattern(
        name="Plus",
        emoji="\u2795",
        description="Fill the center row and center column",
        target=_make_target([
            (0, 2), (1, 2), (2, 0), (2, 1), (2, 2), (2, 3), (2, 4),
            (3, 2), (4, 2),
        ]),
    ),
    BingoPattern(
        name="Diamond",
        emoji="\U0001f48e",
        description="Complete the diamond shape",
        target=_make_target([
            (0, 2), (1, 1), (1, 3), (2, 0), (2, 2), (2, 4),
            (3, 1), (3, 3), (4, 2),
        ]),
    ),
    BingoPattern(
        name="T Shape",
        emoji="\u2b06\ufe0f",
        description="Fill the top row and center column",
        target=_make_target([
            (0, 0), (0, 1), (0, 2), (0, 3), (0, 4),
            (1, 2), (2, 2), (3, 2), (4, 2),
        ]),
    ),
    BingoPattern(
        name="L Shape",
        emoji="\u2199\ufe0f",
        description="Fill the left column and bottom row",
        target=_make_target([
            (0, 0), (1, 0), (2, 0), (3, 0), (4, 0),
            (4, 1), (4, 2), (4, 3), (4, 4),
        ]),
    ),
]


def _pick_pattern(last_idx: int = -1) -> tuple[BingoPattern, int]:
    """Pick a random pattern, avoiding the last one used."""
    choices = list(range(len(BINGO_PATTERNS)))
    if last_idx >= 0 and len(choices) > 1:
        choices.remove(last_idx)
    idx = random.choice(choices)
    return BINGO_PATTERNS[idx], idx


# ── Helpers ──────────────────────────────────────────────────────────────────


def _number_to_bingo(n: int) -> str:
    for letter, rng in BINGO_RANGES:
        if n in rng:
            return f"{letter}{n}"
    return str(n)


def generate_card() -> "BingoCard":
    grid: list[list[int]] = [[0] * 5 for _ in range(5)]
    for col_idx, (_, rng) in enumerate(BINGO_RANGES):
        nums = random.sample(list(rng), 5)
        for row in range(5):
            grid[row][col_idx] = nums[row]
    grid[2][2] = 0  # free space
    marked = [[False] * 5 for _ in range(5)]
    marked[2][2] = True
    return BingoCard(grid=grid, marked=marked)


def mark_card(card: "BingoCard", number: int) -> bool:
    for row in range(5):
        for col in range(5):
            if card.grid[row][col] == number:
                card.marked[row][col] = True
                return True
    return False


def format_card_text(
    card: "BingoCard", pattern: BingoPattern | None = None,
) -> str:
    """Render a bingo card.

    [XX] = marked (number was called)
    >XX< = target cell you still need
     XX  = other cell
    """
    lines = ["  B    I    N    G    O "]
    for row in range(5):
        cells: list[str] = []
        for col in range(5):
            num = card.grid[row][col]
            if row == 2 and col == 2:
                cells.append(" \u2605\u2605 ")
            elif card.marked[row][col]:
                cells.append(f"[{num:02d}]")
            elif pattern and pattern.target[row][col]:
                cells.append(f">{num:02d}<")
            else:
                cells.append(f" {num:02d} ")
        lines.append(" ".join(cells))
    if pattern:
        marked, total = pattern.progress(card)
        need = total - marked
        if need == 0:
            lines.append(f"\n{pattern.emoji} {pattern.name}: COMPLETE!")
        else:
            bar = "\u2588" * marked + "\u2591" * need
            lines.append(f"\n{pattern.emoji} {pattern.name}: {bar} {marked}/{total}")
    return "```\n" + "\n".join(lines) + "\n```"


def _format_called_grid(called_set: set[int]) -> str:
    lines: list[str] = []
    for letter, rng in BINGO_RANGES:
        nums = sorted(n for n in called_set if n in rng)
        if nums:
            lines.append(f"**{letter}**: {' '.join(str(n) for n in nums)}")
        else:
            lines.append(f"**{letter}**: \u2014")
    return "\n".join(lines)


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class BingoCard:
    grid: list[list[int]]  # 5x5, center = 0
    marked: list[list[bool]]  # 5x5, center starts True


@dataclass
class BingoPlayer:
    user_id: int
    display_name: str
    num_cards: int
    cards: list[BingoCard]
    won: bool = False
    winning_card: int = -1  # index into cards
    payout: int = 0

    @property
    def cost(self) -> int:
        return self.num_cards * CARD_PRICE


@dataclass
class BingoTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | calling | finished
    players: dict[int, BingoPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    # Pattern
    pattern: BingoPattern | None = None
    last_pattern_idx: int = -1
    # Bingo state
    called_numbers: list[int] = field(default_factory=list)
    called_set: set[int] = field(default_factory=set)
    pool: list[int] = field(default_factory=list)
    winners: list[int] = field(default_factory=list)
    call_task: asyncio.Task | None = field(default=None, repr=False)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: BingoTable) -> discord.Embed:
    total_cards = sum(p.num_cards for p in table.players.values())
    pot = total_cards * CARD_PRICE
    pat = table.pattern

    title = f"Bingo \u2014 Join the Table (Round {table.round_num})"
    if pat:
        desc = (
            f"First to complete the **{pat.name}** pattern wins the pot!\n"
            f"{pat.description}."
        )
    else:
        desc = "Join and get your cards!"

    embed = discord.Embed(
        title=title, description=desc, colour=discord.Colour.blurple(),
    )

    if pat:
        embed.add_field(
            name=f"{pat.emoji} Pattern: {pat.name}",
            value=pat.preview_text(),
            inline=False,
        )

    if pot:
        embed.add_field(
            name="Pot", value=f"{pot}c ({total_cards} cards)", inline=True,
        )

    if table.players:
        lines = [
            f"\U0001f3b4 **{p.display_name}** \u2014 "
            f"{p.num_cards} card{'s' if p.num_cards > 1 else ''} ({p.cost}c)"
            for p in table.players.values()
        ]
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet \u2014 click Join!*",
            inline=False,
        )
    embed.set_footer(
        text=(
            f"Host: {table.host_name} \u2502 "
            f"{CARD_PRICE}c/card, max {MAX_CARDS} \u2502 "
            f"Min {MIN_PLAYERS} players"
        ),
    )
    return embed


def _calling_embed(table: BingoTable) -> discord.Embed:
    pat = table.pattern
    embed = discord.Embed(
        title=f"Bingo \u2014 Round {table.round_num}",
        colour=discord.Colour.gold(),
    )

    if table.called_numbers:
        last = table.called_numbers[-1]
        embed.description = f"# \U0001f3b1 **{_number_to_bingo(last)}**"
    else:
        embed.description = "Starting..."

    # Pattern progress — show every player's best card progress
    if pat and table.players:
        progress_lines: list[tuple[int, int, str]] = []
        for p in table.players.values():
            best_m = 0
            best_card = None
            total = 0
            for card in p.cards:
                m, t = pat.progress(card)
                total = t
                if m > best_m:
                    best_m = m
                    best_card = card
            need = total - best_m
            bar = "\u2588" * best_m + "\u2591" * need
            line = f"{bar} {best_m}/{total} **{p.display_name}**"
            if need <= 2 and best_card is not None:
                needed = [
                    _number_to_bingo(best_card.grid[r][c])
                    for r in range(5) for c in range(5)
                    if pat.target[r][c] and not best_card.marked[r][c]
                ]
                if needed:
                    line += f" (needs **{', '.join(needed)}**)"
            progress_lines.append((best_m, total, line))
        progress_lines.sort(key=lambda x: x[0], reverse=True)
        embed.add_field(
            name=f"{pat.emoji} {pat.name}",
            value="\n".join(line for _, _, line in progress_lines),
            inline=False,
        )

    embed.add_field(
        name=f"Called ({len(table.called_numbers)}/75)",
        value=_format_called_grid(table.called_set),
        inline=False,
    )
    embed.set_footer(
        text=f"Host: {table.host_name} \u2502 Click 'My Cards' to see your cards",
    )
    return embed


def _finished_embed(
    table: BingoTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    pat = table.pattern
    winner_names = [table.players[uid].display_name for uid in table.winners]
    pat_str = f"\nPattern: {pat.emoji} {pat.name}" if pat else ""

    if len(winner_names) == 1:
        p = table.players[table.winners[0]]
        desc = f"**{p.display_name}** wins **{p.payout}c**!{pat_str}"
    else:
        winner_payouts = [table.players[uid].payout for uid in table.winners]
        if len(set(winner_payouts)) == 1:
            desc = (
                f"**{' & '.join(winner_names)}** split the pot! "
                f"({winner_payouts[0]}c each){pat_str}"
            )
        else:
            parts = [
                f"**{table.players[uid].display_name}** {table.players[uid].payout}c"
                for uid in table.winners
            ]
            desc = f"{', '.join(parts)} split the pot!{pat_str}"

    embed = discord.Embed(
        title=f"Bingo \u2014 BINGO! (Round {table.round_num})",
        description=desc,
        colour=discord.Colour.gold(),
    )

    # Show winning card(s)
    for uid in table.winners:
        p = table.players[uid]
        card = p.cards[p.winning_card] if p.winning_card >= 0 else p.cards[0]
        label = f"{p.display_name}'s Winning Card"
        if p.num_cards > 1:
            label += f" (#{p.winning_card + 1} of {p.num_cards})"
        embed.add_field(
            name=label,
            value=format_card_text(card, pat),
            inline=True,
        )

    # Results per player
    lines: list[str] = []
    for p in table.players.values():
        bal = balances.get(p.user_id, 0) if balances else 0
        net = p.payout - p.cost
        sign = "+" if net >= 0 else ""
        cards_str = f"{p.num_cards} card{'s' if p.num_cards > 1 else ''}"
        if p.won:
            lines.append(
                f"\U0001f3c6 **{p.display_name}** ({cards_str}) \u2014 "
                f"{p.cost}c \u2192 {p.payout}c "
                f"(**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        elif p.payout > 0:
            lines.append(
                f"\U0001f4b0 **{p.display_name}** ({cards_str}) \u2014 "
                f"{p.cost}c \u2192 {p.payout}c "
                f"(**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        else:
            lines.append(
                f"\u274c **{p.display_name}** ({cards_str}) \u2014 "
                f"{p.cost}c \u2192 0c "
                f"(**-{p.cost}c**) \u2014 bal: {bal}c"
            )
    embed.add_field(name="Results", value="\n".join(lines), inline=False)
    embed.add_field(
        name="Numbers Called",
        value=str(len(table.called_numbers)),
        inline=True,
    )
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modal ────────────────────────────────────────────────────────────────────


class JoinBingoModal(ui.Modal):
    num_cards_input = ui.TextInput(
        label=f"Number of cards ({CARD_PRICE}c each)",
        placeholder="1-5",
        required=True,
        max_length=1,
    )

    def __init__(
        self, table: BingoTable, view: "BingoTableView", balance: int,
    ) -> None:
        super().__init__(title="Join Bingo")
        self.table = table
        self.table_view = view
        max_affordable = min(MAX_CARDS, balance // CARD_PRICE)
        self.num_cards_input.placeholder = f"1-{max_affordable} (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            num = int(self.num_cards_input.value)
        except ValueError:
            await interaction.response.send_message(
                f"Enter a number 1\u2013{MAX_CARDS}.", ephemeral=True,
            )
            return
        if num < 1 or num > MAX_CARDS:
            await interaction.response.send_message(
                f"Must be 1\u2013{MAX_CARDS} cards.", ephemeral=True,
            )
            return

        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in this round!", ephemeral=True,
            )
            return

        cost = num * CARD_PRICE
        try:
            await queries.update_casino_balance(str(uid), -cost)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            max_affordable = bal // CARD_PRICE
            await interaction.response.send_message(
                f"Not enough coins for {num} cards ({cost}c)! "
                f"You have {bal}c (max {max_affordable} cards).",
                ephemeral=True,
            )
            return

        cards = [generate_card() for _ in range(num)]
        self.table.players[uid] = BingoPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            num_cards=num,
            cards=cards,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


# ── View ─────────────────────────────────────────────────────────────────────


class PatternSelect(ui.Select["BingoTableView"]):
    def __init__(self, table: BingoTable) -> None:
        self.table = table
        options = [
            discord.SelectOption(
                label=p.name,
                value=str(i),
                emoji=p.emoji,
                description=p.description,
                default=(i == table.last_pattern_idx),
            )
            for i, p in enumerate(BINGO_PATTERNS)
        ]
        super().__init__(
            placeholder="Pattern", options=options, row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can pick the pattern!", ephemeral=True,
            )
            return
        idx = int(self.values[0])
        self.table.pattern = BINGO_PATTERNS[idx]
        self.table.last_pattern_idx = idx
        # Update default selection
        for opt in self.options:
            opt.default = (opt.value == self.values[0])
        view: BingoTableView = self.view  # type: ignore[assignment]
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=view,
        )


class BingoTableView(ui.View):
    def __init__(
        self, table: BingoTable, active_tables: dict[int, BingoTable],
    ) -> None:
        super().__init__(timeout=300)
        self.table = table
        self.active_tables = active_tables
        self.pattern_select = PatternSelect(table)
        self.add_item(self.pattern_select)
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        calling = phase == "calling"
        finished = phase == "finished"

        # Row 0
        self.start_btn.disabled = (
            not betting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = calling

        # Row 1
        self.my_card_btn.disabled = not calling
        self.new_round_btn.disabled = not finished
        self.close_btn.disabled = calling

        # Row 2 — pattern selector
        self.pattern_select.disabled = not betting

    # ── Row 0 ────────────────────────────────────────────────────────────────

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
                f"Need at least {MIN_PLAYERS} players!", ephemeral=True,
            )
            return
        await self._start_calling(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary,
        emoji="\U0001f3b4", row=0,
    )
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
        if bal < CARD_PRICE:
            await interaction.response.send_message(
                f"Need at least {CARD_PRICE}c to buy a card! (bal: {bal}c)",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            JoinBingoModal(self.table, self, bal),
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
                "No previous bet \u2014 use Join instead.", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message(
                "Table is full!", ephemeral=True,
            )
            return
        name, num_cards = last
        cost = num_cards * CARD_PRICE
        try:
            await queries.update_casino_balance(str(uid), -cost)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for {num_cards} cards ({cost}c)! "
                f"(have {bal}c)",
                ephemeral=True,
            )
            return
        cards = [generate_card() for _ in range(num_cards)]
        self.table.players[uid] = BingoPlayer(
            user_id=uid, display_name=name, num_cards=num_cards, cards=cards,
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
        if self.table.phase == "calling":
            await interaction.response.send_message(
                "Can't leave mid-game!", ephemeral=True,
            )
            return
        if self.table.phase == "betting":
            await queries.update_casino_balance(str(uid), player.cost)
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
        label="My Cards", style=discord.ButtonStyle.primary,
        emoji="\U0001f440", row=1,
    )
    async def my_card_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        pat = self.table.pattern

        # Build (original_index, card) pairs, sorted by closest to winning
        indexed_cards = list(enumerate(player.cards))
        if pat and len(indexed_cards) > 2:
            indexed_cards.sort(
                key=lambda ic: pat.progress(ic[1])[0], reverse=True,
            )
            indexed_cards = indexed_cards[:2]

        parts: list[str] = []
        for i, card in indexed_cards:
            if player.num_cards > 1:
                if pat:
                    m, t = pat.progress(card)
                    parts.append(f"**Card {i + 1}** ({m}/{t})")
                else:
                    parts.append(f"**Card {i + 1}**")
            parts.append(format_card_text(card, pat))

        if player.num_cards > 2 and pat:
            header = f"**Best 2 of {player.num_cards} cards:**"
        elif player.num_cards > 1:
            header = f"**Your Cards ({player.num_cards}):**"
        else:
            header = "**Your Card:**"
        msg = header + "\n" + "\n".join(parts)
        await interaction.response.send_message(msg, ephemeral=True)

    @ui.button(
        label="New Round", style=discord.ButtonStyle.success,
        emoji="\u25b6\ufe0f", row=1,
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
        if self.table.phase == "calling":
            await interaction.response.send_message(
                "Can't close mid-game!", ephemeral=True,
            )
            return
        if self.table.phase == "betting":
            for p in self.table.players.values():
                try:
                    await queries.update_casino_balance(
                        str(p.user_id), p.cost,
                    )
                except Exception:
                    pass
        await self._close(interaction, "Table closed by host.")

    # ── Game logic ───────────────────────────────────────────────────────────

    async def _start_calling(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "calling"
        table.pool = list(range(1, 76))
        random.shuffle(table.pool)
        table.called_numbers = []
        table.called_set = set()
        table.winners = []

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_calling_embed(table), view=self,
        )
        table.call_task = asyncio.create_task(self._call_loop())

    async def _call_loop(self) -> None:
        table = self.table
        try:
            while table.pool:
                await asyncio.sleep(CALL_INTERVAL)

                number = table.pool.pop()
                table.called_numbers.append(number)
                table.called_set.add(number)

                # Auto-mark all player cards
                for player in table.players.values():
                    for card in player.cards:
                        mark_card(card, number)

                # Check for winners against the pattern
                round_winners: list[int] = []
                if table.pattern:
                    for uid, player in table.players.items():
                        for i, card in enumerate(player.cards):
                            if table.pattern.check(card):
                                player.winning_card = i
                                round_winners.append(uid)
                                break  # one win per player

                if round_winners:
                    table.winners = round_winners
                    await self._resolve_win()
                    return

                # Update display
                if table.message:
                    try:
                        await table.message.edit(
                            embed=_calling_embed(table), view=self,
                        )
                    except discord.HTTPException:
                        pass

            # All 75 called, no winner
            await self._resolve_no_winner()

        except asyncio.CancelledError:
            pass
        except Exception:
            if table.phase == "calling":
                table.phase = "finished"
                await self._refund_all()

    async def _resolve_win(self) -> None:
        table = self.table
        table.phase = "finished"

        # Side-pot payouts (no house edge for bingo)
        bets = {uid: p.cost for uid, p in table.players.items()}
        payouts = compute_side_pot_payouts(bets, table.winners, house_edge=0.0)
        for uid in table.winners:
            table.players[uid].won = True
        for uid, payout in payouts.items():
            table.players[uid].payout = payout

        # Credit payouts
        balances: dict[int, int] = {}
        for uid, player in table.players.items():
            if player.payout > 0:
                balances[uid] = await queries.update_casino_balance(
                    str(uid), player.payout,
                )
            else:
                bal = await queries.get_casino_balance(str(uid))
                balances[uid] = bal or 0

        # Save last bets for re-bet
        for uid, player in table.players.items():
            table.last_bets[uid] = (player.display_name, player.num_cards)

        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(
                    embed=_finished_embed(table, balances=balances), view=self,
                )
            except discord.HTTPException:
                pass

    async def _resolve_no_winner(self) -> None:
        table = self.table
        table.phase = "finished"
        await self._refund_all()
        if table.message:
            try:
                embed = discord.Embed(
                    title=f"Bingo \u2014 Round {table.round_num} (No Winner)",
                    description=(
                        "All 75 numbers called with no winner! Bets refunded."
                    ),
                    colour=discord.Colour.dark_grey(),
                )
                self._update_buttons()
                await table.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _start_new_round(self) -> None:
        table = self.table
        table.players.clear()
        table.phase = "betting"
        table.round_num += 1
        table.called_numbers.clear()
        table.called_set.clear()
        table.pool.clear()
        table.winners.clear()
        table.call_task = None
        # Pick a new pattern (different from last round)
        pattern, idx = _pick_pattern(table.last_pattern_idx)
        table.pattern = pattern
        table.last_pattern_idx = idx
        # Refresh the select default
        for opt in self.pattern_select.options:
            opt.default = (opt.value == str(idx))

    async def _refund_all(self) -> None:
        for p in self.table.players.values():
            try:
                await queries.update_casino_balance(str(p.user_id), p.cost)
            except Exception:
                pass

    async def _close(
        self, interaction: discord.Interaction, reason: str,
    ) -> None:
        embed = discord.Embed(
            title="Bingo Table \u2014 Closed",
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

        if table.call_task and not table.call_task.done():
            table.call_task.cancel()

        if table.phase == "finished":
            self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    embed = discord.Embed(
                        title="Bingo Table \u2014 Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    pass
            return

        # Betting or calling — refund all
        await self._refund_all()
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="Bingo Table \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class BingoCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, BingoTable] = {}

    @app_commands.command(
        name="bingo", description="Open a Bingo table (multiplayer)",
    )
    async def bingo(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a Bingo table in this channel!",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        # Pick the first pattern
        pattern, idx = _pick_pattern()

        table = BingoTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
            pattern=pattern,
            last_pattern_idx=idx,
        )
        self.active_tables[channel_id] = table

        view = BingoTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BingoCog(bot))
