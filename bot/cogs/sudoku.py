"""Casino cog — multiplayer /sudoku sprint game.

Everyone gets the same 4x4 mini-Sudoku puzzle. First to submit the correct
solution wins the round. First to WINS_TO_WIN round wins takes the pot.
Submissions via button modal (private).
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from itertools import groupby

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 1
ROUND_TIME = 90  # seconds per round
ROUND_DELAY = 5  # seconds between rounds
WINS_TO_WIN = 3  # first to N wins
MAX_ROUNDS = 15  # safety cap
BLANKS = 8  # cells to remove from the 4x4 grid (out of 16)

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

MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]

# ── Sudoku logic (4x4) ──────────────────────────────────────────────────────

Grid = list[list[int]]  # 4x4, 0 = blank


def _generate_grid() -> Grid:
    """Generate a random valid completed 4x4 Sudoku grid."""
    grid: Grid = [[0] * 4 for _ in range(4)]

    def _valid(g: Grid, r: int, c: int, num: int) -> bool:
        if num in g[r]:
            return False
        if any(g[row][c] == num for row in range(4)):
            return False
        br, bc = (r // 2) * 2, (c // 2) * 2
        for dr in range(2):
            for dc in range(2):
                if g[br + dr][bc + dc] == num:
                    return False
        return True

    def _fill(g: Grid, pos: int = 0) -> bool:
        if pos == 16:
            return True
        r, c = divmod(pos, 4)
        nums = [1, 2, 3, 4]
        random.shuffle(nums)
        for n in nums:
            if _valid(g, r, c, n):
                g[r][c] = n
                if _fill(g, pos + 1):
                    return True
                g[r][c] = 0
        return False

    _fill(grid)
    return grid


def _count_solutions(puzzle: Grid, limit: int = 2) -> int:
    """Count solutions for a puzzle, stopping early at *limit*."""
    count = 0

    def _valid(g: Grid, r: int, c: int, num: int) -> bool:
        if num in g[r]:
            return False
        if any(g[row][c] == num for row in range(4)):
            return False
        br, bc = (r // 2) * 2, (c // 2) * 2
        for dr in range(2):
            for dc in range(2):
                if g[br + dr][bc + dc] == num:
                    return False
        return True

    blanks = [(r, c) for r in range(4) for c in range(4) if puzzle[r][c] == 0]

    def _solve(idx: int) -> None:
        nonlocal count
        if count >= limit:
            return
        if idx == len(blanks):
            count += 1
            return
        r, c = blanks[idx]
        for n in range(1, 5):
            if _valid(puzzle, r, c, n):
                puzzle[r][c] = n
                _solve(idx + 1)
                puzzle[r][c] = 0

    _solve(0)
    return count


def _make_puzzle(solution: Grid, n_blanks: int = BLANKS) -> Grid:
    """Remove *n_blanks* cells from a completed grid, ensuring unique solution."""
    puzzle: Grid = [row[:] for row in solution]
    cells = [(r, c) for r in range(4) for c in range(4)]
    random.shuffle(cells)
    removed = 0
    for r, c in cells:
        if removed >= n_blanks:
            break
        saved = puzzle[r][c]
        puzzle[r][c] = 0
        if _count_solutions([row[:] for row in puzzle]) == 1:
            removed += 1
        else:
            puzzle[r][c] = saved
    return puzzle


def _format_grid(puzzle: Grid) -> str:
    """Pretty-print a 4x4 Sudoku grid as a code block."""
    lines: list[str] = []
    lines.append("┌───────┬───────┐")
    for r in range(4):
        cells = []
        for c in range(4):
            v = puzzle[r][c]
            cells.append(str(v) if v != 0 else "_")
        row_str = f"│ {cells[0]}   {cells[1]} │ {cells[2]}   {cells[3]} │"
        lines.append(row_str)
        if r == 1:
            lines.append("├───────┼───────┤")
    lines.append("└───────┴───────┘")
    return "```\n" + "\n".join(lines) + "\n```"


def _row_display(puzzle: Grid, row: int) -> str:
    """Display a single row with blanks as underscores, for modal placeholder."""
    return "".join(str(puzzle[row][c]) if puzzle[row][c] != 0 else "_" for c in range(4))


# ── Payout helpers ───────────────────────────────────────────────────────────


def _compute_payouts(
    players: dict[int, "SudokuPlayer"], prize_pool: int, n_players: int,
) -> dict[int, int]:
    """Compute per-player payouts using the paytable."""
    pct_table = PAYTABLE.get(n_players, PAYTABLE[8])

    in_money = sorted(
        [p for p in players.values() if p.rounds_won > 0],
        key=lambda p: p.rounds_won,
        reverse=True,
    )

    payouts: dict[int, int] = {uid: 0 for uid in players}

    if not in_money:
        return payouts

    paid_positions = len(pct_table)
    pos = 0
    for _wins, group_iter in groupby(in_money, key=lambda p: p.rounds_won):
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
        top_wins = in_money[0].rounds_won
        top_group = [p for p in in_money if p.rounds_won == top_wins]
        extra = leftover // len(top_group)
        for p in top_group:
            payouts[p.user_id] += extra

    return payouts


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class SudokuPlayer:
    user_id: int
    display_name: str
    bet: int
    rounds_won: int = 0
    solved: bool = False
    solve_time: float = 0.0
    attempts: int = 0


@dataclass
class SudokuTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | between_rounds | closed
    players: dict[int, SudokuPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 0
    puzzle: Grid = field(default_factory=lambda: [[0] * 4 for _ in range(4)])
    solution: Grid = field(default_factory=lambda: [[0] * 4 for _ in range(4)])
    round_start_time: float = 0.0
    round_winner: int | None = None
    race_task: asyncio.Task | None = field(default=None, repr=False)
    round_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    total_rounds_played: int = 0


# ── Embeds ───────────────────────────────────────────────────────────────────


def _scoreboard(table: SudokuTable) -> str:
    sorted_players = sorted(
        table.players.values(), key=lambda p: p.rounds_won, reverse=True,
    )
    lines: list[str] = []
    for i, p in enumerate(sorted_players):
        prefix = MEDALS[i] if i < len(MEDALS) and p.rounds_won > 0 else "\u25aa\ufe0f"
        line = f"{prefix} **{p.display_name}** \u2014 {p.rounds_won}/{WINS_TO_WIN}"
        if p.rounds_won == WINS_TO_WIN - 1:
            line += " *(match point!)*"
        lines.append(line)
    return "\n".join(lines) if lines else "No scores yet"


def _solve_status(table: SudokuTable) -> str:
    """Show solve status for each player (no spoilers)."""
    lines: list[str] = []
    for p in table.players.values():
        if p.solved:
            lines.append(f"\u2705 **{p.display_name}** \u2014 solved!")
        else:
            att = f" ({p.attempts} attempt{'s' if p.attempts != 1 else ''})" if p.attempts else ""
            lines.append(f"\U0001f7e6 **{p.display_name}** \u2014 solving{att}")
    return "\n".join(lines) if lines else "No players"


def _betting_embed(table: SudokuTable) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values())
    n = len(table.players)

    embed = discord.Embed(
        title="\U0001f9e9 Sudoku Sprint",
        description=(
            "Race to fill in the 4\u00d74 mini-Sudoku grid!\n"
            f"**First to {WINS_TO_WIN} wins** takes the pot.\n"
            "Each row, column, and 2\u00d72 box uses digits 1\u20134 exactly once."
        ),
        colour=discord.Colour.blue(),
    )

    if pot:
        embed.add_field(name="Pot", value=f"{pot}c", inline=True)
    embed.add_field(name="Goal", value=f"First to {WINS_TO_WIN}", inline=True)

    if n >= MIN_PLAYERS:
        pt = PAYTABLE.get(n, PAYTABLE[8])
        pt_parts = [
            f"{MEDALS[i] if i < 3 else chr(0x25aa) + chr(0xfe0f)} {int(s * 100)}%"
            for i, s in enumerate(pt)
        ]
        embed.add_field(name="Paytable", value=" | ".join(pt_parts), inline=True)

    if table.players:
        lines = [
            f"\U0001f4dd **{p.display_name}** \u2014 {p.bet}c"
            + (f" ({p.rounds_won}W)" if p.rounds_won > 0 else "")
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
        text=f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} players",
    )
    return embed


def _playing_embed(table: SudokuTable, remaining: int | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"\U0001f9e9 Sudoku Sprint \u2014 Round {table.round_num} (First to {WINS_TO_WIN})",
        colour=discord.Colour.gold(),
    )

    grid_text = _format_grid(table.puzzle)
    embed.description = (
        f"{grid_text}\n"
        "**Click Solve to submit your answer!**\n"
        "Fill each row with digits 1\u20134 \u2014 first correct solution wins."
    )

    secs = remaining if remaining is not None else ROUND_TIME
    embed.add_field(name="\u23f1\ufe0f Time", value=f"**{secs}s**", inline=True)

    pot = sum(p.bet for p in table.players.values())
    embed.add_field(name="Pot", value=f"{pot}c", inline=True)

    embed.add_field(name="Status", value=_solve_status(table), inline=False)
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _round_result_embed(table: SudokuTable) -> discord.Embed:
    is_last = False
    if table.round_winner is not None:
        winner = table.players[table.round_winner]
        is_last = winner.rounds_won >= WINS_TO_WIN or table.round_num >= MAX_ROUNDS
    else:
        max_wins = max((p.rounds_won for p in table.players.values()), default=0)
        is_last = max_wins >= WINS_TO_WIN or table.round_num >= MAX_ROUNDS

    embed = discord.Embed(
        title=f"\U0001f9e9 Sudoku Sprint \u2014 Round {table.round_num}",
        colour=discord.Colour.green() if table.round_winner else discord.Colour.dark_grey(),
    )

    grid_text = _format_grid(table.solution)

    if table.round_winner is not None:
        winner = table.players[table.round_winner]
        elapsed = winner.solve_time - table.round_start_time
        embed.description = (
            f"\U0001f3c6 **{winner.display_name}** wins in **{elapsed:.1f}s**!\n\n"
            f"Solution:{grid_text}"
        )
    else:
        embed.description = f"Time's up! Nobody solved it.\n\nSolution:{grid_text}"

    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round complete \u2014 calculating results\u2026")
    return embed


def _final_embed(
    table: SudokuTable,
    *,
    payouts: dict[int, int],
    balances: dict[int, int],
) -> discord.Embed:
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)
    is_refund = max_wins == 0

    embed = discord.Embed(
        title="\U0001f9e9 Sudoku Sprint \u2014 Results",
        colour=discord.Colour.gold() if not is_refund else discord.Colour.dark_grey(),
    )

    if is_refund:
        embed.description = "No rounds were won \u2014 all bets refunded!"
    else:
        sorted_p = sorted(
            table.players.values(), key=lambda p: p.rounds_won, reverse=True,
        )
        winner = sorted_p[0]
        rw = winner.rounds_won
        embed.description = (
            f"\U0001f3c6 **{winner.display_name}** wins with "
            f"**{rw}** round{'s' if rw != 1 else ''}!"
        )

    sorted_players = sorted(
        table.players.values(), key=lambda p: p.rounds_won, reverse=True,
    )
    lines: list[str] = []
    for i, p in enumerate(sorted_players):
        payout = payouts.get(p.user_id, 0)
        bal = balances.get(p.user_id, 0)
        net = payout - p.bet
        sign = "+" if net >= 0 else ""
        medal = MEDALS[i] if i < len(MEDALS) and p.rounds_won > 0 else "\u25aa\ufe0f"
        lines.append(
            f"{medal} **{p.display_name}** ({p.rounds_won}W) \u2014 "
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
        name="Rounds Played", value=str(table.total_rounds_played), inline=True,
    )
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinSudokuModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(
        self, table: SudokuTable, view: "SudokuTableView", balance: int,
    ) -> None:
        super().__init__(title="Join Sudoku Sprint")
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

        self.table.players[uid] = SudokuPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


class SolveModal(ui.Modal):
    row1 = ui.TextInput(
        label="Row 1 (4 digits)", placeholder="e.g. 1234",
        required=True, max_length=4, min_length=4,
        style=discord.TextStyle.short,
    )
    row2 = ui.TextInput(
        label="Row 2 (4 digits)", placeholder="e.g. 3412",
        required=True, max_length=4, min_length=4,
        style=discord.TextStyle.short,
    )
    row3 = ui.TextInput(
        label="Row 3 (4 digits)", placeholder="e.g. 2143",
        required=True, max_length=4, min_length=4,
        style=discord.TextStyle.short,
    )
    row4 = ui.TextInput(
        label="Row 4 (4 digits)", placeholder="e.g. 4321",
        required=True, max_length=4, min_length=4,
        style=discord.TextStyle.short,
    )

    def __init__(self, table: SudokuTable, view: "SudokuTableView") -> None:
        super().__init__(title="Sudoku Sprint \u2014 Solve")
        self.table = table
        self.table_view = view
        # Pre-fill placeholders with the puzzle row hints
        self.row1.placeholder = _row_display(table.puzzle, 0)
        self.row2.placeholder = _row_display(table.puzzle, 1)
        self.row3.placeholder = _row_display(table.puzzle, 2)
        self.row4.placeholder = _row_display(table.puzzle, 3)
        # Pre-fill default values with given digits
        self.row1.default = _row_display(table.puzzle, 0)
        self.row2.default = _row_display(table.puzzle, 1)
        self.row3.default = _row_display(table.puzzle, 2)
        self.row4.default = _row_display(table.puzzle, 3)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id

        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "Round is not active!", ephemeral=True,
            )
            return

        player = self.table.players[uid]
        if player.solved:
            await interaction.response.send_message(
                "You already solved this round!", ephemeral=True,
            )
            return

        # Parse submission
        rows_raw = [self.row1.value.strip(), self.row2.value.strip(),
                     self.row3.value.strip(), self.row4.value.strip()]
        try:
            submitted: Grid = []
            for row_str in rows_raw:
                if len(row_str) != 4 or not row_str.isdigit():
                    raise ValueError
                row = [int(c) for c in row_str]
                if any(v < 1 or v > 4 for v in row):
                    raise ValueError
                submitted.append(row)
        except ValueError:
            await interaction.response.send_message(
                "Each row must be exactly 4 digits (1\u20134).", ephemeral=True,
            )
            return

        # Check that given cells weren't changed
        for r in range(4):
            for c in range(4):
                if self.table.puzzle[r][c] != 0 and submitted[r][c] != self.table.puzzle[r][c]:
                    await interaction.response.send_message(
                        f"Row {r + 1}: you changed a given digit! "
                        f"Position {c + 1} must be {self.table.puzzle[r][c]}.",
                        ephemeral=True,
                    )
                    return

        player.attempts += 1

        # Validate against solution
        if submitted == self.table.solution:
            player.solved = True
            player.solve_time = time.monotonic()

            elapsed = player.solve_time - self.table.round_start_time
            await interaction.response.send_message(
                f"\u2705 **Correct!** Solved in **{elapsed:.1f}s**. "
                "Waiting for the round to end\u2026",
                ephemeral=True,
            )

            # Update main embed
            if self.table.message:
                try:
                    await self.table.message.edit(
                        embed=_playing_embed(self.table), view=self.table_view,
                    )
                except discord.HTTPException:
                    pass

            # First solver ends the round immediately
            self.table.round_solved.set()
        else:
            # Find what's wrong for a helpful hint
            hint = _find_error(submitted)
            await interaction.response.send_message(
                f"\u274c Not quite! {hint} Try again.",
                ephemeral=True,
            )

            # Update main embed to show attempt count
            if self.table.message:
                try:
                    await self.table.message.edit(
                        embed=_playing_embed(self.table), view=self.table_view,
                    )
                except discord.HTTPException:
                    pass


def _find_error(grid: Grid) -> str:
    """Return a brief hint about what's wrong with the submitted grid."""
    # Check rows
    for r in range(4):
        if sorted(grid[r]) != [1, 2, 3, 4]:
            return f"Row {r + 1} doesn't have 1\u20134 exactly once."
    # Check columns
    for c in range(4):
        col = [grid[r][c] for r in range(4)]
        if sorted(col) != [1, 2, 3, 4]:
            return f"Column {c + 1} doesn't have 1\u20134 exactly once."
    # Check 2x2 boxes
    for br in (0, 2):
        for bc in (0, 2):
            box = [grid[br + dr][bc + dc] for dr in range(2) for dc in range(2)]
            if sorted(box) != [1, 2, 3, 4]:
                label = "top-left" if (br, bc) == (0, 0) else \
                        "top-right" if (br, bc) == (0, 2) else \
                        "bottom-left" if (br, bc) == (2, 0) else "bottom-right"
                return f"The {label} box doesn't have 1\u20134 exactly once."
    return "Something's off."


# ── View ─────────────────────────────────────────────────────────────────────


class SudokuTableView(ui.View):
    def __init__(
        self, table: SudokuTable, active_tables: dict[int, SudokuTable],
    ) -> None:
        super().__init__(timeout=900)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        playing = phase == "playing"
        racing = playing or phase == "between_rounds"

        self.start_btn.disabled = (
            not betting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = not betting
        self.solve_btn.disabled = not playing
        self.close_btn.disabled = racing

    # ── Row 0: Betting ────────────────────────────────────────────────────

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
        await self._start_race(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary,
        emoji="\U0001f4dd", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Race in progress! Wait for the next game.", ephemeral=True,
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
            JoinSudokuModal(self.table, self, bal),
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
        self.table.players[uid] = SudokuPlayer(
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
                "Can't leave during a race!", ephemeral=True,
            )
            return
        await queries.update_casino_balance(str(uid), player.bet)
        del self.table.players[uid]
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Row 1: Solve / Close ──────────────────────────────────────────────

    @ui.button(
        label="Solve", style=discord.ButtonStyle.success,
        emoji="\u270d\ufe0f", row=1,
    )
    async def solve_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "No round in progress!", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        player = self.table.players[uid]
        if player.solved:
            await interaction.response.send_message(
                "You already solved this round!", ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            SolveModal(self.table, self),
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
        if self.table.phase in ("playing", "between_rounds"):
            await interaction.response.send_message(
                "Can't close during a race! Wait for it to finish.",
                ephemeral=True,
            )
            return
        await self._close_table(interaction)

    # ── Race logic ────────────────────────────────────────────────────────

    def _new_puzzle(self) -> None:
        """Generate a new puzzle for the current round."""
        solution = _generate_grid()
        puzzle = _make_puzzle(solution)
        self.table.solution = solution
        self.table.puzzle = puzzle

    async def _start_race(self, interaction: discord.Interaction) -> None:
        table = self.table

        for uid, p in table.players.items():
            table.last_bets[uid] = (p.display_name, p.bet)

        self._new_puzzle()
        table.round_num = 1
        table.round_winner = None
        table.round_solved.clear()
        table.phase = "playing"
        table.round_start_time = time.monotonic()

        for p in table.players.values():
            p.solved = False
            p.solve_time = 0.0
            p.attempts = 0

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(table), view=self,
        )

        table.race_task = asyncio.create_task(self._race_loop())

    def _resolve_round_winner(self) -> None:
        """Determine the round winner: first solver (by solve_time)."""
        table = self.table
        solvers = [p for p in table.players.values() if p.solved]
        if not solvers:
            table.round_winner = None
            return
        solvers.sort(key=lambda p: p.solve_time)
        winner = solvers[0]
        winner.rounds_won += 1
        table.round_winner = winner.user_id

    async def _wait_for_round_end(self) -> None:
        """Wait for a solver or for timeout."""
        table = self.table
        deadline = table.round_start_time + ROUND_TIME

        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                return

            wait = min(15.0, remaining)
            try:
                await asyncio.wait_for(table.round_solved.wait(), timeout=wait)
                return  # someone solved it
            except asyncio.TimeoutError:
                if table.round_solved.is_set():
                    return
                now = time.monotonic()
                secs_left = max(0, int(deadline - now))
                if secs_left > 0 and table.message:
                    try:
                        await table.message.edit(
                            embed=_playing_embed(table, remaining=secs_left),
                            view=self,
                        )
                    except discord.HTTPException:
                        pass

    async def _race_loop(self) -> None:
        table = self.table
        try:
            rnd = 0
            while True:
                rnd += 1

                if rnd > 1:
                    self._new_puzzle()
                    table.round_num = rnd
                    table.round_winner = None
                    table.round_solved.clear()
                    table.phase = "playing"
                    table.round_start_time = time.monotonic()

                    for p in table.players.values():
                        p.solved = False
                        p.solve_time = 0.0
                        p.attempts = 0

                    self._update_buttons()
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_playing_embed(table), view=self,
                            )
                        except discord.HTTPException:
                            pass

                await self._wait_for_round_end()
                self._resolve_round_winner()
                table.total_rounds_played += 1

                if table.message:
                    try:
                        await table.message.edit(
                            embed=_round_result_embed(table), view=self,
                        )
                    except discord.HTTPException:
                        pass

                if any(p.rounds_won >= WINS_TO_WIN for p in table.players.values()):
                    break
                if rnd >= MAX_ROUNDS:
                    break

                table.phase = "between_rounds"
                await asyncio.sleep(ROUND_DELAY)

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
        max_wins = max((p.rounds_won for p in table.players.values()), default=0)

        if max_wins == 0:
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
            await queries.log_casino_result(str(uid), "sudoku", p.bet, payout)

        return payouts, balances

    async def _end_game(self) -> None:
        table = self.table
        table.phase = "closed"

        payouts, balances = await self._compute_and_apply_payouts()
        embed = _final_embed(table, payouts=payouts, balances=balances)

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

        if table.total_rounds_played == 0:
            for p in table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass
            embed = discord.Embed(
                title="\U0001f9e9 Sudoku Sprint \u2014 Closed",
                description="Table closed. All bets refunded.",
                colour=discord.Colour.dark_grey(),
            )
            for child in self.children:
                child.disabled = True  # type: ignore[union-attr]
            self.stop()
            self.active_tables.pop(table.channel_id, None)
            await interaction.response.edit_message(embed=embed, view=self)
            return

        table.phase = "closed"
        payouts, balances = await self._compute_and_apply_payouts()
        embed = _final_embed(table, payouts=payouts, balances=balances)

        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table

        if table.race_task and not table.race_task.done():
            table.race_task.cancel()

        if table.phase == "closed":
            return

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
                    title="\U0001f9e9 Sudoku Sprint \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class SudokuCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, SudokuTable] = {}

    @app_commands.command(
        name="sudoku",
        description="Open a Sudoku Sprint table (multiplayer)",
    )
    async def sudoku(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a Sudoku table in this channel!",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = SudokuTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = SudokuTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SudokuCog(bot))
