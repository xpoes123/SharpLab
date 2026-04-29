"""Casino cog — /nflsim fake NFL game simulator.

Two random NFL teams are drawn. Each gets offense, defense, and coaching ratings.
Players pick a market (ML/Spread/O-U) and side, and bet coins. A quarter-by-quarter
simulation runs with football-style scoring, and winners are paid based on market.
"""

import asyncio
import math
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries
import logging

log = logging.getLogger(__name__)
# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 10
MIN_PLAYERS = 1
QUARTER_DELAY = 2.0  # seconds between quarter updates
NUM_QUARTERS = 4
OT_DELAY = 1.5

# All 32 NFL teams: (full_name, abbreviation)
NFL_TEAMS: list[tuple[str, str]] = [
    ("Arizona Cardinals", "ARI"),
    ("Atlanta Falcons", "ATL"),
    ("Baltimore Ravens", "BAL"),
    ("Buffalo Bills", "BUF"),
    ("Carolina Panthers", "CAR"),
    ("Chicago Bears", "CHI"),
    ("Cincinnati Bengals", "CIN"),
    ("Cleveland Browns", "CLE"),
    ("Dallas Cowboys", "DAL"),
    ("Denver Broncos", "DEN"),
    ("Detroit Lions", "DET"),
    ("Green Bay Packers", "GB"),
    ("Houston Texans", "HOU"),
    ("Indianapolis Colts", "IND"),
    ("Jacksonville Jaguars", "JAX"),
    ("Kansas City Chiefs", "KC"),
    ("Las Vegas Raiders", "LV"),
    ("Los Angeles Chargers", "LAC"),
    ("Los Angeles Rams", "LAR"),
    ("Miami Dolphins", "MIA"),
    ("Minnesota Vikings", "MIN"),
    ("New England Patriots", "NE"),
    ("New Orleans Saints", "NO"),
    ("New York Giants", "NYG"),
    ("New York Jets", "NYJ"),
    ("Philadelphia Eagles", "PHI"),
    ("Pittsburgh Steelers", "PIT"),
    ("San Francisco 49ers", "SF"),
    ("Seattle Seahawks", "SEA"),
    ("Tampa Bay Buccaneers", "TB"),
    ("Tennessee Titans", "TEN"),
    ("Washington Commanders", "WAS"),
]

# Scoring plays and their point values
SCORING_PLAYS = [
    (7, "TD + XP"),
    (3, "FG"),
    (6, "TD (missed XP)"),
    (8, "TD + 2pt"),
    (2, "Safety"),
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _pick_matchup() -> tuple[tuple[str, str], tuple[str, str]]:
    pair = random.sample(NFL_TEAMS, 2)
    return (pair[0], pair[1])


# ── Team ratings — 2024 NFL season ───────────────────────────────────────────
# All values normalized to [45, 95] where 70 ≈ league average.
# Offense: based on points/drive and DVOA; Defense: based on points allowed and
# DVOA (inverted); Coaching: win% vs. Pythagorean expectations + system quality.
# Sources: Football-Reference, ESPN, Pro-Football-Reference (2024 season).
NFL_TEAM_RATINGS: dict[str, tuple[float, float, float]] = {
    # (offense, defense, coaching)
    "BAL": (88.0, 84.0, 83.0),  # Lamar Jackson MVP, elite on both sides
    "DET": (88.0, 72.0, 83.0),  # Best offense in NFC, Dan Campbell system
    "PHI": (87.0, 82.0, 82.0),  # Saquon + Hurts, Super Bowl run
    "KC":  (85.0, 82.0, 92.0),  # Dynasty — Andy Reid GOAT coaching bump
    "BUF": (84.0, 76.0, 79.0),  # Josh Allen MVP race, strong team
    "CIN": (80.0, 69.0, 73.0),  # Burrow healthy, weapons back
    "MIN": (78.0, 79.0, 79.0),  # JJ McCarthy era start, solid defense
    "WAS": (79.0, 73.0, 73.0),  # Jayden Daniels phenom rookie season
    "LAR": (79.0, 76.0, 77.0),  # McVay system, Stafford solid
    "SF":  (79.0, 78.0, 81.0),  # Shanahan offense, Brock Purdy, injury year
    "LAC": (77.0, 76.0, 76.0),  # Harbaugh Year 1 turnaround
    "GB":  (77.0, 76.0, 75.0),  # Jordan Love developing, solid unit
    "MIA": (75.0, 68.0, 70.0),  # Tua-led aerial attack, suspect defense
    "DEN": (74.0, 75.0, 72.0),  # Bo Nix surprise improvement
    "ATL": (73.0, 70.0, 69.0),  # Kirk Cousins revival
    "HOU": (76.0, 74.0, 74.0),  # CJ Stroud Year 2, talented young team
    "TB":  (76.0, 73.0, 72.0),  # Baker Mayfield solid, playoff push
    "PIT": (71.0, 79.0, 74.0),  # Russell Wilson, elite D still
    "DAL": (72.0, 74.0, 67.0),  # Dak struggles, McCarthy seat warm
    "SEA": (70.0, 68.0, 68.0),  # Geno Smith steady, nothing special
    "NYJ": (68.0, 74.0, 64.0),  # Rodgers return disappointing
    "IND": (66.0, 68.0, 65.0),  # Anthony Richardson project
    "ARI": (67.0, 65.0, 63.0),  # Kyler Murray inconsistent
    "NO":  (65.0, 70.0, 65.0),  # Derek Carr era fading, aging roster
    "JAX": (65.0, 65.0, 60.0),  # Trevor Lawrence, Doug Pederson hot seat
    "CLE": (57.0, 70.0, 62.0),  # Watson injuries, disaster season
    "CHI": (64.0, 67.0, 67.0),  # Caleb Williams rookie, patience needed
    "NE":  (56.0, 66.0, 60.0),  # Post-Belichick rebuild, Jerod Mayo era
    "LV":  (60.0, 64.0, 57.0),  # Antonio Pierce, rudderless franchise
    "TEN": (58.0, 65.0, 59.0),  # Will Levis, full rebuild
    "NYG": (53.0, 64.0, 56.0),  # Daniel Jones benched, franchise low point
    "CAR": (51.0, 63.0, 53.0),  # Bryce Young benched, worst team in league
}


def _generate_ratings(abbr: str = "") -> tuple[float, float, float]:
    """Return (offense, defense, coaching) for the given team abbreviation.

    Looks up calibrated 2024 real-season ratings from NFL_TEAM_RATINGS.
    Falls back to a random entry from the table if abbr is unknown.
    """
    if abbr in NFL_TEAM_RATINGS:
        return NFL_TEAM_RATINGS[abbr]
    return random.choice(list(NFL_TEAM_RATINGS.values()))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _compute_home_prob(
    home_off: float, home_def: float, home_coa: float,
    away_off: float, away_def: float, away_coa: float,
) -> float:
    home_net = (home_off * 0.5 + home_coa * 0.3) - (away_def * 0.5 + away_coa * 0.2)
    away_net = (away_off * 0.5 + away_coa * 0.3) - (home_def * 0.5 + home_coa * 0.2)
    raw = _sigmoid((home_net - away_net) / 15)
    return max(0.20, min(0.80, raw))


def _compute_spread(home_prob: float) -> float:
    raw = (home_prob - 0.5) * 56
    rounded = round(raw)
    if rounded >= 0:
        return rounded + 0.5
    else:
        return rounded - 0.5


def _compute_total(
    home_off: float, home_def: float, away_off: float, away_def: float,
) -> float:
    raw = 46 + (home_off + away_off - 130) / 10 - (home_def + away_def - 130) / 15
    return round(raw * 2) / 2


def _payout_multiplier(prob: float) -> float:
    return 1 / prob


def _juice_payout(bet: int) -> int:
    return bet + int(bet * 100 / 110)


def _prob_to_american(prob: float) -> str:
    if prob >= 0.5:
        odds = -round(prob / (1 - prob) * 100)
        return str(odds)
    else:
        odds = round((1 - prob) / prob * 100)
        return f"+{odds}"


def _simulate_quarter(
    home_off: float, home_def: float, home_coa: float,
    away_off: float, away_def: float, away_coa: float,
) -> tuple[int, int]:
    home_pts = 0
    away_pts = 0

    home_score_chance = 0.35 + (home_off - 65) / 200 - (away_def - 65) / 250
    home_score_chance = max(0.15, min(0.55, home_score_chance))

    away_score_chance = 0.35 + (away_off - 65) / 200 - (home_def - 65) / 250
    away_score_chance = max(0.15, min(0.55, away_score_chance))

    home_drives = random.randint(2, 4) + (1 if home_coa > 75 else 0)
    away_drives = random.randint(2, 4) + (1 if away_coa > 75 else 0)

    for _ in range(home_drives):
        if random.random() < home_score_chance:
            pts, _label = random.choices(
                SCORING_PLAYS,
                weights=[60, 25, 5, 8, 2],
            )[0]
            home_pts += pts

    for _ in range(away_drives):
        if random.random() < away_score_chance:
            pts, _label = random.choices(
                SCORING_PLAYS,
                weights=[60, 25, 5, 8, 2],
            )[0]
            away_pts += pts

    return home_pts, away_pts


def _simulate_ot(home_prob: float) -> tuple[int, int]:
    home_pts = 0
    away_pts = 0

    if random.random() < (1 - home_prob) * 0.45:
        pts, _ = random.choices(
            SCORING_PLAYS[:2],
            weights=[55, 45],
        )[0]
        away_pts += pts
        if pts == 7:
            return home_pts, away_pts

    if random.random() < home_prob * 0.45:
        pts, _ = random.choices(
            SCORING_PLAYS[:2],
            weights=[55, 45],
        )[0]
        home_pts += pts

    if home_pts == away_pts:
        if random.random() < home_prob:
            home_pts += 3
        else:
            away_pts += 3
    return home_pts, away_pts


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class NflSimPlayer:
    user_id: int
    display_name: str
    bet: int
    side: str  # "home" or "away" for ml/spread; "over" or "under" for ou
    market: str = "ml"  # "ml" | "spread" | "ou"
    payout: int = 0
    won: bool = False


@dataclass
class NflSimTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | finished
    # Matchup
    home_team: tuple[str, str] = ("", "")  # (full_name, abbr)
    away_team: tuple[str, str] = ("", "")
    home_prob: float = 0.5
    # Ratings
    home_offense: float = 65.0
    home_defense: float = 65.0
    home_coaching: float = 65.0
    away_offense: float = 65.0
    away_defense: float = 65.0
    away_coaching: float = 65.0
    # Lines
    spread: float = 0.0
    total: float = 46.0
    # Players
    players: dict[int, NflSimPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    last_bets: dict[int, tuple[str, int, str, str]] = field(default_factory=dict)
    # Sim state
    quarter: int = 0
    home_score: int = 0
    away_score: int = 0
    quarter_scores: list[tuple[int, int]] = field(default_factory=list)
    ot_count: int = 0
    sim_task: asyncio.Task | None = field(default=None, repr=False)
    thread: discord.Thread | None = field(default=None, repr=False)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _betting_embed(table: NflSimTable) -> discord.Embed:
    total_wagered = sum(p.bet for p in table.players.values())

    home_name, home_abbr = table.home_team
    away_name, away_abbr = table.away_team

    embed = discord.Embed(
        title=f"\U0001f3c8 NFL Sim \u2014 Place Your Bets (Round {table.round_num})",
        description=(
            "Pick a market and side and bet coins on the outcome!\n"
            "Odds reveal when game starts!"
        ),
        colour=discord.Colour.dark_green(),
    )

    home_ratings = (
        f"OFF {table.home_offense:.0f} | DEF {table.home_defense:.0f} | COA {table.home_coaching:.0f}"
    )
    away_ratings = (
        f"OFF {table.away_offense:.0f} | DEF {table.away_defense:.0f} | COA {table.away_coaching:.0f}"
    )

    matchup_text = (
        f"**{away_abbr}** {away_name}\n"
        f"\u2003{away_ratings}\n"
        f"\u2003Odds: ???\n\n"
        f"**{home_abbr}** {home_name}\n"
        f"\u2003{home_ratings}\n"
        f"\u2003Odds: ???"
    )
    embed.add_field(name=f"{away_abbr} @ {home_abbr}", value=matchup_text, inline=False)

    embed.add_field(
        name="Markets",
        value="ML / Spread / Over-Under | All odds hidden until start",
        inline=False,
    )

    if total_wagered:
        embed.add_field(name="Total Wagered", value=f"{total_wagered}c", inline=True)

    if table.players:
        player_lines = []
        for p in table.players.values():
            if p.market == "ml":
                side_label = f"ML {home_abbr if p.side == 'home' else away_abbr}"
            elif p.market == "spread":
                side_label = f"Spread {home_abbr if p.side == 'home' else away_abbr}"
            else:
                side_label = f"{'Over' if p.side == 'over' else 'Under'}"
            player_lines.append(
                f"\U0001f3b0 **{p.display_name}** \u2014 {p.bet}c on {side_label}"
            )
        embed.add_field(name="Players", value="\n".join(player_lines), inline=False)
    else:
        embed.add_field(
            name="Players",
            value="*No players yet \u2014 click Join!*",
            inline=False,
        )

    embed.set_footer(
        text=f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} player(s) \u2502 Markets: ml / spread / ou",
    )
    return embed


def _scoreboard_text(table: NflSimTable) -> str:
    _, home_abbr = table.home_team
    _, away_abbr = table.away_team

    header = f"{'':>5s}"
    for q in range(1, len(table.quarter_scores) + 1):
        if q <= NUM_QUARTERS:
            header += f"  Q{q}"
        else:
            header += f"  OT"
    header += "   T"

    away_line = f"{away_abbr:>5s}"
    for aq, _hq in table.quarter_scores:
        away_line += f"  {aq:>2d}"
    remaining = max(0, NUM_QUARTERS - len(table.quarter_scores))
    away_line += "   -" * remaining
    away_line += f"  {table.away_score:>3d}"

    home_line = f"{home_abbr:>5s}"
    for _aq, hq in table.quarter_scores:
        home_line += f"  {hq:>2d}"
    home_line += "   -" * remaining
    home_line += f"  {table.home_score:>3d}"

    return f"```\n{header}\n{away_line}\n{home_line}\n```"


def _lines_text(table: NflSimTable) -> str:
    _, home_abbr = table.home_team
    _, away_abbr = table.away_team

    home_ml = _prob_to_american(table.home_prob)
    away_ml = _prob_to_american(1 - table.home_prob)

    spread = table.spread
    if spread >= 0:
        home_spread_str = f"-{spread}"
        away_spread_str = f"+{spread}"
    else:
        home_spread_str = f"+{abs(spread)}"
        away_spread_str = f"-{abs(spread)}"

    ml_line = f"ML: {home_abbr} {home_ml} / {away_abbr} {away_ml}"
    spread_line = f"Spread: {home_abbr} {home_spread_str} / {away_abbr} {away_spread_str} (-110)"
    total_line = f"O/U {table.total} (-110)"

    return f"{ml_line}\n{spread_line}\n{total_line}"


def _playing_embed(table: NflSimTable) -> discord.Embed:
    _, home_abbr = table.home_team
    _, away_abbr = table.away_team

    if table.quarter <= NUM_QUARTERS:
        period_label = f"Q{table.quarter}"
    else:
        period_label = "OT"

    embed = discord.Embed(
        title=f"\U0001f3c8 NFL Sim \u2014 {away_abbr} @ {home_abbr} ({period_label})",
        colour=discord.Colour.gold(),
    )
    embed.description = _scoreboard_text(table)

    embed.add_field(name="Lines", value=_lines_text(table), inline=False)

    bet_lines: list[str] = []
    for p in table.players.values():
        if p.market == "ml":
            side_label = f"ML {home_abbr if p.side == 'home' else away_abbr}"
        elif p.market == "spread":
            side_label = f"Spread {home_abbr if p.side == 'home' else away_abbr}"
        else:
            side_label = f"{'Over' if p.side == 'over' else 'Under'}"
        bet_lines.append(f"**{p.display_name}** \u2014 {p.bet}c on {side_label}")
    if bet_lines:
        embed.add_field(name="Bets", value="\n".join(bet_lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _finished_embed(
    table: NflSimTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    _, home_abbr = table.home_team
    _, away_abbr = table.away_team

    if table.home_score > table.away_score:
        winner_abbr = home_abbr
    else:
        winner_abbr = away_abbr

    ot_text = ""
    if table.ot_count > 0:
        ot_text = " (OT)"

    embed = discord.Embed(
        title=f"\U0001f3c8 NFL Sim \u2014 Final{ot_text} (Round {table.round_num})",
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

    embed.add_field(name="Lines", value=_lines_text(table), inline=False)

    lines: list[str] = []
    for p in table.players.values():
        bal = balances.get(p.user_id, 0) if balances else 0
        if p.market == "ml":
            side_label = f"ML {home_abbr if p.side == 'home' else away_abbr}"
        elif p.market == "spread":
            side_label = f"Spread {home_abbr if p.side == 'home' else away_abbr}"
        else:
            side_label = f"{'Over' if p.side == 'over' else 'Under'}"
        net = p.payout - p.bet
        sign = "+" if net >= 0 else ""
        if p.won:
            lines.append(
                f"\U0001f3c6 **{p.display_name}** ({side_label}) \u2014 "
                f"{p.bet}c \u2192 {p.payout}c (**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        else:
            lines.append(
                f"\u274c **{p.display_name}** ({side_label}) \u2014 "
                f"{p.bet}c \u2192 0c (**-{p.bet}c**) \u2014 bal: {bal}c"
            )
    if lines:
        embed.add_field(name="Results", value="\n".join(lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Modal ────────────────────────────────────────────────────────────────────


class JoinNflSimModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )
    market_input = ui.TextInput(
        label="Market (ml / spread / ou)",
        placeholder="ml / spread / ou",
        required=True,
        max_length=6,
    )
    side_input = ui.TextInput(
        label="Side (home/away or over/under)",
        placeholder="home / away / over / under",
        required=True,
        max_length=5,
    )

    def __init__(
        self, table: NflSimTable, view: "NflSimTableView", balance: int,
    ) -> None:
        _, home_abbr = table.home_team
        _, away_abbr = table.away_team
        super().__init__(title=f"NFL Sim \u2014 {away_abbr} @ {home_abbr}")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 100 (bal: {balance}c)"
        self.side_input.placeholder = f"home ({home_abbr}) / away ({away_abbr}) / over / under"

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

        raw_market = self.market_input.value.strip().lower()
        if raw_market in ("ml", "moneyline"):
            market = "ml"
        elif raw_market in ("spread", "sp"):
            market = "spread"
        elif raw_market in ("ou", "o/u", "over-under", "total"):
            market = "ou"
        else:
            await interaction.response.send_message(
                "Market must be **ml**, **spread**, or **ou**.", ephemeral=True,
            )
            return

        _, home_abbr = self.table.home_team
        _, away_abbr = self.table.away_team
        raw_side = self.side_input.value.strip().lower()

        if market in ("ml", "spread"):
            if raw_side in ("home", "h", home_abbr.lower()):
                side = "home"
            elif raw_side in ("away", "a", away_abbr.lower()):
                side = "away"
            else:
                await interaction.response.send_message(
                    f"For {market}, enter **home** ({home_abbr}) or **away** ({away_abbr}).",
                    ephemeral=True,
                )
                return
        else:
            if raw_side in ("over", "o"):
                side = "over"
            elif raw_side in ("under", "u"):
                side = "under"
            else:
                await interaction.response.send_message(
                    "For ou, enter **over** or **under**.", ephemeral=True,
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

        self.table.players[uid] = NflSimPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
            side=side,
            market=market,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


# ── View ─────────────────────────────────────────────────────────────────────


class NflSimTableView(ui.View):
    def __init__(
        self, table: NflSimTable, active_tables: dict[int, NflSimTable],
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
        label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f3c8", row=0,
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
            JoinNflSimModal(self.table, self, bal),
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
        name, amt, market, side = last
        try:
            await queries.update_casino_balance(str(uid), -amt)
        except ValueError:
            bal = await queries.get_or_create_casino_wallet(str(uid))
            await interaction.response.send_message(
                f"Not enough coins for {amt}c re-bet! (have {bal}c)",
                ephemeral=True,
            )
            return
        self.table.players[uid] = NflSimPlayer(
            user_id=uid, display_name=name, bet=amt, side=side, market=market,
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
                    log.exception("Unhandled error in nflsim.py")
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
        home_name, _ = table.home_team
        away_name, _ = table.away_team
        in_progress = discord.Embed(
            title=f"\U0001f3c8 NFL Sim \u2014 {away_name} @ {home_name}",
            description="Simulation running in the thread below! \U0001f4fa",
            colour=discord.Colour.dark_green(),
        )
        players_text = ", ".join(p.display_name for p in table.players.values())
        in_progress.add_field(name="Bettors", value=players_text or "\u2014", inline=False)
        await interaction.response.edit_message(embed=in_progress, view=self)

        msg = await interaction.original_response()
        thread = await msg.create_thread(
            name=f"NFL Sim \u2014 {away_name} @ {home_name}"
        )
        table.thread = thread

        table.message = await thread.send(embed=_playing_embed(table))
        table.sim_task = asyncio.create_task(self._sim_loop())

    async def _sim_loop(self) -> None:
        table = self.table
        try:
            for q in range(1, NUM_QUARTERS + 1):
                await asyncio.sleep(QUARTER_DELAY)
                table.quarter = q
                h_pts, a_pts = _simulate_quarter(
                    table.home_offense, table.home_defense, table.home_coaching,
                    table.away_offense, table.away_defense, table.away_coaching,
                )
                table.home_score += h_pts
                table.away_score += a_pts
                table.quarter_scores.append((a_pts, h_pts))

                if table.message:
                    try:
                        await table.message.edit(
                            embed=_playing_embed(table),
                        )
                    except discord.HTTPException:
                        pass

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
                            embed=_playing_embed(table),
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

        home_won = table.home_score > table.away_score
        score_diff = table.home_score - table.away_score
        total_score = table.home_score + table.away_score

        for p in table.players.values():
            if p.market == "ml":
                if (p.side == "home" and home_won) or (p.side == "away" and not home_won):
                    p.won = True
                    prob = table.home_prob if p.side == "home" else (1 - table.home_prob)
                    p.payout = int(p.bet * _payout_multiplier(prob))
            elif p.market == "spread":
                if p.side == "home" and score_diff > table.spread:
                    p.won = True
                    p.payout = _juice_payout(p.bet)
                elif p.side == "away" and score_diff < table.spread:
                    p.won = True
                    p.payout = _juice_payout(p.bet)
            else:  # ou
                if p.side == "over" and total_score > table.total:
                    p.won = True
                    p.payout = _juice_payout(p.bet)
                elif p.side == "under" and total_score < table.total:
                    p.won = True
                    p.payout = _juice_payout(p.bet)

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
                str(uid), "nflsim", player.bet, player.payout,
            )

        for uid, player in table.players.items():
            table.last_bets[uid] = (
                player.display_name, player.bet, player.market, player.side,
            )

        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(
                    embed=_finished_embed(table, balances=balances),
                )
            except discord.HTTPException:
                pass

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _start_new_round(self) -> None:
        table = self.table
        table.players.clear()
        table.phase = "betting"
        table.round_num += 1
        home, away = _pick_matchup()
        table.home_team = home
        table.away_team = away

        h_off, h_def, h_coa = _generate_ratings(home[1])
        a_off, a_def, a_coa = _generate_ratings(away[1])
        table.home_offense = h_off
        table.home_defense = h_def
        table.home_coaching = h_coa
        table.away_offense = a_off
        table.away_defense = a_def
        table.away_coaching = a_coa
        table.home_prob = _compute_home_prob(h_off, h_def, h_coa, a_off, a_def, a_coa)
        table.spread = _compute_spread(table.home_prob)
        table.total = _compute_total(h_off, h_def, a_off, a_def)

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
                log.exception("Unhandled error in nflsim.py")

    async def _close(
        self, interaction: discord.Interaction, reason: str,
    ) -> None:
        embed = discord.Embed(
            title="\U0001f3c8 NFL Sim Table \u2014 Closed",
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
                        title="\U0001f3c8 NFL Sim Table \u2014 Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    log.exception("Unhandled error in nflsim.py")
            if table.thread:
                try:
                    await table.thread.edit(archived=True)
                except Exception:
                    log.exception("Unhandled error in nflsim.py")
            return

        await self._refund_all()
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="\U0001f3c8 NFL Sim Table \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("Unhandled error in nflsim.py")

        if table.thread:
            try:
                await table.thread.edit(archived=True)
            except Exception:
                log.exception("Unhandled error in nflsim.py")


# ── Cog ──────────────────────────────────────────────────────────────────────


class NflSimCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, NflSimTable] = {}

    @app_commands.command(
        name="nflsim", description="Bet on a simulated NFL game (casino)",
    )
    async def nflsim(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            _has_running = any(
                (t := getattr(existing, n, None)) is not None and not t.done()
                for n in ("game_task", "race_task", "sim_task", "round_task", "_round_task", "trade_task", "fly_task", "_shot_clock_task", "_countdown_task")
            )
            if _has_running:
                await interaction.response.send_message(
                    "There's already an NFL Sim table in this channel!",
                    ephemeral=True,
                )
                return
            del self.active_tables[channel_id]

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        home, away = _pick_matchup()
        h_off, h_def, h_coa = _generate_ratings(home[1])
        a_off, a_def, a_coa = _generate_ratings(away[1])
        home_prob = _compute_home_prob(h_off, h_def, h_coa, a_off, a_def, a_coa)
        spread = _compute_spread(home_prob)
        total = _compute_total(h_off, h_def, a_off, a_def)

        table = NflSimTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
            home_team=home,
            away_team=away,
            home_prob=home_prob,
            home_offense=h_off,
            home_defense=h_def,
            home_coaching=h_coa,
            away_offense=a_off,
            away_defense=a_def,
            away_coaching=a_coa,
            spread=spread,
            total=total,
        )
        self.active_tables[channel_id] = table

        view = NflSimTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NflSimCog(bot))
