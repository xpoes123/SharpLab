"""Casino cog — /mathsprint speed arithmetic game.

10 rapid-fire problems.  Fastest correct answer wins each problem.
Problem types: multiplication, division, percentages, squares, cubes,
powers, roots, remainders, combinations, GCD, factorial ratios, addition,
subtraction.  Party mode, 1-8 players.
"""

import asyncio
import math
import random
import time
from dataclasses import dataclass, field
from itertools import groupby

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs._elo_helpers import update_elo_multiplayer

from db import queries
import logging

log = logging.getLogger(__name__)
# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 1
NUM_PROBLEMS = 10
ROUND_SECONDS = 20
ROUND_DELAY = 4  # seconds between problems
INACTIVITY_ROUNDS = 5  # auto-end after N consecutive unanswered rounds

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


# ── Problem Generation ───────────────────────────────────────────────────────


@dataclass
class Problem:
    category: str
    emoji: str
    display: str  # e.g. "47 \u00d7 23 = ?"
    answer: int


def _gen_multiply() -> Problem:
    a, b = random.randint(12, 99), random.randint(12, 99)
    return Problem("Multiply", "\u2716\ufe0f", f"{a} \u00d7 {b} = ?", a * b)


def _gen_divide() -> Problem:
    b = random.randint(7, 25)
    result = random.randint(8, 50)
    a = b * result
    return Problem("Divide", "\u2797", f"{a} \u00f7 {b} = ?", result)


def _gen_percentage() -> Problem:
    pct = random.choice([5, 10, 12, 15, 20, 25, 30, 35, 40, 50, 60, 75])
    divisor = 100 // math.gcd(pct, 100)
    base = divisor * random.randint(2, 40)
    answer = base * pct // 100
    return Problem("Percentage", "\U0001f4af", f"{pct}% of {base} = ?", answer)


def _gen_square() -> Problem:
    n = random.randint(11, 35)
    return Problem("Square", "\u00b2", f"{n}\u00b2 = ?", n * n)


def _gen_cube() -> Problem:
    n = random.randint(3, 12)
    return Problem("Cube", "\u00b3", f"{n}\u00b3 = ?", n ** 3)


def _gen_add_three() -> Problem:
    a, b, c = random.randint(100, 999), random.randint(100, 999), random.randint(100, 999)
    return Problem("Add", "\u2795", f"{a} + {b} + {c} = ?", a + b + c)


def _gen_subtract() -> Problem:
    a = random.randint(1000, 9999)
    b = random.randint(100, a - 1)
    return Problem("Subtract", "\u2796", f"{a} \u2212 {b} = ?", a - b)


def _gen_power() -> Problem:
    pairs = (
        [(2, n) for n in range(7, 14)]
        + [(3, n) for n in range(4, 8)]
        + [(4, 4), (4, 5), (5, 3), (5, 4), (6, 3), (7, 3)]
    )
    base, exp = random.choice(pairs)
    return Problem("Power", "\u2b06\ufe0f", f"{base}^{exp} = ?", base ** exp)


def _gen_remainder() -> Problem:
    b = random.randint(7, 23)
    a = random.randint(100, 999)
    return Problem("Remainder", "\U0001f522", f"{a} mod {b} = ?", a % b)


def _gen_sqrt() -> Problem:
    n = random.randint(10, 31)
    return Problem("Square Root", "\u221a", f"\u221a{n * n} = ?", n)


def _gen_factorial_ratio() -> Problem:
    n = random.randint(6, 12)
    m = random.randint(max(1, n - 3), n - 1)
    result = 1
    for i in range(m + 1, n + 1):
        result *= i
    return Problem("Factorial", "\u2757", f"{n}! \u00f7 {m}! = ?", result)


def _gen_combination() -> Problem:
    n = random.randint(5, 12)
    k = random.randint(2, min(4, n - 1))
    return Problem("Choose", "\U0001f3af", f"C({n},{k}) = ?", math.comb(n, k))


def _gen_gcd() -> Problem:
    a, b = random.randint(24, 200), random.randint(24, 200)
    return Problem("GCD", "\U0001f517", f"GCD({a}, {b}) = ?", math.gcd(a, b))


# Weighted pool — multiplication appears more often
_GENERATORS = [
    _gen_multiply, _gen_multiply,
    _gen_divide, _gen_percentage,
    _gen_square, _gen_cube,
    _gen_add_three, _gen_subtract,
    _gen_power, _gen_remainder,
    _gen_sqrt, _gen_factorial_ratio,
    _gen_combination, _gen_gcd,
]


def generate_problem() -> Problem:
    return random.choice(_GENERATORS)()


# ── Answer Validation ────────────────────────────────────────────────────────


def check_answer(raw: str, correct: int) -> bool:
    """Parse a player's answer and check against the correct value."""
    cleaned = raw.strip().replace(",", "").replace(" ", "")
    try:
        val = int(cleaned)
        return val == correct
    except ValueError:
        pass
    try:
        fval = float(cleaned)
        return abs(fval - correct) < 0.01
    except ValueError:
        return False


# ── Payout Logic ─────────────────────────────────────────────────────────────


def _compute_payouts(
    players: dict[int, "SprintPlayer"], prize_pool: int, n_players: int,
) -> dict[int, int]:
    """Paytable payouts.  Ties split combined shares for tied positions."""
    pct_table = PAYTABLE.get(n_players, PAYTABLE[8])

    in_money = sorted(
        [p for p in players.values() if p.points > 0],
        key=lambda p: (-p.points, p.total_time),
    )

    payouts: dict[int, int] = {uid: 0 for uid in players}
    if not in_money:
        return payouts

    paid_positions = len(pct_table)
    pos = 0
    for _key, group_iter in groupby(in_money, key=lambda p: (-p.points, round(p.total_time, 2))):
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
        top = in_money[0]
        top_group = [p for p in in_money if p.points == top.points]
        extra = leftover // len(top_group)
        for p in top_group:
            payouts[p.user_id] += extra

    return payouts


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class SprintPlayer:
    user_id: int
    display_name: str
    bet: int
    points: int = 0
    total_time: float = 0.0  # sum of solve times (tiebreaker)
    solved_this_round: bool = False
    answer_time: float | None = None


@dataclass
class SprintTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | between | closed
    players: dict[int, SprintPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    lobby_message: discord.Message | None = None  # original channel message (buttons)
    thread: discord.Thread | None = None
    current_thread_msg: discord.Message | None = None
    round_num: int = 0
    problem: Problem | None = None
    round_start: float = 0.0
    round_winner: int | None = None
    round_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    solved_players: set[int] = field(default_factory=set)
    race_task: asyncio.Task | None = field(default=None, repr=False)
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    game_num: int = 1
    stop_requested: bool = False


# ── Embeds ───────────────────────────────────────────────────────────────────


def _scoreboard(table: SprintTable) -> str:
    ranked = sorted(
        table.players.values(),
        key=lambda p: (-p.points, p.total_time),
    )
    lines: list[str] = []
    for i, p in enumerate(ranked):
        prefix = MEDALS[i] if i < len(MEDALS) and p.points > 0 else "\u25aa\ufe0f"
        line = f"{prefix} **{p.display_name}** \u2014 {p.points} pts"
        if p.solved_this_round:
            line += " \u2705"
        lines.append(line)
    return "\n".join(lines) if lines else "No scores yet"


def _betting_embed(table: SprintTable) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values())
    n = len(table.players)
    embed = discord.Embed(
        title=f"\U0001f9e0 Math Sprint \u2014 Join (Game {table.game_num})",
        description=(
            f"**{NUM_PROBLEMS} rapid-fire math problems!**\n"
            "Multiplication, percentages, powers, roots, combos & more.\n"
            f"**{ROUND_SECONDS}s** per problem \u2014 fastest correct answer wins the point.\n"
            "Most points after 10 problems wins the pot!"
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
        lines = [f"\U0001f9ee **{p.display_name}** \u2014 {p.bet}c" for p in table.players.values()]
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Players", value="*No players yet \u2014 click Join!*", inline=False)
    embed.set_footer(text=f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} player{'s' if MIN_PLAYERS != 1 else ''}")
    return embed


def _playing_embed(table: SprintTable, remaining: int | None = None) -> discord.Embed:
    prob = table.problem
    secs = remaining if remaining is not None else ROUND_SECONDS
    embed = discord.Embed(
        title=f"\U0001f9e0 Math Sprint \u2014 Problem {table.round_num}/{NUM_PROBLEMS}",
        colour=discord.Colour.gold(),
    )
    embed.description = (
        f"### {prob.emoji} {prob.category}\n"
        f"# {prob.display}\n\n"
        "Type your answer in this thread!"
    )
    embed.add_field(name="\u23f1\ufe0f Time", value=f"**{secs}s**", inline=True)
    pot = sum(p.bet for p in table.players.values())
    embed.add_field(name="Pot", value=f"{pot}c", inline=True)
    solved = len(table.solved_players)
    total = len(table.players)
    if solved > 0:
        embed.add_field(name="Solved", value=f"{solved}/{total}", inline=True)
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _round_result_embed(table: SprintTable) -> discord.Embed:
    prob = table.problem
    winner = table.players[table.round_winner]
    solve_time = winner.answer_time - table.round_start

    embed = discord.Embed(
        title=f"\U0001f9e0 Math Sprint \u2014 Problem {table.round_num}/{NUM_PROBLEMS} \u2705",
        colour=discord.Colour.green(),
    )

    also: list[str] = []
    for uid in table.solved_players:
        if uid != table.round_winner:
            p = table.players[uid]
            t = p.answer_time - table.round_start
            also.append(f"{p.display_name} ({t:.1f}s)")

    desc = (
        f"\U0001f3c6 **{winner.display_name}** answered first in **{solve_time:.1f}s**!\n\n"
        f"{prob.emoji} {prob.display[:-4]} = **{prob.answer}**"
    )
    if also:
        desc += f"\nAlso solved: {', '.join(also)}"
    embed.description = desc
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if table.round_num < NUM_PROBLEMS:
        embed.set_footer(text="Next problem in a few seconds\u2026")
    else:
        embed.set_footer(text="Final problem complete \u2014 calculating results\u2026")
    return embed


def _timeout_embed(table: SprintTable) -> discord.Embed:
    prob = table.problem
    embed = discord.Embed(
        title=f"\U0001f9e0 Math Sprint \u2014 Problem {table.round_num}/{NUM_PROBLEMS} (Time\u2019s Up!)",
        colour=discord.Colour.dark_grey(),
    )
    embed.description = (
        f"Nobody solved it in {ROUND_SECONDS} seconds!\n\n"
        f"{prob.emoji} {prob.display[:-4]} = **{prob.answer}**"
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if table.round_num < NUM_PROBLEMS:
        embed.set_footer(text="Next problem in a few seconds\u2026")
    else:
        embed.set_footer(text="Final problem complete \u2014 calculating results\u2026")
    return embed


def _final_embed(
    table: SprintTable, *, payouts: dict[int, int], balances: dict[int, int],
) -> discord.Embed:
    max_pts = max((p.points for p in table.players.values()), default=0)
    is_refund = max_pts == 0

    embed = discord.Embed(
        title=f"\U0001f9e0 Math Sprint \u2014 Results (Game {table.game_num})",
        colour=discord.Colour.gold() if not is_refund else discord.Colour.dark_grey(),
    )

    if is_refund:
        embed.description = "No problems were solved \u2014 all bets refunded!"
    else:
        ranked = sorted(table.players.values(), key=lambda p: (-p.points, p.total_time))
        winner = ranked[0]
        embed.description = (
            f"\U0001f3c6 **{winner.display_name}** wins with **{winner.points}** "
            f"point{'s' if winner.points != 1 else ''} "
            f"(total solve time: {winner.total_time:.1f}s)!"
        )

    ranked = sorted(table.players.values(), key=lambda p: (-p.points, p.total_time))
    lines: list[str] = []
    for i, p in enumerate(ranked):
        payout = payouts.get(p.user_id, 0)
        bal = balances.get(p.user_id, 0)
        net = payout - p.bet
        sign = "+" if net >= 0 else ""
        medal = MEDALS[i] if i < len(MEDALS) and p.points > 0 else "\u25aa\ufe0f"
        time_str = f" ({p.total_time:.1f}s)" if p.total_time > 0 else ""
        lines.append(
            f"{medal} **{p.display_name}** ({p.points} pts{time_str}) \u2014 "
            f"{p.bet}c \u2192 {payout}c "
            f"(**{sign}{net}c**) \u2014 bal: {bal}c"
        )
    embed.add_field(name="Results", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinSprintModal(ui.Modal):
    amount = ui.TextInput(label="Bet amount (coins)", placeholder="e.g. 100", required=True, max_length=10)

    def __init__(self, table: SprintTable, view: "SprintTableView", balance: int) -> None:
        super().__init__(title="Join Math Sprint")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 100 (bal: {balance}c)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if amt < 1:
            await interaction.response.send_message("Must be at least 1 coin.", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(f"Not enough coins! (have {bal}c)", ephemeral=True)
            return
        self.table.players[uid] = SprintPlayer(user_id=uid, display_name=interaction.user.display_name, bet=amt)
        self.table_view._update_buttons()
        await interaction.response.edit_message(embed=_betting_embed(self.table), view=self.table_view)


# ── View ─────────────────────────────────────────────────────────────────────


class SprintEndGameView(ui.View):
    """Button posted in the thread so any player can stop the game early."""

    def __init__(self, table: SprintTable) -> None:
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
        self.table.round_solved.set()  # wake up the game loop immediately
        button.disabled = True
        button.label = "Ending\u2026"
        await interaction.response.edit_message(view=self)


class SprintTableView(ui.View):
    def __init__(self, table: SprintTable, active_tables: dict[int, SprintTable]) -> None:
        super().__init__(timeout=600)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        racing = phase in ("playing", "between")

        self.start_btn.disabled = not betting or len(self.table.players) < MIN_PLAYERS
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = not betting

        self.close_btn.disabled = racing

    # ── Row 0: Betting ───────────────────────────────────────────────────

    @ui.button(label="Start", style=discord.ButtonStyle.success, emoji="\u25b6\ufe0f", row=0)
    async def start_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message("Only the host can start!", ephemeral=True)
            return
        if self.table.phase != "betting":
            await interaction.response.send_message("Already started!", ephemeral=True)
            return
        if len(self.table.players) < MIN_PLAYERS:
            await interaction.response.send_message(f"Need at least {MIN_PLAYERS} player!", ephemeral=True)
            return
        await self._start_sprint(interaction)

    @ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f9ee", row=0)
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Sprint in progress! Wait for the next game.", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        bal = await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(JoinSprintModal(self.table, self, bal))

    @ui.button(label="Re-bet", style=discord.ButtonStyle.primary, emoji="\U0001f504", row=0)
    async def rebet_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Sprint in progress!", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return
        last = self.table.last_bets.get(uid)
        if last is None:
            await interaction.response.send_message("No previous bet \u2014 use Join instead.", ephemeral=True)
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
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
        self.table.players[uid] = SprintPlayer(user_id=uid, display_name=name, bet=amt)
        self._update_buttons()
        await interaction.response.edit_message(embed=_betting_embed(self.table), view=self)

    @ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=0)
    async def leave_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if player is None:
            await interaction.response.send_message("You're not at this table.", ephemeral=True)
            return
        if self.table.phase != "betting":
            await interaction.response.send_message("Can't leave during a sprint!", ephemeral=True)
            return
        await queries.update_casino_balance(str(uid), player.bet)
        del self.table.players[uid]
        self._update_buttons()
        await interaction.response.edit_message(embed=_betting_embed(self.table), view=self)

    # ── Row 1: Game controls ─────────────────────────────────────────────

    @ui.button(label="Close Table", style=discord.ButtonStyle.danger, emoji="\u2716\ufe0f", row=1)
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message("Only the host can close the table!", ephemeral=True)
            return
        if self.table.phase in ("playing", "between"):
            await interaction.response.send_message("Can't close during a sprint!", ephemeral=True)
            return
        await self._close_table(interaction)

    # ── Game logic ───────────────────────────────────────────────────────

    async def _start_sprint(self, interaction: discord.Interaction) -> None:
        table = self.table
        for uid, p in table.players.items():
            table.last_bets[uid] = (p.display_name, p.bet)

        table.problem = generate_problem()
        table.round_num = 1
        table.round_winner = None
        table.round_solved.clear()
        table.solved_players.clear()
        table.phase = "playing"

        for p in table.players.values():
            p.solved_this_round = False
            p.answer_time = None

        self._update_buttons()

        in_progress_embed = discord.Embed(
            title="\U0001f9e0 Math Sprint \u2014 In Progress",
            description="Game running! Check the thread below for problems.",
            colour=discord.Colour.gold(),
        )
        in_progress_embed.set_footer(text=f"Host: {table.host_name}")
        await interaction.response.edit_message(embed=in_progress_embed, view=self)

        # Create thread from the lobby message, then start the clock
        if table.message:
            table.lobby_message = table.message  # keep ref so we can update it on game end
            thread = await table.message.create_thread(name="Math Sprint")
            table.thread = thread
            await thread.send(
                f"\U0001f9e0 **Math Sprint started!** Type your answer directly here. "
                f"**{ROUND_SECONDS}s** per problem \u2014 fastest correct answer wins the point!",
                view=SprintEndGameView(table),
            )

        table.round_start = time.monotonic()
        table.race_task = asyncio.create_task(self._sprint_loop())

    async def _wait_for_solve_or_timeout(self) -> bool:
        table = self.table
        deadline = table.round_start + ROUND_SECONDS
        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                return table.round_winner is not None
            wait = min(10.0, remaining)
            try:
                await asyncio.wait_for(table.round_solved.wait(), timeout=wait)
                return True
            except asyncio.TimeoutError:
                if table.round_winner is not None:
                    return True
                secs_left = max(0, int(deadline - time.monotonic()))
                if secs_left > 0 and table.current_thread_msg:
                    try:
                        await table.current_thread_msg.edit(
                            embed=_playing_embed(table, remaining=secs_left),
                        )
                    except discord.HTTPException:
                        pass

    async def _sprint_loop(self) -> None:
        table = self.table
        try:
            rnd = 0
            consecutive_unanswered = 0
            while True:
                rnd += 1
                if rnd > 1:
                    table.problem = generate_problem()
                    table.round_num = rnd
                    table.round_winner = None
                    table.round_solved.clear()
                    table.solved_players.clear()
                    table.phase = "playing"
                    table.round_start = time.monotonic()
                    for p in table.players.values():
                        p.solved_this_round = False
                        p.answer_time = None

                # Post problem to thread
                if table.thread:
                    try:
                        table.current_thread_msg = await table.thread.send(
                            embed=_playing_embed(table),
                        )
                    except discord.HTTPException:
                        pass

                solved = await self._wait_for_solve_or_timeout()

                if table.stop_requested:
                    break

                if solved and table.round_winner is not None:
                    if table.current_thread_msg:
                        try:
                            await table.current_thread_msg.edit(embed=_round_result_embed(table))
                        except discord.HTTPException:
                            pass
                else:
                    if table.current_thread_msg:
                        try:
                            await table.current_thread_msg.edit(embed=_timeout_embed(table))
                        except discord.HTTPException:
                            pass

                # Inactivity: end if nobody answered N rounds in a row
                if table.round_winner is None:
                    consecutive_unanswered += 1
                else:
                    consecutive_unanswered = 0
                if consecutive_unanswered >= INACTIVITY_ROUNDS:
                    if table.thread:
                        try:
                            await table.thread.send(
                                "\u23f8\ufe0f No one answered for 5 consecutive rounds — ending due to inactivity."
                            )
                        except discord.HTTPException:
                            pass
                    break

                if rnd >= NUM_PROBLEMS:
                    break

                table.phase = "between"
                await asyncio.sleep(ROUND_DELAY)

                if table.stop_requested:
                    break

            if table.stop_requested and table.thread:
                try:
                    await table.thread.send("\u23f9\ufe0f Game ended early.")
                except discord.HTTPException:
                    pass
            await self._end_game()

        except asyncio.CancelledError:
            table.phase = "closed"
            self.active_tables.pop(table.channel_id, None)
            await self._update_lobby(
                "\U0001f9e0 Math Sprint \u2014 Cancelled",
                "Game was cancelled.",
            )
            if table.thread:
                try:
                    await table.thread.edit(archived=True)
                except Exception:
                    log.exception("Unhandled error in mathsprint.py")
        except Exception:
            table.phase = "closed"
            self.active_tables.pop(table.channel_id, None)
            await self._update_lobby(
                "\U0001f9e0 Math Sprint \u2014 Error",
                "Game ended due to an error.",
            )
            if table.thread:
                try:
                    await table.thread.edit(archived=True)
                except Exception:
                    log.exception("Unhandled error in mathsprint.py")

    async def _compute_and_apply_payouts(self) -> tuple[dict[int, int], dict[int, int]]:
        table = self.table
        n_players = len(table.players)
        pot = sum(p.bet for p in table.players.values())
        max_pts = max((p.points for p in table.players.values()), default=0)

        if max_pts == 0:
            payouts = {uid: p.bet for uid, p in table.players.items()}
            for uid, refund in payouts.items():
                try:
                    await queries.update_casino_balance(str(uid), refund)
                except Exception:
                    log.exception("Unhandled error in mathsprint.py")
        else:
            payouts = _compute_payouts(table.players, pot, n_players)
            for uid, payout in payouts.items():
                if payout > 0:
                    try:
                        await queries.update_casino_balance(str(uid), payout)
                    except Exception:
                        log.exception("Unhandled error in mathsprint.py")

        balances: dict[int, int] = {}
        for uid in table.players:
            bal = await queries.get_casino_balance(str(uid))
            balances[uid] = bal or 0
        for uid, p in table.players.items():
            await queries.log_casino_result(str(uid), "mathsprint", p.bet, payouts.get(uid, 0))

        return payouts, balances

    async def _update_lobby(self, title: str, description: str) -> None:
        """Edit the original channel lobby message (not the thread)."""
        table = self.table
        if table.lobby_message:
            embed = discord.Embed(
                title=title, description=description,
                colour=discord.Colour.dark_grey(),
            )
            try:
                await table.lobby_message.edit(embed=embed, view=None)
            except Exception:
                pass

    async def _end_game(self) -> None:
        table = self.table
        table.phase = "closed"
        payouts, balances = await self._compute_and_apply_payouts()

        if len(table.players) >= 2:
            sorted_p = sorted(table.players.values(), key=lambda p: (-p.points, p.total_time))
            finish_order = [p.user_id for p in sorted_p]
            try:
                await update_elo_multiplayer(finish_order, "mathsprint", "mathsprint")
            except Exception:
                log.exception("Unhandled error in mathsprint.py")

        embed = _final_embed(table, payouts=payouts, balances=balances)

        # Post final results to thread and archive it
        if table.thread:
            try:
                await table.thread.send(embed=embed)
            except discord.HTTPException:
                pass
            try:
                await table.thread.edit(archived=True)
            except discord.HTTPException:
                pass

        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)

        # Update the original lobby message so buttons don't show "interaction failed"
        await self._update_lobby(
            "\U0001f9e0 Math Sprint \u2014 Finished",
            "Game complete! See results in the thread.",
        )

    async def _close_table(self, interaction: discord.Interaction) -> None:
        table = self.table
        if table.round_num == 0:
            for p in table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    log.exception("Unhandled error in mathsprint.py")
            embed = discord.Embed(
                title="\U0001f9e0 Math Sprint \u2014 Closed",
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

        if table.thread:
            try:
                await table.thread.send(embed=embed)
            except discord.HTTPException:
                pass
            try:
                await table.thread.edit(archived=True)
            except discord.HTTPException:
                pass

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
                log.exception("Unhandled error in mathsprint.py")
        table.phase = "closed"
        self.active_tables.pop(table.channel_id, None)
        # Also update the lobby message
        await self._update_lobby(
            "\U0001f9e0 Math Sprint \u2014 Timed Out",
            "Table timed out. All bets refunded.",
        )

        if table.thread:
            try:
                await table.thread.edit(archived=True)
            except Exception:
                log.exception("Unhandled error in mathsprint.py")


# ── Cog ──────────────────────────────────────────────────────────────────────


class MathSprintCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, SprintTable] = {}

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message) -> None:
        """Listen for typed answers in Math Sprint threads."""
        if message.author.bot:
            return

        # Find the table whose thread matches this channel
        table: SprintTable | None = None
        for t in self.active_tables.values():
            if t.thread and t.thread.id == message.channel.id:
                table = t
                break

        if table is None:
            return

        if table.phase != "playing":
            return

        uid = message.author.id
        if uid not in table.players:
            return

        if uid in table.solved_players:
            return

        if table.problem is None:
            return

        # Snapshot mutable state before processing (no awaits before state write,
        # but guard against concurrent messages resolving the same round)
        captured_problem = table.problem
        captured_round_num = table.round_num

        raw = message.content.strip()
        if not raw:
            return

        if check_answer(raw, captured_problem.answer):
            # Re-validate: problem may have advanced between the phase check and now
            if table.round_num != captured_round_num or table.problem is not captured_problem:
                return
            if uid in table.solved_players:
                return

            now = time.monotonic()
            player = table.players[uid]
            player.solved_this_round = True
            player.answer_time = now
            solve_time = now - table.round_start
            player.total_time += solve_time

            if table.round_winner is None:
                table.round_winner = uid
                player.points += 1

            table.solved_players.add(uid)

            try:
                await message.add_reaction("\u2705")
            except discord.HTTPException:
                pass

            if len(table.solved_players) >= len(table.players):
                table.round_solved.set()
        else:
            try:
                await message.add_reaction("\u274c")
            except discord.HTTPException:
                pass

    @app_commands.command(name="mathsprint", description="Mental Math Sprint \u2014 10 rapid-fire problems, fastest wins!")
    async def mathsprint(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            _has_running = any(
                (t := getattr(existing, n, None)) is not None and not t.done()
                for n in ("game_task", "race_task", "sim_task", "round_task", "_round_task", "trade_task", "fly_task", "_shot_clock_task", "_countdown_task")
            )
            if _has_running:
                await interaction.response.send_message(
                    "There's already a Math Sprint in this channel!", ephemeral=True,
                )
                return
            del self.active_tables[channel_id]
        await queries.get_or_create_casino_wallet(str(interaction.user.id))
        table = SprintTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table
        view = SprintTableView(table, self.active_tables)
        await interaction.response.send_message(embed=_betting_embed(table), view=view)
        msg = await interaction.original_response()
        table.message = msg
        table.lobby_message = msg


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MathSprintCog(bot))
