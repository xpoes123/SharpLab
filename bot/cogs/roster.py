"""Casino cog — multiplayer /nba-trivia and /nfl-trivia speed games.

Given a player name, first to type the correct team wins the round.
First to WINS_TO_WIN round wins takes the pot.
"""

import asyncio
import random
import time
import unicodedata
from dataclasses import dataclass, field
from itertools import groupby

import discord
from discord import app_commands, ui
from discord.ext import commands

from db import queries

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 1
ROUND_TIME = 15  # seconds per round (team names are short)
ROUND_DELAY = 4  # seconds between rounds
WINS_TO_WIN = 3  # first to N wins
MAX_ROUNDS = 15  # safety cap

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

MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]  # gold, silver, bronze

# ── Sport configs ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SportConfig:
    name: str          # "NBA" or "NFL"
    slug: str          # casino log key: "nba-trivia" / "nfl-trivia"
    emoji: str         # 🏀 / 🏈
    colour: int        # embed colour
    teams: dict[str, list[str]]           # team_key -> [accepted answers]
    players: dict[str, tuple[str, str]]   # player -> (position, team_key)


# ── NBA data ─────────────────────────────────────────────────────────────────
# Update rosters as trades happen. team_key must match a key in NBA_TEAMS.

NBA_TEAMS: dict[str, list[str]] = {
    "Hawks": ["Hawks", "Atlanta Hawks", "Atlanta", "ATL"],
    "Celtics": ["Celtics", "Boston Celtics", "Boston", "BOS"],
    "Nets": ["Nets", "Brooklyn Nets", "Brooklyn", "BKN"],
    "Hornets": ["Hornets", "Charlotte Hornets", "Charlotte", "CHA"],
    "Bulls": ["Bulls", "Chicago Bulls", "Chicago", "CHI"],
    "Cavaliers": ["Cavaliers", "Cavs", "Cleveland Cavaliers", "Cleveland", "CLE"],
    "Mavericks": ["Mavericks", "Mavs", "Dallas Mavericks", "Dallas", "DAL"],
    "Nuggets": ["Nuggets", "Denver Nuggets", "Denver", "DEN"],
    "Pistons": ["Pistons", "Detroit Pistons", "Detroit", "DET"],
    "Warriors": ["Warriors", "Golden State Warriors", "Golden State", "GSW", "GS"],
    "Rockets": ["Rockets", "Houston Rockets", "Houston", "HOU"],
    "Pacers": ["Pacers", "Indiana Pacers", "Indiana", "IND"],
    "Clippers": ["Clippers", "LA Clippers", "Los Angeles Clippers", "LAC"],
    "Lakers": ["Lakers", "LA Lakers", "Los Angeles Lakers", "LAL"],
    "Grizzlies": ["Grizzlies", "Memphis Grizzlies", "Memphis", "MEM"],
    "Heat": ["Heat", "Miami Heat", "Miami", "MIA"],
    "Bucks": ["Bucks", "Milwaukee Bucks", "Milwaukee", "MIL"],
    "Timberwolves": ["Timberwolves", "Wolves", "Minnesota Timberwolves", "Minnesota", "MIN"],
    "Pelicans": ["Pelicans", "New Orleans Pelicans", "New Orleans", "NOP", "NOLA"],
    "Knicks": ["Knicks", "New York Knicks", "NYK"],
    "Thunder": ["Thunder", "Oklahoma City Thunder", "Oklahoma City", "OKC"],
    "Magic": ["Magic", "Orlando Magic", "Orlando", "ORL"],
    "76ers": ["76ers", "Sixers", "Philadelphia 76ers", "Philadelphia", "PHI"],
    "Suns": ["Suns", "Phoenix Suns", "Phoenix", "PHX"],
    "Trail Blazers": ["Trail Blazers", "Blazers", "Portland Trail Blazers", "Portland", "POR"],
    "Kings": ["Kings", "Sacramento Kings", "Sacramento", "SAC"],
    "Spurs": ["Spurs", "San Antonio Spurs", "San Antonio", "SAS"],
    "Raptors": ["Raptors", "Toronto Raptors", "Toronto", "TOR"],
    "Jazz": ["Jazz", "Utah Jazz", "Utah", "UTA"],
    "Wizards": ["Wizards", "Washington Wizards", "Washington", "WAS"],
}

# player_name -> (position, team_key)
NBA_PLAYERS: dict[str, tuple[str, str]] = {
    # ── Hawks ──
    "Trae Young": ("PG", "Hawks"),
    "Jalen Johnson": ("SF", "Hawks"),
    "De'Andre Hunter": ("SF", "Hawks"),
    "Zaccharie Risacher": ("SF", "Hawks"),
    # ── Celtics ──
    "Jayson Tatum": ("SF", "Celtics"),
    "Jaylen Brown": ("SG", "Celtics"),
    "Derrick White": ("SG", "Celtics"),
    "Jrue Holiday": ("PG", "Celtics"),
    "Kristaps Porzingis": ("C", "Celtics"),
    # ── Nets ──
    "Cam Thomas": ("SG", "Nets"),
    "Ben Simmons": ("PG", "Nets"),
    # ── Hornets ──
    "LaMelo Ball": ("PG", "Hornets"),
    "Brandon Miller": ("SF", "Hornets"),
    "Mark Williams": ("C", "Hornets"),
    # ── Bulls ──
    "Zach LaVine": ("SG", "Bulls"),
    "Coby White": ("SG", "Bulls"),
    "Nikola Vucevic": ("C", "Bulls"),
    # ── Cavaliers ──
    "Donovan Mitchell": ("SG", "Cavaliers"),
    "Darius Garland": ("PG", "Cavaliers"),
    "Evan Mobley": ("PF", "Cavaliers"),
    "Jarrett Allen": ("C", "Cavaliers"),
    # ── Mavericks ──
    "Luka Doncic": ("PG", "Mavericks"),
    "Kyrie Irving": ("PG", "Mavericks"),
    "Klay Thompson": ("SG", "Mavericks"),
    "PJ Washington": ("PF", "Mavericks"),
    # ── Nuggets ──
    "Nikola Jokic": ("C", "Nuggets"),
    "Jamal Murray": ("PG", "Nuggets"),
    "Michael Porter Jr": ("SF", "Nuggets"),
    "Aaron Gordon": ("PF", "Nuggets"),
    # ── Pistons ──
    "Cade Cunningham": ("PG", "Pistons"),
    "Jaden Ivey": ("SG", "Pistons"),
    "Ausar Thompson": ("SF", "Pistons"),
    # ── Warriors ──
    "Stephen Curry": ("PG", "Warriors"),
    "Draymond Green": ("PF", "Warriors"),
    "Andrew Wiggins": ("SF", "Warriors"),
    "Jonathan Kuminga": ("SF", "Warriors"),
    # ── Rockets ──
    "Jalen Green": ("SG", "Rockets"),
    "Alperen Sengun": ("C", "Rockets"),
    "Jabari Smith Jr": ("PF", "Rockets"),
    "Amen Thompson": ("SG", "Rockets"),
    # ── Pacers ──
    "Tyrese Haliburton": ("PG", "Pacers"),
    "Pascal Siakam": ("PF", "Pacers"),
    "Myles Turner": ("C", "Pacers"),
    "Andrew Nembhard": ("PG", "Pacers"),
    # ── Clippers ──
    "James Harden": ("PG", "Clippers"),
    "Kawhi Leonard": ("SF", "Clippers"),
    "Norman Powell": ("SG", "Clippers"),
    "Ivica Zubac": ("C", "Clippers"),
    # ── Lakers ──
    "LeBron James": ("SF", "Lakers"),
    "Anthony Davis": ("PF", "Lakers"),
    "Austin Reaves": ("SG", "Lakers"),
    "Rui Hachimura": ("PF", "Lakers"),
    # ── Grizzlies ──
    "Ja Morant": ("PG", "Grizzlies"),
    "Desmond Bane": ("SG", "Grizzlies"),
    "Jaren Jackson Jr": ("PF", "Grizzlies"),
    # ── Heat ──
    "Bam Adebayo": ("C", "Heat"),
    "Tyler Herro": ("SG", "Heat"),
    "Jimmy Butler": ("SF", "Heat"),
    # ── Bucks ──
    "Giannis Antetokounmpo": ("PF", "Bucks"),
    "Damian Lillard": ("PG", "Bucks"),
    "Khris Middleton": ("SF", "Bucks"),
    "Brook Lopez": ("C", "Bucks"),
    # ── Timberwolves ──
    "Anthony Edwards": ("SG", "Timberwolves"),
    "Julius Randle": ("PF", "Timberwolves"),
    "Rudy Gobert": ("C", "Timberwolves"),
    "Jaden McDaniels": ("SF", "Timberwolves"),
    # ── Pelicans ──
    "Zion Williamson": ("PF", "Pelicans"),
    "Brandon Ingram": ("SF", "Pelicans"),
    "CJ McCollum": ("PG", "Pelicans"),
    "Trey Murphy III": ("SF", "Pelicans"),
    # ── Knicks ──
    "Jalen Brunson": ("PG", "Knicks"),
    "Karl-Anthony Towns": ("C", "Knicks"),
    "Mikal Bridges": ("SF", "Knicks"),
    "OG Anunoby": ("SF", "Knicks"),
    # ── Thunder ──
    "Shai Gilgeous-Alexander": ("PG", "Thunder"),
    "Jalen Williams": ("SG", "Thunder"),
    "Chet Holmgren": ("C", "Thunder"),
    "Lu Dort": ("SG", "Thunder"),
    # ── Magic ──
    "Paolo Banchero": ("PF", "Magic"),
    "Franz Wagner": ("SF", "Magic"),
    "Jalen Suggs": ("PG", "Magic"),
    "Wendell Carter Jr": ("C", "Magic"),
    # ── 76ers ──
    "Joel Embiid": ("C", "76ers"),
    "Tyrese Maxey": ("PG", "76ers"),
    "Paul George": ("SF", "76ers"),
    # ── Suns ──
    "Kevin Durant": ("SF", "Suns"),
    "Devin Booker": ("SG", "Suns"),
    "Bradley Beal": ("SG", "Suns"),
    # ── Trail Blazers ──
    "Anfernee Simons": ("SG", "Trail Blazers"),
    "Scoot Henderson": ("PG", "Trail Blazers"),
    "Deandre Ayton": ("C", "Trail Blazers"),
    "Jerami Grant": ("SF", "Trail Blazers"),
    # ── Kings ──
    "Domantas Sabonis": ("C", "Kings"),
    "DeMar DeRozan": ("SF", "Kings"),
    "Keegan Murray": ("SF", "Kings"),
    # ── Spurs ──
    "Victor Wembanyama": ("C", "Spurs"),
    "Devin Vassell": ("SG", "Spurs"),
    "Jeremy Sochan": ("PF", "Spurs"),
    "Keldon Johnson": ("SF", "Spurs"),
    "De'Aaron Fox": ("PG", "Spurs"),
    # ── Raptors ──
    "Scottie Barnes": ("SF", "Raptors"),
    "RJ Barrett": ("SG", "Raptors"),
    "Immanuel Quickley": ("PG", "Raptors"),
    # ── Jazz ──
    "Lauri Markkanen": ("PF", "Jazz"),
    "Jordan Clarkson": ("SG", "Jazz"),
    "John Collins": ("PF", "Jazz"),
    "Walker Kessler": ("C", "Jazz"),
    # ── Wizards ──
    "Kyle Kuzma": ("PF", "Wizards"),
    "Jordan Poole": ("SG", "Wizards"),
    "Bilal Coulibaly": ("SF", "Wizards"),
}

NBA_CONFIG = SportConfig(
    name="NBA",
    slug="nba-trivia",
    emoji="\U0001f3c0",
    colour=0xF58426,  # orange
    teams=NBA_TEAMS,
    players=NBA_PLAYERS,
)

# ── NFL data ─────────────────────────────────────────────────────────────────
# QBs, WRs, and RBs only.  Update as free agency / trades happen.

NFL_TEAMS: dict[str, list[str]] = {
    "Cardinals": ["Cardinals", "Arizona Cardinals", "Arizona", "ARI"],
    "Falcons": ["Falcons", "Atlanta Falcons", "Atlanta", "ATL"],
    "Ravens": ["Ravens", "Baltimore Ravens", "Baltimore", "BAL"],
    "Bills": ["Bills", "Buffalo Bills", "Buffalo", "BUF"],
    "Panthers": ["Panthers", "Carolina Panthers", "Carolina", "CAR"],
    "Bears": ["Bears", "Chicago Bears", "Chicago", "CHI"],
    "Bengals": ["Bengals", "Cincinnati Bengals", "Cincinnati", "CIN"],
    "Browns": ["Browns", "Cleveland Browns", "Cleveland", "CLE"],
    "Cowboys": ["Cowboys", "Dallas Cowboys", "Dallas", "DAL"],
    "Broncos": ["Broncos", "Denver Broncos", "Denver", "DEN"],
    "Lions": ["Lions", "Detroit Lions", "Detroit", "DET"],
    "Packers": ["Packers", "Green Bay Packers", "Green Bay", "GB"],
    "Texans": ["Texans", "Houston Texans", "Houston", "HOU"],
    "Colts": ["Colts", "Indianapolis Colts", "Indianapolis", "IND"],
    "Jaguars": ["Jaguars", "Jags", "Jacksonville Jaguars", "Jacksonville", "JAX"],
    "Chiefs": ["Chiefs", "Kansas City Chiefs", "Kansas City", "KC"],
    "Raiders": ["Raiders", "Las Vegas Raiders", "Las Vegas", "LV"],
    "Chargers": ["Chargers", "LA Chargers", "Los Angeles Chargers", "LAC"],
    "Rams": ["Rams", "LA Rams", "Los Angeles Rams", "LAR"],
    "Dolphins": ["Dolphins", "Miami Dolphins", "Miami", "MIA"],
    "Vikings": ["Vikings", "Minnesota Vikings", "Minnesota", "MIN"],
    "Patriots": ["Patriots", "Pats", "New England Patriots", "New England", "NE"],
    "Saints": ["Saints", "New Orleans Saints", "New Orleans", "NO", "NOLA"],
    "Giants": ["Giants", "New York Giants", "NYG"],
    "Jets": ["Jets", "New York Jets", "NYJ"],
    "Eagles": ["Eagles", "Philadelphia Eagles", "Philadelphia", "PHI"],
    "Steelers": ["Steelers", "Pittsburgh Steelers", "Pittsburgh", "PIT"],
    "49ers": ["49ers", "Niners", "San Francisco 49ers", "San Francisco", "SF"],
    "Seahawks": ["Seahawks", "Seattle Seahawks", "Seattle", "SEA"],
    "Buccaneers": ["Buccaneers", "Bucs", "Tampa Bay Buccaneers", "Tampa Bay", "Tampa", "TB"],
    "Titans": ["Titans", "Tennessee Titans", "Tennessee", "TEN"],
    "Commanders": ["Commanders", "Washington Commanders", "Washington", "WAS"],
}

# player_name -> (position, team_key)
NFL_PLAYERS: dict[str, tuple[str, str]] = {
    # ── Quarterbacks ──
    "Patrick Mahomes": ("QB", "Chiefs"),
    "Josh Allen": ("QB", "Bills"),
    "Lamar Jackson": ("QB", "Ravens"),
    "Joe Burrow": ("QB", "Bengals"),
    "Jalen Hurts": ("QB", "Eagles"),
    "Dak Prescott": ("QB", "Cowboys"),
    "Justin Herbert": ("QB", "Chargers"),
    "Tua Tagovailoa": ("QB", "Dolphins"),
    "C.J. Stroud": ("QB", "Texans"),
    "Brock Purdy": ("QB", "49ers"),
    "Jordan Love": ("QB", "Packers"),
    "Anthony Richardson": ("QB", "Colts"),
    "Trevor Lawrence": ("QB", "Jaguars"),
    "Jared Goff": ("QB", "Lions"),
    "Baker Mayfield": ("QB", "Buccaneers"),
    "Kirk Cousins": ("QB", "Falcons"),
    "Caleb Williams": ("QB", "Bears"),
    "Jayden Daniels": ("QB", "Commanders"),
    "Drake Maye": ("QB", "Patriots"),
    "Bo Nix": ("QB", "Broncos"),
    "Kyler Murray": ("QB", "Cardinals"),
    "Matthew Stafford": ("QB", "Rams"),
    "Russell Wilson": ("QB", "Steelers"),
    "Derek Carr": ("QB", "Saints"),
    "Geno Smith": ("QB", "Seahawks"),
    "Bryce Young": ("QB", "Panthers"),
    "Aaron Rodgers": ("QB", "Jets"),
    "Deshaun Watson": ("QB", "Browns"),
    "Will Levis": ("QB", "Titans"),
    "Aidan O'Connell": ("QB", "Raiders"),
    # ── Wide Receivers ──
    "Tyreek Hill": ("WR", "Dolphins"),
    "Ja'Marr Chase": ("WR", "Bengals"),
    "Justin Jefferson": ("WR", "Vikings"),
    "CeeDee Lamb": ("WR", "Cowboys"),
    "A.J. Brown": ("WR", "Eagles"),
    "Amon-Ra St. Brown": ("WR", "Lions"),
    "Deebo Samuel": ("WR", "49ers"),
    "DK Metcalf": ("WR", "Seahawks"),
    "Terry McLaurin": ("WR", "Commanders"),
    "Mike Evans": ("WR", "Buccaneers"),
    "Chris Olave": ("WR", "Saints"),
    "Garrett Wilson": ("WR", "Jets"),
    "Puka Nacua": ("WR", "Rams"),
    "Nico Collins": ("WR", "Texans"),
    "Drake London": ("WR", "Falcons"),
    "DeVonta Smith": ("WR", "Eagles"),
    "Jaylen Waddle": ("WR", "Dolphins"),
    "Marvin Harrison Jr": ("WR", "Cardinals"),
    "Malik Nabers": ("WR", "Giants"),
    "Rome Odunze": ("WR", "Bears"),
    "George Pickens": ("WR", "Steelers"),
    "DJ Moore": ("WR", "Bears"),
    "Tank Dell": ("WR", "Texans"),
    "Zay Flowers": ("WR", "Ravens"),
    "Rashee Rice": ("WR", "Chiefs"),
    "Brandon Aiyuk": ("WR", "49ers"),
    "Cooper Kupp": ("WR", "Rams"),
    "Chris Godwin": ("WR", "Buccaneers"),
    "Courtland Sutton": ("WR", "Broncos"),
    "Calvin Ridley": ("WR", "Titans"),
    "Davante Adams": ("WR", "Jets"),
    # ── Running Backs ──
    "Derrick Henry": ("RB", "Ravens"),
    "Saquon Barkley": ("RB", "Eagles"),
    "Josh Jacobs": ("RB", "Packers"),
    "Christian McCaffrey": ("RB", "49ers"),
    "Breece Hall": ("RB", "Jets"),
    "Bijan Robinson": ("RB", "Falcons"),
    "Jonathan Taylor": ("RB", "Colts"),
    "Joe Mixon": ("RB", "Texans"),
    "Travis Etienne": ("RB", "Jaguars"),
    "Tony Pollard": ("RB", "Titans"),
    "Jahmyr Gibbs": ("RB", "Lions"),
    "David Montgomery": ("RB", "Lions"),
    "Kyren Williams": ("RB", "Rams"),
    "Isiah Pacheco": ("RB", "Chiefs"),
    "De'Von Achane": ("RB", "Dolphins"),
    "James Cook": ("RB", "Bills"),
    "Rhamondre Stevenson": ("RB", "Patriots"),
    "Aaron Jones": ("RB", "Vikings"),
    "Kenneth Walker III": ("RB", "Seahawks"),
    "Nick Chubb": ("RB", "Browns"),
    "Alvin Kamara": ("RB", "Saints"),
    "Rachaad White": ("RB", "Buccaneers"),
    "Brian Robinson Jr": ("RB", "Commanders"),
    "Chuba Hubbard": ("RB", "Panthers"),
    "Jerome Ford": ("RB", "Browns"),
}

NFL_CONFIG = SportConfig(
    name="NFL",
    slug="nfl-trivia",
    emoji="\U0001f3c8",
    colour=0x013369,  # NFL blue
    teams=NFL_TEAMS,
    players=NFL_PLAYERS,
)


# ── Answer matching ──────────────────────────────────────────────────────────


def _normalize(s: str) -> str:
    """Lowercase, strip accents and non-alphanumeric chars for fuzzy matching."""
    nfkd = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return "".join(c.lower() for c in stripped if c.isalnum() or c == " ").strip()


def check_answer(guess: str, accepted: list[str]) -> bool:
    norm_guess = _normalize(guess)
    if not norm_guess:
        return False
    for ans in accepted:
        if _normalize(ans) == norm_guess:
            return True
    return False


# ── Payout helpers ───────────────────────────────────────────────────────────


def _compute_payouts(
    players: dict[int, "RosterPlayer"], prize_pool: int, n_players: int,
) -> dict[int, int]:
    pct_table = PAYTABLE.get(n_players, PAYTABLE[8])

    in_money = sorted(
        [p for p in players.values() if p.rounds_won > 0],
        key=lambda p: p.rounds_won,
        reverse=True,
    )

    payouts: dict[int, int] = {uid: 0 for uid in players}

    if not in_money:
        return payouts

    paid_positions = len(pct_table)
    pos = 0
    for _wins, group_iter in groupby(in_money, key=lambda p: p.rounds_won):
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
        top_wins = in_money[0].rounds_won
        top_group = [p for p in in_money if p.rounds_won == top_wins]
        extra = leftover // len(top_group)
        for p in top_group:
            payouts[p.user_id] += extra

    return payouts


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class RosterPlayer:
    user_id: int
    display_name: str
    bet: int
    rounds_won: int = 0
    answer: str | None = None
    answer_time: float | None = None


@dataclass
class RosterTable:
    channel_id: int
    host_id: int
    host_name: str
    config: SportConfig
    phase: str = "betting"  # betting | playing | between_rounds | closed
    players: dict[int, RosterPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 0
    current_player: str = ""
    current_position: str = ""
    current_team: str = ""       # canonical display name (team key)
    current_answers: list[str] = field(default_factory=list)
    round_start_time: float = 0.0
    round_winner: int | None = None
    race_task: asyncio.Task | None = field(default=None, repr=False)
    round_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    total_rounds_played: int = 0
    used_players: list[str] = field(default_factory=list)


# ── Embeds ───────────────────────────────────────────────────────────────────


def _scoreboard(table: RosterTable) -> str:
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


def _betting_embed(table: RosterTable) -> discord.Embed:
    cfg = table.config
    pot = sum(p.bet for p in table.players.values())
    n = len(table.players)

    embed = discord.Embed(
        title=f"{cfg.emoji} {cfg.name} Roster Trivia",
        description=(
            f"Name the team! **First to {WINS_TO_WIN} wins** takes the pot.\n"
            "Type your answer directly in chat \u2014 fastest correct answer wins each round!"
        ),
        colour=discord.Colour(cfg.colour),
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
            f"{cfg.emoji} **{p.display_name}** \u2014 {p.bet}c"
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


def _playing_embed(table: RosterTable, remaining: int | None = None) -> discord.Embed:
    cfg = table.config
    embed = discord.Embed(
        title=f"{cfg.emoji} Round {table.round_num} (First to {WINS_TO_WIN})",
        colour=discord.Colour.gold(),
    )

    embed.description = (
        f"# What team does **{table.current_player}** ({table.current_position}) play for?\n\n"
        "**Type your answer in chat!**"
    )

    secs = remaining if remaining is not None else ROUND_TIME
    embed.add_field(name="\u23f1\ufe0f Time", value=f"**{secs}s**", inline=True)

    pot = sum(p.bet for p in table.players.values())
    embed.add_field(name="Pot", value=f"{pot}c", inline=True)

    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _round_result_embed(table: RosterTable) -> discord.Embed:
    cfg = table.config
    winner = table.players[table.round_winner]
    solve_time = winner.answer_time - table.round_start_time
    is_last = winner.rounds_won >= WINS_TO_WIN or table.round_num >= MAX_ROUNDS

    embed = discord.Embed(
        title=f"{cfg.emoji} Round {table.round_num} \u2705",
        colour=discord.Colour.green(),
    )
    embed.description = (
        f"\U0001f3c6 **{winner.display_name}** got it in **{solve_time:.1f}s**!\n\n"
        f"{cfg.emoji} {table.current_player} ({table.current_position}) \u2192 **{table.current_team}**"
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round complete \u2014 calculating results\u2026")
    return embed


def _timeout_embed(table: RosterTable) -> discord.Embed:
    cfg = table.config
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)
    is_last = max_wins >= WINS_TO_WIN or table.round_num >= MAX_ROUNDS

    embed = discord.Embed(
        title=f"{cfg.emoji} Round {table.round_num} (Time's Up!)",
        colour=discord.Colour.dark_grey(),
    )
    embed.description = (
        f"Nobody got it in {ROUND_TIME} seconds!\n\n"
        f"{cfg.emoji} {table.current_player} ({table.current_position}) \u2192 **{table.current_team}**"
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round complete \u2014 calculating results\u2026")
    return embed


def _final_embed(
    table: RosterTable,
    *,
    payouts: dict[int, int],
    balances: dict[int, int],
) -> discord.Embed:
    cfg = table.config
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)
    is_refund = max_wins == 0

    embed = discord.Embed(
        title=f"{cfg.emoji} {cfg.name} Roster Trivia \u2014 Results",
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


class JoinRosterModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(
        self, table: RosterTable, view: "RosterTableView", balance: int,
    ) -> None:
        super().__init__(title=f"Join {table.config.name} Roster Trivia")
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

        self.table.players[uid] = RosterPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


# ── View ─────────────────────────────────────────────────────────────────────


class RosterTableView(ui.View):
    def __init__(
        self, table: RosterTable, active_tables: dict[int, RosterTable],
    ) -> None:
        super().__init__(timeout=900)  # 15 min
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        racing = phase in ("playing", "between_rounds")

        self.start_btn.disabled = (
            not betting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not betting
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = not betting
        self.close_btn.disabled = racing

    # ── Row 0: Betting ───────────────────────────────────────────────────

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
        emoji="\U0001f3ae", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Game in progress! Wait for the next game.", ephemeral=True,
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
            JoinRosterModal(self.table, self, bal),
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
        self.table.players[uid] = RosterPlayer(
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
                "Can't leave during a game!", ephemeral=True,
            )
            return
        await queries.update_casino_balance(str(uid), player.bet)
        del self.table.players[uid]
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Row 1: Close ─────────────────────────────────────────────────────

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
                "Can't close during a game! Wait for it to finish.",
                ephemeral=True,
            )
            return
        await self._close_table(interaction)

    # ── Race logic ───────────────────────────────────────────────────────

    def _pick_player(self) -> tuple[str, str, str, list[str]]:
        """Pick a random player that hasn't been used yet.

        Returns (player_name, position, team_display, accepted_answers).
        """
        cfg = self.table.config
        available = [p for p in cfg.players if p not in self.table.used_players]
        if not available:
            available = list(cfg.players.keys())
            self.table.used_players.clear()
        name = random.choice(available)
        self.table.used_players.append(name)
        pos, team_key = cfg.players[name]
        accepted = cfg.teams[team_key]
        return name, pos, team_key, accepted

    async def _start_race(self, interaction: discord.Interaction) -> None:
        table = self.table

        for uid, p in table.players.items():
            table.last_bets[uid] = (p.display_name, p.bet)

        name, pos, team, accepted = self._pick_player()
        table.current_player = name
        table.current_position = pos
        table.current_team = team
        table.current_answers = accepted
        table.round_num = 1
        table.round_winner = None
        table.round_solved.clear()
        table.phase = "playing"
        table.round_start_time = time.monotonic()

        for p in table.players.values():
            p.answer = None
            p.answer_time = None

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(table), view=self,
        )

        table.race_task = asyncio.create_task(self._race_loop())

    async def _wait_for_solve_or_timeout(self) -> bool:
        table = self.table
        deadline = table.round_start_time + ROUND_TIME

        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                return table.round_winner is not None

            wait = min(5.0, remaining)
            try:
                await asyncio.wait_for(table.round_solved.wait(), timeout=wait)
                return True
            except asyncio.TimeoutError:
                if table.round_winner is not None:
                    return True
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
                    name, pos, team, accepted = self._pick_player()
                    table.current_player = name
                    table.current_position = pos
                    table.current_team = team
                    table.current_answers = accepted
                    table.round_num = rnd
                    table.round_winner = None
                    table.round_solved.clear()
                    table.phase = "playing"
                    table.round_start_time = time.monotonic()

                    for p in table.players.values():
                        p.answer = None
                        p.answer_time = None

                    self._update_buttons()
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_playing_embed(table), view=self,
                            )
                        except discord.HTTPException:
                            pass

                solved = await self._wait_for_solve_or_timeout()
                table.total_rounds_played += 1

                if solved and table.round_winner is not None:
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_round_result_embed(table), view=self,
                            )
                        except discord.HTTPException:
                            pass
                else:
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_timeout_embed(table), view=self,
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
            await queries.log_casino_result(
                str(uid), table.config.slug, p.bet, payout,
            )

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
                title=f"{table.config.emoji} {table.config.name} Trivia \u2014 Closed",
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
                    title=f"{table.config.emoji} {table.config.name} Trivia \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ── Cog ──────────────────────────────────────────────────────────────────────


class RosterCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, RosterTable] = {}

    async def _open_table(
        self, interaction: discord.Interaction, config: SportConfig,
    ) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a roster trivia table in this channel!",
                ephemeral=True,
            )
            return

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        table = RosterTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
            config=config,
        )
        self.active_tables[channel_id] = table

        view = RosterTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()

    @app_commands.command(
        name="nba-trivia",
        description="Open an NBA Roster Trivia table (multiplayer)",
    )
    async def nba_trivia(self, interaction: discord.Interaction) -> None:
        await self._open_table(interaction, NBA_CONFIG)

    @app_commands.command(
        name="nfl-trivia",
        description="Open an NFL Roster Trivia table (multiplayer)",
    )
    async def nfl_trivia(self, interaction: discord.Interaction) -> None:
        await self._open_table(interaction, NFL_CONFIG)

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        table = self.active_tables.get(message.channel.id)
        if table is None or table.phase != "playing":
            return

        uid = message.author.id
        if uid not in table.players:
            return

        if table.round_winner is not None:
            return

        guess = message.content.strip()
        if len(guess) < 2 or guess.isdigit():
            return

        alpha_chars = sum(1 for c in guess if c.isalpha())
        if alpha_chars < len(guess) * 0.4:
            return

        if check_answer(guess, table.current_answers):
            now = time.monotonic()
            player = table.players[uid]
            player.answer = guess
            player.answer_time = now
            player.rounds_won += 1
            table.round_winner = uid

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
    await bot.add_cog(RosterCog(bot))
