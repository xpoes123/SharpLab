"""Casino cog — /sequence math pattern guessing game."""

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
from bot.cogs._elo_helpers import update_elo_multiplayer, fmt_elo_change
from bot.cogs._pool import compute_side_pot_payouts
import logging

log = logging.getLogger(__name__)
# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 1
ROUNDS_PER_GAME = 5
ROUND_TIME = 30  # seconds per round
MAX_BET = 500

# Points for 1st, 2nd, 3rd correct answer
POINTS = [3, 2, 1]

# ── Sequence Bank ────────────────────────────────────────────────────────────
# (terms_shown, answer, name)

SEQUENCE_BANK: list[tuple[list[int], int, str]] = [
    # -- Arithmetic --
    ([3, 7, 11, 15, 19], 23, "Arithmetic +4"),
    ([5, 12, 19, 26, 33], 40, "Arithmetic +7"),
    ([100, 88, 76, 64, 52], 40, "Arithmetic -12"),
    ([2, 5, 8, 11, 14], 17, "Arithmetic +3"),
    ([50, 43, 36, 29, 22], 15, "Arithmetic -7"),
    # -- Geometric --
    ([2, 6, 18, 54], 162, "Geometric x3"),
    ([3, 12, 48, 192], 768, "Geometric x4"),
    ([1000, 500, 250, 125], 62, "Geometric /2 (rounded)"),
    ([5, 10, 20, 40, 80], 160, "Geometric x2"),
    ([1, 3, 9, 27, 81], 243, "Geometric x3"),
    # -- Squares --
    ([1, 4, 9, 16, 25], 36, "Perfect squares"),
    ([4, 9, 16, 25, 36], 49, "Squares starting at 2"),
    ([1, 4, 9, 16, 25, 36], 49, "Perfect squares (longer)"),
    # -- Cubes --
    ([1, 8, 27, 64, 125], 216, "Perfect cubes"),
    ([8, 27, 64, 125, 216], 343, "Cubes starting at 2"),
    # -- Triangular --
    ([1, 3, 6, 10, 15], 21, "Triangular numbers"),
    ([3, 6, 10, 15, 21], 28, "Triangular starting at 3"),
    ([6, 10, 15, 21, 28], 36, "Triangular starting at 6"),
    # -- Fibonacci-like --
    ([1, 1, 2, 3, 5, 8], 13, "Fibonacci"),
    ([2, 2, 4, 6, 10, 16], 26, "Fibonacci x2 seed"),
    ([1, 3, 4, 7, 11, 18], 29, "Lucas numbers"),
    ([1, 1, 2, 3, 5], 8, "Fibonacci (short)"),
    # -- Primes --
    ([2, 3, 5, 7, 11, 13], 17, "Prime numbers"),
    ([11, 13, 17, 19, 23], 29, "Primes starting at 11"),
    ([29, 31, 37, 41, 43], 47, "Primes starting at 29"),
    # -- Powers of 2 --
    ([1, 2, 4, 8, 16, 32], 64, "Powers of 2"),
    ([16, 32, 64, 128, 256], 512, "Powers of 2 (large)"),
    ([2, 4, 8, 16, 32], 64, "Powers of 2 (from 2)"),
    # -- Factorials --
    ([1, 1, 2, 6, 24, 120], 720, "Factorials"),
    ([2, 6, 24, 120, 720], 5040, "Factorials (from 2!)"),
    # -- Polynomial / quadratic --
    ([2, 6, 12, 20, 30], 42, "n(n+1) oblong numbers"),
    ([0, 3, 8, 15, 24], 35, "n^2 - 1"),
    ([1, 5, 14, 30, 55], 91, "Pyramidal numbers"),
    ([0, 1, 4, 9, 16], 25, "Squares from 0"),
    ([2, 8, 18, 32, 50], 72, "2n^2"),
    # -- Alternating / tricky --
    ([1, 4, 2, 8, 3, 12], 4, "Alternating: n, 4n pattern"),
    ([3, 5, 9, 15, 23], 33, "Differences increase by 2"),
    ([1, 2, 4, 7, 11, 16], 22, "Add 1,2,3,4,5,6"),
    ([2, 3, 5, 8, 13, 21], 34, "Add previous two (shifted)"),
    ([1, 3, 7, 15, 31], 63, "2^n - 1"),
    # -- Catalan --
    ([1, 1, 2, 5, 14, 42], 132, "Catalan numbers"),
    # -- Centered polygonal --
    ([1, 7, 19, 37, 61], 91, "Centered hexagonal"),
    ([1, 5, 13, 25, 41], 61, "Centered square numbers"),
    # -- Misc interesting --
    ([0, 1, 1, 2, 3, 5], 8, "Fibonacci from 0"),
    ([1, 2, 6, 24, 120], 720, "Factorials (from 1)"),
    ([10, 11, 13, 17, 25], 41, "Add 1,2,4,8,16"),
    ([1, 10, 100, 1000], 10000, "Powers of 10"),
    ([4, 8, 16, 32, 64], 128, "Powers of 2 from 4"),
    ([1, 4, 10, 20, 35], 56, "Tetrahedral numbers"),
    ([2, 5, 10, 17, 26], 37, "n^2 + 1"),
    ([1, 3, 6, 10, 15, 21], 28, "Triangular (longer)"),
]


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class SeqPlayer:
    user_id: int
    display_name: str
    bet: int
    score: int = 0
    answered_this_round: bool = False


@dataclass
class SeqTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | finished
    players: dict[int, SeqPlayer] = field(default_factory=dict)
    round_num: int = 0
    rounds: list[tuple[list[int], int, str]] = field(default_factory=list)
    current_answer: int = 0
    current_name: str = ""
    correct_order: list[int] = field(default_factory=list)  # uids in order of correct answer
    message: discord.Message | None = None
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    round_event: asyncio.Event = field(default_factory=asyncio.Event)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: SeqTable) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values())
    embed = discord.Embed(
        title="Sequence \u2014 Join the Game",
        description=(
            "Guess the next number in mathematical sequences!\n"
            f"**{ROUNDS_PER_GAME}** rounds, **{ROUND_TIME}s** per round.\n"
            "First correct = 3 pts, second = 2 pts, third = 1 pt."
        ),
        colour=discord.Colour.blue(),
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
    embed.set_footer(text=f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} player(s)")
    return embed


def _round_embed(table: SeqTable, terms: list[int], time_left: int | None = None) -> discord.Embed:
    seq_str = ", ".join(str(t) for t in terms) + ", **?**"
    embed = discord.Embed(
        title=f"\U0001f522 Sequence \u2014 Round {table.round_num}/{ROUNDS_PER_GAME}",
        description=f"```{', '.join(str(t) for t in terms)}, ?```",
        colour=discord.Colour.blue(),
    )

    # Scoreboard
    sorted_players = sorted(table.players.values(), key=lambda p: p.score, reverse=True)
    lines = []
    for i, p in enumerate(sorted_players):
        medal = {0: "\U0001f947", 1: "\U0001f948", 2: "\U0001f949"}.get(i, f"**{i+1}.**")
        status = "\u2705" if p.answered_this_round else "\u23f3"
        lines.append(f"{medal} {status} **{p.display_name}** \u2014 {p.score} pts")
    embed.add_field(name="Scoreboard", value="\n".join(lines), inline=False)

    if time_left is not None:
        embed.set_footer(text=f"{time_left}s remaining")

    return embed


def _round_result_embed(
    table: SeqTable, terms: list[int], answer: int, name: str,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"\U0001f522 Sequence \u2014 Round {table.round_num} Results",
        description=f"```{', '.join(str(t) for t in terms)}, {answer}```\n**{name}**",
        colour=discord.Colour.green(),
    )

    if table.correct_order:
        lines = []
        for i, uid in enumerate(table.correct_order):
            p = table.players[uid]
            pts = POINTS[i] if i < len(POINTS) else 0
            lines.append(f"**{i+1}.** {p.display_name} (+{pts} pts)")
        embed.add_field(name="Correct answers", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Result", value="No one got it!", inline=False)

    # Scoreboard
    sorted_players = sorted(table.players.values(), key=lambda p: p.score, reverse=True)
    lines = []
    for i, p in enumerate(sorted_players):
        medal = {0: "\U0001f947", 1: "\U0001f948", 2: "\U0001f949"}.get(i, f"**{i+1}.**")
        lines.append(f"{medal} **{p.display_name}** \u2014 {p.score} pts")
    embed.add_field(name="Standings", value="\n".join(lines), inline=False)

    return embed


def _final_embed(table: SeqTable, payouts: dict[int, int]) -> discord.Embed:
    sorted_players = sorted(table.players.values(), key=lambda p: p.score, reverse=True)
    embed = discord.Embed(
        title="\U0001f3c6 Sequence \u2014 Final Results",
        colour=discord.Colour.gold(),
    )
    lines = []
    for i, p in enumerate(sorted_players):
        medal = {0: "\U0001f947", 1: "\U0001f948", 2: "\U0001f949"}.get(i, f"**{i+1}.**")
        payout = payouts.get(p.user_id, 0)
        net = payout - p.bet
        net_str = f" ({net:+,}c)" if net != 0 else ""
        lines.append(f"{medal} **{p.display_name}** \u2014 {p.score} pts \u2014 {payout}c{net_str}")
    embed.description = "\n".join(lines)
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinSeqModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 50",
        required=True,
        max_length=10,
    )

    def __init__(self, table: SeqTable, view: "SeqView", balance: int) -> None:
        super().__init__(title="Join Sequence")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 50 (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if amt < 1 or amt > MAX_BET:
            await interaction.response.send_message(f"Bet must be 1\u2013{MAX_BET}c.", ephemeral=True)
            return

        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return

        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins! (have {bal}c)", ephemeral=True,
            )
            return

        self.table.players[uid] = SeqPlayer(
            user_id=uid, display_name=interaction.user.display_name, bet=amt,
        )
        self.table.last_bets[uid] = (interaction.user.display_name, amt)
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


class AnswerModal(ui.Modal):
    answer = ui.TextInput(
        label="What comes next?",
        placeholder="Enter a number",
        required=True,
        max_length=20,
    )

    def __init__(self, table: SeqTable, view: "SeqView") -> None:
        super().__init__(title="Your Answer")
        self.table = table
        self.table_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if not player:
            await interaction.response.send_message("You're not in this game!", ephemeral=True)
            return
        if player.answered_this_round:
            await interaction.response.send_message("You already answered this round!", ephemeral=True)
            return

        try:
            guess = int(self.answer.value.strip().replace(",", ""))
        except ValueError:
            await interaction.response.send_message("Enter a valid integer.", ephemeral=True)
            return

        player.answered_this_round = True

        if guess == self.table.current_answer:
            self.table.correct_order.append(uid)
            idx = len(self.table.correct_order) - 1
            pts = POINTS[idx] if idx < len(POINTS) else 0
            player.score += pts
            await interaction.response.send_message(
                f"\u2705 Correct! (+{pts} pts)", ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"\u274c Wrong! You guessed {guess}.", ephemeral=True,
            )

        # Check if all players have answered
        if all(p.answered_this_round for p in self.table.players.values()):
            self.table.round_event.set()


# ── View ─────────────────────────────────────────────────────────────────────


class SeqView(ui.View):
    def __init__(self, table: SeqTable, active_tables: dict[int, SeqTable]) -> None:
        super().__init__(timeout=600)
        self.table = table
        self.active_tables = active_tables
        self._round_task: asyncio.Task | None = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        betting = self.table.phase == "betting"
        playing = self.table.phase == "playing"
        finished = self.table.phase == "finished"

        self.start_btn.disabled = not betting or len(self.table.players) < MIN_PLAYERS
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = not betting

        self.answer_btn.disabled = not playing
        self.new_round_btn.disabled = not finished
        self.close_btn.disabled = playing

    # ── Betting buttons (row 0) ──────────────────────────────────────────

    @ui.button(label="Start", style=discord.ButtonStyle.success, emoji="\u25b6", row=0)
    async def start_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Game already started!", ephemeral=True)
            return
        if len(self.table.players) < MIN_PLAYERS:
            await interaction.response.send_message(
                f"Need at least {MIN_PLAYERS} player(s)!", ephemeral=True,
            )
            return

        # Pick rounds
        self.table.rounds = random.sample(
            SEQUENCE_BANK, min(ROUNDS_PER_GAME, len(SEQUENCE_BANK)),
        )
        self.table.phase = "playing"
        self._update_buttons()
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="\U0001f522 Sequence",
                description="Game starting...",
                colour=discord.Colour.blue(),
            ),
            view=self,
        )
        self._round_task = asyncio.create_task(self._run_game())

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f3b2", row=0)
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Game in progress!", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Game is full!", ephemeral=True)
            return
        bal = await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(JoinSeqModal(self.table, self, bal))

    @ui.button(label="Re-bet", style=discord.ButtonStyle.primary, emoji="\U0001f504", row=0)
    async def rebet_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Game in progress!", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return
        last = self.table.last_bets.get(uid)
        if last is None:
            await interaction.response.send_message(
                "No previous bet \u2014 use Join instead.", ephemeral=True,
            )
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Game is full!", ephemeral=True)
            return
        name, amt = last
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for {amt}c re-bet! (have {bal}c)", ephemeral=True,
            )
            return
        self.table.players[uid] = SeqPlayer(
            user_id=uid, display_name=name, bet=amt,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=0)
    async def leave_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Can't leave mid-game!", ephemeral=True)
            return
        uid = interaction.user.id
        player = self.table.players.pop(uid, None)
        if not player:
            await interaction.response.send_message("You're not in!", ephemeral=True)
            return
        await queries.update_casino_balance(str(uid), player.bet)
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Playing buttons (row 1) ──────────────────────────────────────────

    @ui.button(label="Answer", style=discord.ButtonStyle.success, emoji="\U0001f4dd", row=1)
    async def answer_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "playing":
            await interaction.response.send_message("No round in progress!", ephemeral=True)
            return
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if not player:
            await interaction.response.send_message("You're not in this game!", ephemeral=True)
            return
        if player.answered_this_round:
            await interaction.response.send_message("You already answered!", ephemeral=True)
            return
        await interaction.response.send_modal(AnswerModal(self.table, self))

    # ── Finished buttons (row 2) ─────────────────────────────────────────

    @ui.button(label="New Game", style=discord.ButtonStyle.success, emoji="\U0001f504", row=2)
    async def new_round_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "finished":
            await interaction.response.send_message("Game not over yet!", ephemeral=True)
            return
        # Reset for new game
        for p in self.table.players.values():
            p.score = 0
            p.answered_this_round = False
        new_players: dict[int, SeqPlayer] = {}
        for uid, p in self.table.players.items():
            self.table.last_bets[uid] = (p.display_name, p.bet)
        self.table.players.clear()
        self.table.phase = "betting"
        self.table.round_num = 0
        self.table.correct_order.clear()
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(label="Close", style=discord.ButtonStyle.danger, emoji="\u2716", row=2)
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase == "playing":
            await interaction.response.send_message("Can't close mid-game!", ephemeral=True)
            return
        # Refund anyone still in betting phase
        if self.table.phase == "betting":
            for p in self.table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    log.exception("Unhandled error in sequence.py")
        self.active_tables.pop(self.table.channel_id, None)
        embed = discord.Embed(
            title="\u2716 Sequence \u2014 Table Closed",
            colour=discord.Colour.dark_grey(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    # ── Game loop ────────────────────────────────────────────────────────

    async def _run_game(self) -> None:
        table = self.table
        msg = table.message
        if not msg:
            return

        for rnd_idx in range(len(table.rounds)):
            terms, answer, name = table.rounds[rnd_idx]
            table.round_num = rnd_idx + 1
            table.current_answer = answer
            table.current_name = name
            table.correct_order.clear()
            table.round_event.clear()
            for p in table.players.values():
                p.answered_this_round = False

            # Show the sequence
            self._update_buttons()
            embed = _round_embed(table, terms, ROUND_TIME)
            try:
                await msg.edit(embed=embed, view=self)
            except Exception:
                return

            # Wait for all answers or timeout
            try:
                await asyncio.wait_for(table.round_event.wait(), timeout=ROUND_TIME)
            except asyncio.TimeoutError:
                pass

            # Show round results
            embed = _round_result_embed(table, terms, answer, name)
            try:
                await msg.edit(embed=embed, view=self)
            except Exception:
                return

            await asyncio.sleep(4)

        # ── Game over ────────────────────────────────────────────────────
        table.phase = "finished"

        # Determine winner(s) by score
        sorted_players = sorted(table.players.values(), key=lambda p: p.score, reverse=True)
        if sorted_players:
            top_score = sorted_players[0].score
            winner_uids = [p.user_id for p in sorted_players if p.score == top_score]
        else:
            winner_uids = []

        bets = {uid: p.bet for uid, p in table.players.items()}
        payouts = compute_side_pot_payouts(bets, winner_uids)

        # Credit payouts
        for uid, payout in payouts.items():
            if payout > 0:
                await queries.update_casino_balance(str(uid), payout)

        # Log results
        for uid, p in table.players.items():
            await queries.log_casino_result(
                str(uid), "sequence", p.bet, payouts.get(uid, 0),
            )

        # ELO update
        if len(sorted_players) >= 2:
            finish_order = [p.user_id for p in sorted_players]
            try:
                elo_changes = await update_elo_multiplayer(finish_order, "sequence", "sequence", scores={p.user_id: p.score for p in sorted_players})
            except Exception:
                elo_changes = {}
        else:
            elo_changes = {}

        embed = _final_embed(table, payouts)

        # Add ELO changes
        if elo_changes:
            elo_lines = []
            for p in sorted_players:
                change = elo_changes.get(p.user_id)
                if change:
                    old_r, new_r = change
                    elo_lines.append(f"**{p.display_name}**: {fmt_elo_change(old_r, new_r)}")
            if elo_lines:
                embed.add_field(name="ELO", value="\n".join(elo_lines), inline=False)

        self._update_buttons()
        try:
            await msg.edit(embed=embed, view=self)
        except Exception:
            log.exception("Unhandled error in sequence.py")

    # ── Timeout ──────────────────────────────────────────────────────────

    async def on_timeout(self) -> None:
        if self._round_task and not self._round_task.done():
            self._round_task.cancel()

        if self.table.phase == "betting":
            for p in self.table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    log.exception("Unhandled error in sequence.py")

        self.active_tables.pop(self.table.channel_id, None)
        if self.table.message:
            try:
                embed = discord.Embed(
                    title="\u2716 Sequence \u2014 Timed Out",
                    description="Table timed out. Bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await self.table.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("Unhandled error in sequence.py")


# ── Cog ──────────────────────────────────────────────────────────────────────


class SequenceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, SeqTable] = {}

    async def sequence(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            _has_running = any(
                (t := getattr(existing, n, None)) is not None and not t.done()
                for n in ("game_task", "race_task", "sim_task", "round_task", "_round_task", "trade_task", "fly_task", "_shot_clock_task", "_countdown_task")
            )
            if _has_running:
                await interaction.response.send_message(
                    "There's already a Sequence game in this channel!", ephemeral=True,
                )
                return
            del self.active_tables[channel_id]

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = SeqTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        view = SeqView(table, self.active_tables)
        embed = _betting_embed(table)
        try:
            await interaction.response.send_message(embed=embed, view=view)
        except discord.NotFound:
            return  # interaction expired — don't leave a ghost table
        self.active_tables[channel_id] = table
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SequenceCog(bot))
