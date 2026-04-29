"""Casino cog — /prisoner  Iterated Prisoner's Dilemma tournament."""

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
from bot.cogs._elo_helpers import update_elo_multiplayer, fmt_elo_change
from bot.cogs._pool import compute_side_pot_payouts

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 2
NUM_ROUNDS = 10
CHOICE_TIME = 20  # seconds per round
MAX_BET = 500

# Payoff matrix (row = my choice, col = opponent choice)
# CC=3, CD=0, DC=5, DD=1
PAYOFF = {
    ("C", "C"): (3, 3),
    ("C", "D"): (0, 5),
    ("D", "C"): (5, 0),
    ("D", "D"): (1, 1),
}

COOP_EMOJI = "\U0001f91d"  # handshake
DEFECT_EMOJI = "\U0001f5e1"  # dagger


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class PDPlayer:
    user_id: int
    display_name: str
    bet: int
    score: int = 0
    choice: str | None = None  # "C" or "D" for current round
    history: list[str] = field(default_factory=list)  # all choices


@dataclass
class PDTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | finished
    players: dict[int, PDPlayer] = field(default_factory=dict)
    round_num: int = 0
    message: discord.Message | None = None
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    round_event: asyncio.Event = field(default_factory=asyncio.Event)
    bot_id: int = -1  # sentinel for solo bot player


# ── Bot strategy ─────────────────────────────────────────────────────────────


def _tit_for_tat(player_history: list[str], _round: int) -> str:
    """Tit-for-tat: cooperate first, then mirror opponent's last move."""
    if not player_history:
        return "C"
    # Look at what the human did last round
    return player_history[-1]


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: PDTable) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values() if p.user_id != table.bot_id)
    embed = discord.Embed(
        title=f"{COOP_EMOJI} Prisoner's Dilemma \u2014 Join",
        description=(
            "Iterated Prisoner's Dilemma — cooperate or betray?\n\n"
            "**Payoff matrix** (your pts / their pts):\n"
            "Both Cooperate: **3/3** | You Defect: **5/0**\n"
            "Both Defect: **1/1** | They Defect: **0/5**\n\n"
            f"**{NUM_ROUNDS}** rounds, {CHOICE_TIME}s each. "
            "Highest total score wins."
        ),
        colour=discord.Colour.teal(),
    )
    if pot:
        embed.add_field(name="Pot", value=f"{pot}c", inline=True)
    real_players = [p for p in table.players.values() if p.user_id != table.bot_id]
    if real_players:
        lines = [f"{COOP_EMOJI} **{p.display_name}** \u2014 {p.bet}c" for p in real_players]
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet \u2014 click Join!*",
            inline=False,
        )
    embed.set_footer(
        text=f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} players (or solo vs bot)",
    )
    return embed


def _round_embed(table: PDTable) -> discord.Embed:
    embed = discord.Embed(
        title=f"{COOP_EMOJI} Prisoner's Dilemma \u2014 Round {table.round_num}/{NUM_ROUNDS}",
        description="Choose: **Cooperate** or **Defect**",
        colour=discord.Colour.teal(),
    )

    # Show who has chosen (without revealing)
    lines = []
    sorted_players = sorted(table.players.values(), key=lambda p: p.score, reverse=True)
    for p in sorted_players:
        if p.user_id == table.bot_id:
            status = "\u2705"  # bot always ready
        elif p.choice is not None:
            status = "\u2705"
        else:
            status = "\u23f3"
        lines.append(f"{status} **{p.display_name}** \u2014 {p.score} pts")
    embed.add_field(name="Players", value="\n".join(lines), inline=False)
    return embed


def _round_result_embed(table: PDTable) -> discord.Embed:
    embed = discord.Embed(
        title=f"{COOP_EMOJI} Prisoner's Dilemma \u2014 Round {table.round_num} Results",
        colour=discord.Colour.dark_teal(),
    )

    # Show choices
    lines = []
    for p in table.players.values():
        emoji = COOP_EMOJI if p.choice == "C" else DEFECT_EMOJI
        label = "Cooperated" if p.choice == "C" else "Defected"
        lines.append(f"{emoji} **{p.display_name}** \u2014 {label}")
    embed.add_field(name="Choices", value="\n".join(lines), inline=False)

    # Scoreboard
    sorted_players = sorted(table.players.values(), key=lambda p: p.score, reverse=True)
    score_lines = []
    for i, p in enumerate(sorted_players):
        medal = {0: "\U0001f947", 1: "\U0001f948", 2: "\U0001f949"}.get(i, f"**{i+1}.**")
        score_lines.append(f"{medal} **{p.display_name}** \u2014 {p.score} pts")
    embed.add_field(name="Standings", value="\n".join(score_lines), inline=False)

    return embed


def _final_embed(table: PDTable, payouts: dict[int, int]) -> discord.Embed:
    sorted_players = sorted(table.players.values(), key=lambda p: p.score, reverse=True)
    embed = discord.Embed(
        title="\U0001f3c6 Prisoner's Dilemma \u2014 Final Results",
        colour=discord.Colour.gold(),
    )
    lines = []
    for i, p in enumerate(sorted_players):
        medal = {0: "\U0001f947", 1: "\U0001f948", 2: "\U0001f949"}.get(i, f"**{i+1}.**")
        coop_count = p.history.count("C")
        defect_count = p.history.count("D")
        strategy = f"({coop_count}C/{defect_count}D)"

        if p.user_id == table.bot_id:
            payout_str = ""
        else:
            payout = payouts.get(p.user_id, 0)
            net = payout - p.bet
            payout_str = f" \u2014 {payout}c ({net:+,}c)"
        lines.append(
            f"{medal} **{p.display_name}** \u2014 {p.score} pts {strategy}{payout_str}"
        )
    embed.description = "\n".join(lines)
    return embed


# ── Modals ───────────────────────────────────────────────────────────────────


class JoinPDModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 50",
        required=True,
        max_length=10,
    )

    def __init__(self, table: PDTable, view: "PDView", balance: int) -> None:
        super().__init__(title="Join Prisoner's Dilemma")
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

        self.table.players[uid] = PDPlayer(
            user_id=uid, display_name=interaction.user.display_name, bet=amt,
        )
        self.table.last_bets[uid] = (interaction.user.display_name, amt)
        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


# ── View ─────────────────────────────────────────────────────────────────────


class PDView(ui.View):
    def __init__(self, table: PDTable, active_tables: dict[int, PDTable]) -> None:
        super().__init__(timeout=600)
        self.table = table
        self.active_tables = active_tables
        self._round_task: asyncio.Task | None = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        betting = self.table.phase == "betting"
        playing = self.table.phase == "playing"
        finished = self.table.phase == "finished"

        self.start_btn.disabled = not betting or len(
            [p for p in self.table.players.values() if p.user_id != self.table.bot_id]
        ) < 1
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = not betting

        self.coop_btn.disabled = not playing
        self.defect_btn.disabled = not playing
        self.history_btn.disabled = not playing and not finished

        self.new_round_btn.disabled = not finished
        self.close_btn.disabled = playing

    # ── Betting buttons (row 0) ──────────────────────────────────────────

    @ui.button(label="Start", style=discord.ButtonStyle.success, emoji="\u25b6", row=0)
    async def start_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message("Already started!", ephemeral=True)
            return
        real_players = [p for p in self.table.players.values() if p.user_id != self.table.bot_id]
        if len(real_players) < 1:
            await interaction.response.send_message("Need at least 1 player!", ephemeral=True)
            return

        # Add bot if solo
        if len(real_players) == 1:
            self.table.players[self.table.bot_id] = PDPlayer(
                user_id=self.table.bot_id,
                display_name="Tit-for-Tat Bot",
                bet=0,
            )

        self.table.phase = "playing"
        self._update_buttons()
        await interaction.response.edit_message(
            embed=discord.Embed(
                title=f"{COOP_EMOJI} Prisoner's Dilemma",
                description="Game starting...",
                colour=discord.Colour.teal(),
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
        await interaction.response.send_modal(JoinPDModal(self.table, self, bal))

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
                f"Not enough coins for {amt}c! (have {bal}c)", ephemeral=True,
            )
            return
        self.table.players[uid] = PDPlayer(
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

    @ui.button(label="Cooperate", style=discord.ButtonStyle.success, emoji=COOP_EMOJI, row=1)
    async def coop_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_choice(interaction, "C")

    @ui.button(label="Defect", style=discord.ButtonStyle.danger, emoji=DEFECT_EMOJI, row=1)
    async def defect_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._handle_choice(interaction, "D")

    @ui.button(label="History", style=discord.ButtonStyle.secondary, emoji="\U0001f4dc", row=1)
    async def history_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if not player:
            await interaction.response.send_message("You're not in this game!", ephemeral=True)
            return
        if not player.history:
            await interaction.response.send_message("No history yet!", ephemeral=True)
            return

        lines = []
        for rnd, choice in enumerate(player.history, 1):
            emoji = COOP_EMOJI if choice == "C" else DEFECT_EMOJI
            lines.append(f"Round {rnd}: {emoji} {'Cooperate' if choice == 'C' else 'Defect'}")

        # Show all players' histories
        all_lines = []
        for p in self.table.players.values():
            h = " ".join(COOP_EMOJI if c == "C" else DEFECT_EMOJI for c in p.history)
            all_lines.append(f"**{p.display_name}**: {h}")

        await interaction.response.send_message(
            "\n".join(all_lines) if all_lines else "No history.",
            ephemeral=True,
        )

    async def _handle_choice(self, interaction: discord.Interaction, choice: str) -> None:
        if self.table.phase != "playing":
            await interaction.response.send_message("No round in progress!", ephemeral=True)
            return
        uid = interaction.user.id
        player = self.table.players.get(uid)
        if not player:
            await interaction.response.send_message("You're not in this game!", ephemeral=True)
            return
        if player.choice is not None:
            await interaction.response.send_message("You already chose this round!", ephemeral=True)
            return

        player.choice = choice
        label = "Cooperate" if choice == "C" else "Defect"
        emoji = COOP_EMOJI if choice == "C" else DEFECT_EMOJI
        await interaction.response.send_message(
            f"{emoji} You chose **{label}**.", ephemeral=True,
        )

        # Check if all real players have chosen
        real_players = [p for p in self.table.players.values() if p.user_id != self.table.bot_id]
        if all(p.choice is not None for p in real_players):
            self.table.round_event.set()

    # ── Finished buttons (row 2) ─────────────────────────────────────────

    @ui.button(label="New Game", style=discord.ButtonStyle.success, emoji="\U0001f504", row=2)
    async def new_round_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "finished":
            await interaction.response.send_message("Game not over yet!", ephemeral=True)
            return
        for uid, p in self.table.players.items():
            if p.user_id != self.table.bot_id:
                self.table.last_bets[uid] = (p.display_name, p.bet)
        # Remove bot
        self.table.players.pop(self.table.bot_id, None)
        self.table.players.clear()
        self.table.phase = "betting"
        self.table.round_num = 0
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(label="Close", style=discord.ButtonStyle.danger, emoji="\u2716", row=2)
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase == "playing":
            await interaction.response.send_message("Can't close mid-game!", ephemeral=True)
            return
        if self.table.phase == "betting":
            for p in self.table.players.values():
                if p.user_id != self.table.bot_id:
                    try:
                        await queries.update_casino_balance(str(p.user_id), p.bet)
                    except Exception:
                        pass
        self.active_tables.pop(self.table.channel_id, None)
        embed = discord.Embed(
            title="\u2716 Prisoner's Dilemma \u2014 Table Closed",
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

        # Collect human player histories for bot's tit-for-tat
        human_uids = [uid for uid in table.players if uid != table.bot_id]

        for rnd in range(1, NUM_ROUNDS + 1):
            table.round_num = rnd
            table.round_event.clear()
            for p in table.players.values():
                p.choice = None

            # Bot chooses (tit-for-tat based on aggregate of human choices)
            bot = table.players.get(table.bot_id)
            if bot:
                # Use the most common human choice from last round
                if rnd == 1 or not human_uids:
                    bot.choice = "C"
                else:
                    last_choices = [
                        table.players[uid].history[-1]
                        for uid in human_uids
                        if table.players[uid].history
                    ]
                    if last_choices:
                        # Mirror majority
                        c_count = last_choices.count("C")
                        bot.choice = "C" if c_count >= len(last_choices) / 2 else "D"
                    else:
                        bot.choice = "C"

            # Show round
            self._update_buttons()
            embed = _round_embed(table)
            try:
                await msg.edit(embed=embed, view=self)
            except Exception:
                return

            # Wait for all choices or timeout
            try:
                await asyncio.wait_for(table.round_event.wait(), timeout=CHOICE_TIME)
            except asyncio.TimeoutError:
                pass

            # Assign random choice to anyone who didn't choose
            for p in table.players.values():
                if p.choice is None:
                    p.choice = random.choice(["C", "D"])

            # Score pairwise
            player_list = list(table.players.values())
            for i in range(len(player_list)):
                for j in range(i + 1, len(player_list)):
                    p1, p2 = player_list[i], player_list[j]
                    pts1, pts2 = PAYOFF[(p1.choice, p2.choice)]
                    p1.score += pts1
                    p2.score += pts2

            # Record history
            for p in table.players.values():
                p.history.append(p.choice)

            # Show results
            embed = _round_result_embed(table)
            try:
                await msg.edit(embed=embed, view=self)
            except Exception:
                return

            await asyncio.sleep(3)

        # ── Game over ────────────────────────────────────────────────────
        table.phase = "finished"

        # Determine winner(s) — only real players compete for money
        sorted_players = sorted(table.players.values(), key=lambda p: p.score, reverse=True)
        real_sorted = [p for p in sorted_players if p.user_id != table.bot_id]

        if real_sorted:
            real_top = real_sorted[0].score
            winner_uids = [p.user_id for p in real_sorted if p.score == real_top]
        else:
            winner_uids = []

        bets = {p.user_id: p.bet for p in table.players.values() if p.user_id != table.bot_id}
        payouts = compute_side_pot_payouts(bets, winner_uids)

        # Credit
        for uid, payout in payouts.items():
            if payout > 0:
                await queries.update_casino_balance(str(uid), payout)

        # Log
        for uid, p in table.players.items():
            if uid != table.bot_id:
                await queries.log_casino_result(
                    str(uid), "prisoner", p.bet, payouts.get(uid, 0),
                )

        # ELO (real players only)
        elo_changes: dict[int, tuple[float, float]] = {}
        if len(real_sorted) >= 2:
            finish_order = [p.user_id for p in real_sorted]
            try:
                elo_changes = await update_elo_multiplayer(finish_order, "prisoner", "prisoner")
            except Exception:
                pass

        embed = _final_embed(table, payouts)

        if elo_changes:
            elo_lines = []
            for p in real_sorted:
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
            pass

    # ── Timeout ──────────────────────────────────────────────────────────

    async def on_timeout(self) -> None:
        if self._round_task and not self._round_task.done():
            self._round_task.cancel()

        if self.table.phase == "betting":
            for p in self.table.players.values():
                if p.user_id != self.table.bot_id:
                    try:
                        await queries.update_casino_balance(str(p.user_id), p.bet)
                    except Exception:
                        pass

        self.active_tables.pop(self.table.channel_id, None)
        if self.table.message:
            try:
                embed = discord.Embed(
                    title="\u2716 Prisoner's Dilemma \u2014 Timed Out",
                    description="Table timed out. Bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await self.table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class PrisonerCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, PDTable] = {}

    @app_commands.command(
        name="prisoner",
        description="Iterated Prisoner's Dilemma tournament \u2014 cooperate or betray?",
    )
    async def prisoner(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            if getattr(existing, "phase", None) == "closed":
                del self.active_tables[channel_id]
            else:
                await interaction.response.send_message(
                    "There's already a Prisoner's Dilemma game here!", ephemeral=True,
                )
                return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = PDTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = PDView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PrisonerCog(bot))
