"""Casino cog — /soccersim fake soccer match simulator.

Two random soccer teams are drawn. Each gets a win probability based on
team ratings. Players pick a side and bet coins. A half-by-half simulation
runs with match events (goals, cards, subs), and winners are paid fixed
odds based on the pre-game probability.

Also supports /soccersim-tournament — an 8-team mini-tournament with
group stage + knockout rounds.
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
HALF_DELAY = 2.5  # seconds between half updates
EVENT_DELAY = 1.5

# Tournament-specific pacing
GROUP_MATCH_DELAY = 1.8   # seconds between group match results
KO_HALF_DELAY = 3.5       # seconds between knockout half updates
KO_PERIOD_DELAY = 2.0     # seconds between ET / penalty phases
TOURNAMENT_TIMEOUT = 600  # 10 min view timeout (tournaments take longer)

# ── Team Data ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SoccerTeam:
    name: str
    abbr: str
    attack: int
    midfield: int
    defense: int
    goalkeeper: int
    team_type: str  # 'club' | 'national'
    players: list[str] = field(default_factory=list)


CLUB_TEAMS: list[SoccerTeam] = [
    SoccerTeam("Real Madrid", "RMA", 92, 90, 85, 88, "club",
               ["Vinicius", "Bellingham", "Rodrygo", "Valverde", "Modric",
                "Tchouameni", "Militao", "Rudiger", "Carvajal", "Courtois"]),
    SoccerTeam("Barcelona", "BAR", 90, 91, 82, 85, "club",
               ["Yamal", "Lewandowski", "Raphinha", "Pedri", "Gavi",
                "de Jong", "Kounde", "Araujo", "Balde", "ter Stegen"]),
    SoccerTeam("Man City", "MCI", 89, 92, 87, 86, "club",
               ["Haaland", "Foden", "De Bruyne", "Bernardo", "Rodri",
                "Grealish", "Dias", "Stones", "Walker", "Ederson"]),
    SoccerTeam("Arsenal", "ARS", 87, 86, 85, 84, "club",
               ["Saka", "Havertz", "Odegaard", "Rice", "Martinelli",
                "Trossard", "Saliba", "Gabriel", "White", "Raya"]),
    SoccerTeam("Liverpool", "LIV", 88, 87, 84, 87, "club",
               ["Salah", "Nunez", "Diaz", "Szoboszlai", "Mac Allister",
                "Gravenberch", "Van Dijk", "Konate", "Alexander-Arnold", "Alisson"]),
    SoccerTeam("Chelsea", "CHE", 83, 82, 83, 84, "club",
               ["Palmer", "Jackson", "Mudryk", "Enzo", "Caicedo",
                "Gallagher", "Colwill", "Fofana", "James", "Sanchez"]),
    SoccerTeam("Man United", "MUN", 82, 80, 79, 82, "club",
               ["Rashford", "Hojlund", "Garnacho", "Bruno", "Mount",
                "Casemiro", "Martinez", "Varane", "Shaw", "Onana"]),
    SoccerTeam("Bayern Munich", "BAY", 90, 88, 86, 88, "club",
               ["Kane", "Sane", "Musiala", "Muller", "Kimmich",
                "Goretzka", "Upamecano", "Kim", "Davies", "Neuer"]),
    SoccerTeam("Dortmund", "BVB", 84, 82, 78, 82, "club",
               ["Adeyemi", "Fullkrug", "Brandt", "Sabitzer", "Can",
                "Reus", "Hummels", "Schlotterbeck", "Ryerson", "Kobel"]),
    SoccerTeam("PSG", "PSG", 89, 85, 81, 83, "club",
               ["Dembele", "Kolo Muani", "Barcola", "Vitinha", "Zaaire-Emery",
                "Ruiz", "Marquinhos", "Skriniar", "Hakimi", "Donnarumma"]),
    SoccerTeam("Juventus", "JUV", 82, 83, 84, 83, "club",
               ["Vlahovic", "Chiesa", "Yildiz", "Locatelli", "Rabiot",
                "Pogba", "Bremer", "Gatti", "Danilo", "Szczesny"]),
    SoccerTeam("AC Milan", "ACM", 83, 82, 82, 83, "club",
               ["Leao", "Giroud", "Pulisic", "Loftus-Cheek", "Reijnders",
                "Bennacer", "Tomori", "Thiaw", "Theo", "Maignan"]),
    SoccerTeam("Inter Milan", "INT", 85, 84, 85, 84, "club",
               ["Lautaro", "Thuram", "Calhanoglu", "Barella", "Mkhitaryan",
                "Bastoni", "Acerbi", "Pavard", "Dimarco", "Sommer"]),
    SoccerTeam("Atletico Madrid", "ATM", 80, 82, 87, 86, "club",
               ["Griezmann", "Morata", "Correa", "Koke", "De Paul",
                "Llorente", "Savic", "Gimenez", "Hermoso", "Oblak"]),
    SoccerTeam("Ajax", "AJA", 78, 79, 74, 76, "club",
               ["Bergwijn", "Brobbey", "Berghuis", "Taylor", "Klaassen",
                "Alvarez", "Timber", "Bassey", "Blind", "Pasveer"]),
    SoccerTeam("Porto", "POR", 79, 78, 76, 78, "club",
               ["Taremi", "Galeno", "Pepe", "Conceicao", "Eustaquio",
                "Uribe", "Pepe D", "Cardoso", "Zaidu", "Costa"]),
    SoccerTeam("Benfica", "BEN", 80, 79, 77, 78, "club",
               ["Neres", "Goncalo", "Joao Mario", "Kokcu", "Aursnes",
                "Florentino", "Otamendi", "Antonio", "Grimaldo", "Trubin"]),
    SoccerTeam("Napoli", "NAP", 84, 83, 82, 82, "club",
               ["Osimhen", "Kvara", "Politano", "Zielinski", "Anguissa",
                "Lobotka", "Kim", "Rrahmani", "Di Lorenzo", "Meret"]),
    SoccerTeam("RB Leipzig", "RBL", 82, 81, 79, 80, "club",
               ["Openda", "Sesko", "Xavi", "Szoboszlai L", "Kampl",
                "Laimer", "Orban", "Gvardiol L", "Raum", "Gulacsi"]),
    SoccerTeam("Leverkusen", "LEV", 85, 84, 82, 83, "club",
               ["Wirtz", "Schick", "Diaby", "Hofmann", "Andrich",
                "Xhaka", "Tah", "Hincapie", "Frimpong", "Hradecky"]),
]

NATIONAL_TEAMS: list[SoccerTeam] = [
    SoccerTeam("Brazil", "BRA", 91, 88, 83, 84, "national",
               ["Vinicius", "Rodrygo", "Endrick", "Paqueta", "Bruno G",
                "Casemiro", "Militao", "Marquinhos", "Danilo", "Alisson"]),
    SoccerTeam("France", "FRA", 90, 89, 86, 87, "national",
               ["Mbappe", "Griezmann", "Dembele", "Tchouameni", "Rabiot",
                "Kante", "Kounde", "Upamecano", "Theo", "Maignan"]),
    SoccerTeam("England", "ENG", 88, 86, 84, 86, "national",
               ["Kane", "Saka", "Foden", "Bellingham", "Rice",
                "Alexander-Arnold", "Stones", "Maguire", "Shaw", "Pickford"]),
    SoccerTeam("Argentina", "ARG", 90, 87, 84, 85, "national",
               ["Messi", "Alvarez", "Di Maria", "De Paul", "Mac Allister",
                "Enzo", "Romero", "Otamendi", "Molina", "Martinez"]),
    SoccerTeam("Spain", "ESP", 86, 90, 85, 86, "national",
               ["Yamal", "Morata", "Olmo", "Pedri", "Rodri",
                "Gavi", "Carvajal", "Laporte", "Cucurella", "Simon"]),
    SoccerTeam("Germany", "GER", 85, 86, 83, 85, "national",
               ["Havertz", "Sane", "Musiala", "Gundogan", "Kroos",
                "Kimmich", "Rudiger", "Tah", "Raum", "Neuer"]),
    SoccerTeam("Portugal", "PRT", 88, 85, 82, 84, "national",
               ["Ronaldo", "Leao", "Bernardo", "Bruno", "Vitinha",
                "Palhinha", "Dias", "Pepe", "Cancelo", "Costa"]),
    SoccerTeam("Netherlands", "NED", 84, 85, 80, 82, "national",
               ["Gakpo", "Depay", "Simons", "Reijnders", "de Jong",
                "Schouten", "Van Dijk", "de Ligt", "Dumfries", "Verbruggen"]),
    SoccerTeam("Italy", "ITA", 82, 84, 86, 85, "national",
               ["Chiesa", "Scamacca", "Raspadori", "Barella", "Jorginho",
                "Pellegrini", "Bastoni", "Bonucci", "Di Lorenzo", "Donnarumma"]),
    SoccerTeam("Belgium", "BEL", 84, 83, 81, 83, "national",
               ["Lukaku", "Doku", "Trossard", "De Bruyne", "Tielemans",
                "Onana A", "Vertonghen", "Faes", "Castagne", "Courtois"]),
    SoccerTeam("Croatia", "CRO", 82, 85, 80, 80, "national",
               ["Kramaric", "Perisic", "Modric", "Kovacic", "Brozovic",
                "Vlasic", "Gvardiol", "Lovren", "Juranovic", "Livakovic"]),
    SoccerTeam("Uruguay", "URU", 83, 80, 82, 81, "national",
               ["Nunez", "Suarez", "Pellistri", "Valverde", "De Arrascaeta",
                "Bentancur", "Gimenez", "Araujo", "Olivera", "Rochet"]),
    SoccerTeam("USA", "USA", 78, 76, 77, 79, "national",
               ["Pulisic", "Reyna", "Weah", "McKennie", "Musah",
                "Adams", "Robinson", "Ream", "Dest", "Turner"]),
    SoccerTeam("Mexico", "MEX", 79, 78, 76, 78, "national",
               ["Lozano", "Jimenez", "Vega", "Edson", "Romo",
                "Guardado", "Araujo J", "Montes", "Sanchez J", "Ochoa"]),
    SoccerTeam("Japan", "JPN", 80, 81, 78, 79, "national",
               ["Mitoma", "Kubo", "Doan", "Kamada", "Endo",
                "Tanaka", "Tomiyasu", "Itakura", "Nagatomo", "Suzuki"]),
    SoccerTeam("Senegal", "SEN", 81, 78, 77, 78, "national",
               ["Mane", "Dia", "Sarr", "Gueye", "Kouyate",
                "N Mendy", "Koulibaly", "Diallo", "Sabaly", "E Mendy"]),
    SoccerTeam("Morocco", "MAR", 80, 80, 82, 80, "national",
               ["Hakimi A", "En-Nesyri", "Ziyech", "Amrabat", "Ounahi",
                "Boufal", "Saiss", "Aguerd", "Mazraoui", "Bounou"]),
    SoccerTeam("Australia", "AUS", 75, 74, 74, 76, "national",
               ["Goodwin", "Duke", "Leckie", "Mooy", "Irvine",
                "McGree", "Souttar", "Wright", "Behich", "Ryan"]),
    SoccerTeam("Colombia", "COL", 82, 80, 78, 79, "national",
               ["Luis Diaz", "Duran", "James", "Arias", "Lerma",
                "Barrios", "Sanchez D", "Lucumi", "Mojica", "Vargas"]),
    SoccerTeam("Denmark", "DEN", 81, 82, 80, 81, "national",
               ["Hojlund", "Skov Olsen", "Eriksen", "Hojbjerg", "Delaney",
                "Lindstrom", "Christensen", "Andersen", "Maehle", "Schmeichel"]),
]

ALL_TEAMS: list[SoccerTeam] = CLUB_TEAMS + NATIONAL_TEAMS

# Lookup dicts for commands
_TEAM_BY_NAME: dict[str, SoccerTeam] = {t.name.lower(): t for t in ALL_TEAMS}
_TEAM_BY_ABBR: dict[str, SoccerTeam] = {t.abbr.lower(): t for t in ALL_TEAMS}


def _find_team(query: str) -> SoccerTeam | None:
    q = query.strip().lower()
    if q in _TEAM_BY_NAME:
        return _TEAM_BY_NAME[q]
    if q in _TEAM_BY_ABBR:
        return _TEAM_BY_ABBR[q]
    # Partial match
    for name, team in _TEAM_BY_NAME.items():
        if q in name:
            return team
    return None


# ── Match Engine ─────────────────────────────────────────────────────────────


def _team_strength(team: SoccerTeam) -> float:
    return (
        team.attack * 0.30
        + team.midfield * 0.25
        + team.defense * 0.30
        + team.goalkeeper * 0.15
    )


def _generate_win_prob(home: SoccerTeam, away: SoccerTeam) -> float:
    """Generate home-team win probability based on strength diff + home boost."""
    h_str = _team_strength(home)
    a_str = _team_strength(away)
    diff = h_str - a_str
    # Sigmoid-ish: convert strength diff to probability
    # Small home boost (+2% baseline)
    raw = 0.50 + 0.02 + diff / 100.0
    return max(0.15, min(0.85, raw))


def _payout_multiplier(prob: float) -> float:
    return 1.0 / prob


def _prob_to_american(prob: float) -> str:
    if prob >= 0.5:
        odds = -round(prob / (1 - prob) * 100)
        return str(odds)
    else:
        odds = round((1 - prob) / prob * 100)
        return f"+{odds}"


@dataclass
class MatchEvent:
    minute: int
    event_type: str  # goal | yellow | red | sub
    team_name: str
    player: str
    detail: str = ""


def _event_line(event: MatchEvent) -> str:
    emoji_map = {
        "goal": "\u26bd", "yellow": "\U0001f7e8", "red": "\U0001f7e5",
        "sub": "\U0001f504", "save": "\U0001f9e4", "miss": "\U0001f4a8",
        "crossbar": "\U0001f6a7", "chance": "\U0001f525",
    }
    emoji = emoji_map.get(event.event_type, "\u2753")
    detail = f" {event.detail}" if event.detail else ""
    return f"`{event.minute}'` {emoji} **{event.team_name}** \u2014 {event.player}{detail}"


def _pick_player(team: SoccerTeam) -> str:
    if team.players:
        return random.choice(team.players)
    return "Unknown"


def _simulate_half(
    home: SoccerTeam, away: SoccerTeam, half_num: int, home_prob: float,
) -> tuple[int, int, list[MatchEvent]]:
    """Simulate one half. Returns (home_goals, away_goals, events)."""
    events: list[MatchEvent] = []
    minute_base = 0 if half_num == 1 else 45

    # Expected ~1.3 goals per team per match → ~0.65 per half
    home_xg = 0.65 * (home.attack / 85.0) * (85.0 / max(away.defense, 1))
    away_xg = 0.65 * (away.attack / 85.0) * (85.0 / max(home.defense, 1))

    home_goals = 0
    away_goals = 0

    # Generate goal events
    for _ in range(int(home_xg) + 1):
        if random.random() < home_xg / max(int(home_xg) + 1, 1):
            minute = random.randint(minute_base + 1, minute_base + 45)
            home_goals += 1
            events.append(MatchEvent(
                minute=minute, event_type="goal",
                team_name=home.abbr, player=_pick_player(home),
            ))

    for _ in range(int(away_xg) + 1):
        if random.random() < away_xg / max(int(away_xg) + 1, 1):
            minute = random.randint(minute_base + 1, minute_base + 45)
            away_goals += 1
            events.append(MatchEvent(
                minute=minute, event_type="goal",
                team_name=away.abbr, player=_pick_player(away),
            ))

    # Yellow cards: 0-2 per half per team
    for team in (home, away):
        n_yellows = random.choices([0, 1, 2], weights=[50, 35, 15])[0]
        for _ in range(n_yellows):
            minute = random.randint(minute_base + 1, minute_base + 45)
            events.append(MatchEvent(
                minute=minute, event_type="yellow",
                team_name=team.abbr, player=_pick_player(team),
            ))

    # Red cards: ~5% chance per match → ~2.5% per half
    for team in (home, away):
        if random.random() < 0.025:
            minute = random.randint(minute_base + 1, minute_base + 45)
            events.append(MatchEvent(
                minute=minute, event_type="red",
                team_name=team.abbr, player=_pick_player(team),
            ))

    # Near misses: saves, shots wide, crossbar — adds drama
    for team, opp in ((home, away), (away, home)):
        attack_factor = team.attack / 85.0
        # 1-3 chances per half depending on attack rating
        n_chances = random.choices([1, 2, 3], weights=[30, 50, 20])[0]
        n_chances = max(1, int(n_chances * attack_factor))
        for _ in range(n_chances):
            minute = random.randint(minute_base + 1, minute_base + 45)
            chance_type = random.choices(
                ["save", "miss", "crossbar", "chance"],
                weights=[40, 30, 10, 20],
            )[0]
            detail_map = {
                "save": random.choice(["great save!", "fingertip save", "blocked", "point blank save"]),
                "miss": random.choice(["shot wide", "blazed over", "dragged wide", "headed over"]),
                "crossbar": random.choice(["hits the bar!", "off the post!", "rattles the woodwork!"]),
                "chance": random.choice(["dangerous cross", "through ball!", "counter attack", "free kick"]),
            }
            events.append(MatchEvent(
                minute=minute, event_type=chance_type,
                team_name=team.abbr, player=_pick_player(team),
                detail=detail_map[chance_type],
            ))

    # Substitutions: 1-2 in second half only
    if half_num == 2:
        for team in (home, away):
            n_subs = random.randint(1, 2)
            for _ in range(n_subs):
                minute = random.randint(minute_base + 1, minute_base + 45)
                events.append(MatchEvent(
                    minute=minute, event_type="sub",
                    team_name=team.abbr, player=_pick_player(team),
                    detail="on",
                ))

    events.sort(key=lambda e: e.minute)
    return home_goals, away_goals, events


def _simulate_extra_time(
    home: SoccerTeam, away: SoccerTeam, home_prob: float,
) -> tuple[int, int, list[MatchEvent]]:
    """Simulate 30 min extra time (lower scoring)."""
    events: list[MatchEvent] = []
    home_xg = 0.30 * (home.attack / 85.0) * (85.0 / max(away.defense, 1))
    away_xg = 0.30 * (away.attack / 85.0) * (85.0 / max(home.defense, 1))

    home_goals = 1 if random.random() < home_xg else 0
    away_goals = 1 if random.random() < away_xg else 0

    if home_goals:
        minute = random.randint(91, 120)
        events.append(MatchEvent(
            minute=minute, event_type="goal",
            team_name=home.abbr, player=_pick_player(home),
        ))
    if away_goals:
        minute = random.randint(91, 120)
        events.append(MatchEvent(
            minute=minute, event_type="goal",
            team_name=away.abbr, player=_pick_player(away),
        ))

    events.sort(key=lambda e: e.minute)
    return home_goals, away_goals, events


def _simulate_penalties(
    home: SoccerTeam, away: SoccerTeam,
) -> tuple[int, int, list[str]]:
    """Simulate penalty shootout. Returns (home_pens, away_pens, log_lines).
    Always produces a winner."""
    base_save_rate = 0.25
    home_save = base_save_rate + (home.goalkeeper - 80) * 0.005
    away_save = base_save_rate + (away.goalkeeper - 80) * 0.005
    home_save = max(0.10, min(0.40, home_save))
    away_save = max(0.10, min(0.40, away_save))

    home_score = 0
    away_score = 0
    log: list[str] = []

    # First 5 rounds
    for rnd in range(1, 6):
        # Home takes
        shooter = _pick_player(home)
        if random.random() > away_save:
            home_score += 1
            log.append(f"Round {rnd}: {home.abbr} {shooter} \u26bd GOAL")
        else:
            log.append(f"Round {rnd}: {home.abbr} {shooter} \u274c SAVED")
        # Away takes
        shooter = _pick_player(away)
        if random.random() > home_save:
            away_score += 1
            log.append(f"Round {rnd}: {away.abbr} {shooter} \u26bd GOAL")
        else:
            log.append(f"Round {rnd}: {away.abbr} {shooter} \u274c SAVED")

    # Sudden death if tied
    sd_round = 6
    while home_score == away_score:
        h_shooter = _pick_player(home)
        a_shooter = _pick_player(away)
        h_scored = random.random() > away_save
        a_scored = random.random() > home_save
        if h_scored:
            home_score += 1
            log.append(f"SD {sd_round}: {home.abbr} {h_shooter} \u26bd GOAL")
        else:
            log.append(f"SD {sd_round}: {home.abbr} {h_shooter} \u274c SAVED")
        if a_scored:
            away_score += 1
            log.append(f"SD {sd_round}: {away.abbr} {a_shooter} \u26bd GOAL")
        else:
            log.append(f"SD {sd_round}: {away.abbr} {a_shooter} \u274c SAVED")
        sd_round += 1

    return home_score, away_score, log


def _sim_group_match(
    home: SoccerTeam, away: SoccerTeam,
) -> tuple[int, int]:
    """Instant result for group-stage match (no events/delays)."""
    home_prob = _generate_win_prob(home, away)
    h1, a1, _ = _simulate_half(home, away, 1, home_prob)
    h2, a2, _ = _simulate_half(home, away, 2, home_prob)
    return h1 + h2, a1 + a2


# ── Single Match Dataclasses ────────────────────────────────────────────────


@dataclass
class SoccerSimPlayer:
    user_id: int
    display_name: str
    bet: int
    side: str  # "home" or "away"
    payout: int = 0
    won: bool = False


@dataclass
class SoccerSimTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | finished
    home_team: SoccerTeam | None = None
    away_team: SoccerTeam | None = None
    home_prob: float = 0.5
    players: dict[int, SoccerSimPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 1
    last_bets: dict[int, tuple[str, int, str]] = field(default_factory=dict)
    home_score: int = 0
    away_score: int = 0
    half: int = 0
    events: list[MatchEvent] = field(default_factory=list)
    sim_task: asyncio.Task | None = field(default=None, repr=False)
    extra_time_played: bool = False
    penalty_score: tuple[int, int] | None = None


# ── Single Match Embeds ─────────────────────────────────────────────────────


def _ratings_bar(team: SoccerTeam) -> str:
    return f"ATK {team.attack} | MID {team.midfield} | DEF {team.defense} | GK {team.goalkeeper}"


def _betting_embed(table: SoccerSimTable) -> discord.Embed:
    home = table.home_team
    away = table.away_team
    total_wagered = sum(p.bet for p in table.players.values())
    away_prob = 1 - table.home_prob

    home_odds = _prob_to_american(table.home_prob)
    away_odds = _prob_to_american(away_prob)
    home_mult = _payout_multiplier(table.home_prob)
    away_mult = _payout_multiplier(away_prob)

    embed = discord.Embed(
        title=f"\u26bd Soccer Sim \u2014 Place Your Bets (Round {table.round_num})",
        description=(
            "Pick a side and bet coins on the outcome!\n"
            "Odds are based on each team's simulated win probability."
        ),
        colour=discord.Colour.dark_green(),
    )

    matchup_text = (
        f"**{away.abbr}** {away.name}  ({away.team_type})\n"
        f"\u2003{_ratings_bar(away)}\n"
        f"\u2003Win: {away_prob * 100:.0f}% ({away_odds}) \u2014 **{away_mult:.1f}x** payout\n\n"
        f"**{home.abbr}** {home.name}  ({home.team_type})\n"
        f"\u2003{_ratings_bar(home)}\n"
        f"\u2003Win: {table.home_prob * 100:.0f}% ({home_odds}) \u2014 **{home_mult:.1f}x** payout"
    )
    embed.add_field(name=f"{away.abbr} @ {home.abbr}", value=matchup_text, inline=False)

    if total_wagered:
        embed.add_field(name="Total Wagered", value=f"{total_wagered}c", inline=True)

    if table.players:
        player_lines = []
        for p in table.players.values():
            side_abbr = home.abbr if p.side == "home" else away.abbr
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


def _period_label(table: SoccerSimTable) -> str:
    if table.penalty_score is not None:
        return "Penalties"
    if table.extra_time_played:
        return "Extra Time"
    if table.half == 1:
        return "1st Half"
    if table.half == 2:
        return "2nd Half"
    return "Pre-Match"


def _playing_embed(table: SoccerSimTable) -> discord.Embed:
    home = table.home_team
    away = table.away_team

    label = _period_label(table)
    embed = discord.Embed(
        title=f"\u26bd Soccer Sim \u2014 {away.abbr} vs {home.abbr} ({label})",
        colour=discord.Colour.gold(),
    )

    score_line = f"**{away.abbr}** {table.away_score} \u2014 {table.home_score} **{home.abbr}**"
    if table.penalty_score:
        ph, pa = table.penalty_score
        score_line += f"\n(Penalties: {away.abbr} {pa} \u2014 {ph} {home.abbr})"
    embed.description = score_line

    if table.events:
        event_text = "\n".join(_event_line(e) for e in table.events[-15:])
        embed.add_field(name="Match Events", value=event_text, inline=False)

    bet_lines: list[str] = []
    for p in table.players.values():
        side_abbr = home.abbr if p.side == "home" else away.abbr
        bet_lines.append(f"**{p.display_name}** \u2014 {p.bet}c on {side_abbr}")
    if bet_lines:
        embed.add_field(name="Bets", value="\n".join(bet_lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _finished_embed(
    table: SoccerSimTable, *, balances: dict[int, int] | None = None,
) -> discord.Embed:
    home = table.home_team
    away = table.away_team

    # Determine winner
    h_total = table.home_score
    a_total = table.away_score
    pen = table.penalty_score
    if pen:
        ph, pa = pen
        if ph > pa:
            winner_abbr = home.abbr
        else:
            winner_abbr = away.abbr
        score_text = (
            f"{away.abbr} {a_total} \u2014 {h_total} {home.abbr} "
            f"(Pens: {away.abbr} {pa} \u2014 {ph} {home.abbr})"
        )
    elif h_total > a_total:
        winner_abbr = home.abbr
        score_text = f"{away.abbr} {a_total} \u2014 {h_total} {home.abbr}"
    elif a_total > h_total:
        winner_abbr = away.abbr
        score_text = f"{away.abbr} {a_total} \u2014 {h_total} {home.abbr}"
    else:
        winner_abbr = None  # draw
        score_text = f"{away.abbr} {a_total} \u2014 {h_total} {home.abbr}"

    extra = ""
    if table.extra_time_played:
        extra = " (AET)"
    if pen:
        extra = " (Pens)"

    if winner_abbr is not None:
        result_line = f"\U0001f3c6 **{winner_abbr}** wins! {score_text}"
        colour = discord.Colour.green()
    else:
        result_line = f"\U0001f91d Draw! {score_text}"
        colour = discord.Colour.gold()

    embed = discord.Embed(
        title=f"\u26bd Soccer Sim \u2014 Full Time{extra} (Round {table.round_num})",
        description=result_line,
        colour=colour,
    )

    if table.events:
        event_text = "\n".join(_event_line(e) for e in table.events[-20:])
        embed.add_field(name="Match Events", value=event_text, inline=False)

    lines: list[str] = []
    for p in table.players.values():
        bal = balances.get(p.user_id, 0) if balances else 0
        side_abbr = home.abbr if p.side == "home" else away.abbr
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


# ── Tournament Dataclasses ──────────────────────────────────────────────────


@dataclass
class TournamentPlayer:
    user_id: int
    display_name: str
    bet: int
    team_name: str  # which team they bet on to win
    payout: int = 0
    won: bool = False


@dataclass
class TournamentTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | finished
    team_pool: str = "mixed"  # clubs | national | mixed
    teams: list[SoccerTeam] = field(default_factory=list)
    groups: dict[str, list[SoccerTeam]] = field(default_factory=dict)
    group_results: dict[str, list[dict]] = field(default_factory=dict)
    knockout_bracket: list = field(default_factory=list)
    current_stage: str = "groups"  # groups | semis | final | finished
    players: dict[int, TournamentPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    last_bets: dict[int, tuple[str, int, str]] = field(default_factory=dict)
    round_num: int = 1
    sim_task: asyncio.Task | None = field(default=None, repr=False)


# ── Tournament Logic ────────────────────────────────────────────────────────


def _draw_groups(teams: list[SoccerTeam]) -> dict[str, list[SoccerTeam]]:
    shuffled = list(teams)
    random.shuffle(shuffled)
    return {"A": shuffled[:4], "B": shuffled[4:8]}


def _empty_standing(team: SoccerTeam) -> dict:
    return {
        "team": team, "P": 0, "W": 0, "D": 0, "L": 0,
        "GF": 0, "GA": 0, "Pts": 0,
    }


def _group_fixtures(
    groups: dict[str, list[SoccerTeam]],
) -> list[tuple[str, SoccerTeam, SoccerTeam]]:
    """Return all group match fixtures as (group_name, home, away) tuples."""
    fixtures: list[tuple[str, SoccerTeam, SoccerTeam]] = []
    for grp_name, grp_teams in groups.items():
        for i in range(len(grp_teams)):
            for j in range(i + 1, len(grp_teams)):
                fixtures.append((grp_name, grp_teams[i], grp_teams[j]))
    return fixtures


def _init_group_standings(
    groups: dict[str, list[SoccerTeam]],
) -> dict[str, dict[str, dict]]:
    """Initialize empty standings dicts per group."""
    return {
        grp_name: {t.abbr: _empty_standing(t) for t in grp_teams}
        for grp_name, grp_teams in groups.items()
    }


def _apply_match_result(
    standings: dict[str, dict], home_abbr: str, away_abbr: str,
    hg: int, ag: int,
) -> None:
    """Apply a single match result to standings in-place."""
    standings[home_abbr]["P"] += 1
    standings[away_abbr]["P"] += 1
    standings[home_abbr]["GF"] += hg
    standings[home_abbr]["GA"] += ag
    standings[away_abbr]["GF"] += ag
    standings[away_abbr]["GA"] += hg
    if hg > ag:
        standings[home_abbr]["W"] += 1
        standings[home_abbr]["Pts"] += 3
        standings[away_abbr]["L"] += 1
    elif hg < ag:
        standings[away_abbr]["W"] += 1
        standings[away_abbr]["Pts"] += 3
        standings[home_abbr]["L"] += 1
    else:
        standings[home_abbr]["D"] += 1
        standings[away_abbr]["D"] += 1
        standings[home_abbr]["Pts"] += 1
        standings[away_abbr]["Pts"] += 1


def _sorted_standings(standings: dict[str, dict]) -> list[dict]:
    return sorted(
        standings.values(),
        key=lambda s: (s["Pts"], s["GF"] - s["GA"], s["GF"]),
        reverse=True,
    )


def _run_group_stage(
    groups: dict[str, list[SoccerTeam]],
) -> dict[str, list[dict]]:
    """Round-robin each group. Returns standings sorted by Pts > GD > GF."""
    all_standings = _init_group_standings(groups)
    for grp_name, home, away in _group_fixtures(groups):
        hg, ag = _sim_group_match(home, away)
        _apply_match_result(all_standings[grp_name], home.abbr, away.abbr, hg, ag)
    return {
        grp_name: _sorted_standings(st)
        for grp_name, st in all_standings.items()
    }


def _group_standings_text(standings: list[dict]) -> str:
    header = f"{'Team':<5s} {'P':>2s} {'W':>2s} {'D':>2s} {'L':>2s} {'GF':>3s} {'GA':>3s} {'GD':>3s} {'Pts':>3s}"
    lines = [header]
    for s in standings:
        t = s["team"]
        gd = s["GF"] - s["GA"]
        gd_str = f"+{gd}" if gd > 0 else str(gd)
        lines.append(
            f"{t.abbr:<5s} {s['P']:>2d} {s['W']:>2d} {s['D']:>2d} {s['L']:>2d} "
            f"{s['GF']:>3d} {s['GA']:>3d} {gd_str:>3s} {s['Pts']:>3d}"
        )
    return "```\n" + "\n".join(lines) + "\n```"


def _tournament_team_win_prob(team: SoccerTeam, all_teams: list[SoccerTeam]) -> float:
    """Estimate probability of a team winning the tournament from strength."""
    strengths = [_team_strength(t) for t in all_teams]
    total = sum(strengths)
    if total == 0:
        return 1.0 / len(all_teams)
    my_str = _team_strength(team)
    # Squaring to reward stronger teams more
    sq_total = sum(s ** 2 for s in strengths)
    return (my_str ** 2) / sq_total if sq_total > 0 else 1.0 / len(all_teams)


def _tournament_group_embed(
    table: TournamentTable, *,
    match_log: list[str] | None = None,
    current_match: str | None = None,
) -> discord.Embed:
    title = f"\u26bd Soccer Tournament \u2014 Group Stage (Round {table.round_num})"
    if current_match:
        title = f"\u26bd Soccer Tournament \u2014 Groups: {current_match}"
    embed = discord.Embed(title=title, colour=discord.Colour.dark_green())
    for grp_name in ("A", "B"):
        standings = table.group_results.get(grp_name, [])
        if standings:
            embed.add_field(
                name=f"Group {grp_name}",
                value=_group_standings_text(standings),
                inline=False,
            )
        else:
            teams = table.groups.get(grp_name, [])
            embed.add_field(
                name=f"Group {grp_name}",
                value=", ".join(t.abbr for t in teams),
                inline=False,
            )

    if match_log:
        # Show last 6 results to avoid embed overflow
        log_text = "\n".join(match_log[-6:])
        embed.add_field(name="Results", value=log_text, inline=False)

    if table.players:
        lines = []
        for p in table.players.values():
            lines.append(f"\U0001f3b0 **{p.display_name}** \u2014 {p.bet}c on **{p.team_name}**")
        embed.add_field(name="Bets", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Bets", value="*No players yet \u2014 click Join!*", inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _tournament_live_ko_embed(
    table: TournamentTable, stage: str,
    home: SoccerTeam, away: SoccerTeam,
    home_score: int, away_score: int,
    events: list[MatchEvent],
    period: str,
    *,
    prev_results: list[tuple[str, str, str]] | None = None,
    pen_score: tuple[int, int] | None = None,
) -> discord.Embed:
    """Live match embed during a knockout game in tournament."""
    embed = discord.Embed(
        title=f"\u26bd {stage} \u2014 {away.abbr} vs {home.abbr} ({period})",
        colour=discord.Colour.gold(),
    )
    score_line = f"**{away.abbr}** {away_score} \u2014 {home_score} **{home.abbr}**"
    if pen_score:
        ph, pa = pen_score
        score_line += f"\n(Pens: {away.abbr} {pa} \u2014 {ph} {home.abbr})"
    embed.description = score_line

    if events:
        event_text = "\n".join(_event_line(e) for e in events[-12:])
        embed.add_field(name="Match Events", value=event_text, inline=False)

    if prev_results:
        prev_text = "\n".join(f"{m} \u2014 {s} \u2192 **{w}**" for m, s, w in prev_results)
        embed.add_field(name="Earlier Results", value=prev_text, inline=False)

    embed.set_footer(text=f"Host: {table.host_name} \u2502 Round {table.round_num}")
    return embed


def _tournament_knockout_embed(
    table: TournamentTable, stage: str, results: list[tuple[str, str, str]],
) -> discord.Embed:
    """stage = 'Semi-Finals' or 'Final'. results = [(matchup_str, score_str, winner), ...]"""
    embed = discord.Embed(
        title=f"\u26bd Soccer Tournament \u2014 {stage} (Round {table.round_num})",
        colour=discord.Colour.gold(),
    )
    for grp_name in ("A", "B"):
        standings = table.group_results.get(grp_name, [])
        if standings:
            embed.add_field(
                name=f"Group {grp_name}", value=_group_standings_text(standings), inline=False,
            )
    match_text = "\n".join(f"{m} \u2014 {s} \u2192 **{w}**" for m, s, w in results)
    embed.add_field(name=stage, value=match_text, inline=False)

    if table.players:
        lines = []
        for p in table.players.values():
            lines.append(f"\U0001f3b0 **{p.display_name}** \u2014 {p.bet}c on **{p.team_name}**")
        embed.add_field(name="Bets", value="\n".join(lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _tournament_final_embed(
    table: TournamentTable, winner: SoccerTeam, *,
    semis: list[tuple[str, str, str]],
    final_result: tuple[str, str, str],
    balances: dict[int, int] | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"\u26bd Soccer Tournament \u2014 Complete (Round {table.round_num})",
        description=f"\U0001f3c6 **{winner.name}** ({winner.abbr}) wins the tournament!",
        colour=discord.Colour.green(),
    )
    for grp_name in ("A", "B"):
        standings = table.group_results.get(grp_name, [])
        if standings:
            embed.add_field(
                name=f"Group {grp_name}", value=_group_standings_text(standings), inline=False,
            )
    semi_text = "\n".join(f"{m} \u2014 {s} \u2192 **{w}**" for m, s, w in semis)
    embed.add_field(name="Semi-Finals", value=semi_text, inline=False)

    fm, fs, fw = final_result
    embed.add_field(name="Final", value=f"{fm} \u2014 {fs} \u2192 **{fw}**", inline=False)

    lines: list[str] = []
    for p in table.players.values():
        bal = balances.get(p.user_id, 0) if balances else 0
        net = p.payout - p.bet
        sign = "+" if net >= 0 else ""
        if p.won:
            lines.append(
                f"\U0001f3c6 **{p.display_name}** ({p.team_name}) \u2014 "
                f"{p.bet}c \u2192 {p.payout}c (**{sign}{net}c**) \u2014 bal: {bal}c"
            )
        else:
            lines.append(
                f"\u274c **{p.display_name}** ({p.team_name}) \u2014 "
                f"{p.bet}c \u2192 0c (**-{p.bet}c**) \u2014 bal: {bal}c"
            )
    if lines:
        embed.add_field(name="Results", value="\n".join(lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Knockout match helper ────────────────────────────────────────────────────


def _sim_knockout_match(
    home: SoccerTeam, away: SoccerTeam,
) -> tuple[SoccerTeam, str]:
    """Simulate a knockout match (with ET + pens if needed).
    Returns (winner, score_string)."""
    home_prob = _generate_win_prob(home, away)
    h1, a1, _ = _simulate_half(home, away, 1, home_prob)
    h2, a2, _ = _simulate_half(home, away, 2, home_prob)
    hg = h1 + h2
    ag = a1 + a2

    if hg != ag:
        winner = home if hg > ag else away
        return winner, f"{hg}-{ag}"

    # Extra time
    eth, eta, _ = _simulate_extra_time(home, away, home_prob)
    hg += eth
    ag += eta
    if hg != ag:
        winner = home if hg > ag else away
        return winner, f"{hg}-{ag} AET"

    # Penalties
    ph, pa, _ = _simulate_penalties(home, away)
    winner = home if ph > pa else away
    return winner, f"{hg}-{ag} ({ph}-{pa} Pens)"


# ── Modals ──────────────────────────────────────────────────────────────────


class JoinSoccerSimModal(ui.Modal):
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
        self, table: SoccerSimTable, view: "SoccerSimTableView", balance: int,
    ) -> None:
        home = table.home_team
        away = table.away_team
        super().__init__(title=f"Soccer Sim \u2014 {away.abbr} @ {home.abbr}")
        self.table = table
        self.table_view = view
        self.amount.placeholder = f"e.g. 100 (bal: {balance}c)"
        self.side_input.placeholder = f"home ({home.abbr}) / away ({away.abbr})"

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
        home = self.table.home_team
        away = self.table.away_team
        if raw in ("home", "h", home.abbr.lower()):
            side = "home"
        elif raw in ("away", "a", away.abbr.lower()):
            side = "away"
        else:
            await interaction.response.send_message(
                f"Enter **home** ({home.abbr}) or **away** ({away.abbr}).",
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

        self.table.players[uid] = SoccerSimPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
            side=side,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


class JoinTournamentModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )
    team_input = ui.TextInput(
        label="Team to bet on (name or abbreviation)",
        placeholder="e.g. Brazil or BRA",
        required=True,
        max_length=20,
    )

    def __init__(
        self, table: TournamentTable, view: "TournamentView", balance: int,
    ) -> None:
        super().__init__(title="Soccer Tournament \u2014 Pick a Winner")
        self.table = table
        self.table_view = view
        team_names = ", ".join(t.abbr for t in table.teams)
        self.amount.placeholder = f"e.g. 100 (bal: {balance}c)"
        self.team_input.placeholder = f"Pick from: {team_names}"[:100]

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

        raw = self.team_input.value.strip().lower()
        picked = None
        for t in self.table.teams:
            if raw in (t.name.lower(), t.abbr.lower()):
                picked = t
                break
        if picked is None:
            # Partial match
            for t in self.table.teams:
                if raw in t.name.lower():
                    picked = t
                    break
        if picked is None:
            valid = ", ".join(t.abbr for t in self.table.teams)
            await interaction.response.send_message(
                f"Team not found. Valid teams: {valid}", ephemeral=True,
            )
            return

        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message(
                "You're already in this tournament!", ephemeral=True,
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

        self.table.players[uid] = TournamentPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
            team_name=picked.abbr,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_tournament_group_embed(self.table), view=self.table_view,
        )


# ── Views ───────────────────────────────────────────────────────────────────


class SoccerSimTableView(ui.View):
    def __init__(
        self, table: SoccerSimTable, active_tables: dict,
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
        label="Join", style=discord.ButtonStyle.primary, emoji="\u26bd", row=0,
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
            JoinSoccerSimModal(self.table, self, bal),
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
        self.table.players[uid] = SoccerSimPlayer(
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
                    log.exception("Unhandled error in soccersim.py")
        await self._close(interaction, "Table closed by host.")

    # ── Game logic ───────────────────────────────────────────────────────────

    async def _start_sim(self, interaction: discord.Interaction) -> None:
        table = self.table
        table.phase = "playing"
        table.half = 1
        table.home_score = 0
        table.away_score = 0
        table.events = []
        table.extra_time_played = False
        table.penalty_score = None

        self._update_buttons()
        await interaction.response.edit_message(
            embed=_playing_embed(table), view=self,
        )
        table.sim_task = asyncio.create_task(self._sim_loop())

    async def _sim_loop(self) -> None:
        table = self.table
        try:
            # 1st half
            await asyncio.sleep(HALF_DELAY)
            table.half = 1
            hg, ag, evts = _simulate_half(
                table.home_team, table.away_team, 1, table.home_prob,
            )
            table.home_score += hg
            table.away_score += ag
            table.events.extend(evts)

            if table.message:
                try:
                    await table.message.edit(
                        embed=_playing_embed(table), view=self,
                    )
                except discord.HTTPException:
                    pass

            # 2nd half
            await asyncio.sleep(HALF_DELAY)
            table.half = 2
            hg, ag, evts = _simulate_half(
                table.home_team, table.away_team, 2, table.home_prob,
            )
            table.home_score += hg
            table.away_score += ag
            table.events.extend(evts)

            if table.message:
                try:
                    await table.message.edit(
                        embed=_playing_embed(table), view=self,
                    )
                except discord.HTTPException:
                    pass

            # In a standard single match, draws are valid — no ET/pens needed
            # Winner is determined; draws mean nobody wins their bet
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

        h = table.home_score
        a = table.away_score
        pen = table.penalty_score

        if pen:
            ph, pa = pen
            home_won = ph > pa
        elif h != a:
            home_won = h > a
        else:
            # Draw — nobody wins
            home_won = None

        balances: dict[int, int] = {}
        for uid, player in table.players.items():
            if home_won is not None:
                if (player.side == "home" and home_won) or (player.side == "away" and not home_won):
                    player.won = True
                    prob = table.home_prob if player.side == "home" else (1 - table.home_prob)
                    player.payout = int(player.bet * _payout_multiplier(prob))

            if player.won and player.payout > 0:
                balances[uid] = await queries.update_casino_balance(
                    str(uid), player.payout,
                )
            else:
                bal = await queries.get_casino_balance(str(uid))
                balances[uid] = bal or 0
            await queries.log_casino_result(
                str(uid), "soccersim", player.bet, player.payout,
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
        home, away = random.sample(ALL_TEAMS, 2)
        table.home_team = home
        table.away_team = away
        table.home_prob = _generate_win_prob(home, away)
        table.half = 0
        table.home_score = 0
        table.away_score = 0
        table.events.clear()
        table.extra_time_played = False
        table.penalty_score = None
        table.sim_task = None

    async def _refund_all(self) -> None:
        for p in self.table.players.values():
            try:
                await queries.update_casino_balance(str(p.user_id), p.bet)
            except Exception:
                log.exception("Unhandled error in soccersim.py")

    async def _close(
        self, interaction: discord.Interaction, reason: str,
    ) -> None:
        embed = discord.Embed(
            title="\u26bd Soccer Sim Table \u2014 Closed",
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
                        title="\u26bd Soccer Sim Table \u2014 Timed Out",
                        description="Table timed out between rounds.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    log.exception("Unhandled error in soccersim.py")
            return

        await self._refund_all()
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="\u26bd Soccer Sim Table \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("Unhandled error in soccersim.py")


# ── Tournament View ─────────────────────────────────────────────────────────


class TournamentView(ui.View):
    def __init__(
        self, table: TournamentTable, active_tables: dict,
    ) -> None:
        super().__init__(timeout=TOURNAMENT_TIMEOUT)
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
        self.leave_btn.disabled = playing
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
        self.table.phase = "playing"
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_tournament_group_embed(self.table), view=self,
        )
        self.table.sim_task = asyncio.create_task(self._tournament_loop())

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary, emoji="\u26bd", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Tournament in progress!", ephemeral=True,
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
            JoinTournamentModal(self.table, self, bal),
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
                "You're not in this tournament.", ephemeral=True,
            )
            return
        if self.table.phase == "playing":
            await interaction.response.send_message(
                "Can't leave mid-tournament!", ephemeral=True,
            )
            return
        if self.table.phase == "betting":
            await queries.update_casino_balance(str(uid), player.bet)
            del self.table.players[uid]
            self._update_buttons()
            await interaction.response.edit_message(
                embed=_tournament_group_embed(self.table), view=self,
            )
            return
        await interaction.response.send_message(
            "Tournament is over.", ephemeral=True,
        )

    @ui.button(
        label="Close", style=discord.ButtonStyle.danger, emoji="\u2716\ufe0f", row=0,
    )
    async def close_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can close!", ephemeral=True,
            )
            return
        if self.table.phase == "playing":
            await interaction.response.send_message(
                "Can't close mid-tournament!", ephemeral=True,
            )
            return
        if self.table.phase == "betting":
            for p in self.table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    log.exception("Unhandled error in soccersim.py")
        await self._close(interaction, "Tournament closed by host.")

    # ── Tournament logic ─────────────────────────────────────────────────────

    async def _edit_msg(self, embed: discord.Embed) -> None:
        if self.table.message:
            try:
                await self.table.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    async def _sim_ko_match_live(
        self, home: SoccerTeam, away: SoccerTeam, stage: str, *,
        prev_results: list[tuple[str, str, str]] | None = None,
    ) -> tuple[SoccerTeam, str, list[MatchEvent]]:
        """Simulate a knockout match with live half-by-half embed updates.
        Returns (winner, score_string, all_events)."""
        table = self.table
        home_prob = _generate_win_prob(home, away)
        all_events: list[MatchEvent] = []
        h_total = 0
        a_total = 0

        # 1st half
        await self._edit_msg(_tournament_live_ko_embed(
            table, stage, home, away, 0, 0, [], "1st Half",
            prev_results=prev_results,
        ))
        await asyncio.sleep(KO_HALF_DELAY)

        hg, ag, evts = _simulate_half(home, away, 1, home_prob)
        h_total += hg
        a_total += ag
        all_events.extend(evts)

        await self._edit_msg(_tournament_live_ko_embed(
            table, stage, home, away, h_total, a_total, all_events, "Half-Time",
            prev_results=prev_results,
        ))
        await asyncio.sleep(KO_HALF_DELAY)

        # 2nd half
        hg, ag, evts = _simulate_half(home, away, 2, home_prob)
        h_total += hg
        a_total += ag
        all_events.extend(evts)

        await self._edit_msg(_tournament_live_ko_embed(
            table, stage, home, away, h_total, a_total, all_events, "Full Time",
            prev_results=prev_results,
        ))

        if h_total != a_total:
            winner = home if h_total > a_total else away
            return winner, f"{h_total}-{a_total}", all_events

        # Extra time
        await asyncio.sleep(KO_PERIOD_DELAY)
        eth, eta, et_evts = _simulate_extra_time(home, away, home_prob)
        h_total += eth
        a_total += eta
        all_events.extend(et_evts)

        await self._edit_msg(_tournament_live_ko_embed(
            table, stage, home, away, h_total, a_total, all_events, "Extra Time",
            prev_results=prev_results,
        ))

        if h_total != a_total:
            await asyncio.sleep(KO_PERIOD_DELAY)
            winner = home if h_total > a_total else away
            return winner, f"{h_total}-{a_total} AET", all_events

        # Penalties
        await asyncio.sleep(KO_PERIOD_DELAY)
        ph, pa, pen_log = _simulate_penalties(home, away)

        await self._edit_msg(_tournament_live_ko_embed(
            table, stage, home, away, h_total, a_total, all_events, "Penalties",
            prev_results=prev_results, pen_score=(ph, pa),
        ))
        await asyncio.sleep(KO_PERIOD_DELAY)

        winner = home if ph > pa else away
        return winner, f"{h_total}-{a_total} ({ph}-{pa} Pens)", all_events

    async def _tournament_loop(self) -> None:
        table = self.table
        try:
            # ── Group stage: show matches one by one ──────────────────────
            table.current_stage = "groups"
            all_standings = _init_group_standings(table.groups)
            fixtures = _group_fixtures(table.groups)
            match_log: list[str] = []

            for grp_name, home, away in fixtures:
                # Show "now playing" before result
                current = f"{home.abbr} vs {away.abbr} (Group {grp_name})"
                # Update standings snapshot for display
                table.group_results = {
                    gn: _sorted_standings(st)
                    for gn, st in all_standings.items()
                }
                await self._edit_msg(_tournament_group_embed(
                    table, match_log=match_log, current_match=current,
                ))
                await asyncio.sleep(GROUP_MATCH_DELAY)

                # Simulate and record
                hg, ag = _sim_group_match(home, away)
                _apply_match_result(all_standings[grp_name], home.abbr, away.abbr, hg, ag)

                # Result emoji
                if hg > ag:
                    result_str = f"\u26bd **{home.abbr}** {hg}-{ag} {away.abbr}"
                elif ag > hg:
                    result_str = f"\u26bd {home.abbr} {hg}-{ag} **{away.abbr}**"
                else:
                    result_str = f"\U0001f91d {home.abbr} {hg}-{ag} {away.abbr}"
                match_log.append(result_str)

            # Final group standings
            table.group_results = {
                gn: _sorted_standings(st) for gn, st in all_standings.items()
            }
            await self._edit_msg(_tournament_group_embed(
                table, match_log=match_log,
            ))
            await asyncio.sleep(KO_HALF_DELAY)

            # ── Semi-finals: live simulated ───────────────────────────────
            table.current_stage = "semis"
            a_standings = table.group_results["A"]
            b_standings = table.group_results["B"]
            a1 = a_standings[0]["team"]
            a2 = a_standings[1]["team"]
            b1 = b_standings[0]["team"]
            b2 = b_standings[1]["team"]

            sf1_winner, sf1_score, _ = await self._sim_ko_match_live(
                a1, b2, "Semi-Final 1",
            )
            semis_results = [
                (f"{a1.abbr} vs {b2.abbr}", sf1_score, sf1_winner.abbr),
            ]
            await asyncio.sleep(KO_HALF_DELAY)

            sf2_winner, sf2_score, _ = await self._sim_ko_match_live(
                b1, a2, "Semi-Final 2",
                prev_results=semis_results,
            )
            semis_results.append(
                (f"{b1.abbr} vs {a2.abbr}", sf2_score, sf2_winner.abbr),
            )

            # Show both semi results
            await self._edit_msg(
                _tournament_knockout_embed(table, "Semi-Finals", semis_results),
            )
            await asyncio.sleep(KO_HALF_DELAY)

            # ── Final: live simulated ─────────────────────────────────────
            table.current_stage = "final"
            final_winner, final_score, _ = await self._sim_ko_match_live(
                sf1_winner, sf2_winner, "\U0001f3c6 FINAL",
                prev_results=semis_results,
            )
            final_result = (
                f"{sf1_winner.abbr} vs {sf2_winner.abbr}",
                final_score,
                final_winner.abbr,
            )

            # ── Resolve bets ──────────────────────────────────────────────
            table.phase = "finished"
            balances: dict[int, int] = {}
            for uid, player in table.players.items():
                if player.team_name == final_winner.abbr:
                    player.won = True
                    win_prob = _tournament_team_win_prob(final_winner, table.teams)
                    player.payout = int(player.bet * _payout_multiplier(win_prob))

                if player.won and player.payout > 0:
                    balances[uid] = await queries.update_casino_balance(
                        str(uid), player.payout,
                    )
                else:
                    bal = await queries.get_casino_balance(str(uid))
                    balances[uid] = bal or 0
                await queries.log_casino_result(
                    str(uid), "soccersim", player.bet, player.payout,
                )

            for uid, player in table.players.items():
                table.last_bets[uid] = (
                    player.display_name, player.bet, player.team_name,
                )

            self._update_buttons()
            await self._edit_msg(_tournament_final_embed(
                table, final_winner,
                semis=semis_results, final_result=final_result,
                balances=balances,
            ))

        except asyncio.CancelledError:
            pass
        except Exception:
            if table.phase == "playing":
                table.phase = "finished"
                await self._refund_all()

    async def _refund_all(self) -> None:
        for p in self.table.players.values():
            try:
                await queries.update_casino_balance(str(p.user_id), p.bet)
            except Exception:
                log.exception("Unhandled error in soccersim.py")

    async def _close(
        self, interaction: discord.Interaction, reason: str,
    ) -> None:
        embed = discord.Embed(
            title="\u26bd Soccer Tournament \u2014 Closed",
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
                        title="\u26bd Soccer Tournament \u2014 Timed Out",
                        description="Tournament timed out.",
                        colour=discord.Colour.dark_grey(),
                    )
                    await table.message.edit(embed=embed, view=None)
                except Exception:
                    log.exception("Unhandled error in soccersim.py")
            return

        await self._refund_all()
        self.active_tables.pop(table.channel_id, None)
        if table.message:
            try:
                embed = discord.Embed(
                    title="\u26bd Soccer Tournament \u2014 Timed Out",
                    description="Tournament timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("Unhandled error in soccersim.py")


# ── Cog ──────────────────────────────────────────────────────────────────────


class SoccerSimCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, SoccerSimTable | TournamentTable] = {}

    @app_commands.command(
        name="soccersim", description="Bet on a simulated soccer match (casino)",
    )
    @app_commands.describe(
        team1="Home team name or abbreviation (random if omitted)",
        team2="Away team name or abbreviation (random if omitted)",
    )
    async def soccersim(
        self, interaction: discord.Interaction,
        team1: str | None = None, team2: str | None = None,
    ) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            _has_running = any(
                (t := getattr(existing, n, None)) is not None and not t.done()
                for n in ("game_task", "race_task", "sim_task", "round_task", "_round_task", "trade_task", "fly_task", "_shot_clock_task", "_countdown_task")
            )
            if _has_running:
                await interaction.response.send_message(
                    "There's already a Soccer Sim table in this channel!",
                    ephemeral=True,
                )
                return
            del self.active_tables[channel_id]

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        if team1 and team2:
            home = _find_team(team1)
            away = _find_team(team2)
            if home is None:
                await interaction.response.send_message(
                    f"Team not found: `{team1}`", ephemeral=True,
                )
                return
            if away is None:
                await interaction.response.send_message(
                    f"Team not found: `{team2}`", ephemeral=True,
                )
                return
            if home.abbr == away.abbr:
                await interaction.response.send_message(
                    "Pick two different teams!", ephemeral=True,
                )
                return
        elif team1 or team2:
            specified = _find_team(team1 or team2)
            if specified is None:
                await interaction.response.send_message(
                    f"Team not found: `{team1 or team2}`", ephemeral=True,
                )
                return
            pool = [t for t in ALL_TEAMS if t.abbr != specified.abbr]
            opponent = random.choice(pool)
            if team1:
                home, away = specified, opponent
            else:
                home, away = opponent, specified
        else:
            home, away = random.sample(ALL_TEAMS, 2)

        table = SoccerSimTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
            home_team=home,
            away_team=away,
            home_prob=_generate_win_prob(home, away),
        )
        self.active_tables[channel_id] = table

        view = SoccerSimTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()

    @app_commands.command(
        name="soccersim-tournament",
        description="Run an 8-team soccer tournament with group stage + knockout (casino)",
    )
    @app_commands.describe(pool="Team pool: clubs, national, or mixed (default)")
    @app_commands.choices(pool=[
        app_commands.Choice(name="Clubs", value="clubs"),
        app_commands.Choice(name="National Teams", value="national"),
        app_commands.Choice(name="Mixed", value="mixed"),
    ])
    async def soccersim_tournament(
        self, interaction: discord.Interaction,
        pool: str = "mixed",
    ) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            _has_running = any(
                (t := getattr(existing, n, None)) is not None and not t.done()
                for n in ("game_task", "race_task", "sim_task", "round_task", "_round_task", "trade_task", "fly_task", "_shot_clock_task", "_countdown_task")
            )
            if _has_running:
                await interaction.response.send_message(
                    "There's already a Soccer Sim table in this channel!",
                    ephemeral=True,
                )
                return
            del self.active_tables[channel_id]

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

        if pool == "clubs":
            source = CLUB_TEAMS
        elif pool == "national":
            source = NATIONAL_TEAMS
        else:
            source = ALL_TEAMS

        teams = random.sample(source, min(8, len(source)))
        groups = _draw_groups(teams)

        table = TournamentTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
            team_pool=pool,
            teams=teams,
            groups=groups,
        )
        self.active_tables[channel_id] = table

        view = TournamentView(table, self.active_tables)
        embed = _tournament_group_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SoccerSimCog(bot))
