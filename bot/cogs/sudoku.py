"""Casino cog — multiplayer /sudoku sprint game.

Everyone gets the same 4x4 mini-Sudoku puzzle. First to submit the correct
solution wins the round. First to WINS_TO_WIN round wins takes the pot.
Submissions via button modal (private).
"""

import asyncio
import os
import time
from dataclasses import dataclass, field

import discord
import httpx
from discord import app_commands, ui
from discord.ext import commands, tasks

from db import queries
from shared.sudoku_logic import (
    Grid, MAX_PLAYERS, MIN_PLAYERS, ROUND_TIME, ROUND_DELAY,
    WINS_TO_WIN, MAX_ROUNDS, BLANKS, PAYTABLE, MEDALS,
    generate_grid as _generate_grid,
    make_puzzle as _make_puzzle,
    find_error as _find_error,
    format_grid as _format_grid,
    row_display as _row_display,
    compute_payouts as _compute_payouts,
)

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


WEB_API_BASE = os.environ.get("WEB_API_BASE", "https://djiang.xyz")
WEB_API_SECRET = os.environ.get("WEB_API_SECRET", "dev-secret")


class SudokuCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, SudokuTable] = {}
        self._pending_web_rooms: dict[str, int] = {}  # room_id -> channel_id

    async def cog_load(self) -> None:
        self._poll_web_results.start()

    async def cog_unload(self) -> None:
        self._poll_web_results.cancel()

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

    # ── Web Sudoku ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="sudoku-web",
        description="Open a Sudoku Sprint game on the web (better UI)",
    )
    async def sudoku_web(self, interaction: discord.Interaction) -> None:
        uid = str(interaction.user.id)
        channel_id = str(interaction.channel_id)
        await queries.get_or_create_casino_wallet(uid)

        # Create room via web API
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{WEB_API_BASE}/api/v1/sudoku/rooms",
                json={
                    "host_discord_id": uid,
                    "channel_id": channel_id,
                    "host_display_name": interaction.user.display_name,
                },
                headers={"X-Api-Key": WEB_API_SECRET},
            )
        if resp.status_code != 200:
            await interaction.response.send_message(
                "Failed to create web game room.", ephemeral=True,
            )
            return

        room_id = resp.json()["room_id"]
        self._pending_web_rooms[room_id] = interaction.channel_id

        embed = discord.Embed(
            title="\U0001f9e9 Sudoku Sprint (Web)",
            description=(
                "Play in your browser with an interactive grid!\n\n"
                "Click **Join** below to enter your bet and get your game link."
            ),
            colour=discord.Colour.gold(),
        )
        embed.set_footer(text=f"Room {room_id} \u2022 First to 3 wins")

        view = WebSudokuLobbyView(room_id, self.bot)
        await interaction.response.send_message(embed=embed, view=view)

    # ── Result polling ─────────────────────────────────────────────────────

    @tasks.loop(seconds=10)
    async def _poll_web_results(self) -> None:
        for room_id in list(self._pending_web_rooms):
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(
                        f"{WEB_API_BASE}/api/v1/sudoku/rooms/{room_id}/result",
                    )
                if resp.status_code != 200:
                    continue

                result = resp.json()
                channel_id = self._pending_web_rooms.pop(room_id)
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    continue

                embed = discord.Embed(
                    title="\U0001f9e9 Sudoku Sprint — Results",
                    colour=discord.Colour.green(),
                )
                lines = []
                medals = ["\U0001f947", "\U0001f948", "\U0001f949"]
                for i, r in enumerate(result.get("results", [])):
                    badge = medals[i] if i < 3 else f"`{i+1}.`"
                    net = r["net"]
                    sign = "+" if net > 0 else ""
                    lines.append(
                        f"{badge} **{r['display_name']}** \u2014 "
                        f"{r['rounds_won']}W \u2014 "
                        f"{r['wager']}c \u2192 {r['payout']}c ({sign}{net}c)"
                    )
                embed.description = "\n".join(lines) if lines else "No results."
                embed.set_footer(
                    text=f"Room {room_id} \u2022 {result.get('total_rounds', 0)} rounds played"
                )
                await channel.send(embed=embed)
            except Exception:
                pass

    @_poll_web_results.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()


# ── Web Sudoku Lobby View ────────────────────────────────────────────────────


class WebSudokuJoinModal(ui.Modal, title="Join Sudoku Sprint"):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        min_length=1,
        max_length=10,
    )

    def __init__(self, room_id: str) -> None:
        super().__init__()
        self.room_id = room_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Invalid amount.", ephemeral=True)
            return

        if amt <= 0:
            await interaction.response.send_message("Bet must be positive.", ephemeral=True)
            return

        uid = str(interaction.user.id)
        bal = await queries.get_casino_balance(uid)
        if bal is None or bal < amt:
            await interaction.response.send_message(
                f"Insufficient balance (you have {bal or 0}c).", ephemeral=True,
            )
            return

        # Deduct coins
        await queries.update_casino_balance(uid, -amt)

        # Create token via web API
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{WEB_API_BASE}/api/v1/sudoku/rooms/{self.room_id}/tokens",
                    json={
                        "discord_user": uid,
                        "display_name": interaction.user.display_name,
                        "wager": amt,
                    },
                    headers={"X-Api-Key": WEB_API_SECRET},
                )
            if resp.status_code != 200:
                # Refund on failure
                await queries.update_casino_balance(uid, amt)
                detail = resp.json().get("detail", "Unknown error")
                await interaction.response.send_message(
                    f"Failed to join: {detail}", ephemeral=True,
                )
                return

            url = resp.json()["url"]
            await interaction.response.send_message(
                f"\U0001f517 **[Click here to play]({url})**\n"
                f"Your {amt}c bet is locked in. Open the link to connect.",
                ephemeral=True,
            )
        except Exception:
            await queries.update_casino_balance(uid, amt)
            await interaction.response.send_message(
                "Failed to connect to game server. Bet refunded.", ephemeral=True,
            )


class WebSudokuLobbyView(ui.View):
    def __init__(self, room_id: str, bot: commands.Bot) -> None:
        super().__init__(timeout=1800)
        self.room_id = room_id
        self.bot = bot

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f4dd")
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        await interaction.response.send_modal(WebSudokuJoinModal(self.room_id))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SudokuCog(bot))
