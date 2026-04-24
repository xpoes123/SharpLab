"""Cog — /mlbsim fake MLB game simulator.

Two random MLB teams are drawn. Each gets a win probability.
Players join to watch an inning-by-inning simulation — no coins change hands.
"""

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from shared.models import TEAM_ABBR_MLB

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 10
MIN_PLAYERS = 1
INNING_DELAY = 2.5  # seconds between inning updates
NUM_INNINGS = 9
EXTRA_INNING_DELAY = 2.0

# Full team names for display
MLB_TEAMS: list[tuple[str, str]] = [
    (full, abbr) for full, abbr in sorted(TEAM_ABBR_MLB.items())
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _pick_matchup() -> tuple[tuple[str, str], tuple[str, str]]:
    """Pick two random MLB teams. Returns ((name, abbr), (name, abbr))."""
    pair = random.sample(MLB_TEAMS, 2)
    return (pair[0], pair[1])


def _generate_win_prob() -> float:
    """Generate a win probability for the home team (0.25–0.75 range)."""
    return max(0.20, min(0.80, random.betavariate(3, 3)))


def _prob_to_american(prob: float) -> str:
    """Convert a probability to American odds string."""
    if prob >= 0.5:
        odds = -round(prob / (1 - prob) * 100)
        return str(odds)
    else:
        odds = round((1 - prob) / prob * 100)
        return f"+{odds}"


def _simulate_half_inning(team_prob: float) -> int:
    """Simulate one half-inning for a team. Returns runs scored.

    Models ~3-5 batters reaching base opportunity per half-inning.
    Scoring probability weighted by team's win probability.
    Most half-innings are 0-1 runs; big innings (3+) are rare.
    """
    runs = 0
    outs = 0
    # Each team gets at-bats until 3 outs
    while outs < 3:
        # Chance of reaching base (hit/walk) weighted by team strength
        reach_prob = team_prob * 0.36  # ~18% for .500 team
        if random.random() < reach_prob:
            # Runner on — chance of scoring
            score_roll = random.random()
            if score_roll < 0.25:
                runs += 1  # single RBI
            elif score_roll < 0.30:
                runs += 2  # extra-base hit, 2 RBI
            elif score_roll < 0.32:
                runs += random.randint(2, 4)  # homer with runners on
            # else: runner stranded
            # Out still happens sometimes on productive ABs
            if random.random() < 0.35:
                outs += 1
        else:
            outs += 1
    return runs


def _simulate_inning(home_prob: float) -> tuple[int, int]:
    """Simulate one full inning. Returns (away_runs, home_runs)."""
    away_prob = 1 - home_prob
    away_runs = _simulate_half_inning(away_prob)
    home_runs = _simulate_half_inning(home_prob)
    return away_runs, home_runs


def _simulate_extra_inning(home_prob: float) -> tuple[int, int]:
    """Simulate an extra inning. Same as regular but with tiebreak flavor.

    MLB extra innings start runner on 2nd (Manfred runner), modeled as
    slightly higher scoring probability.
    """
    away_prob = 1 - home_prob
    # Manfred runner bump: +1 base run each, plus normal sim
    away_base = 1 if random.random() < 0.45 else 0
    home_base = 1 if random.random() < 0.45 else 0
    away_runs = away_base + _simulate_half_inning(away_prob)
    home_runs = home_base + _simulate_half_inning(home_prob)

    # Guarantee no tie
    if away_runs == home_runs:
        if random.random() < home_prob:
            home_runs += 1
        else:
            away_runs += 1
    return away_runs, home_runs


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class MlbSimPlayer:
    user_id: int
    display_name: str


@dataclass
class MlbSimTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "waiting"  # waiting | playing | finished
    # Matchup
    home_team: tuple[str, str] = ("", "")  # (full_name, abbr)
    away_team: tuple[str, str] = ("", "")
    home_prob: float = 0.5
    # Players
    players: dict[int, MlbSimPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    # Sim state
    inning: int = 0
    home_score: int = 0
    away_score: int = 0
    inning_scores: list[tuple[int, int]] = field(default_factory=list)  # (away, home) per inning
    extra_innings: int = 0
    sim_task: asyncio.Task | None = field(default=None, repr=False)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _waiting_embed(table: MlbSimTable) -> discord.Embed:
    home_name, home_abbr = table.home_team
    away_name, away_abbr = table.away_team
    away_prob = 1 - table.home_prob

    home_odds = _prob_to_american(table.home_prob)
    away_odds = _prob_to_american(away_prob)

    embed = discord.Embed(
        title=f"\u26be MLB Sim \u2014 Watch the Game (Round {table.round_num})",
        description="Join to watch the simulated game — no coins needed!",
        colour=discord.Colour.blue(),
    )

    matchup_text = (
        f"**{away_abbr}** {away_name}\n"
        f"\u2003Win probability: {away_prob * 100:.0f}% ({away_odds})\n\n"
        f"**{home_abbr}** {home_name}\n"
        f"\u2003Win probability: {table.home_prob * 100:.0f}% ({home_odds})"
    )
    embed.add_field(name=f"{away_abbr} @ {home_abbr}", value=matchup_text, inline=False)

    if table.players:
        viewer_lines = [f"\U0001f440 **{p.display_name}**" for p in table.players.values()]
        embed.add_field(name="Viewers", value="\n".join(viewer_lines), inline=False)
    else:
        embed.add_field(
            name="Viewers",
            value="*No one yet \u2014 click Join!*",
            inline=False,
        )

    embed.set_footer(
        text=f"Host: {table.host_name} \u2502 Min {MIN_PLAYERS} player(s) to start",
    )
    return embed


def _scoreboard_text(table: MlbSimTable) -> str:
    """Render an ASCII baseball linescore."""
    _, home_abbr = table.home_team
    _, away_abbr = table.away_team

    num_cols = len(table.inning_scores)
    remaining = max(0, NUM_INNINGS - num_cols)

    # Header: inning numbers
    header = f"{'':>5s}"
    for i in range(1, num_cols + 1):
        header += f" {i:>2d}"
    for i in range(num_cols + 1, NUM_INNINGS + 1):
        header += f" {i:>2d}"
    header += "   R"

    # Away line
    away_line = f"{away_abbr:>5s}"
    for a_runs, _h_runs in table.inning_scores:
        away_line += f" {a_runs:>2d}"
    away_line += "  -" * remaining
    away_line += f"  {table.away_score:>2d}"

    # Home line
    home_line = f"{home_abbr:>5s}"
    for _a_runs, h_runs in table.inning_scores:
        home_line += f" {h_runs:>2d}"
    home_line += "  -" * remaining
    home_line += f"  {table.home_score:>2d}"

    return f"```\n{header}\n{away_line}\n{home_line}\n```"


def _playing_embed(table: MlbSimTable) -> discord.Embed:
    _, home_abbr = table.home_team
    _, away_abbr = table.away_team

    if table.inning <= NUM_INNINGS:
        period_label = f"Inning {table.inning}"
    else:
        period_label = f"Extra {table.inning - NUM_INNINGS}"

    embed = discord.Embed(
        title=f"\u26be MLB Sim \u2014 {away_abbr} @ {home_abbr} ({period_label})",
        colour=discord.Colour.gold(),
    )
    embed.description = _scoreboard_text(table)

    if table.players:
        viewer_lines = [f"**{p.display_name}**" for p in table.players.values()]
        embed.add_field(name="Viewers", value="\n".join(viewer_lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _finished_embed(table: MlbSimTable) -> discord.Embed:
    _, home_abbr = table.home_team
    _, away_abbr = table.away_team

    if table.home_score > table.away_score:
        winner_abbr = home_abbr
    else:
        winner_abbr = away_abbr

    extra_text = ""
    if table.extra_innings > 0:
        extra_text = f" ({table.extra_innings} extra)"

    embed = discord.Embed(
        title=f"\u26be MLB Sim \u2014 Final{extra_text} (Round {table.round_num})",
        description=(
            f"\U0001f3c6 **{winner_abbr}** wins! "
            f"{away_abbr} {table.away_score} \u2014 {table.home_score} {home_abbr}"
        ),
        colour=discord.Colour.green(),
    )

    embed.add_field(
        name="Linescore",
        value=_scoreboard_text(table),
        inline=False,
    )

    if table.players:
        viewer_lines = [f"**{p.display_name}**" for p in table.players.values()]
        embed.add_field(name="Viewers", value="\n".join(viewer_lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── View ─────────────────────────────────────────────────────────────────────


class MlbSimTableView(ui.View):
    def __init__(
        self, table: MlbSimTable, active_tables: dict[int, MlbSimTable],
    ) -> None:
        super().__init__(timeout=300)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        waiting = phase == "waiting"
        playing = phase == "playing"
        finished = phase == "finished"

        self.start_btn.disabled = (
            not waiting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not waiting
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
        if self.table.phase != "waiting":
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
        label="Join", style=discord.ButtonStyle.primary, emoji="\u26be", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "waiting":
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
        self.table.players[uid] = MlbSimPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_waiting_embed(self.table), view=self,
        )

    @ui.button(
        label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=0,
    )
    async def leave_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        uid = interaction.user.id
        if uid not in self.table.players:
            await interaction.response.send_message(
                "You're not at this table.", ephemeral=True,
            )
            return
        if self.table.phase == "playing":
            await interaction.response.send_message(
                "Can't leave mid-game!", ephemeral=True,
            )
            return
        if self.table.phase == "waiting":
            del self.table.players[uid]
            self._update_buttons()
            await interaction.response.edit_message(
                embed=_waiting_embed(self.table), view=self,
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
            embed=_waiting_embed(self.table), view=self,
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
        await self._close(interaction, "Table closed by host.")

    # ── Game logic ───────────────────────────────────────────────────────────

    async def _start_sim(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "playing"
        table.inning = 1
        table.home_score = 0
        table.away_score = 0
        table.inning_scores = []
        table.extra_innings = 0

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(table), view=self,
        )
        table.sim_task = asyncio.create_task(self._sim_loop())

    async def _sim_loop(self) -> None:
        table = self.table
        try:
            # Regulation innings
            for inn in range(1, NUM_INNINGS + 1):
                await asyncio.sleep(INNING_DELAY)
                table.inning = inn
                a_runs, h_runs = _simulate_inning(table.home_prob)
                table.away_score += a_runs
                table.home_score += h_runs
                table.inning_scores.append((a_runs, h_runs))

                if table.message:
                    try:
                        await table.message.edit(
                            embed=_playing_embed(table), view=self,
                        )
                    except discord.HTTPException:
                        pass

            # Extra innings if tied
            while table.home_score == table.away_score:
                table.extra_innings += 1
                table.inning += 1
                await asyncio.sleep(EXTRA_INNING_DELAY)
                a_runs, h_runs = _simulate_extra_inning(table.home_prob)
                table.away_score += a_runs
                table.home_score += h_runs
                table.inning_scores.append((a_runs, h_runs))

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
                self._update_buttons()
                if table.message:
                    try:
                        await table.message.edit(
                            embed=_finished_embed(table), view=self,
                        )
                    except Exception:
                        pass

    async def _resolve(self) -> None:
        table = self.table
        table.phase = "finished"

        self._update_buttons()
        if table.message:
            try:
                await table.message.edit(
                    embed=_finished_embed(table), view=self,
                )
            except discord.HTTPException:
                pass

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _start_new_round(self) -> None:
        table = self.table
        table.players.clear()
        table.phase = "waiting"
        table.round_num += 1
        # New matchup + new probabilities each round
        home, away = _pick_matchup()
        table.home_team = home
        table.away_team = away
        table.home_prob = _generate_win_prob()
        table.inning = 0
        table.home_score = 0
        table.away_score = 0
        table.inning_scores.clear()
        table.extra_innings = 0
        table.sim_task = None

    async def _close(
        self, interaction: discord.Interaction, reason: str,
    ) -> None:
        embed = discord.Embed(
            title="\u26be MLB Sim Table \u2014 Closed",
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

        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="\u26be MLB Sim Table \u2014 Timed Out",
                    description="Table timed out.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class MlbSimCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, MlbSimTable] = {}

    @app_commands.command(
        name="mlbsim", description="Watch a simulated MLB game",
    )
    async def mlbsim(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already an MLB Sim table in this channel!",
                ephemeral=True,
            )
            return

        home, away = _pick_matchup()
        table = MlbSimTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
            home_team=home,
            away_team=away,
            home_prob=_generate_win_prob(),
        )
        self.active_tables[channel_id] = table

        view = MlbSimTableView(table, self.active_tables)
        embed = _waiting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MlbSimCog(bot))
