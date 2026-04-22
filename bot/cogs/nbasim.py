"""Casino cog — /nbasim fake NBA game simulator.

Two random NBA teams are drawn. Each gets a win probability (Dirichlet-style).
Players pick a side and bet coins. A quarter-by-quarter simulation runs, and
winners are paid fixed odds based on the pre-game probability.
"""

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
from shared.models import TEAM_ABBR_NBA

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 10
MIN_PLAYERS = 1
QUARTER_DELAY = 2.0  # seconds between quarter updates
NUM_QUARTERS = 4
OT_DELAY = 1.5

# Full team names for display
NBA_TEAMS: list[tuple[str, str]] = [
    (full, abbr) for full, abbr in sorted(TEAM_ABBR_NBA.items())
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _pick_matchup() -> tuple[tuple[str, str], tuple[str, str]]:
    """Pick two random NBA teams. Returns ((name, abbr), (name, abbr))."""
    pair = random.sample(NBA_TEAMS, 2)
    return (pair[0], pair[1])


def _generate_win_prob() -> float:
    """Generate a win probability for the home team (0.25–0.75 range)."""
    # Beta distribution centered at 0.5 with decent spread
    return max(0.20, min(0.80, random.betavariate(3, 3)))


def _payout_multiplier(prob: float) -> float:
    """Return payout multiplier for a bet on a side with given win probability."""
    return 1 / prob


def _prob_to_american(prob: float) -> str:
    """Convert a probability to American odds string."""
    if prob >= 0.5:
        odds = -round(prob / (1 - prob) * 100)
        return str(odds)
    else:
        odds = round((1 - prob) / prob * 100)
        return f"+{odds}"


def _simulate_quarter(
    home_prob: float, home_score: int, away_score: int,
) -> tuple[int, int]:
    """Simulate one quarter. Returns (home_pts, away_pts) for the quarter.

    Scoring is weighted by win probability — the better team tends to
    outscore the worse team, but with enough variance for upsets.
    """
    # Base scoring: each team scores ~25 pts per quarter on average
    base = 25.0
    # Home team gets a scoring bump proportional to their edge
    home_edge = (home_prob - 0.5) * 10  # e.g. 60% → +1.0 pts avg
    home_pts = max(15, int(random.gauss(base + home_edge, 5)))
    away_pts = max(15, int(random.gauss(base - home_edge, 5)))
    return home_pts, away_pts


def _simulate_ot(home_prob: float) -> tuple[int, int]:
    """Simulate an OT period. Shorter, lower scoring."""
    base = 8.0
    home_edge = (home_prob - 0.5) * 3
    home_pts = max(2, int(random.gauss(base + home_edge, 3)))
    away_pts = max(2, int(random.gauss(base - home_edge, 3)))
    # Guarantee no tie — give trailing team +1 or leader +1
    if home_pts == away_pts:
        if random.random() < home_prob:
            home_pts += 1
        else:
            away_pts += 1
    return home_pts, away_pts


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class NbaSimPlayer:
    user_id: int
    display_name: str
    bet: int
    side: str  # "home" or "away"
    payout: int = 0
    won: bool = False


@dataclass
class NbaSimTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | finished
    # Matchup
    home_team: tuple[str, str] = ("", "")  # (full_name, abbr)
    away_team: tuple[str, str] = ("", "")
    home_prob: float = 0.5
    # Players
    players: dict[int, NbaSimPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    last_bets: dict[int, tuple[str, int, str]] = field(default_factory=dict)
    # Sim state
    quarter: int = 0
    home_score: int = 0
    away_score: int = 0
    quarter_scores: list[tuple[int, int]] = field(default_factory=list)
    ot_count: int = 0
    sim_task: asyncio.Task | None = field(default=None, repr=False)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: NbaSimTable) -> discord.Embed:
    total_wagered = sum(p.bet for p in table.players.values())

    home_name, home_abbr = table.home_team
    away_name, away_abbr = table.away_team
    away_prob = 1 - table.home_prob

    home_odds = _prob_to_american(table.home_prob)
    away_odds = _prob_to_american(away_prob)
    home_mult = _payout_multiplier(table.home_prob)
    away_mult = _payout_multiplier(away_prob)

    embed = discord.Embed(
        title=f"\U0001f3c0 NBA Sim \u2014 Place Your Bets (Round {table.round_num})",
        description=(
            "Pick a side and bet coins on the outcome!\n"
            "Odds are based on each team's simulated win probability."
        ),
        colour=discord.Colour.orange(),
    )

    matchup_text = (
        f"**{away_abbr}** {away_name}\n"
        f"\u2003Win: {away_prob * 100:.0f}% ({away_odds}) \u2014 **{away_mult:.1f}x** payout\n\n"
        f"**{home_abbr}** {home_name}\n"
        f"\u2003Win: {table.home_prob * 100:.0f}% ({home_odds}) \u2014 **{home_mult:.1f}x** payout"
    )
    embed.add_field(name=f"{away_abbr} @ {home_abbr}", value=matchup_text, inline=False)

    if total_wagered:
        embed.add_field(name="Total Wagered", value=f"{total_wagered}c", inline=True)

    if table.players:
        player_lines = []
        for p in table.players.values():
            side_abbr = home_abbr if p.side == "home" else away_abbr
            player_lines.append(f"\U0001f3b0 **{p.display_name}** \u2014 {p.bet}c on **{side_abbr}**")
        embed.add_field(name="Players", value="\n".join(player_lines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet \u2014 click Join!*",
            inline=False,
        )

    embed.set_footer(
        text=f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} player(s) \u2502 Pick home or away in modal",
    )
    return embed


def _scoreboard_text(table: NbaSimTable) -> str:
    """Render an ASCII scoreboard."""
    _, home_abbr = table.home_team
    _, away_abbr = table.away_team

    # Header
    header = f"{'':>5s}"
    for q in range(1, len(table.quarter_scores) + 1):
        if q <= NUM_QUARTERS:
            header += f"  Q{q}"
        else:
            header += f" OT{q - NUM_QUARTERS}"
    header += "   T"

    # Away line
    away_line = f"{away_abbr:>5s}"
    away_total = 0
    for aq, hq in table.quarter_scores:
        away_line += f"  {aq:>2d}"
        away_total += aq
    # Pad remaining quarters
    remaining = max(0, NUM_QUARTERS - len(table.quarter_scores))
    away_line += "   -" * remaining
    away_line += f"  {table.away_score:>3d}"

    # Home line
    home_line = f"{home_abbr:>5s}"
    home_total = 0
    for aq, hq in table.quarter_scores:
        home_line += f"  {hq:>2d}"
        home_total += hq
    home_line += "   -" * remaining
    home_line += f"  {table.home_score:>3d}"

    return f"```\n{header}\n{away_line}\n{home_line}\n```"


def _playing_embed(table: NbaSimTable) -> discord.Embed:
    _, home_abbr = table.home_team
    _, away_abbr = table.away_team

    if table.quarter <= NUM_QUARTERS:
        period_label = f"Q{table.quarter}"
    else:
        period_label = f"OT{table.quarter - NUM_QUARTERS}"

    embed = discord.Embed(
        title=f"\U0001f3c0 NBA Sim \u2014 {away_abbr} @ {home_abbr} ({period_label})",
        colour=discord.Colour.gold(),
    )
    embed.description = _scoreboard_text(table)

    # Show bets
    bet_lines: list[str] = []
    for p in table.players.values():
        side_abbr = home_abbr if p.side == "home" else away_abbr
        bet_lines.append(f"**{p.display_name}** \u2014 {p.bet}c on {side_abbr}")
    if bet_lines:
        embed.add_field(name="Bets", value="\n".join(bet_lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _finished_embed(
    table: NbaSimTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    _, home_abbr = table.home_team
    _, away_abbr = table.away_team

    if table.home_score > table.away_score:
        winner_abbr = home_abbr
    else:
        winner_abbr = away_abbr

    ot_text = ""
    if table.ot_count > 0:
        ot_text = f" ({table.ot_count}OT)"

    embed = discord.Embed(
        title=f"\U0001f3c0 NBA Sim \u2014 Final{ot_text} (Round {table.round_num})",
        description=(
            f"\U0001f3c6 **{winner_abbr}** wins! "
            f"{away_abbr} {table.away_score} \u2014 {table.home_score} {home_abbr}"
        ),
        colour=discord.Colour.green(),
    )

    embed.add_field(
        name="Box Score",
        value=_scoreboard_text(table),
        inline=False,
    )

    # Results per player
    lines: list[str] = []
    for p in table.players.values():
        bal = balances.get(p.user_id, 0) if balances else 0
        side_abbr = home_abbr if p.side == "home" else away_abbr
        net = p.payout - p.bet
        sign = "+" if net >= 0 else ""
        if p.won:
            lines.append(
                f"\U0001f3c6 **{p.display_name}** ({side_abbr}) \u2014 "
                f"{p.bet}c \u2192 {p.payout}c (**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        else:
            lines.append(
                f"\u274c **{p.display_name}** ({side_abbr}) \u2014 "
                f"{p.bet}c \u2192 0c (**-{p.bet}c**) \u2014 bal: {bal}c"
            )
    if lines:
        embed.add_field(name="Results", value="\n".join(lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modal ────────────────────────────────────────────────────────────────────


class JoinNbaSimModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )
    side_input = ui.TextInput(
        label="Side (home or away)",
        placeholder="home / away",
        required=True,
        max_length=4,
    )

    def __init__(
        self, table: NbaSimTable, view: "NbaSimTableView", balance: int,
    ) -> None:
        _, home_abbr = table.home_team
        _, away_abbr = table.away_team
        super().__init__(title=f"NBA Sim \u2014 {away_abbr} @ {home_abbr}")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 100 (bal: {balance}c)"
        self.side_input.placeholder = f"home ({home_abbr}) / away ({away_abbr})"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Validate bet
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
        # Validate side
        raw = self.side_input.value.strip().lower()
        _, home_abbr = self.table.home_team
        _, away_abbr = self.table.away_team
        if raw in ("home", "h", home_abbr.lower()):
            side = "home"
        elif raw in ("away", "a", away_abbr.lower()):
            side = "away"
        else:
            await interaction.response.send_message(
                f"Enter **home** ({home_abbr}) or **away** ({away_abbr}).",
                ephemeral=True,
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

        self.table.players[uid] = NbaSimPlayer(
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


class NbaSimTableView(ui.View):
    def __init__(
        self, table: NbaSimTable, active_tables: dict[int, NbaSimTable],
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
        label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f3c0", row=0,
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
            JoinNbaSimModal(self.table, self, bal),
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
        self.table.players[uid] = NbaSimPlayer(
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

    async def _start_sim(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "playing"
        table.quarter = 1
        table.home_score = 0
        table.away_score = 0
        table.quarter_scores = []
        table.ot_count = 0

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(table), view=self,
        )
        table.sim_task = asyncio.create_task(self._sim_loop())

    async def _sim_loop(self) -> None:
        table = self.table
        try:
            # Regulation quarters
            for q in range(1, NUM_QUARTERS + 1):
                await asyncio.sleep(QUARTER_DELAY)
                table.quarter = q
                h_pts, a_pts = _simulate_quarter(
                    table.home_prob, table.home_score, table.away_score,
                )
                table.home_score += h_pts
                table.away_score += a_pts
                table.quarter_scores.append((a_pts, h_pts))

                if table.message:
                    try:
                        await table.message.edit(
                            embed=_playing_embed(table), view=self,
                        )
                    except discord.HTTPException:
                        pass

            # Overtime if tied
            while table.home_score == table.away_score:
                table.ot_count += 1
                table.quarter += 1
                await asyncio.sleep(OT_DELAY)
                h_pts, a_pts = _simulate_ot(table.home_prob)
                table.home_score += h_pts
                table.away_score += a_pts
                table.quarter_scores.append((a_pts, h_pts))

                if table.message:
                    try:
                        await table.message.edit(
                            embed=_playing_embed(table), view=self,
                        )
                    except discord.HTTPException:
                        pass

            await asyncio.sleep(1.0)  # brief pause before results
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

        home_won = table.home_score > table.away_score

        for p in table.players.values():
            if (p.side == "home" and home_won) or (p.side == "away" and not home_won):
                p.won = True
                prob = table.home_prob if p.side == "home" else (1 - table.home_prob)
                p.payout = int(p.bet * _payout_multiplier(prob))

        # Credit winners and log
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
                str(uid), "nbasim", player.bet, player.payout,
            )

        # Save last bets
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
        # New matchup + new probabilities each round
        home, away = _pick_matchup()
        table.home_team = home
        table.away_team = away
        table.home_prob = _generate_win_prob()
        table.quarter = 0
        table.home_score = 0
        table.away_score = 0
        table.quarter_scores.clear()
        table.ot_count = 0
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
            title="\U0001f3c0 NBA Sim Table \u2014 Closed",
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
                        title="\U0001f3c0 NBA Sim Table \u2014 Timed Out",
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
                    title="\U0001f3c0 NBA Sim Table \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class NbaSimCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, NbaSimTable] = {}

    @app_commands.command(
        name="nbasim", description="Bet on a simulated NBA game (casino)",
    )
    async def nbasim(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already an NBA Sim table in this channel!",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        home, away = _pick_matchup()
        table = NbaSimTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
            home_team=home,
            away_team=away,
            home_prob=_generate_win_prob(),
        )
        self.active_tables[channel_id] = table

        view = NbaSimTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NbaSimCog(bot))
