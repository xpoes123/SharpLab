"""Casino cog — /tennissim fake tennis match simulator.

Two random ATP/WTA players are drawn. Each gets a win probability.
Players pick a side and bet coins. A set-by-set simulation runs with
realistic tennis scoring (games, tiebreaks, best-of-3 sets), and
winners are paid fixed odds based on the pre-match probability.
"""

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 10
MIN_PLAYERS = 1
SET_DELAY = 2.5  # seconds between set updates
SETS_TO_WIN = 2  # best-of-3

# Top ATP + WTA players for random matchup draws
TENNIS_PLAYERS: list[tuple[str, str]] = [
    # (full_name, short_name)  — ATP
    ("Jannik Sinner", "Sinner"),
    ("Carlos Alcaraz", "Alcaraz"),
    ("Novak Djokovic", "Djokovic"),
    ("Alexander Zverev", "Zverev"),
    ("Daniil Medvedev", "Medvedev"),
    ("Andrey Rublev", "Rublev"),
    ("Casper Ruud", "Ruud"),
    ("Holger Rune", "Rune"),
    ("Stefanos Tsitsipas", "Tsitsipas"),
    ("Taylor Fritz", "Fritz"),
    ("Tommy Paul", "T. Paul"),
    ("Ben Shelton", "Shelton"),
    ("Hubert Hurkacz", "Hurkacz"),
    ("Felix Auger-Aliassime", "FAA"),
    ("Alex de Minaur", "de Minaur"),
    ("Frances Tiafoe", "Tiafoe"),
    # WTA
    ("Aryna Sabalenka", "Sabalenka"),
    ("Iga Swiatek", "Swiatek"),
    ("Coco Gauff", "Gauff"),
    ("Elena Rybakina", "Rybakina"),
    ("Jessica Pegula", "Pegula"),
    ("Ons Jabeur", "Jabeur"),
    ("Qinwen Zheng", "Zheng"),
    ("Jasmine Paolini", "Paolini"),
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _pick_matchup() -> tuple[tuple[str, str], tuple[str, str]]:
    """Pick two random tennis players. Returns ((name, short), (name, short))."""
    pair = random.sample(TENNIS_PLAYERS, 2)
    return (pair[0], pair[1])


def _generate_win_prob() -> float:
    """Generate a win probability for player 1 (0.25–0.75 range)."""
    return max(0.20, min(0.80, random.betavariate(3, 3)))


def _payout_multiplier(prob: float) -> float:
    return 1 / prob


def _prob_to_american(prob: float) -> str:
    if prob >= 0.5:
        odds = -round(prob / (1 - prob) * 100)
        return str(odds)
    else:
        odds = round((1 - prob) / prob * 100)
        return f"+{odds}"


def _simulate_game(server_prob: float) -> int:
    """Simulate one service game. Returns 1 if server holds, 0 if broken.

    Server holds ~70-80% of the time at the top level.
    server_prob is the server's match-level win probability.
    """
    # Translate match prob into service hold probability
    # A .500 match player holds serve ~65% of the time
    hold_prob = 0.50 + server_prob * 0.35  # range: ~57% to ~78%
    return 1 if random.random() < hold_prob else 0


def _simulate_tiebreak(p1_prob: float) -> tuple[int, int]:
    """Simulate a 7-point tiebreak. Returns (p1_pts, p2_pts)."""
    p1 = 0
    p2 = 0
    # p1 serves first point, then alternate every 2
    serving_p1 = True
    point_num = 0
    while True:
        point_num += 1
        if serving_p1:
            if random.random() < (0.45 + p1_prob * 0.20):
                p1 += 1
            else:
                p2 += 1
        else:
            p2_prob = 1 - p1_prob
            if random.random() < (0.45 + p2_prob * 0.20):
                p2 += 1
            else:
                p1 += 1

        # Check win: first to 7 with 2-point lead
        if p1 >= 7 and p1 - p2 >= 2:
            return p1, p2
        if p2 >= 7 and p2 - p1 >= 2:
            return p1, p2
        # Cap at 20 to avoid infinite loops
        if p1 + p2 >= 40:
            if p1 > p2:
                return p1, p2
            elif p2 > p1:
                return p1, p2
            else:
                # Force a winner
                if random.random() < p1_prob:
                    return p1 + 1, p2
                return p1, p2 + 1

        # Switch server: after first point, then every 2
        if point_num == 1 or (point_num > 1 and (point_num - 1) % 2 == 0):
            serving_p1 = not serving_p1


def _simulate_set(p1_prob: float) -> tuple[int, int]:
    """Simulate one set. Returns (p1_games, p2_games).

    Alternates serve. If 6-6 → tiebreak.
    """
    p1_games = 0
    p2_games = 0
    p1_serving = random.random() < 0.5  # random first server

    while True:
        if p1_serving:
            held = _simulate_game(p1_prob)
            if held:
                p1_games += 1
            else:
                p2_games += 1
        else:
            p2_prob = 1 - p1_prob
            held = _simulate_game(p2_prob)
            if held:
                p2_games += 1
            else:
                p1_games += 1

        p1_serving = not p1_serving

        # Check for set win: first to 6 with 2-game lead
        if p1_games >= 6 and p1_games - p2_games >= 2:
            return p1_games, p2_games
        if p2_games >= 6 and p2_games - p1_games >= 2:
            return p1_games, p2_games

        # Tiebreak at 6-6
        if p1_games == 6 and p2_games == 6:
            tb_p1, tb_p2 = _simulate_tiebreak(p1_prob)
            if tb_p1 > tb_p2:
                return 7, 6
            else:
                return 6, 7


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class TennisSimPlayer:
    user_id: int
    display_name: str
    bet: int
    side: str  # "p1" or "p2"
    payout: int = 0
    won: bool = False


@dataclass
class TennisSimTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | finished
    # Matchup — p1 listed first (like "home"), p2 second
    p1: tuple[str, str] = ("", "")  # (full_name, short_name)
    p2: tuple[str, str] = ("", "")
    p1_prob: float = 0.5
    # Players
    players: dict[int, TennisSimPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    last_bets: dict[int, tuple[str, int, str]] = field(default_factory=dict)
    # Sim state
    current_set: int = 0
    p1_sets: int = 0
    p2_sets: int = 0
    set_scores: list[tuple[int, int]] = field(default_factory=list)  # (p1_games, p2_games)
    sim_task: asyncio.Task | None = field(default=None, repr=False)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: TennisSimTable) -> discord.Embed:
    total_wagered = sum(p.bet for p in table.players.values())

    p1_name, p1_short = table.p1
    p2_name, p2_short = table.p2
    p2_prob = 1 - table.p1_prob

    p1_odds = _prob_to_american(table.p1_prob)
    p2_odds = _prob_to_american(p2_prob)
    p1_mult = _payout_multiplier(table.p1_prob)
    p2_mult = _payout_multiplier(p2_prob)

    embed = discord.Embed(
        title=f"\U0001f3be Tennis Sim \u2014 Place Your Bets (Round {table.round_num})",
        description=(
            "Pick a player and bet coins on the match winner!\n"
            f"Best of {SETS_TO_WIN * 2 - 1} sets."
        ),
        colour=discord.Colour.teal(),
    )

    matchup_text = (
        f"**{p1_short}** {p1_name}\n"
        f"\u2003Win: {table.p1_prob * 100:.0f}% ({p1_odds}) \u2014 **{p1_mult:.1f}x** payout\n\n"
        f"**{p2_short}** {p2_name}\n"
        f"\u2003Win: {p2_prob * 100:.0f}% ({p2_odds}) \u2014 **{p2_mult:.1f}x** payout"
    )
    embed.add_field(
        name=f"{p1_short} vs {p2_short}", value=matchup_text, inline=False,
    )

    if total_wagered:
        embed.add_field(name="Total Wagered", value=f"{total_wagered}c", inline=True)

    if table.players:
        player_lines = []
        for p in table.players.values():
            pick_short = p1_short if p.side == "p1" else p2_short
            player_lines.append(
                f"\U0001f3b0 **{p.display_name}** \u2014 {p.bet}c on **{pick_short}**"
            )
        embed.add_field(name="Players", value="\n".join(player_lines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet \u2014 click Join!*",
            inline=False,
        )

    embed.set_footer(
        text=(
            f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} player(s) "
            f"\u2502 Enter \"{p1_short.lower()}\" or \"{p2_short.lower()}\" in modal"
        ),
    )
    return embed


def _scoreboard_text(table: TennisSimTable) -> str:
    """Render an ASCII tennis scoreboard."""
    _, p1_short = table.p1
    _, p2_short = table.p2

    max_name = max(len(p1_short), len(p2_short))

    # Header
    header = " " * (max_name + 1)
    for s in range(1, len(table.set_scores) + 1):
        header += f"  S{s}"
    remaining = max(0, SETS_TO_WIN * 2 - 1 - len(table.set_scores))
    for s in range(len(table.set_scores) + 1, len(table.set_scores) + remaining + 1):
        header += f"  S{s}"

    # Player 1 line
    p1_line = f"{p1_short:>{max_name}s}"
    for p1g, _p2g in table.set_scores:
        p1_line += f"  {p1g:>2d}"
    p1_line += "   -" * remaining
    p1_line += f"   [{table.p1_sets}]"

    # Player 2 line
    p2_line = f"{p2_short:>{max_name}s}"
    for _p1g, p2g in table.set_scores:
        p2_line += f"  {p2g:>2d}"
    p2_line += "   -" * remaining
    p2_line += f"   [{table.p2_sets}]"

    return f"```\n{header}\n{p1_line}\n{p2_line}\n```"


def _playing_embed(table: TennisSimTable) -> discord.Embed:
    _, p1_short = table.p1
    _, p2_short = table.p2

    embed = discord.Embed(
        title=(
            f"\U0001f3be Tennis Sim \u2014 {p1_short} vs {p2_short} "
            f"(Set {table.current_set})"
        ),
        colour=discord.Colour.gold(),
    )
    embed.description = _scoreboard_text(table)

    bet_lines: list[str] = []
    for p in table.players.values():
        pick_short = p1_short if p.side == "p1" else p2_short
        bet_lines.append(f"**{p.display_name}** \u2014 {p.bet}c on {pick_short}")
    if bet_lines:
        embed.add_field(name="Bets", value="\n".join(bet_lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _finished_embed(
    table: TennisSimTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    _, p1_short = table.p1
    _, p2_short = table.p2

    winner_short = p1_short if table.p1_sets > table.p2_sets else p2_short
    sets_text = f"{table.p1_sets}-{table.p2_sets}"

    embed = discord.Embed(
        title=f"\U0001f3be Tennis Sim \u2014 Final (Round {table.round_num})",
        description=(
            f"\U0001f3c6 **{winner_short}** wins {sets_text}! "
        ),
        colour=discord.Colour.green(),
    )

    embed.add_field(
        name="Match Score",
        value=_scoreboard_text(table),
        inline=False,
    )

    lines: list[str] = []
    for p in table.players.values():
        bal = balances.get(p.user_id, 0) if balances else 0
        pick_short = p1_short if p.side == "p1" else p2_short
        net = p.payout - p.bet
        sign = "+" if net >= 0 else ""
        if p.won:
            lines.append(
                f"\U0001f3c6 **{p.display_name}** ({pick_short}) \u2014 "
                f"{p.bet}c \u2192 {p.payout}c (**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        else:
            lines.append(
                f"\u274c **{p.display_name}** ({pick_short}) \u2014 "
                f"{p.bet}c \u2192 0c (**-{p.bet}c**) \u2014 bal: {bal}c"
            )
    if lines:
        embed.add_field(name="Results", value="\n".join(lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modal ────────────────────────────────────────────────────────────────────


class JoinTennisSimModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )
    side_input = ui.TextInput(
        label="Pick a player",
        placeholder="player name",
        required=True,
        max_length=20,
    )

    def __init__(
        self, table: TennisSimTable, view: "TennisSimTableView", balance: int,
    ) -> None:
        _, p1_short = table.p1
        _, p2_short = table.p2
        super().__init__(title=f"Tennis Sim \u2014 {p1_short} vs {p2_short}")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 100 (bal: {balance}c)"
        self.side_input.placeholder = f"{p1_short} / {p2_short}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amt = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message(
                "Enter a whole number for bet.", ephemeral=True,
            )
            return
        if amt < 1:
            await interaction.response.send_message(
                "Must be at least 1 coin.", ephemeral=True,
            )
            return

        raw = self.side_input.value.strip().lower()
        p1_full, p1_short = self.table.p1
        p2_full, p2_short = self.table.p2
        if raw in (p1_short.lower(), p1_full.lower(), "p1", "1"):
            side = "p1"
        elif raw in (p2_short.lower(), p2_full.lower(), "p2", "2"):
            side = "p2"
        else:
            await interaction.response.send_message(
                f"Enter **{p1_short}** or **{p2_short}**.",
                ephemeral=True,
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

        self.table.players[uid] = TennisSimPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
            side=side,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


# ── View ─────────────────────────────────────────────────────────────────────


class TennisSimTableView(ui.View):
    def __init__(
        self, table: TennisSimTable, active_tables: dict[int, TennisSimTable],
    ) -> None:
        super().__init__(timeout=300)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        playing = phase == "playing"
        finished = phase == "finished"

        self.start_btn.disabled = (
            not betting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = playing

        self.new_round_btn.disabled = not finished
        self.close_btn.disabled = playing

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
                f"Need at least {MIN_PLAYERS} player(s)!", ephemeral=True,
            )
            return
        await self._start_sim(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f3be", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Match in progress! Wait for the next round.", ephemeral=True,
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
            JoinTennisSimModal(self.table, self, bal),
        )

    @ui.button(
        label="Re-bet", style=discord.ButtonStyle.primary, emoji="\U0001f504", row=0,
    )
    async def rebet_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Match in progress!", ephemeral=True,
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
        name, amt, side = last
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for {amt}c re-bet! (have {bal}c)",
                ephemeral=True,
            )
            return
        self.table.players[uid] = TennisSimPlayer(
            user_id=uid, display_name=name, bet=amt, side=side,
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
                "Can't leave mid-match!", ephemeral=True,
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
                "Match still in progress!", ephemeral=True,
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
        if self.table.phase == "playing":
            await interaction.response.send_message(
                "Can't close mid-match!", ephemeral=True,
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

    async def _start_sim(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "playing"
        table.current_set = 1
        table.p1_sets = 0
        table.p2_sets = 0
        table.set_scores = []

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(table), view=self,
        )
        table.sim_task = asyncio.create_task(self._sim_loop())

    async def _sim_loop(self) -> None:
        table = self.table
        try:
            while table.p1_sets < SETS_TO_WIN and table.p2_sets < SETS_TO_WIN:
                await asyncio.sleep(SET_DELAY)
                p1_games, p2_games = _simulate_set(table.p1_prob)
                table.set_scores.append((p1_games, p2_games))

                if p1_games > p2_games:
                    table.p1_sets += 1
                else:
                    table.p2_sets += 1

                table.current_set = len(table.set_scores)

                if table.message:
                    try:
                        await table.message.edit(
                            embed=_playing_embed(table), view=self,
                        )
                    except discord.HTTPException:
                        pass

            await asyncio.sleep(1.0)
            await self._resolve()

        except asyncio.CancelledError:
            pass
        except Exception:
            if table.phase == "playing":
                table.phase = "finished"
                await self._refund_all()

    async def _resolve(self) -> None:
        table = self.table
        table.phase = "finished"

        p1_won = table.p1_sets > table.p2_sets

        for p in table.players.values():
            if (p.side == "p1" and p1_won) or (p.side == "p2" and not p1_won):
                p.won = True
                prob = table.p1_prob if p.side == "p1" else (1 - table.p1_prob)
                p.payout = int(p.bet * _payout_multiplier(prob))

        balances: dict[int, int] = {}
        for uid, player in table.players.items():
            if player.won and player.payout > 0:
                balances[uid] = await queries.update_casino_balance(
                    str(uid), player.payout,
                )
            else:
                bal = await queries.get_casino_balance(str(uid))
                balances[uid] = bal or 0
            await queries.log_casino_result(
                str(uid), "tennissim", player.bet, player.payout,
            )

        for uid, player in table.players.items():
            table.last_bets[uid] = (
                player.display_name, player.bet, player.side,
            )

        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(
                    embed=_finished_embed(table, balances=balances), view=self,
                )
            except discord.HTTPException:
                pass

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _start_new_round(self) -> None:
        table = self.table
        table.players.clear()
        table.phase = "betting"
        table.round_num += 1
        p1, p2 = _pick_matchup()
        table.p1 = p1
        table.p2 = p2
        table.p1_prob = _generate_win_prob()
        table.current_set = 0
        table.p1_sets = 0
        table.p2_sets = 0
        table.set_scores.clear()
        table.sim_task = None

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
            title="\U0001f3be Tennis Sim Table \u2014 Closed",
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

        if table.sim_task and not table.sim_task.done():
            table.sim_task.cancel()

        if table.phase == "finished":
            self.active_tables.pop(table.channel_id, None)
            if table.message:
                try:
                    embed = discord.Embed(
                        title="\U0001f3be Tennis Sim Table \u2014 Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    pass
            return

        await self._refund_all()
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="\U0001f3be Tennis Sim Table \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class TennisSimCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, TennisSimTable] = {}

    @app_commands.command(
        name="tennissim", description="Bet on a simulated tennis match (casino)",
    )
    async def tennissim(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a Tennis Sim table in this channel!",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        p1, p2 = _pick_matchup()
        table = TennisSimTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
            p1=p1,
            p2=p2,
            p1_prob=_generate_win_prob(),
        )
        self.active_tables[channel_id] = table

        view = TennisSimTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TennisSimCog(bot))
