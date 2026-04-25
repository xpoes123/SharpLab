"""NBA Player Guess — name the player from their career teams."""

import asyncio
import random
import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs._elo_helpers import fmt_elo_change, update_elo_multiplayer

# ── Constants ────────────────────────────────────────────────────────────────

ROUND_TIME = 45       # total round time in seconds
CLUE_INTERVAL = 8     # seconds between team reveals
ROUND_DELAY = 4       # seconds between rounds
MIN_PLAYERS = 1
MAX_PLAYERS = 8
DEFAULT_ROUNDS = 10
MAX_ROUNDS_CAP = 20
INACTIVITY_ROUNDS = 5  # auto-end after N consecutive unanswered rounds

MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]

# ── Player Data ──────────────────────────────────────────────────────────────
# (id, name, [alt_names], [career_teams_oldest_first])
# Teams are de-duplicated for consecutive stints on the same team.

NBA_PLAYERS_DATA: list[tuple[int, str, list[str], list[str]]] = [
    (1, "LeBron James", ["LeBron", "Bron", "King James"], ["Cavaliers", "Heat", "Cavaliers", "Lakers"]),
    (2, "Stephen Curry", ["Steph Curry", "Steph", "Chef Curry"], ["Warriors"]),
    (3, "Kevin Durant", ["KD", "Durant"], ["Supersonics", "Thunder", "Warriors", "Nets", "Suns"]),
    (4, "James Harden", ["Harden", "The Beard"], ["Thunder", "Rockets", "Nets", "76ers", "Clippers"]),
    (5, "Chris Paul", ["CP3"], ["Hornets", "Clippers", "Rockets", "Thunder", "Suns", "Warriors", "Spurs"]),
    (6, "Jimmy Butler", ["Jimmy Buckets", "Jimmy"], ["Bulls", "Timberwolves", "76ers", "Heat"]),
    (7, "Kawhi Leonard", ["Kawhi", "The Claw"], ["Spurs", "Raptors", "Clippers"]),
    (8, "Paul George", ["PG13", "PG"], ["Pacers", "Thunder", "Clippers", "76ers"]),
    (9, "Damian Lillard", ["Dame", "Dame Lillard", "Dame Time"], ["Trail Blazers", "Bucks"]),
    (10, "Bradley Beal", ["Beal"], ["Wizards", "Suns"]),
    (11, "Draymond Green", ["Draymond"], ["Warriors"]),
    (12, "Nikola Jokic", ["Jokic", "The Joker"], ["Nuggets"]),
    (13, "Joel Embiid", ["Embiid", "The Process"], ["76ers"]),
    (14, "Giannis Antetokounmpo", ["Giannis", "Greek Freak"], ["Bucks"]),
    (15, "Russell Westbrook", ["Russ", "Westbrook", "Brodie"], ["Thunder", "Rockets", "Lakers", "Clippers", "Nuggets"]),
    (16, "Kyle Lowry", ["Lowry"], ["Grizzlies", "Rockets", "Raptors", "Heat", "76ers"]),
    (17, "DeMar DeRozan", ["DeRozan", "DeMar"], ["Raptors", "Spurs", "Bulls", "Kings"]),
    (18, "Al Horford", ["Horford"], ["Hawks", "Celtics", "76ers", "Thunder", "Celtics"]),
    (19, "Brook Lopez", ["Brook"], ["Nets", "Lakers", "Bucks"]),
    (20, "Jrue Holiday", ["Jrue"], ["76ers", "Pelicans", "Bucks", "Celtics"]),
    (21, "Derrick Rose", ["D-Rose", "Rose"], ["Bulls", "Knicks", "Cavaliers", "Timberwolves", "Pistons", "Knicks"]),
    (22, "Mike Conley", ["Conley"], ["Grizzlies", "Jazz", "Timberwolves"]),
    (23, "Marcus Smart", ["Smart"], ["Celtics", "Grizzlies"]),
    (24, "Khris Middleton", ["Middleton"], ["Pistons", "Bucks"]),
    (25, "Tobias Harris", ["Tobias"], ["Bucks", "Magic", "Pistons", "Clippers", "76ers", "Pistons"]),
    (26, "Andre Drummond", ["Drummond"], ["Pistons", "Cavaliers", "Lakers", "76ers", "Nets", "Bulls", "76ers"]),
    (27, "Clint Capela", ["Capela"], ["Rockets", "Hawks"]),
    (28, "Steven Adams", ["Adams"], ["Thunder", "Pelicans", "Grizzlies", "Rockets"]),
    (29, "Julius Randle", ["Randle"], ["Lakers", "Pelicans", "Knicks", "Timberwolves"]),
    (30, "Buddy Hield", ["Buddy"], ["Pelicans", "Kings", "Pacers", "76ers", "Warriors"]),
    (31, "Myles Turner", ["Turner"], ["Pacers"]),
    (32, "CJ McCollum", ["CJ"], ["Trail Blazers", "Pelicans"]),
    (33, "Zach LaVine", ["LaVine"], ["Timberwolves", "Bulls"]),
    (34, "Terry Rozier", ["Scary Terry", "Rozier"], ["Celtics", "Hornets", "Heat"]),
    (35, "Aaron Gordon", ["AG", "Gordon"], ["Magic", "Nuggets"]),
    (36, "Nikola Vucevic", ["Vucevic", "Vooch"], ["76ers", "Magic", "Bulls"]),
    (37, "Rudy Gobert", ["Gobert", "The Stifle Tower"], ["Jazz", "Timberwolves"]),
    (38, "Donovan Mitchell", ["Spida", "Mitchell"], ["Jazz", "Cavaliers"]),
    (39, "Pascal Siakam", ["Siakam", "Spicy P"], ["Raptors", "Pacers"]),
    (40, "Fred VanVleet", ["FVV", "VanVleet"], ["Raptors", "Rockets"]),
    (41, "Andrew Wiggins", ["Wiggins"], ["Timberwolves", "Warriors"]),
    (42, "D'Angelo Russell", ["DLo", "D'Angelo"], ["Lakers", "Nets", "Warriors", "Timberwolves", "Lakers", "Nets"]),
    (43, "Spencer Dinwiddie", ["Dinwiddie"], ["Pistons", "Nets", "Wizards", "Mavericks", "Nets", "Raptors", "Lakers"]),
    (44, "Kemba Walker", ["Kemba"], ["Bobcats", "Hornets", "Celtics", "Knicks", "Mavericks"]),
    (45, "John Collins", ["Collins"], ["Hawks", "Jazz"]),
    (46, "Jonas Valanciunas", ["JV", "Jonas"], ["Raptors", "Grizzlies", "Pelicans", "Wizards"]),
    (47, "Eric Gordon", ["EG"], ["Clippers", "Hornets", "Rockets", "Suns", "76ers"]),
    (48, "Reggie Jackson", ["Reggie"], ["Thunder", "Pistons", "Clippers", "Nuggets", "76ers"]),
    (49, "Harrison Barnes", ["Barnes"], ["Warriors", "Mavericks", "Kings", "Spurs"]),
    (50, "Thaddeus Young", ["Thad Young", "Thaddeus"], ["76ers", "Timberwolves", "Nets", "Pacers", "Bulls", "Spurs", "Raptors", "Suns"]),
    (51, "Kentavious Caldwell-Pope", ["KCP"], ["Pistons", "Lakers", "Wizards", "Nuggets", "Magic"]),
    (52, "Tim Hardaway Jr.", ["THJ", "Hardaway Jr"], ["Knicks", "Hawks", "Mavericks"]),
    (53, "Robert Covington", ["RoCo", "Covington"], ["76ers", "Timberwolves", "Rockets", "Trail Blazers", "Clippers", "76ers"]),
    (54, "Caris LeVert", ["LeVert"], ["Nets", "Pacers", "Cavaliers"]),
    (55, "Norman Powell", ["Norm Powell", "Powell"], ["Raptors", "Trail Blazers", "Clippers"]),
    (56, "Gary Harris", ["Gary"], ["Nuggets", "Magic"]),
    (57, "Jusuf Nurkic", ["Nurkic", "The Bosnian Beast"], ["Nuggets", "Trail Blazers", "Suns"]),
    (58, "Kelly Oubre Jr.", ["Oubre"], ["Wizards", "Suns", "Warriors", "Hornets", "76ers"]),
    (59, "Marcus Morris Sr.", ["Marcus Morris"], ["Rockets", "Suns", "Pistons", "Celtics", "Knicks", "Clippers"]),
    (60, "Montrezl Harrell", ["Trezz", "Harrell"], ["Rockets", "Clippers", "Lakers", "Wizards", "Hornets", "76ers"]),
    (61, "Bojan Bogdanovic", ["Bojan", "Bogdanovic"], ["Nets", "Wizards", "Pacers", "Jazz", "Pistons", "Knicks"]),
    (62, "Gordon Hayward", ["Hayward"], ["Jazz", "Celtics", "Hornets", "Thunder"]),
    (63, "Derrick Favors", ["Favors"], ["Nets", "Jazz", "Pelicans", "Thunder", "Rockets"]),
    (64, "LaMarcus Aldridge", ["LMA", "Aldridge"], ["Trail Blazers", "Spurs", "Nets"]),
    (65, "Trevor Ariza", ["Ariza"], ["Knicks", "Magic", "Lakers", "Rockets", "Hornets", "Wizards", "Kings", "Trail Blazers", "Heat", "Lakers"]),
    (66, "Andre Iguodala", ["Iggy", "Iguodala"], ["76ers", "Nuggets", "Warriors", "Heat", "Warriors"]),
    (67, "Dwight Howard", ["Dwight", "Superman"], ["Magic", "Lakers", "Rockets", "Hawks", "Hornets", "Wizards", "Lakers", "76ers"]),
    (68, "Jeff Green", ["Green"], ["Supersonics", "Thunder", "Celtics", "Grizzlies", "Clippers", "Magic", "Cavaliers", "Wizards", "Rockets", "Nets", "Nuggets"]),
    (69, "Danilo Gallinari", ["Gallo", "Gallinari"], ["Knicks", "Nuggets", "Clippers", "Thunder", "Hawks", "Celtics", "Wizards", "Bucks"]),
    (70, "Patty Mills", ["Patty"], ["Trail Blazers", "Spurs", "Nets", "Hawks", "Heat"]),
    (71, "Danny Green", ["Danny"], ["Cavaliers", "Spurs", "Raptors", "Lakers", "76ers", "Grizzlies", "Cavaliers", "76ers"]),
    (72, "JJ Redick", ["Redick"], ["Magic", "Bucks", "Clippers", "76ers", "Pelicans", "Mavericks"]),
    (73, "Jarrett Allen", ["Allen"], ["Nets", "Cavaliers"]),
    (74, "Domantas Sabonis", ["Sabonis"], ["Thunder", "Pacers", "Kings"]),
    (75, "Malcolm Brogdon", ["Brogdon"], ["Bucks", "Pacers", "Celtics", "Trail Blazers", "Wizards"]),
    (76, "De'Aaron Fox", ["Fox"], ["Kings"]),
    (77, "Bam Adebayo", ["Bam"], ["Heat"]),
    (78, "OG Anunoby", ["OG"], ["Raptors", "Knicks"]),
    (79, "Jaren Jackson Jr.", ["JJJ", "Jaren Jackson"], ["Grizzlies"]),
    (80, "Shai Gilgeous-Alexander", ["SGA", "Shai"], ["Clippers", "Thunder"]),
    (81, "Dejounte Murray", ["Dejounte"], ["Spurs", "Hawks", "Pelicans"]),
    (82, "Brandon Ingram", ["BI", "Ingram"], ["Lakers", "Pelicans"]),
    (83, "Tyler Herro", ["Herro"], ["Heat"]),
    (84, "Jamal Murray", ["Murray"], ["Nuggets"]),
    (85, "Jayson Tatum", ["Tatum", "JT"], ["Celtics"]),
    (86, "Jaylen Brown", ["Brown"], ["Celtics"]),
    (87, "Devin Booker", ["Book", "Booker", "D-Book"], ["Suns"]),
    (88, "Karl-Anthony Towns", ["KAT", "Towns"], ["Timberwolves", "Knicks"]),
    (89, "Trae Young", ["Trae", "Ice Trae"], ["Hawks"]),
    (90, "Luka Doncic", ["Luka"], ["Mavericks", "Lakers"]),
    (91, "Kyrie Irving", ["Kyrie", "Uncle Drew"], ["Cavaliers", "Celtics", "Nets", "Mavericks"]),
    (92, "Anthony Davis", ["AD", "The Brow"], ["Pelicans", "Lakers"]),
    (93, "Klay Thompson", ["Klay"], ["Warriors", "Mavericks"]),
    (94, "Dillon Brooks", ["Brooks"], ["Grizzlies", "Rockets"]),
    (95, "Mitchell Robinson", ["Mitch Rob", "Robinson"], ["Knicks"]),
    (96, "Ivica Zubac", ["Zubac"], ["Lakers", "Clippers"]),
    (97, "P.J. Tucker", ["PJ Tucker", "Tucker"], ["Raptors", "Suns", "Rockets", "Bucks", "76ers", "Clippers"]),
    (98, "Malik Beasley", ["Beasley"], ["Nuggets", "Timberwolves", "Jazz", "Bucks", "Pistons"]),
    (99, "Lonzo Ball", ["Zo", "Lonzo"], ["Lakers", "Pelicans", "Bulls"]),
    (100, "Richaun Holmes", ["Holmes"], ["76ers", "Suns", "Kings", "Mavericks"]),
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _normalize(s: str) -> str:
    """Lowercase, strip accents and non-alphanumeric chars."""
    nfkd = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return "".join(c.lower() for c in stripped if c.isalnum()).strip()


def _fuzzy_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def check_nba_answer(guess: str, entry: tuple) -> bool:
    norm_guess = _normalize(guess)
    if not norm_guess or len(norm_guess) < 3:
        return False
    _, name, alts, _ = entry
    for ans in [name] + alts:
        norm_ans = _normalize(ans)
        # Exact normalized match
        if norm_guess == norm_ans:
            return True
        # Fuzzy match (>=85% similarity on normalized strings)
        if len(norm_guess) >= 5 and _fuzzy_ratio(norm_guess, norm_ans) >= 0.85:
            return True
        # Last name only match
        parts = ans.split()
        if len(parts) > 1:
            last = _normalize(parts[-1])
            if norm_guess == last and len(last) >= 4:
                return True
    return False


def _calc_points(clues_at_solve: int) -> int:
    if clues_at_solve <= 1:
        return 5
    elif clues_at_solve == 2:
        return 4
    elif clues_at_solve == 3:
        return 3
    elif clues_at_solve == 4:
        return 2
    else:
        return 1


def _pick_player(used_ids: set[int]) -> tuple:
    pool = [e for e in NBA_PLAYERS_DATA if e[0] not in used_ids]
    if not pool:
        used_ids.clear()
        pool = list(NBA_PLAYERS_DATA)
    choice = random.choice(pool)
    used_ids.add(choice[0])
    return choice


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class NbaGuessPlayer:
    user_id: int
    display_name: str
    score: int = 0
    answer: str | None = None
    answer_time: float | None = None


@dataclass
class NbaGuessTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | between_rounds | closed
    players: dict[int, NbaGuessPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 0
    total_rounds: int = DEFAULT_ROUNDS
    current_entry: tuple | None = None
    clues_revealed: int = 0
    round_start_time: float = 0.0
    round_winner: int | None = None
    round_points: int = 0
    race_task: asyncio.Task | None = field(default=None, repr=False)
    thread: discord.Thread | None = field(default=None, repr=False)
    round_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    used_ids: set[int] = field(default_factory=set)
    round_messages: list[discord.Message] = field(default_factory=list)
    stop_requested: bool = False


# ── Embeds ───────────────────────────────────────────────────────────────────


def _scoreboard(table: NbaGuessTable) -> str:
    sorted_players = sorted(
        table.players.values(), key=lambda p: p.score, reverse=True,
    )
    lines: list[str] = []
    for i, p in enumerate(sorted_players):
        prefix = MEDALS[i] if i < len(MEDALS) and p.score > 0 else "\u25aa\ufe0f"
        lines.append(f"{prefix} **{p.display_name}** \u2014 {p.score} pts")
    return "\n".join(lines) or "*No players*"


def _betting_embed(table: NbaGuessTable) -> discord.Embed:
    embed = discord.Embed(
        title="\U0001f3c0 NBA Player Guess",
        description=(
            f"**Rounds:** {table.total_rounds}\n"
            "Guess the NBA player from their career teams!\n"
            "Teams are revealed one at a time \u2014 fewer clues = more points."
        ),
        colour=discord.Colour.orange(),
    )
    if table.players:
        lines = [
            f"\U0001f3c0 **{p.display_name}**"
            + (f" ({p.score} pts)" if p.score > 0 else "")
            for p in table.players.values()
        ]
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Players", value="*No players yet \u2014 click Join!*", inline=False)
    embed.add_field(
        name="Scoring",
        value="1 team = 5 pts \u2502 2 = 4 pts \u2502 3 = 3 pts \u2502 4 = 2 pts \u2502 5+ = 1 pt",
        inline=False,
    )
    embed.set_footer(text=f"Host: {table.host_name} \u2503 Min {MIN_PLAYERS} players")
    return embed


def _team_clues_text(table: NbaGuessTable) -> str:
    _, _, _, teams = table.current_entry
    lines: list[str] = []
    for i, team in enumerate(teams, 1):
        if i <= table.clues_revealed:
            lines.append(f"{i}. {team}")
        else:
            lines.append(f"{i}. ???")
    return "\n".join(lines)


def _playing_embed(table: NbaGuessTable, remaining: int | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"\U0001f3c0 NBA Player Guess \u2014 Round {table.round_num}/{table.total_rounds}",
        colour=discord.Colour.dark_orange(),
    )
    embed.description = (
        f"**Career Teams:**\n{_team_clues_text(table)}\n\n"
        "**Type the player name in chat!**"
    )
    secs = remaining if remaining is not None else ROUND_TIME
    embed.add_field(name="\u23f1\ufe0f Time", value=f"**{secs}s**", inline=True)
    embed.add_field(
        name=f"Clues: {table.clues_revealed}/{len(table.current_entry[3])}",
        value=f"Next clue in {CLUE_INTERVAL}s" if table.clues_revealed < len(table.current_entry[3]) else "All teams revealed!",
        inline=True,
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _round_result_embed(table: NbaGuessTable) -> discord.Embed:
    winner = table.players[table.round_winner]
    _, name, _, teams = table.current_entry
    solve_time = winner.answer_time - table.round_start_time
    is_last = table.round_num >= table.total_rounds

    embed = discord.Embed(
        title=f"\U0001f3c0 Round {table.round_num} \u2705",
        colour=discord.Colour.green(),
    )
    embed.description = (
        f"\U0001f3c6 **{winner.display_name}** got it in **{solve_time:.1f}s** "
        f"for **{table.round_points} pts**!\n\n"
        f"It's **{name}**! ({' \u2192 '.join(teams)})"
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round \u2014 calculating results\u2026")
    return embed


def _timeout_embed(table: NbaGuessTable) -> discord.Embed:
    _, name, _, teams = table.current_entry
    is_last = table.round_num >= table.total_rounds

    embed = discord.Embed(
        title=f"\U0001f3c0 Round {table.round_num} (Time's Up!)",
        colour=discord.Colour.dark_grey(),
    )
    embed.description = (
        f"Nobody got it in {ROUND_TIME} seconds!\n\n"
        f"It was **{name}**! ({' \u2192 '.join(teams)})"
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round \u2014 calculating results\u2026")
    return embed


def _final_embed(
    table: NbaGuessTable,
    elo_changes: dict[int, tuple[float, float]] | None = None,
) -> discord.Embed:
    max_score = max((p.score for p in table.players.values()), default=0)
    no_winner = max_score == 0

    embed = discord.Embed(
        title="\U0001f3c0 NBA Player Guess \u2014 Results",
        colour=discord.Colour.gold() if not no_winner else discord.Colour.dark_grey(),
    )

    sorted_players = sorted(
        table.players.values(), key=lambda p: p.score, reverse=True,
    )

    if no_winner:
        embed.description = "No rounds were won \u2014 game over!"
    else:
        winner = sorted_players[0]
        embed.description = (
            f"\U0001f3c6 **{winner.display_name}** wins with "
            f"**{winner.score}** point{'s' if winner.score != 1 else ''}!"
        )

    lines: list[str] = []
    for i, p in enumerate(sorted_players):
        medal = MEDALS[i] if i < len(MEDALS) and p.score > 0 else "\u25aa\ufe0f"
        lines.append(f"{medal} **{p.display_name}** \u2014 {p.score} pts")
    embed.add_field(name="Results", value="\n".join(lines), inline=False)
    embed.add_field(name="Rounds Played", value=str(table.round_num), inline=True)

    if elo_changes:
        elo_lines: list[str] = []
        for p in sorted_players:
            if p.user_id in elo_changes:
                old, new = elo_changes[p.user_id]
                elo_lines.append(f"**{p.display_name}**: {fmt_elo_change(old, new)}")
        if elo_lines:
            embed.add_field(name="\U0001f4c8 ELO", value="\n".join(elo_lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Rounds selector options ──────────────────────────────────────────────────

_ROUNDS_OPTIONS = [
    discord.SelectOption(label="5 Rounds", value="5", emoji="\u0035\ufe0f\u20e3"),
    discord.SelectOption(label="10 Rounds", value="10", emoji="\U0001f51f", default=True),
    discord.SelectOption(label="15 Rounds", value="15", emoji="\U0001f4af"),
    discord.SelectOption(label="20 Rounds", value="20", emoji="\U0001f525"),
]


# ── View ─────────────────────────────────────────────────────────────────────


class NbaEndGameView(ui.View):
    """Button posted in the thread so any player can stop the game early."""

    def __init__(self, table: NbaGuessTable) -> None:
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


class NbaGuessView(ui.View):
    def __init__(
        self, table: NbaGuessTable, active_tables: dict[int, NbaGuessTable],
    ) -> None:
        super().__init__(timeout=900)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        racing = phase in ("playing", "between_rounds")

        self.start_btn.disabled = not betting or len(self.table.players) < MIN_PLAYERS
        self.join_btn.disabled = not betting
        self.leave_btn.disabled = not betting
        self.close_btn.disabled = racing
        self.rounds_select.disabled = not betting

    # ── Buttons ──────────────────────────────────────────────────────────

    @ui.button(
        label="Start", style=discord.ButtonStyle.success, emoji="\u25b6\ufe0f", row=0,
    )
    async def start_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message("Only the host can start!", ephemeral=True)
            return
        if self.table.phase != "betting":
            await interaction.response.send_message("Already started!", ephemeral=True)
            return
        if len(self.table.players) < MIN_PLAYERS:
            await interaction.response.send_message(
                f"Need at least {MIN_PLAYERS} player(s)!", ephemeral=True,
            )
            return
        await self._start_race(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f3c0", row=0,
    )
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Game in progress! Wait for the next one.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        self.table.players[uid] = NbaGuessPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=0,
    )
    async def leave_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid not in self.table.players:
            await interaction.response.send_message("You're not at this table.", ephemeral=True)
            return
        if self.table.phase != "betting":
            await interaction.response.send_message("Can't leave during a game!", ephemeral=True)
            return
        del self.table.players[uid]
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Close Table", style=discord.ButtonStyle.danger, emoji="\u2716\ufe0f", row=1,
    )
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can close the table!", ephemeral=True,
            )
            return
        if self.table.phase in ("playing", "between_rounds"):
            await interaction.response.send_message(
                "Can't close during a game! Wait for it to finish.", ephemeral=True,
            )
            return
        await self._close_table(interaction)

    # ── Rounds selector ──────────────────────────────────────────────────

    @ui.select(
        placeholder="Rounds: 10",
        options=_ROUNDS_OPTIONS,
        row=2,
    )
    async def rounds_select(
        self, interaction: discord.Interaction, select: ui.Select,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can change the rounds!", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Can't change rounds during a game!", ephemeral=True,
            )
            return
        val = int(select.values[0])
        self.table.total_rounds = val
        select.placeholder = f"Rounds: {val}"
        for opt in select.options:
            opt.default = opt.value == str(val)
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Game logic ───────────────────────────────────────────────────────

    async def _start_race(self, interaction: discord.Interaction) -> None:
        table = self.table

        entry = _pick_player(table.used_ids)
        table.current_entry = entry
        table.clues_revealed = 1
        table.round_num = 1
        table.round_winner = None
        table.round_points = 0
        table.round_solved.clear()
        table.round_messages.clear()
        table.phase = "playing"
        table.round_start_time = time.monotonic()

        for p in table.players.values():
            p.answer = None
            p.answer_time = None

        self._update_buttons()
        in_progress = discord.Embed(
            title="\U0001f3c0 NBA Player Guess \u2014 In Progress",
            description="Game running in the thread below! Type your answers there.",
            colour=discord.Colour.orange(),
        )
        players_text = ", ".join(p.display_name for p in table.players.values())
        in_progress.add_field(name="Players", value=players_text or "\u2014", inline=False)
        await interaction.response.edit_message(embed=in_progress, view=self)

        msg = await interaction.original_response()
        thread = await msg.create_thread(name=f"NBA Player Guess \u2014 {table.host_name}")
        table.thread = thread

        await thread.send(
            "\U0001f3c1 **NBA Player Guess started!** Type your answers here.",
            view=NbaEndGameView(table),
        )

        table.message = await thread.send(embed=_playing_embed(table))
        table.race_task = asyncio.create_task(self._race_loop())

    async def _wait_for_solve_or_timeout(self) -> bool:
        """Wait for someone to solve or timeout. Returns True if solved."""
        table = self.table
        deadline = table.round_start_time + ROUND_TIME
        total_teams = len(table.current_entry[3])

        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                return table.round_winner is not None

            next_events: list[float] = []
            # Next clue reveal
            if table.clues_revealed < total_teams:
                next_clue_at = table.round_start_time + (table.clues_revealed * CLUE_INTERVAL)
                time_to_clue = next_clue_at - now
                if time_to_clue > 0:
                    next_events.append(time_to_clue)
                else:
                    next_events.append(0.1)  # reveal immediately
            # Timer tick every 5 seconds
            next_events.append(min(5.0, remaining))
            wait = min(next_events)

            try:
                await asyncio.wait_for(table.round_solved.wait(), timeout=wait)
                return True
            except asyncio.TimeoutError:
                if table.round_winner is not None:
                    return True

                now2 = time.monotonic()
                # Reveal next team clue
                if table.clues_revealed < total_teams:
                    next_clue_at = table.round_start_time + (table.clues_revealed * CLUE_INTERVAL)
                    if now2 >= next_clue_at:
                        table.clues_revealed += 1

                secs_left = max(0, int(deadline - now2))
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
            consecutive_unanswered = 0
            while True:
                rnd += 1

                if rnd > 1:
                    entry = _pick_player(table.used_ids)
                    table.current_entry = entry
                    table.clues_revealed = 1
                    table.round_num = rnd
                    table.round_winner = None
                    table.round_points = 0
                    table.round_solved.clear()
                    table.round_messages.clear()
                    table.phase = "playing"
                    table.round_start_time = time.monotonic()

                    for p in table.players.values():
                        p.answer = None
                        p.answer_time = None

                    self._update_buttons()
                    if table.thread:
                        try:
                            table.message = await table.thread.send(
                                embed=_playing_embed(table),
                            )
                        except discord.HTTPException:
                            pass

                solved = await self._wait_for_solve_or_timeout()

                if table.stop_requested:
                    break

                if solved and table.round_winner is not None:
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_round_result_embed(table),
                            )
                        except discord.HTTPException:
                            pass
                else:
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_timeout_embed(table),
                            )
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

                if rnd >= table.total_rounds:
                    break

                await self._clear_round_messages()

                table.phase = "between_rounds"
                await asyncio.sleep(ROUND_DELAY)

                if table.stop_requested:
                    break

            await self._clear_round_messages()
            if table.stop_requested and table.thread:
                try:
                    await table.thread.send("\u23f9\ufe0f Game ended early.")
                except discord.HTTPException:
                    pass
            await self._end_game()

        except asyncio.CancelledError:
            pass
        except Exception:
            table.phase = "closed"
            self.active_tables.pop(table.channel_id, None)

    async def _clear_round_messages(self) -> None:
        messages = list(self.table.round_messages)
        self.table.round_messages.clear()
        for msg in messages:
            try:
                await msg.delete()
            except discord.HTTPException:
                pass

    async def _end_game(self) -> None:
        table = self.table
        table.phase = "closed"

        # ELO update — rank by score (highest = 1st)
        elo_changes: dict[int, tuple[float, float]] = {}
        max_score = max((p.score for p in table.players.values()), default=0)
        if max_score > 0 and len(table.players) >= 2:
            sorted_players = sorted(
                table.players.values(),
                key=lambda p: p.score,
                reverse=True,
            )
            finish_order = [p.user_id for p in sorted_players]
            try:
                elo_changes = await update_elo_multiplayer(finish_order, "nbaguess", "nbaguess")
            except Exception:
                pass

        embed = _final_embed(table, elo_changes)

        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)

        if table.message:
            try:
                await table.message.edit(embed=embed)
            except discord.HTTPException:
                pass

        if table.thread:
            try:
                await table.thread.edit(archived=True)
            except discord.HTTPException:
                pass

    async def _close_table(self, interaction: discord.Interaction) -> None:
        table = self.table

        if table.round_num == 0:
            embed = discord.Embed(
                title="\U0001f3c0 NBA Player Guess \u2014 Closed",
                description="Table closed.",
                colour=discord.Colour.dark_grey(),
            )
            for child in self.children:
                child.disabled = True  # type: ignore[union-attr]
            self.stop()
            self.active_tables.pop(table.channel_id, None)
            await interaction.response.edit_message(embed=embed, view=self)
            return

        table.phase = "closed"
        embed = _final_embed(table)

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

        table.phase = "closed"
        self.active_tables.pop(table.channel_id, None)

        if table.message:
            try:
                embed = discord.Embed(
                    title="\U0001f3c0 NBA Player Guess \u2014 Timed Out",
                    description="Table timed out.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass

        if table.thread:
            try:
                await table.thread.edit(archived=True)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class NbaGuessCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, NbaGuessTable] = {}

    @app_commands.command(
        name="nba",
        description="NBA Player Guess \u2014 name the player from their career teams!",
    )
    @app_commands.describe(rounds="Number of rounds (5-20, default 10)")
    async def nba(self, interaction: discord.Interaction, rounds: int = DEFAULT_ROUNDS) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already an NBA guess game in this channel!",
                ephemeral=True,
            )
            return

        rounds = max(1, min(rounds, MAX_ROUNDS_CAP))

        table = NbaGuessTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
            total_rounds=rounds,
        )
        self.active_tables[channel_id] = table

        view = NbaGuessView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        uid = message.author.id
        guess = message.content.strip()

        table = None
        for t in self.active_tables.values():
            if t.thread is not None and t.thread.id == message.channel.id:
                table = t
                break
        if table is None or table.phase != "playing":
            return
        if uid not in table.players or table.round_winner is not None:
            return
        if len(guess) < 3:
            return
        alpha_chars = sum(1 for c in guess if c.isalpha())
        if alpha_chars < len(guess) * 0.5:
            return

        table.round_messages.append(message)

        if check_nba_answer(guess, table.current_entry):
            now = time.monotonic()
            player = table.players[uid]
            points = _calc_points(table.clues_revealed)
            player.answer = guess
            player.answer_time = now
            player.score += points
            table.round_winner = uid
            table.round_points = points
            try:
                await message.add_reaction("\u2705")
            except discord.HTTPException:
                pass
            table.round_solved.set()
        else:
            try:
                await message.add_reaction("\u274c")
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NbaGuessCog(bot))
