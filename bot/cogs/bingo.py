"""Casino cog — multiplayer /bingo game."""

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 10
MAX_BET = 500
MIN_PLAYERS = 2
CALL_INTERVAL = 4.0  # seconds between number calls
HOUSE_EDGE = 0.05  # 5% rake

BINGO_RANGES: list[tuple[str, range]] = [
    ("B", range(1, 16)),
    ("I", range(16, 31)),
    ("N", range(31, 46)),
    ("G", range(46, 61)),
    ("O", range(61, 76)),
]
BINGO_LETTERS = ["B", "I", "N", "G", "O"]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _number_to_bingo(n: int) -> str:
    """Convert a number to its bingo call, e.g. 42 -> 'N42'."""
    for letter, rng in BINGO_RANGES:
        if n in rng:
            return f"{letter}{n}"
    return str(n)


def generate_card() -> "BingoCard":
    """Generate a random 5x5 bingo card with free center."""
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
    """Mark a number on the card if present. Returns True if found."""
    for row in range(5):
        for col in range(5):
            if card.grid[row][col] == number:
                card.marked[row][col] = True
                return True
    return False


def check_win(card: "BingoCard") -> list[str]:
    """Check for completed lines. Returns list of winning pattern names."""
    wins: list[str] = []
    # Rows
    for r in range(5):
        if all(card.marked[r][c] for c in range(5)):
            wins.append(f"Row {r + 1}")
    # Columns
    for c in range(5):
        if all(card.marked[r][c] for r in range(5)):
            wins.append(f"Col {BINGO_LETTERS[c]}")
    # Diagonals
    if all(card.marked[i][i] for i in range(5)):
        wins.append("Diagonal \\")
    if all(card.marked[i][4 - i] for i in range(5)):
        wins.append("Diagonal /")
    return wins


def format_card_text(card: "BingoCard") -> str:
    """Render a bingo card as monospace text for Discord."""
    lines = ["  B    I    N    G    O "]
    for row in range(5):
        cells: list[str] = []
        for col in range(5):
            num = card.grid[row][col]
            if row == 2 and col == 2:
                if card.marked[row][col]:
                    cells.append(" \u2605\u2605 ")  # ★★
                else:
                    cells.append(" \u2605\u2605 ")
            elif card.marked[row][col]:
                cells.append(f"[{num:02d}]")
            else:
                cells.append(f" {num:02d} ")
        lines.append(" ".join(cells))
    return "```\n" + "\n".join(lines) + "\n```"


def _format_called_grid(called_set: set[int]) -> str:
    """Format called numbers grouped by B-I-N-G-O column."""
    lines: list[str] = []
    for letter, rng in BINGO_RANGES:
        nums = sorted(n for n in called_set if n in rng)
        if nums:
            lines.append(f"**{letter}**: {' '.join(str(n) for n in nums)}")
        else:
            lines.append(f"**{letter}**: —")
    return "\n".join(lines)


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class BingoCard:
    grid: list[list[int]]  # 5x5, grid[row][col]; center = 0
    marked: list[list[bool]]  # 5x5, center starts True


@dataclass
class BingoPlayer:
    user_id: int
    display_name: str
    bet: int
    card: BingoCard
    won: bool = False
    win_patterns: list[str] = field(default_factory=list)
    payout: int = 0


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
    # Bingo-specific
    called_numbers: list[int] = field(default_factory=list)
    called_set: set[int] = field(default_factory=set)
    pool: list[int] = field(default_factory=list)  # shuffled uncalled
    winners: list[int] = field(default_factory=list)  # winner user_ids
    call_task: asyncio.Task | None = field(default=None, repr=False)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: BingoTable) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values())
    embed = discord.Embed(
        title=f"Bingo \u2014 Join the Table (Round {table.round_num})",
        description=(
            "Join and get your card! First to complete a **line** "
            "(row, column, or diagonal) wins the pot."
        ),
        colour=discord.Colour.blurple(),
    )
    if pot:
        embed.add_field(
            name="Pot",
            value=f"{pot}c (5% house rake)",
            inline=True,
        )
    if table.players:
        lines = [
            f"\U0001f3b4 **{p.display_name}** \u2014 {p.bet}c"
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


def _calling_embed(table: BingoTable) -> discord.Embed:
    embed = discord.Embed(
        title=f"Bingo \u2014 Round {table.round_num}",
        colour=discord.Colour.gold(),
    )
    if table.called_numbers:
        last = table.called_numbers[-1]
        embed.description = f"# \U0001f3b1 **{_number_to_bingo(last)}**"
    else:
        embed.description = "Starting..."
    embed.add_field(
        name=f"Called ({len(table.called_numbers)}/75)",
        value=_format_called_grid(table.called_set),
        inline=False,
    )
    names = ", ".join(p.display_name for p in table.players.values())
    embed.add_field(
        name=f"Players ({len(table.players)})",
        value=names,
        inline=False,
    )
    embed.set_footer(
        text=f"Host: {table.host_name} \u2502 Click 'My Card' to see your card",
    )
    return embed


def _finished_embed(
    table: BingoTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    # Winner announcement
    winner_names = [
        table.players[uid].display_name for uid in table.winners
    ]
    if len(winner_names) == 1:
        p = table.players[table.winners[0]]
        desc = (
            f"**{p.display_name}** wins **{p.payout}c**!\n"
            f"Pattern: {', '.join(p.win_patterns)}"
        )
    else:
        per = table.players[table.winners[0]].payout
        desc = (
            f"**{' & '.join(winner_names)}** split the pot! "
            f"({per}c each)"
        )

    embed = discord.Embed(
        title=f"Bingo \u2014 BINGO! (Round {table.round_num})",
        description=desc,
        colour=discord.Colour.gold(),
    )

    # Show winning card(s)
    for uid in table.winners:
        p = table.players[uid]
        embed.add_field(
            name=f"{p.display_name}'s Card",
            value=format_card_text(p.card),
            inline=True,
        )

    # Results per player
    lines: list[str] = []
    for p in table.players.values():
        bal = balances.get(p.user_id, 0) if balances else 0
        net = p.payout - p.bet
        sign = "+" if net >= 0 else ""
        if p.won:
            lines.append(
                f"\U0001f3c6 **{p.display_name}** \u2014 {p.bet}c \u2192 {p.payout}c "
                f"(**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        else:
            lines.append(
                f"\u274c **{p.display_name}** \u2014 {p.bet}c \u2192 0c "
                f"(**-{p.bet}c**) \u2014 bal: {bal}c"
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
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(
        self, table: BingoTable, view: "BingoTableView", balance: int,
    ) -> None:
        super().__init__(title="Join Bingo")
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

        # Deduct coins
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        card = generate_card()
        self.table.players[uid] = BingoPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
            card=card,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


# ── View ─────────────────────────────────────────────────────────────────────


class BingoTableView(ui.View):
    def __init__(
        self, table: BingoTable, active_tables: dict[int, BingoTable],
    ) -> None:
        super().__init__(timeout=300)
        self.table = table
        self.active_tables = active_tables
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
        await self._start_calling(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f3b4", row=0,
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
        await interaction.response.send_modal(
            JoinBingoModal(self.table, self, bal),
        )

    @ui.button(
        label="Re-bet", style=discord.ButtonStyle.primary, emoji="\U0001f504", row=0,
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
        card = generate_card()
        self.table.players[uid] = BingoPlayer(
            user_id=uid, display_name=name, bet=amt, card=card,
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
        if self.table.phase == "calling":
            await interaction.response.send_message(
                "Can't leave mid-game!", ephemeral=True,
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
        label="My Card", style=discord.ButtonStyle.primary, emoji="\U0001f440", row=1,
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
        await interaction.response.send_message(
            f"**Your Bingo Card:**\n{format_card_text(player.card)}",
            ephemeral=True,
        )

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
        if self.table.phase == "calling":
            await interaction.response.send_message(
                "Can't close mid-game!", ephemeral=True,
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
                    mark_card(player.card, number)

                # Check for winners
                round_winners: list[int] = []
                for uid, player in table.players.items():
                    wins = check_win(player.card)
                    if wins:
                        player.win_patterns = wins
                        round_winners.append(uid)

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

            # All 75 called, no winner (near-impossible for line wins)
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

        total_pot = sum(p.bet for p in table.players.values())
        house_take = max(1, int(total_pot * HOUSE_EDGE))
        prize_pool = total_pot - house_take
        payout_each = prize_pool // len(table.winners)

        for uid in table.winners:
            table.players[uid].won = True
            table.players[uid].payout = payout_each

        # Credit winners
        balances: dict[int, int] = {}
        for uid, player in table.players.items():
            if player.won:
                balances[uid] = await queries.update_casino_balance(
                    str(uid), payout_each,
                )
            else:
                bal = await queries.get_casino_balance(str(uid))
                balances[uid] = bal or 0

        # Save last bets
        for uid, player in table.players.items():
            table.last_bets[uid] = (player.display_name, player.bet)

        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(
                    embed=_finished_embed(table, balances=balances), view=self,
                )
            except discord.HTTPException:
                pass

    async def _resolve_no_winner(self) -> None:
        """All numbers called with no winner — refund everyone."""
        table = self.table
        table.phase = "finished"
        await self._refund_all()
        if table.message:
            try:
                embed = discord.Embed(
                    title=f"Bingo \u2014 Round {table.round_num} (No Winner)",
                    description="All 75 numbers called with no winner! Bets refunded.",
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

        table = BingoTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = BingoTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BingoCog(bot))
