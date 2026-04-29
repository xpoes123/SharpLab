"""Casino cog — multiplayer /mastermind code-breaking game."""

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
from bot.cogs._elo_helpers import update_elo_multiplayer
from bot.cogs._pool import compute_side_pot_payouts

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 1
MAX_GUESSES = 10
ROUND_TIMEOUT = 300  # 5 minutes

COLORS: dict[str, tuple[str, str]] = {
    "R": ("Red", "\U0001f534"),      # 🔴
    "G": ("Green", "\U0001f7e2"),    # 🟢
    "B": ("Blue", "\U0001f535"),     # 🔵
    "Y": ("Yellow", "\U0001f7e1"),   # 🟡
    "O": ("Orange", "\U0001f7e0"),   # 🟠
    "P": ("Purple", "\U0001f7e3"),   # 🟣
}

VALID_CODES = set(COLORS.keys())
CODE_LENGTH = 4

# Peg feedback emoji
PEG_BLACK = "\u26ab"   # ⚫ exact match
PEG_WHITE = "\u26aa"   # ⚪ right color wrong position
PEG_EMPTY = "\u2796"   # ➖ no match


# ── Helpers ──────────────────────────────────────────────────────────────────


def _generate_code() -> list[str]:
    """Generate a random 4-color code (repeats allowed)."""
    return [random.choice(list(VALID_CODES)) for _ in range(CODE_LENGTH)]


def _compute_pegs(guess: list[str], secret: list[str]) -> tuple[int, int]:
    """Compute (black_pegs, white_pegs) using standard Mastermind algorithm.

    1. First pass: exact matches (black pegs). Remove those from consideration.
    2. Second pass: for remaining guess colors, check if they exist in remaining
       code colors (white pegs). Each remaining code color consumed at most once.
    """
    black = 0
    remaining_guess: list[str] = []
    remaining_secret: list[str] = []

    # First pass — exact matches
    for g, s in zip(guess, secret):
        if g == s:
            black += 1
        else:
            remaining_guess.append(g)
            remaining_secret.append(s)

    # Second pass — color matches in wrong position
    white = 0
    secret_pool = list(remaining_secret)
    for g in remaining_guess:
        if g in secret_pool:
            white += 1
            secret_pool.remove(g)

    return black, white


def _color_emoji(code: str) -> str:
    """Convert a color code letter to its emoji."""
    return COLORS[code][1]


def _code_to_emoji(code: list[str]) -> str:
    """Render a list of color codes as emoji."""
    return "".join(_color_emoji(c) for c in code)


def _pegs_to_emoji(black: int, white: int) -> str:
    """Render peg feedback as emoji string."""
    parts: list[str] = []
    parts.extend([PEG_BLACK] * black)
    parts.extend([PEG_WHITE] * white)
    empty = CODE_LENGTH - black - white
    parts.extend([PEG_EMPTY] * empty)
    return "".join(parts)


def _parse_guess(raw: str) -> list[str] | None:
    """Parse a guess string like 'RGBY'. Returns None if invalid."""
    cleaned = raw.strip().upper()
    if len(cleaned) != CODE_LENGTH:
        return None
    for ch in cleaned:
        if ch not in VALID_CODES:
            return None
    return list(cleaned)


def _color_legend() -> str:
    """Return a compact color legend string."""
    return " ".join(f"{emoji} {code}" for code, (_, emoji) in COLORS.items())


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class MastermindPlayer:
    user_id: int
    display_name: str
    bet: int
    guesses: list[tuple[list[str], int, int]] = field(default_factory=list)
    solved: bool = False
    solve_count: int | None = None  # guess number that solved it


@dataclass
class MastermindTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | finished
    players: dict[int, MastermindPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    secret_code: list[str] = field(default_factory=list)
    round_task: asyncio.Task | None = field(default=None, repr=False)
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    max_guesses: int = MAX_GUESSES
    winners: list[int] = field(default_factory=list)  # user_ids of round winners


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: MastermindTable) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values())
    embed = discord.Embed(
        title=f"\U0001f9e0 Mastermind \u2014 Join the Table (Round {table.round_num})",
        description=(
            "Crack the secret 4-color code! All players race to solve it simultaneously.\n"
            "First to crack it wins. If nobody cracks it, closest player wins."
        ),
        colour=discord.Colour.purple(),
    )
    if pot:
        embed.add_field(name="Pot", value=f"{pot}c", inline=True)
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
    embed.set_footer(
        text=f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} players \u2502 {MAX_GUESSES} guesses max",
    )
    return embed


def _playing_embed(table: MastermindTable) -> discord.Embed:
    embed = discord.Embed(
        title=f"\U0001f9e0 Mastermind \u2014 Round {table.round_num}",
        description=(
            "Crack the code! Click **Guess** to submit your guess.\n"
            f"Colors: {_color_legend()}"
        ),
        colour=discord.Colour.purple(),
    )

    # Player progress
    lines: list[str] = []
    for p in table.players.values():
        guess_count = len(p.guesses)
        remaining = table.max_guesses - guess_count
        if p.solved:
            lines.append(
                f"\U0001f3c6 **{p.display_name}** \u2014 "
                f"Cracked it in **{p.solve_count}** guesses!"
            )
        else:
            # Show best pegs so far (most black pegs)
            if p.guesses:
                best_black = max(g[1] for g in p.guesses)
                best_white = max(
                    g[2] for g in p.guesses if g[1] == best_black
                )
                progress = _pegs_to_emoji(best_black, best_white)
                lines.append(
                    f"\U0001f50d **{p.display_name}** \u2014 "
                    f"{progress} ({guess_count} guesses, {remaining} left)"
                )
            else:
                lines.append(
                    f"\u2b1c **{p.display_name}** \u2014 "
                    f"No guesses yet ({remaining} left)"
                )

    embed.add_field(name="Players", value="\n".join(lines), inline=False)
    embed.set_footer(
        text=f"Host: {table.host_name} \u2502 Max {table.max_guesses} guesses",
    )
    return embed


def _solved_embed(
    table: MastermindTable, *,
    balances: dict[int, int] | None = None,
    payouts: dict[int, int] | None = None,
) -> discord.Embed:
    code_display = _code_to_emoji(table.secret_code)

    # Determine winner description
    if len(table.winners) == 1:
        winner = table.players[table.winners[0]]
        title_text = f"\U0001f3c6 **{winner.display_name}** cracked it in **{winner.solve_count}** guesses!"
    else:
        winner_names = [table.players[uid].display_name for uid in table.winners]
        count = table.players[table.winners[0]].solve_count
        title_text = (
            f"\U0001f3c6 **{' & '.join(winner_names)}** tied \u2014 "
            f"cracked it in **{count}** guesses!"
        )

    embed = discord.Embed(
        title=f"\U0001f9e0 Mastermind \u2014 Round {table.round_num} Complete",
        description=f"Secret code: {code_display}\n\n{title_text}",
        colour=discord.Colour.gold(),
    )

    # Compute payouts if not provided (e.g. from _timeout_embed)
    if payouts is None:
        bets = {uid: p.bet for uid, p in table.players.items()}
        payouts = compute_side_pot_payouts(bets, table.winners)

    # Results per player
    winner_set = set(table.winners)
    lines: list[str] = []
    for p in table.players.values():
        bal = balances.get(p.user_id, 0) if balances else 0
        payout = payouts.get(p.user_id, 0)
        net = payout - p.bet
        sign = "+" if net >= 0 else ""
        if p.user_id in winner_set:
            if p.solved:
                lines.append(
                    f"\U0001f3c6 **{p.display_name}** \u2014 "
                    f"Solved in {p.solve_count} guesses \u2014 "
                    f"{p.bet}c \u2192 {payout}c (**{sign}{net}c**) \u2014 bal: {bal}c"
                )
            else:
                best_b = max(g[1] for g in p.guesses) if p.guesses else 0
                lines.append(
                    f"\U0001f3c6 **{p.display_name}** \u2014 "
                    f"Closest ({best_b} black pegs) \u2014 "
                    f"{p.bet}c \u2192 {payout}c (**{sign}{net}c**) \u2014 bal: {bal}c"
                )
        elif payout > 0:
            best_b = max(g[1] for g in p.guesses) if p.guesses else 0
            lines.append(
                f"\U0001f4b0 **{p.display_name}** \u2014 "
                f"Best: {best_b} black pegs ({len(p.guesses)} guesses) \u2014 "
                f"{p.bet}c \u2192 {payout}c (**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        else:
            if p.solved:
                lines.append(
                    f"\u2705 **{p.display_name}** \u2014 "
                    f"Solved in {p.solve_count} guesses \u2014 "
                    f"{p.bet}c \u2192 0c (**-{p.bet}c**) \u2014 bal: {bal}c"
                )
            else:
                best_b = max(g[1] for g in p.guesses) if p.guesses else 0
                lines.append(
                    f"\u274c **{p.display_name}** \u2014 "
                    f"Best: {best_b} black pegs ({len(p.guesses)} guesses) \u2014 "
                    f"{p.bet}c \u2192 0c (**-{p.bet}c**) \u2014 bal: {bal}c"
                )

    embed.add_field(name="Results", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _timeout_embed(table: MastermindTable) -> discord.Embed:
    """Embed for when the round times out (5 min) or all players exhaust guesses."""
    code_display = _code_to_emoji(table.secret_code)

    if table.winners:
        if len(table.winners) == 1:
            winner = table.players[table.winners[0]]
            best_b = max(g[1] for g in winner.guesses) if winner.guesses else 0
            winner_text = (
                f"\U0001f3c6 **{winner.display_name}** wins (closest with "
                f"{best_b} black pegs)!"
            )
        else:
            winner_names = [table.players[uid].display_name for uid in table.winners]
            winner_text = f"\U0001f3c6 **{' & '.join(winner_names)}** tied for closest!"
    else:
        winner_text = "No winner this round."

    embed = discord.Embed(
        title=f"\U0001f9e0 Mastermind \u2014 Round {table.round_num} Over",
        description=(
            f"Secret code: {code_display}\n\n"
            f"Nobody cracked it!\n{winner_text}"
        ),
        colour=discord.Colour.dark_red(),
    )
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinMastermindModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(
        self, table: MastermindTable, view: "MastermindTableView", balance: int,
    ) -> None:
        super().__init__(title="Join Mastermind")
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
                "You're already in this round!", ephemeral=True,
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

        self.table.players[uid] = MastermindPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


class GuessModal(ui.Modal):
    guess_input = ui.TextInput(
        label="Enter 4 colors (R/G/B/Y/O/P)",
        placeholder="e.g. RGBY",
        required=True,
        min_length=4,
        max_length=4,
    )

    def __init__(self, table: MastermindTable, view: "MastermindTableView") -> None:
        super().__init__(title="Mastermind \u2014 Your Guess")
        self.table = table
        self.table_view = view
        player = table.players.get(0)  # placeholder, set in view
        self._user_id: int | None = None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)

        if player is None:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return

        if player.solved:
            await interaction.response.send_message(
                "You already cracked the code!", ephemeral=True,
            )
            return

        if len(player.guesses) >= self.table.max_guesses:
            await interaction.response.send_message(
                "You've used all your guesses!", ephemeral=True,
            )
            return

        guess = _parse_guess(self.guess_input.value)
        if guess is None:
            await interaction.response.send_message(
                "Invalid guess! Enter exactly 4 color codes: R, G, B, Y, O, P.\n"
                f"Example: `RGBY`\n"
                f"Colors: {_color_legend()}",
                ephemeral=True,
            )
            return

        # Compute feedback
        black, white = _compute_pegs(guess, self.table.secret_code)
        player.guesses.append((guess, black, white))

        # Check if solved
        if black == CODE_LENGTH:
            player.solved = True
            player.solve_count = len(player.guesses)

        # Build ephemeral history for this player
        history_lines: list[str] = []
        for i, (g, b, w) in enumerate(player.guesses, 1):
            guess_emoji = _code_to_emoji(g)
            pegs_emoji = _pegs_to_emoji(b, w)
            history_lines.append(f"Guess {i}: {guess_emoji}  \u2192  {pegs_emoji}")

        remaining = self.table.max_guesses - len(player.guesses)

        if player.solved:
            history_lines.append(
                f"\n\U0001f3c6 **You cracked it in {player.solve_count} guesses!**"
            )
        else:
            history_lines.append(f"\n{remaining} guesses remaining.")

        # Send ephemeral history to the guesser
        await interaction.response.send_message(
            "\n".join(history_lines), ephemeral=True,
        )

        # Update the main embed to reflect new guess counts
        await self.table_view._update_main_embed()

        # Check if the round should end
        await self.table_view._check_round_end()


# ── View ─────────────────────────────────────────────────────────────────────


class MastermindTableView(ui.View):
    def __init__(
        self, table: MastermindTable, active_tables: dict[int, MastermindTable],
    ) -> None:
        super().__init__(timeout=600)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        playing = phase == "playing"
        finished = phase == "finished"

        # Row 0: Start, Join, Re-bet, Leave
        self.start_btn.disabled = (
            not betting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = playing

        # Row 1: Guess, My History, Rules
        self.guess_btn.disabled = not playing
        self.history_btn.disabled = not playing
        self.rules_btn.disabled = False  # always available

        # Row 2: New Round, Close Table
        self.new_round_btn.disabled = not finished
        self.close_btn.disabled = playing

    async def _update_main_embed(self) -> None:
        """Update the public embed to reflect current player progress."""
        if self.table.message and self.table.phase == "playing":
            try:
                await self.table.message.edit(
                    embed=_playing_embed(self.table), view=self,
                )
            except discord.HTTPException:
                pass

    async def _check_round_end(self) -> None:
        """Check if the round should end (someone solved or all out of guesses)."""
        table = self.table
        if table.phase != "playing":
            return

        # Check if anyone solved it
        solvers = [
            uid for uid, p in table.players.items() if p.solved
        ]

        if solvers:
            # Find the solver(s) with the fewest guesses
            min_count = min(table.players[uid].solve_count for uid in solvers)
            winners = [uid for uid in solvers if table.players[uid].solve_count == min_count]
            await self._finish_round(winners)
            return

        # Check if everyone has used all guesses
        all_done = all(
            len(p.guesses) >= table.max_guesses or p.solved
            for p in table.players.values()
        )

        if all_done:
            # Nobody solved it — find closest (most black pegs, tiebreak: most white)
            await self._finish_by_proximity()

    async def _finish_round(self, winner_uids: list[int]) -> None:
        """Finish the round with the given winners."""
        table = self.table
        table.phase = "finished"
        table.winners = winner_uids

        if table.round_task and not table.round_task.done():
            table.round_task.cancel()

        # Side-pot payouts
        bets = {uid: p.bet for uid, p in table.players.items()}
        payouts = compute_side_pot_payouts(bets, winner_uids)

        # Credit payouts and log results
        balances: dict[int, int] = {}
        for uid, player in table.players.items():
            payout = payouts.get(uid, 0)
            if payout > 0:
                balances[uid] = await queries.update_casino_balance(
                    str(uid), payout,
                )
            else:
                bal = await queries.get_casino_balance(str(uid))
                balances[uid] = bal or 0
            await queries.log_casino_result(
                str(uid), "mastermind", player.bet, payout,
            )

        # ELO update — rank by solve_count (fewer = better), unsolved = last
        if len(table.players) >= 2:
            sorted_p = sorted(
                table.players.values(),
                key=lambda p: p.solve_count if p.solve_count is not None else 999,
            )
            finish_order = [p.user_id for p in sorted_p]
            try:
                await update_elo_multiplayer(finish_order, "mastermind", "mastermind")
            except Exception:
                pass

        # Save last bets for re-bet
        for uid, player in table.players.items():
            table.last_bets[uid] = (player.display_name, player.bet)

        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(
                    embed=_solved_embed(table, balances=balances, payouts=payouts),
                    view=self,
                )
            except discord.HTTPException:
                pass

    async def _finish_by_proximity(self) -> None:
        """Finish when nobody solved — winner is closest (most black pegs, tiebreak: white)."""
        table = self.table

        # Find best (black, white) score per player across all their guesses
        best_scores: dict[int, tuple[int, int]] = {}
        for uid, p in table.players.items():
            if p.guesses:
                best = max(p.guesses, key=lambda g: (g[1], g[2]))
                best_scores[uid] = (best[1], best[2])
            else:
                best_scores[uid] = (0, 0)

        # Find the max score
        max_score = max(best_scores.values())
        winner_uids = [uid for uid, score in best_scores.items() if score == max_score]

        await self._finish_round(winner_uids)

    async def _round_timer(self) -> None:
        """Background task that ends the round after ROUND_TIMEOUT seconds."""
        try:
            await asyncio.sleep(ROUND_TIMEOUT)
        except asyncio.CancelledError:
            return

        table = self.table
        if table.phase != "playing":
            return

        # Time's up — find closest player
        # Check if anyone solved during the wait
        solvers = [uid for uid, p in table.players.items() if p.solved]
        if solvers:
            min_count = min(table.players[uid].solve_count for uid in solvers)
            winners = [uid for uid in solvers if table.players[uid].solve_count == min_count]
            await self._finish_round(winners)
        else:
            await self._finish_by_proximity()

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
        await self._start_game(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f9e0", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Game in progress! Wait for the next round.", ephemeral=True,
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
            JoinMastermindModal(self.table, self, bal),
        )

    @ui.button(
        label="Re-bet", style=discord.ButtonStyle.primary, emoji="\U0001f504", row=0,
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
        self.table.players[uid] = MastermindPlayer(
            user_id=uid, display_name=name, bet=amt,
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
        if self.table.phase == "playing":
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
        label="Guess", style=discord.ButtonStyle.success, emoji="\U0001f3af", row=1,
    )
    async def guess_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "playing":
            await interaction.response.send_message(
                "No game in progress.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return
        if player.solved:
            await interaction.response.send_message(
                "You already cracked the code! Wait for others to finish.",
                ephemeral=True,
            )
            return
        if len(player.guesses) >= self.table.max_guesses:
            await interaction.response.send_message(
                "You've used all your guesses!", ephemeral=True,
            )
            return
        await interaction.response.send_modal(GuessModal(self.table, self))

    @ui.button(
        label="My History", style=discord.ButtonStyle.secondary, emoji="\U0001f4dc", row=1,
    )
    async def history_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message(
                "You're not in this game!", ephemeral=True,
            )
            return

        if not player.guesses:
            await interaction.response.send_message(
                "No guesses yet! Click **Guess** to start.",
                ephemeral=True,
            )
            return

        lines: list[str] = []
        for i, (g, b, w) in enumerate(player.guesses, 1):
            guess_emoji = _code_to_emoji(g)
            pegs_emoji = _pegs_to_emoji(b, w)
            lines.append(f"Guess {i}: {guess_emoji}  \u2192  {pegs_emoji}")

        remaining = self.table.max_guesses - len(player.guesses)
        if player.solved:
            lines.append(
                f"\n\U0001f3c6 **Cracked it in {player.solve_count} guesses!**"
            )
        else:
            lines.append(f"\n{remaining} guesses remaining.")

        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True,
        )

    @ui.button(
        label="Rules", style=discord.ButtonStyle.secondary, emoji="\U0001f4d6", row=1,
    )
    async def rules_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        rules_text = (
            "**Mastermind \u2014 Rules**\n\n"
            "A secret 4-color code is generated from 6 colors (repeats allowed).\n"
            "All players race to crack the same code simultaneously.\n\n"
            f"**Colors:** {_color_legend()}\n\n"
            "**Peg feedback after each guess:**\n"
            f"{PEG_BLACK} Black \u2014 right color, right position\n"
            f"{PEG_WHITE} White \u2014 right color, wrong position\n"
            f"{PEG_EMPTY} Empty \u2014 color not in code (at this slot)\n\n"
            "**How to guess:** Click the Guess button and type 4 letters.\n"
            "Example: `RGBY` = Red, Green, Blue, Yellow\n\n"
            f"**Win condition:** First to get 4 black pegs ({PEG_BLACK}{PEG_BLACK}{PEG_BLACK}{PEG_BLACK}) wins.\n"
            f"Max {MAX_GUESSES} guesses. {ROUND_TIMEOUT // 60}-minute time limit.\n"
            "If nobody cracks it, closest player (most black pegs) wins."
        )
        await interaction.response.send_message(rules_text, ephemeral=True)

    # ── Row 2 ────────────────────────────────────────────────────────────────

    @ui.button(
        label="New Round", style=discord.ButtonStyle.success, emoji="\u25b6\ufe0f", row=2,
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
        label="Close Table", style=discord.ButtonStyle.danger, emoji="\u2716\ufe0f", row=2,
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

    async def _start_game(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "playing"
        table.secret_code = _generate_code()
        table.winners.clear()

        # Reset player state for a fresh round
        for p in table.players.values():
            p.guesses.clear()
            p.solved = False
            p.solve_count = None

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(table), view=self,
        )

        # Start the round timer
        table.round_task = asyncio.create_task(self._round_timer())

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _start_new_round(self) -> None:
        table = self.table
        table.players.clear()
        table.phase = "betting"
        table.round_num += 1
        table.secret_code = []
        table.winners.clear()
        if table.round_task and not table.round_task.done():
            table.round_task.cancel()
        table.round_task = None

    async def _refund_all(self) -> None:
        for p in self.table.players.values():
            try:
                await queries.update_casino_balance(str(p.user_id), p.bet)
            except Exception:
                pass

    async def _close(
        self, interaction: discord.Interaction, reason: str,
    ) -> None:
        if self.table.round_task and not self.table.round_task.done():
            self.table.round_task.cancel()
        embed = discord.Embed(
            title="\U0001f9e0 Mastermind Table \u2014 Closed",
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

        if table.round_task and not table.round_task.done():
            table.round_task.cancel()

        if table.phase == "finished":
            self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    embed = discord.Embed(
                        title="\U0001f9e0 Mastermind Table \u2014 Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    pass
            return

        # Betting or playing — refund all
        await self._refund_all()
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="\U0001f9e0 Mastermind Table \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class MastermindCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, MastermindTable] = {}

    @app_commands.command(
        name="mastermind",
        description="Open a Mastermind code-breaking table (multiplayer)",
    )
    async def mastermind(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            if getattr(existing, "phase", None) == "closed":
                del self.active_tables[channel_id]
            else:
                await interaction.response.send_message(
                    "There's already a Mastermind table in this channel!",
                    ephemeral=True,
                )
                return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = MastermindTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = MastermindTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MastermindCog(bot))
