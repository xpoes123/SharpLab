"""Casino cog — multiplayer /geography speed race.

Given a country, first to type its capital wins the round.
First to WINS_TO_WIN round wins wins!
"""

import asyncio
import logging
import random
import time
import unicodedata
from dataclasses import dataclass, field

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs._elo_helpers import fmt_elo_change, update_elo_multiplayer
from bot.cogs._landmarks import LANDMARKS, _LANDMARK_POOL
from db.queries import record_geo_attempt, get_geo_stats_by_region, get_elo_rating

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 8
MIN_PLAYERS = 1
ROUND_TIME = 30  # seconds per round
ROUND_DELAY = 4  # seconds between rounds
WINS_TO_WIN = 3  # first to N wins
MAX_ROUNDS = 15  # safety cap

MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]  # gold, silver, bronze

# ── Country → Capitals ──────────────────────────────────────────────────────
# value = list of accepted answers (first is the "canonical" display name)

CAPITALS: dict[str, list[str]] = {
    # ── Easy ──
    "France": ["Paris"],
    "Japan": ["Tokyo"],
    "Brazil": ["Brasilia", "Brasília"],
    "Australia": ["Canberra"],
    "Egypt": ["Cairo"],
    "Canada": ["Ottawa"],
    "Italy": ["Rome", "Roma"],
    "Germany": ["Berlin"],
    "China": ["Beijing", "Peking"],
    "India": ["New Delhi"],
    "Mexico": ["Mexico City"],
    "Russia": ["Moscow", "Moskva"],
    "Spain": ["Madrid"],
    "United Kingdom": ["London"],
    "United States": ["Washington DC", "Washington D.C.", "Washington"],
    "South Korea": ["Seoul"],
    "Argentina": ["Buenos Aires"],
    "Greece": ["Athens"],
    "Sweden": ["Stockholm"],
    "Norway": ["Oslo"],
    "Denmark": ["Copenhagen"],
    "Finland": ["Helsinki"],
    "Portugal": ["Lisbon", "Lisboa"],
    "Netherlands": ["Amsterdam"],
    "Belgium": ["Brussels", "Bruxelles"],
    "Switzerland": ["Bern", "Berne"],
    "Austria": ["Vienna", "Wien"],
    "Ireland": ["Dublin"],
    "South Africa": ["Pretoria", "Cape Town", "Bloemfontein"],
    "New Zealand": ["Wellington"],
    "Cuba": ["Havana", "La Habana"],
    "Jamaica": ["Kingston"],
    "Peru": ["Lima"],
    "Chile": ["Santiago"],
    "Colombia": ["Bogota", "Bogotá"],
    "Venezuela": ["Caracas"],
    "Israel": ["Jerusalem"],
    "Saudi Arabia": ["Riyadh"],
    "Thailand": ["Bangkok"],
    "Indonesia": ["Jakarta"],
    "Philippines": ["Manila"],
    "Vietnam": ["Hanoi", "Ha Noi"],
    "Malaysia": ["Kuala Lumpur"],
    # ── Medium ──
    "Turkey": ["Ankara"],
    "Morocco": ["Rabat"],
    "Poland": ["Warsaw", "Warszawa"],
    "Czech Republic": ["Prague", "Praha"],
    "Hungary": ["Budapest"],
    "Romania": ["Bucharest", "Bucuresti"],
    "Ukraine": ["Kyiv", "Kiev"],
    "Croatia": ["Zagreb"],
    "Serbia": ["Belgrade", "Beograd"],
    "Bulgaria": ["Sofia"],
    "Slovakia": ["Bratislava"],
    "Slovenia": ["Ljubljana"],
    "Estonia": ["Tallinn"],
    "Latvia": ["Riga"],
    "Lithuania": ["Vilnius"],
    "Iceland": ["Reykjavik", "Reykjavík"],
    "Nigeria": ["Abuja"],
    "Kenya": ["Nairobi"],
    "Ethiopia": ["Addis Ababa"],
    "Ghana": ["Accra"],
    "Tanzania": ["Dodoma"],
    "Uganda": ["Kampala"],
    "Algeria": ["Algiers"],
    "Tunisia": ["Tunis"],
    "Libya": ["Tripoli"],
    "Iraq": ["Baghdad"],
    "Iran": ["Tehran"],
    "Pakistan": ["Islamabad"],
    "Afghanistan": ["Kabul"],
    "Bangladesh": ["Dhaka", "Dacca"],
    "Nepal": ["Kathmandu"],
    "Cambodia": ["Phnom Penh"],
    "Singapore": ["Singapore"],
    "Taiwan": ["Taipei"],
    "Mongolia": ["Ulaanbaatar", "Ulan Bator"],
    "North Korea": ["Pyongyang"],
    "Ecuador": ["Quito"],
    "Bolivia": ["Sucre", "La Paz"],
    "Paraguay": ["Asuncion", "Asunción"],
    "Uruguay": ["Montevideo"],
    "Panama": ["Panama City"],
    "Costa Rica": ["San Jose", "San José"],
    "Guatemala": ["Guatemala City"],
    "Honduras": ["Tegucigalpa"],
    "El Salvador": ["San Salvador"],
    "Dominican Republic": ["Santo Domingo"],
    "Haiti": ["Port-au-Prince"],
    "Trinidad and Tobago": ["Port of Spain"],
    # ── Hard ──
    "Myanmar": ["Naypyidaw", "Nay Pyi Taw"],
    "Kazakhstan": ["Astana"],
    "Uzbekistan": ["Tashkent"],
    "Turkmenistan": ["Ashgabat"],
    "Kyrgyzstan": ["Bishkek"],
    "Tajikistan": ["Dushanbe"],
    "Azerbaijan": ["Baku"],
    "Georgia": ["Tbilisi"],
    "Armenia": ["Yerevan"],
    "Sri Lanka": ["Sri Jayawardenepura Kotte", "Colombo", "Kotte"],
    "Laos": ["Vientiane"],
    "Brunei": ["Bandar Seri Begawan"],
    "Bhutan": ["Thimphu"],
    "Maldives": ["Male", "Malé"],
    "Madagascar": ["Antananarivo"],
    "Mozambique": ["Maputo"],
    "Zimbabwe": ["Harare"],
    "Zambia": ["Lusaka"],
    "Botswana": ["Gaborone"],
    "Namibia": ["Windhoek"],
    "Senegal": ["Dakar"],
    "Ivory Coast": ["Yamoussoukro"],
    "Cameroon": ["Yaounde", "Yaoundé"],
    "Angola": ["Luanda"],
    "Sudan": ["Khartoum"],
    "Somalia": ["Mogadishu"],
    "Eritrea": ["Asmara"],
    "Rwanda": ["Kigali"],
    "Belize": ["Belmopan"],
    "Suriname": ["Paramaribo"],
    "Guyana": ["Georgetown"],
    "Papua New Guinea": ["Port Moresby"],
    "Fiji": ["Suva"],
    "Malta": ["Valletta"],
    "Luxembourg": ["Luxembourg City", "Luxembourg"],
    "Cyprus": ["Nicosia"],
    "Lebanon": ["Beirut"],
    "Jordan": ["Amman"],
    "Syria": ["Damascus"],
    "Yemen": ["Sanaa", "Sana'a"],
    "Oman": ["Muscat"],
    "Qatar": ["Doha"],
    "Bahrain": ["Manama"],
    "Kuwait": ["Kuwait City"],
    "United Arab Emirates": ["Abu Dhabi"],
    "Montenegro": ["Podgorica"],
    "North Macedonia": ["Skopje"],
    "Albania": ["Tirana"],
    "Bosnia and Herzegovina": ["Sarajevo"],
    "Moldova": ["Chisinau", "Chișinău"],
    "Belarus": ["Minsk"],
    "Liechtenstein": ["Vaduz"],
    "Monaco": ["Monaco"],
    "Andorra": ["Andorra la Vella"],
    "San Marino": ["San Marino"],
}

# ── US State → Capitals ──────────────────────────────────────────────────────
# value = list of accepted answers (first is the canonical display name)

US_STATE_CAPITALS: dict[str, list[str]] = {
    "Alabama": ["Montgomery"],
    "Alaska": ["Juneau"],
    "Arizona": ["Phoenix"],
    "Arkansas": ["Little Rock"],
    "California": ["Sacramento"],
    "Colorado": ["Denver"],
    "Connecticut": ["Hartford"],
    "Delaware": ["Dover"],
    "Florida": ["Tallahassee"],
    "Georgia": ["Atlanta"],
    "Hawaii": ["Honolulu"],
    "Idaho": ["Boise"],
    "Illinois": ["Springfield"],
    "Indiana": ["Indianapolis"],
    "Iowa": ["Des Moines"],
    "Kansas": ["Topeka"],
    "Kentucky": ["Frankfort"],
    "Louisiana": ["Baton Rouge"],
    "Maine": ["Augusta"],
    "Maryland": ["Annapolis"],
    "Massachusetts": ["Boston"],
    "Michigan": ["Lansing"],
    "Minnesota": ["Saint Paul", "St. Paul", "St Paul"],
    "Mississippi": ["Jackson"],
    "Missouri": ["Jefferson City"],
    "Montana": ["Helena"],
    "Nebraska": ["Lincoln"],
    "Nevada": ["Carson City"],
    "New Hampshire": ["Concord"],
    "New Jersey": ["Trenton"],
    "New Mexico": ["Santa Fe"],
    "New York": ["Albany"],
    "North Carolina": ["Raleigh"],
    "North Dakota": ["Bismarck"],
    "Ohio": ["Columbus"],
    "Oklahoma": ["Oklahoma City"],
    "Oregon": ["Salem"],
    "Pennsylvania": ["Harrisburg"],
    "Rhode Island": ["Providence"],
    "South Carolina": ["Columbia"],
    "South Dakota": ["Pierre"],
    "Tennessee": ["Nashville"],
    "Texas": ["Austin"],
    "Utah": ["Salt Lake City"],
    "Vermont": ["Montpelier"],
    "Virginia": ["Richmond"],
    "Washington": ["Olympia"],
    "West Virginia": ["Charleston"],
    "Wisconsin": ["Madison"],
    "Wyoming": ["Cheyenne"],
}

# ── Country → ISO 3166-1 alpha-2 code (for flag images) ─────────────────────
# Used to build flagcdn.com URLs: https://flagcdn.com/w320/{code}.png

COUNTRY_CODES: dict[str, str] = {
    "France": "fr", "Japan": "jp", "Brazil": "br", "Australia": "au",
    "Egypt": "eg", "Canada": "ca", "Italy": "it", "Germany": "de",
    "China": "cn", "India": "in", "Mexico": "mx", "Russia": "ru",
    "Spain": "es", "United Kingdom": "gb", "United States": "us",
    "South Korea": "kr", "Argentina": "ar", "Greece": "gr", "Sweden": "se",
    "Norway": "no", "Denmark": "dk", "Finland": "fi", "Portugal": "pt",
    "Netherlands": "nl", "Belgium": "be", "Switzerland": "ch", "Austria": "at",
    "Ireland": "ie", "South Africa": "za", "New Zealand": "nz", "Cuba": "cu",
    "Jamaica": "jm", "Peru": "pe", "Chile": "cl", "Colombia": "co",
    "Venezuela": "ve", "Israel": "il", "Saudi Arabia": "sa", "Thailand": "th",
    "Indonesia": "id", "Philippines": "ph", "Vietnam": "vn", "Malaysia": "my",
    "Turkey": "tr", "Morocco": "ma", "Poland": "pl", "Czech Republic": "cz",
    "Hungary": "hu", "Romania": "ro", "Ukraine": "ua", "Croatia": "hr",
    "Serbia": "rs", "Bulgaria": "bg", "Slovakia": "sk", "Slovenia": "si",
    "Estonia": "ee", "Latvia": "lv", "Lithuania": "lt", "Iceland": "is",
    "Nigeria": "ng", "Kenya": "ke", "Ethiopia": "et", "Ghana": "gh",
    "Tanzania": "tz", "Uganda": "ug", "Algeria": "dz", "Tunisia": "tn",
    "Libya": "ly", "Iraq": "iq", "Iran": "ir", "Pakistan": "pk",
    "Afghanistan": "af", "Bangladesh": "bd", "Nepal": "np", "Cambodia": "kh",
    "Singapore": "sg", "Taiwan": "tw", "Mongolia": "mn", "North Korea": "kp",
    "Ecuador": "ec", "Bolivia": "bo", "Paraguay": "py", "Uruguay": "uy",
    "Panama": "pa", "Costa Rica": "cr", "Guatemala": "gt", "Honduras": "hn",
    "El Salvador": "sv", "Dominican Republic": "do", "Haiti": "ht",
    "Trinidad and Tobago": "tt", "Myanmar": "mm", "Kazakhstan": "kz",
    "Uzbekistan": "uz", "Turkmenistan": "tm", "Kyrgyzstan": "kg",
    "Tajikistan": "tj", "Azerbaijan": "az", "Georgia": "ge", "Armenia": "am",
    "Sri Lanka": "lk", "Laos": "la", "Brunei": "bn", "Bhutan": "bt",
    "Maldives": "mv", "Madagascar": "mg", "Mozambique": "mz", "Zimbabwe": "zw",
    "Zambia": "zm", "Botswana": "bw", "Namibia": "na", "Senegal": "sn",
    "Ivory Coast": "ci", "Cameroon": "cm", "Angola": "ao", "Sudan": "sd",
    "Somalia": "so", "Eritrea": "er", "Rwanda": "rw", "Belize": "bz",
    "Suriname": "sr", "Guyana": "gy", "Papua New Guinea": "pg", "Fiji": "fj",
    "Malta": "mt", "Luxembourg": "lu", "Cyprus": "cy", "Lebanon": "lb",
    "Jordan": "jo", "Syria": "sy", "Yemen": "ye", "Oman": "om", "Qatar": "qa",
    "Bahrain": "bh", "Kuwait": "kw", "United Arab Emirates": "ae",
    "Montenegro": "me", "North Macedonia": "mk", "Albania": "al",
    "Bosnia and Herzegovina": "ba", "Moldova": "md", "Belarus": "by",
    "Liechtenstein": "li", "Monaco": "mc", "Andorra": "ad", "San Marino": "sm",
}

# ── US State → ISO 3166-2 code (for flag images) ────────────────────────────
# Used to build flagcdn.com URLs: https://flagcdn.com/w320/us-{code}.png

# ── Country name aliases ────────────────────────────────────────────────────
# Maps each canonical country key → list of alternative accepted spellings.
# The canonical name itself is always accepted; these are extras.
# Used for flag and landmark questions where the answer IS the country name.

COUNTRY_ALIASES: dict[str, list[str]] = {
    # North America
    "United States": ["USA", "US", "United States of America", "America", "U.S.", "U.S.A."],
    "Canada": [],
    "Mexico": [],
    # Europe
    "United Kingdom": ["UK", "Great Britain", "Britain", "England", "U.K."],
    "Russia": ["Russian Federation"],
    "Czech Republic": ["Czechia", "Czech"],
    "Turkey": ["Türkiye"],
    "North Macedonia": ["Macedonia"],
    "Bosnia and Herzegovina": ["Bosnia", "Bosnia-Herzegovina"],
    "Ivory Coast": ["Côte d'Ivoire", "Cote d'Ivoire", "Cote dIvoire"],
    # Asia
    "South Korea": ["Korea", "Republic of Korea", "ROK"],
    "North Korea": ["DPRK", "Democratic People's Republic of Korea"],
    "China": ["PRC", "People's Republic of China"],
    "Taiwan": ["Chinese Taipei"],
    "Myanmar": ["Burma"],
    "Iran": ["Persia"],
    "Laos": ["Lao", "Lao PDR"],
    "Brunei": ["Brunei Darussalam"],
    "Sri Lanka": ["Ceylon"],
    "United Arab Emirates": ["UAE", "U.A.E."],
    "Saudi Arabia": ["KSA"],
    # Americas
    "Trinidad and Tobago": ["Trinidad"],
    "Dominican Republic": ["DR"],
    "El Salvador": ["Salvador"],
    # Africa
    "Tanzania": ["United Republic of Tanzania"],
    "Egypt": ["Arab Republic of Egypt"],
    # Oceania
    "Papua New Guinea": ["PNG"],
}


def _alias_answers(canonical: str) -> list[str]:
    """Return accepted answer list for a country name question.

    First element is always the canonical name (displayed after round ends).
    Subsequent elements are aliases/synonyms accepted case-insensitively.
    """
    return [canonical] + COUNTRY_ALIASES.get(canonical, [])


# ── Country/State → Region mapping (for accuracy stats) ───────────────────
# Every country in CAPITALS/COUNTRY_CODES maps to one of five regions.
# US states map to 'Americas'.

COUNTRY_REGIONS: dict[str, str] = {
    # ── Europe ──
    "France": "Europe", "Italy": "Europe", "Germany": "Europe", "Spain": "Europe",
    "United Kingdom": "Europe", "Greece": "Europe", "Sweden": "Europe",
    "Norway": "Europe", "Denmark": "Europe", "Finland": "Europe",
    "Portugal": "Europe", "Netherlands": "Europe", "Belgium": "Europe",
    "Switzerland": "Europe", "Austria": "Europe", "Ireland": "Europe",
    "Poland": "Europe", "Czech Republic": "Europe", "Hungary": "Europe",
    "Romania": "Europe", "Ukraine": "Europe", "Croatia": "Europe",
    "Serbia": "Europe", "Bulgaria": "Europe", "Slovakia": "Europe",
    "Slovenia": "Europe", "Estonia": "Europe", "Latvia": "Europe",
    "Lithuania": "Europe", "Iceland": "Europe", "Malta": "Europe",
    "Luxembourg": "Europe", "Cyprus": "Europe", "Montenegro": "Europe",
    "North Macedonia": "Europe", "Albania": "Europe",
    "Bosnia and Herzegovina": "Europe", "Moldova": "Europe",
    "Belarus": "Europe", "Liechtenstein": "Europe", "Monaco": "Europe",
    "Andorra": "Europe", "San Marino": "Europe", "Russia": "Europe",
    "Turkey": "Europe",
    # ── Asia ──
    "Japan": "Asia", "China": "Asia", "India": "Asia", "South Korea": "Asia",
    "Thailand": "Asia", "Indonesia": "Asia", "Philippines": "Asia",
    "Vietnam": "Asia", "Malaysia": "Asia", "Iraq": "Asia", "Iran": "Asia",
    "Pakistan": "Asia", "Afghanistan": "Asia", "Bangladesh": "Asia",
    "Nepal": "Asia", "Cambodia": "Asia", "Singapore": "Asia",
    "Taiwan": "Asia", "Mongolia": "Asia", "North Korea": "Asia",
    "Myanmar": "Asia", "Kazakhstan": "Asia", "Uzbekistan": "Asia",
    "Turkmenistan": "Asia", "Kyrgyzstan": "Asia", "Tajikistan": "Asia",
    "Azerbaijan": "Asia", "Georgia": "Asia", "Armenia": "Asia",
    "Sri Lanka": "Asia", "Laos": "Asia", "Brunei": "Asia",
    "Bhutan": "Asia", "Maldives": "Asia", "Israel": "Asia",
    "Saudi Arabia": "Asia", "Lebanon": "Asia", "Jordan": "Asia",
    "Syria": "Asia", "Yemen": "Asia", "Oman": "Asia", "Qatar": "Asia",
    "Bahrain": "Asia", "Kuwait": "Asia", "United Arab Emirates": "Asia",
    # ── Africa ──
    "Egypt": "Africa", "South Africa": "Africa", "Morocco": "Africa",
    "Nigeria": "Africa", "Kenya": "Africa", "Ethiopia": "Africa",
    "Ghana": "Africa", "Tanzania": "Africa", "Uganda": "Africa",
    "Algeria": "Africa", "Tunisia": "Africa", "Libya": "Africa",
    "Madagascar": "Africa", "Mozambique": "Africa", "Zimbabwe": "Africa",
    "Zambia": "Africa", "Botswana": "Africa", "Namibia": "Africa",
    "Senegal": "Africa", "Ivory Coast": "Africa", "Cameroon": "Africa",
    "Angola": "Africa", "Sudan": "Africa", "Somalia": "Africa",
    "Eritrea": "Africa", "Rwanda": "Africa",
    # ── Americas ──
    "Brazil": "Americas", "Canada": "Americas", "Mexico": "Americas",
    "United States": "Americas", "Argentina": "Americas", "Cuba": "Americas",
    "Jamaica": "Americas", "Peru": "Americas", "Chile": "Americas",
    "Colombia": "Americas", "Venezuela": "Americas", "Ecuador": "Americas",
    "Bolivia": "Americas", "Paraguay": "Americas", "Uruguay": "Americas",
    "Panama": "Americas", "Costa Rica": "Americas", "Guatemala": "Americas",
    "Honduras": "Americas", "El Salvador": "Americas",
    "Dominican Republic": "Americas", "Haiti": "Americas",
    "Trinidad and Tobago": "Americas", "Belize": "Americas",
    "Suriname": "Americas", "Guyana": "Americas",
    # ── Oceania ──
    "Australia": "Oceania", "New Zealand": "Oceania",
    "Papua New Guinea": "Oceania", "Fiji": "Oceania",
}

US_STATE_CODES: dict[str, str] = {
    "Alabama": "al", "Alaska": "ak", "Arizona": "az", "Arkansas": "ar",
    "California": "ca", "Colorado": "co", "Connecticut": "ct", "Delaware": "de",
    "Florida": "fl", "Georgia": "ga", "Hawaii": "hi", "Idaho": "id",
    "Illinois": "il", "Indiana": "in", "Iowa": "ia", "Kansas": "ks",
    "Kentucky": "ky", "Louisiana": "la", "Maine": "me", "Maryland": "md",
    "Massachusetts": "ma", "Michigan": "mi", "Minnesota": "mn",
    "Mississippi": "ms", "Missouri": "mo", "Montana": "mt", "Nebraska": "ne",
    "Nevada": "nv", "New Hampshire": "nh", "New Jersey": "nj",
    "New Mexico": "nm", "New York": "ny", "North Carolina": "nc",
    "North Dakota": "nd", "Ohio": "oh", "Oklahoma": "ok", "Oregon": "or",
    "Pennsylvania": "pa", "Rhode Island": "ri", "South Carolina": "sc",
    "South Dakota": "sd", "Tennessee": "tn", "Texas": "tx", "Utah": "ut",
    "Vermont": "vt", "Virginia": "va", "Washington": "wa",
    "West Virginia": "wv", "Wisconsin": "wi", "Wyoming": "wy",
}


# ── Answer matching ─────────────────────────────────────────────────────────


def _normalize(s: str) -> str:
    """Lowercase, strip accents and non-alphanumeric chars for fuzzy matching."""
    # Decompose unicode, drop combining marks
    nfkd = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Lowercase, keep only alphanumeric + spaces
    return "".join(c.lower() for c in stripped if c.isalnum() or c == " ").strip()


def check_answer(guess: str, accepted: list[str]) -> bool:
    """Check if a guess matches any accepted answer."""
    norm_guess = _normalize(guess)
    if not norm_guess:
        return False
    for ans in accepted:
        if _normalize(ans) == norm_guess:
            return True
    return False


# ── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class GeoPlayer:
    user_id: int
    display_name: str
    rounds_won: int = 0
    answer: str | None = None
    answer_time: float | None = None


@dataclass
class GeoTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | between_rounds | closed
    players: dict[int, GeoPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 0
    # Category: mixed | country_capitals | state_capitals | country_flags | state_flags
    category: str = "mixed"
    current_subject: str = ""      # display name (country/state) shown in result embeds
    current_question_text: str = ""  # full question string shown during round
    current_answers: list[str] = field(default_factory=list)
    current_q_type: str = ""               # 'country_cap' | 'state_cap' | 'country_flag' | 'state_flag'
    current_image_url: str | None = None  # flag image URL, or None for text-only rounds
    round_start_time: float = 0.0
    round_winner: int | None = None
    race_task: asyncio.Task | None = field(default=None, repr=False)
    thread: discord.Thread | None = field(default=None, repr=False)
    round_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    total_rounds_played: int = 0
    # Tracks (q_type, key) pairs to avoid repeating questions within a game
    used_questions: set[tuple[str, str]] = field(default_factory=set)
    # Messages sent by players during a round; cleared after round ends
    round_messages: list[discord.Message] = field(default_factory=list)
    stop_requested: bool = False


# ── Embeds ──────────────────────────────────────────────────────────────────


def _scoreboard(table: GeoTable) -> str:
    """Build a sorted scoreboard of rounds won."""
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


_CATEGORY_LABELS: dict[str, str] = {
    "mixed": "Mixed (all categories)",
    "country_capitals": "Country Capitals",
    "state_capitals": "US State Capitals",
    "country_flags": "Country Flags",
    "state_flags": "US State Flags",
    "landmarks": "Landmarks",
}


def _betting_embed(table: GeoTable) -> discord.Embed:
    cat_label = _CATEGORY_LABELS.get(table.category, table.category)

    embed = discord.Embed(
        title="\U0001f30d Speed Geography",
        description=(
            f"**Category:** {cat_label}\n"
            f"**First to {WINS_TO_WIN} wins** wins!\n"
            "Type your answer directly in chat \u2014 fastest correct answer wins each round!"
        ),
        colour=discord.Colour.blue(),
    )

    embed.add_field(name="Goal", value=f"First to {WINS_TO_WIN}", inline=True)

    if table.players:
        lines = [
            f"\U0001f30e **{p.display_name}**"
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
        text=(
            f"Host: {table.host_name} \u2502 "
            f"Min {MIN_PLAYERS} players"
        ),
    )
    return embed


def _playing_embed(table: GeoTable, remaining: int | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"\U0001f30d Round {table.round_num} (First to {WINS_TO_WIN})",
        colour=discord.Colour.gold(),
    )

    embed.description = (
        f"# {table.current_question_text}\n\n"
        "**Type your answer in chat!**"
    )

    if table.current_image_url:
        embed.set_image(url=table.current_image_url)

    secs = remaining if remaining is not None else ROUND_TIME
    embed.add_field(name="\u23f1\ufe0f Time", value=f"**{secs}s**", inline=True)

    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _round_result_embed(table: GeoTable) -> discord.Embed:
    winner = table.players[table.round_winner]
    solve_time = winner.answer_time - table.round_start_time
    is_last = winner.rounds_won >= WINS_TO_WIN or table.round_num >= MAX_ROUNDS

    embed = discord.Embed(
        title=f"\U0001f30d Round {table.round_num} \u2705",
        colour=discord.Colour.green(),
    )
    canonical = table.current_answers[0]
    embed.description = (
        f"\U0001f3c6 **{winner.display_name}** got it in **{solve_time:.1f}s**!\n\n"
        f"\U0001f1fa\U0001f1f3 {table.current_subject} \u2192 **{canonical}**"
    )
    if table.current_image_url:
        embed.set_image(url=table.current_image_url)
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round complete \u2014 calculating results\u2026")
    return embed


def _timeout_embed(table: GeoTable) -> discord.Embed:
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)
    is_last = max_wins >= WINS_TO_WIN or table.round_num >= MAX_ROUNDS

    embed = discord.Embed(
        title=f"\U0001f30d Round {table.round_num} (Time's Up!)",
        colour=discord.Colour.dark_grey(),
    )
    canonical = table.current_answers[0]
    embed.description = (
        f"Nobody got it in {ROUND_TIME} seconds!\n\n"
        f"\U0001f1fa\U0001f1f3 {table.current_subject} \u2192 **{canonical}**"
    )
    if table.current_image_url:
        embed.set_image(url=table.current_image_url)
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round complete \u2014 calculating results\u2026")
    return embed


def _final_embed(table: GeoTable, elo_changes: dict[int, tuple[float, float]] | None = None) -> discord.Embed:
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)
    no_winner = max_wins == 0

    embed = discord.Embed(
        title="\U0001f30d Speed Geography \u2014 Results",
        colour=discord.Colour.gold() if not no_winner else discord.Colour.dark_grey(),
    )

    if no_winner:
        embed.description = "No rounds were won \u2014 game over!"
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
        medal = MEDALS[i] if i < len(MEDALS) and p.rounds_won > 0 else "\u25aa\ufe0f"
        lines.append(f"{medal} **{p.display_name}** \u2014 {p.rounds_won} round{'s' if p.rounds_won != 1 else ''} won")
    embed.add_field(name="Results", value="\n".join(lines), inline=False)

    embed.add_field(
        name="Rounds Played", value=str(table.total_rounds_played), inline=True,
    )

    if elo_changes:
        sorted_players = sorted(table.players.values(), key=lambda p: p.rounds_won, reverse=True)
        elo_lines: list[str] = []
        for p in sorted_players:
            if p.user_id in elo_changes:
                old, new = elo_changes[p.user_id]
                elo_lines.append(f"**{p.display_name}**: {fmt_elo_change(old, new)}")
        if elo_lines:
            embed.add_field(name="\U0001f4c8 ELO", value="\n".join(elo_lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── View ────────────────────────────────────────────────────────────────────


_CATEGORY_OPTIONS = [
    discord.SelectOption(
        label="Mixed (all categories)",
        value="mixed",
        description="Country capitals, US state capitals, and flags",
        emoji="\U0001f30d",
        default=True,
    ),
    discord.SelectOption(
        label="Country Capitals",
        value="country_capitals",
        description="Name the capital city of a country",
        emoji="\U0001f3db\ufe0f",
    ),
    discord.SelectOption(
        label="US State Capitals",
        value="state_capitals",
        description="Name the capital city of a US state",
        emoji="\U0001f1fa\U0001f1f8",
    ),
    discord.SelectOption(
        label="Country Flags",
        value="country_flags",
        description="Name the country from its flag image",
        emoji="\U0001f6a9",
    ),
    discord.SelectOption(
        label="US State Flags",
        value="state_flags",
        description="Name the US state from its flag image",
        emoji="\U0001f3f3\ufe0f",
    ),
    discord.SelectOption(
        label="Landmarks",
        value="landmarks",
        description="Name the country from a famous landmark photo",
        emoji="\U0001f3db\ufe0f",
    ),
]


class GeoEndGameView(ui.View):
    """Button posted in the thread so any player can stop the game early."""

    def __init__(self, table: GeoTable) -> None:
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


class GeoTableView(ui.View):
    def __init__(
        self, table: GeoTable, active_tables: dict[int, GeoTable],
    ) -> None:
        super().__init__(timeout=900)  # 15 min
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        playing = phase == "playing"
        racing = playing or phase == "between_rounds"

        self.start_btn.disabled = (
            not betting or len(self.table.players) < MIN_PLAYERS
        )
        self.join_btn.disabled = not betting
        self.leave_btn.disabled = not betting
        self.close_btn.disabled = racing
        self.category_select.disabled = not betting

    # ── Row 0: Betting ──────────────────────────────────────────────────

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
        emoji="\U0001f30e", row=0,
    )
    async def join_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Race in progress! Wait for the next game.", ephemeral=True,
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
        self.table.players[uid] = GeoPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
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
        if self.table.players.get(uid) is None:
            await interaction.response.send_message(
                "You're not at this table.", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Can't leave during a race!", ephemeral=True,
            )
            return
        del self.table.players[uid]
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Row 1: Close ────────────────────────────────────────────────────

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
                "Can't close during a race! Wait for it to finish.",
                ephemeral=True,
            )
            return
        await self._close_table(interaction)

    # ── Category select ─────────────────────────────────────────────────

    @ui.select(
        placeholder="Category: Mixed (all categories)",
        options=_CATEGORY_OPTIONS,
        row=2,
    )
    async def category_select(
        self, interaction: discord.Interaction, select: ui.Select,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can change the category!", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Can't change category once the race has started!", ephemeral=True,
            )
            return
        self.table.category = select.values[0]
        # Update the placeholder to reflect selection
        chosen = next(
            (o for o in _CATEGORY_OPTIONS if o.value == self.table.category), None,
        )
        select.placeholder = f"Category: {chosen.label}" if chosen else "Category"
        # Mark chosen option as default
        for opt in select.options:
            opt.default = opt.value == self.table.category
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Race logic ──────────────────────────────────────────────────────

    def _pick_question(self) -> tuple[str, str, list[str], str | None]:
        """Pick the next question based on the table's category.

        Returns (q_type, subject, accepted_answers, image_url_or_None).
        q_type is one of: 'country_cap', 'state_cap', 'country_flag', 'state_flag'.
        """
        table = self.table
        cat = table.category

        # Build the candidate pool for this category
        def _available(q_type: str, keys: list[str]) -> list[tuple[str, str]]:
            used = table.used_questions
            avail = [(q_type, k) for k in keys if (q_type, k) not in used]
            if not avail:
                # All exhausted — reset just this sub-pool
                table.used_questions = {q for q in used if q[0] != q_type}
                avail = [(q_type, k) for k in keys]
            return avail

        if cat == "country_capitals":
            pool = _available("country_cap", list(CAPITALS.keys()))
        elif cat == "state_capitals":
            pool = _available("state_cap", list(US_STATE_CAPITALS.keys()))
        elif cat == "country_flags":
            pool = _available("country_flag", list(COUNTRY_CODES.keys()))
        elif cat == "state_flags":
            pool = _available("state_flag", list(US_STATE_CODES.keys()))
        elif cat == "landmarks":
            landmark_keys = [name for name, _, _ in _LANDMARK_POOL]
            pool = _available("landmark", landmark_keys)
        else:  # mixed — combine all sub-pools
            country_cap_pool = _available("country_cap", list(CAPITALS.keys()))
            state_cap_pool = _available("state_cap", list(US_STATE_CAPITALS.keys()))
            country_flag_pool = _available("country_flag", list(COUNTRY_CODES.keys()))
            state_flag_pool = _available("state_flag", list(US_STATE_CODES.keys()))
            landmark_pool = _available("landmark", [n for n, _, _ in _LANDMARK_POOL])
            pool = country_cap_pool + state_cap_pool + country_flag_pool + state_flag_pool + landmark_pool
            if not pool:
                table.used_questions.clear()
                pool = (
                    [("country_cap", k) for k in CAPITALS]
                    + [("state_cap", k) for k in US_STATE_CAPITALS]
                    + [("country_flag", k) for k in COUNTRY_CODES]
                    + [("state_flag", k) for k in US_STATE_CODES]
                    + [("landmark", n) for n, _, _ in _LANDMARK_POOL]
                )

        q_type, key = random.choice(pool)
        table.used_questions.add((q_type, key))

        if q_type == "country_cap":
            return q_type, key, CAPITALS[key], None
        elif q_type == "state_cap":
            return q_type, key, US_STATE_CAPITALS[key], None
        elif q_type == "country_flag":
            code = COUNTRY_CODES[key]
            url = f"https://flagcdn.com/w320/{code}.png"
            return q_type, key, _alias_answers(key), url
        elif q_type == "landmark":
            entry = next(((n, c, u) for n, c, u in _LANDMARK_POOL if n == key), None)
            if entry is None:
                raise KeyError(f"Landmark key {key!r} not found in _LANDMARK_POOL")
            name, country, url = entry
            return q_type, name, _alias_answers(country), url
        else:  # state_flag
            code = US_STATE_CODES[key]
            url = f"https://flagcdn.com/w320/us-{code}.png"
            return q_type, key, [key], url

    def _question_text(self, q_type: str, subject: str) -> str:
        """Build the question string shown during a round."""
        if q_type == "country_cap":
            return f"What is the capital of **{subject}**?"
        elif q_type == "state_cap":
            return f"What is the capital of the US state **{subject}**?"
        elif q_type == "country_flag":
            return "Which country does this flag belong to?"
        elif q_type == "landmark":
            return "Which country is this landmark in?"
        else:  # state_flag
            return "Which US state does this flag belong to?"

    async def _start_race(self, interaction: discord.Interaction) -> None:
        """Start the race: set up round 1, show it, then launch the race loop."""
        table = self.table

        # Set up round 1
        q_type, subject, answers, image_url = self._pick_question()
        table.current_subject = subject
        table.current_q_type = q_type
        table.current_question_text = self._question_text(q_type, subject)
        table.current_answers = answers
        table.current_image_url = image_url
        table.round_num = 1
        table.round_winner = None
        table.round_solved.clear()
        table.round_messages.clear()
        table.phase = "playing"
        table.round_start_time = time.monotonic()

        for p in table.players.values():
            p.answer = None
            p.answer_time = None

        self._update_buttons()
        in_progress = discord.Embed(
            title="\U0001f30d Geography \u2014 In Progress",
            description="Game running in the thread below! Type your answers there.",
            colour=discord.Colour.blue(),
        )
        players_text = ", ".join(p.display_name for p in table.players.values())
        in_progress.add_field(name="Players", value=players_text or "\u2014", inline=False)
        await interaction.response.edit_message(embed=in_progress, view=self)

        msg = await interaction.original_response()
        thread = await msg.create_thread(name=f"Geography \u2014 {table.host_name}")
        table.thread = thread

        await thread.send(
            "\U0001f3c1 **Geography started!** Type your answers here.",
            view=GeoEndGameView(table),
        )

        table.message = await thread.send(embed=_playing_embed(table))

        # Launch the race loop
        table.race_task = asyncio.create_task(self._race_loop())

    async def _wait_for_solve_or_timeout(self) -> bool:
        """Wait for someone to solve the round or for timeout.

        Updates the embed with remaining time every 10 seconds.
        Returns True if solved.
        """
        table = self.table
        deadline = table.round_start_time + ROUND_TIME

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

    async def _clear_round_messages(self) -> None:
        """Delete all player guess messages from the current round."""
        messages = list(self.table.round_messages)
        self.table.round_messages.clear()
        for msg in messages:
            try:
                await msg.delete()
            except discord.HTTPException:
                pass

    async def _race_loop(self) -> None:
        """Main race loop. Round 1 is already dealt by _start_race."""
        table = self.table
        try:
            rnd = 0
            while True:
                rnd += 1

                # Round 1 was set up by _start_race; rounds 2+ dealt here
                if rnd > 1:
                    q_type, subject, answers, image_url = self._pick_question()
                    table.current_subject = subject
                    table.current_q_type = q_type
                    table.current_question_text = self._question_text(q_type, subject)
                    table.current_answers = answers
                    table.current_image_url = image_url
                    table.round_num = rnd
                    table.round_winner = None
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

                # Wait for solve or timeout
                solved = await self._wait_for_solve_or_timeout()
                table.total_rounds_played += 1

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

                # Record a miss for players who didn't answer this round
                for p in table.players.values():
                    if p.answer is None:
                        _fire_stat(str(p.user_id), table, correct=False)

                # Check if someone hit the win target or safety cap
                if any(p.rounds_won >= WINS_TO_WIN for p in table.players.values()):
                    break
                if rnd >= MAX_ROUNDS:
                    break

                # Clear guess messages before the next round starts
                await self._clear_round_messages()

                # Delay before next round
                table.phase = "between_rounds"
                await asyncio.sleep(ROUND_DELAY)

                if table.stop_requested:
                    break

            # Clear final round's messages before showing results
            await self._clear_round_messages()

            if table.stop_requested and table.thread:
                try:
                    await table.thread.send("\u23f9\ufe0f Game ended early.")
                except discord.HTTPException:
                    pass

            # Race complete — show results
            await self._end_game()

        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("_race_loop crashed for channel %s", table.channel_id)
            table.phase = "closed"
            self.active_tables.pop(table.channel_id, None)

    async def _end_game(self) -> None:
        """End the race: update ELO and show final results."""
        table = self.table
        table.phase = "closed"

        elo_changes: dict[int, tuple[float, float]] = {}
        if len(table.players) >= 2:
            sorted_p = sorted(table.players.values(), key=lambda p: p.rounds_won, reverse=True)
            finish_order = [p.user_id for p in sorted_p]
            try:
                elo_changes = await update_elo_multiplayer(finish_order, "geography", "geography")
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
        """Close from betting phase or after race ends."""
        table = self.table

        if table.total_rounds_played == 0:
            embed = discord.Embed(
                title="\U0001f30d Geography Table \u2014 Closed",
                description="Table closed before any rounds were played.",
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
                    title="\U0001f30d Geography Table \u2014 Timed Out",
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


# ── Accuracy stat helpers ──────────────────────────────────────────────────

# Map q_type to the DB category column
_QTYPE_TO_CATEGORY: dict[str, str] = {
    "country_cap": "country_cap",
    "state_cap": "state_cap",
    "country_flag": "country_flag",
    "state_flag": "state_flag",
    "landmark": "landmark",
}


def _stat_info(table: GeoTable) -> tuple[str, str, str]:
    """Return (country, region, category) for the current round's question."""
    q_type = table.current_q_type
    category = _QTYPE_TO_CATEGORY.get(q_type, q_type)

    if q_type in ("state_cap", "state_flag"):
        return "US", "Americas", category

    # For landmarks, subject is the landmark name; the country is the answer
    if q_type == "landmark":
        country = table.current_answers[0] if table.current_answers else "Unknown"
    else:
        country = table.current_subject
    region = COUNTRY_REGIONS.get(country, "Unknown")
    return country, region, category


def _fire_stat(discord_user: str, table: GeoTable, correct: bool) -> None:
    """Fire-and-forget a geo accuracy DB write."""
    country, region, category = _stat_info(table)
    asyncio.create_task(
        record_geo_attempt(discord_user, country, region, category, correct)
    )


# ── Cog ─────────────────────────────────────────────────────────────────────


class GeographyCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, GeoTable] = {}

    @app_commands.command(
        name="geography",
        description="Open a Speed Geography table (multiplayer)",
    )
    async def geography(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            await interaction.response.send_message(
                "There's already a geography table in this channel!",
                ephemeral=True,
            )
            return

        table = GeoTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
        )
        self.active_tables[channel_id] = table

        view = GeoTableView(table, self.active_tables)
        embed = _betting_embed(table)
        await interaction.response.send_message(embed=embed, view=view)
        table.message = await interaction.original_response()

    @app_commands.command(
        name="geo-stats",
        description="Your geography accuracy stats by region",
    )
    async def geo_stats(
        self, interaction: discord.Interaction, user: discord.Member | None = None,
    ) -> None:
        target = user or interaction.user
        user_id = str(target.id)

        region_stats = await get_geo_stats_by_region(user_id)
        elo_data = await get_elo_rating(user_id, "geography")

        total_correct = sum(r["correct"] for r in region_stats)
        total_attempts = sum(r["total"] for r in region_stats)
        overall_pct = (total_correct / total_attempts * 100) if total_attempts > 0 else 0.0

        embed = discord.Embed(
            title=f"\U0001f30d Geography Stats — {target.display_name}",
            colour=discord.Colour.blue(),
        )

        if total_attempts == 0:
            embed.description = "No geography attempts recorded yet."
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed.description = f"**Overall Accuracy:** {total_correct}/{total_attempts} ({overall_pct:.1f}%)"

        region_emojis = {
            "Europe": "\U0001f1ea\U0001f1fa",
            "Asia": "\U0001f30f",
            "Africa": "\U0001f30d",
            "Americas": "\U0001f30e",
            "Oceania": "\U0001f3dd\ufe0f",
        }

        weakest_region = None
        weakest_pct = 101.0

        lines: list[str] = []
        for r in region_stats:
            pct = r["correct"] / r["total"] * 100 if r["total"] > 0 else 0.0
            if r["total"] > 0 and pct < weakest_pct:
                weakest_pct = pct
                weakest_region = r["region"]

            if pct >= 70:
                indicator = "\U0001f7e2"
            elif pct >= 40:
                indicator = "\U0001f7e1"
            else:
                indicator = "\U0001f534"

            emoji = region_emojis.get(r["region"], "\U0001f30d")
            lines.append(
                f"{indicator} {emoji} **{r['region']}** — "
                f"{r['correct']}/{r['total']} ({pct:.1f}%)"
            )

        embed.add_field(name="By Region", value="\n".join(lines), inline=False)

        if weakest_region:
            emoji = region_emojis.get(weakest_region, "\U0001f30d")
            embed.add_field(
                name="\U0001f4aa Weakest Region",
                value=f"{emoji} {weakest_region} ({weakest_pct:.1f}%)",
                inline=True,
            )

        games_played = elo_data.get("games_played", 0)
        embed.add_field(name="Games Played", value=str(games_played), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message) -> None:
        """Listen for chat answers during active geography rounds."""
        if message.author.bot:
            return

        table = None
        for t in self.active_tables.values():
            if t.thread is not None and t.thread.id == message.channel.id:
                table = t
                break
        if table is None or table.phase != "playing":
            return

        uid = message.author.id
        if uid not in table.players:
            return

        if table.round_winner is not None:
            return

        guess = message.content.strip()
        # Ignore very short or numeric messages
        if len(guess) < 3 or guess.isdigit():
            return

        # Only react on things that look like geography answers (mostly letters)
        alpha_chars = sum(1 for c in guess if c.isalpha())
        if alpha_chars < len(guess) * 0.5:
            return

        # Track all guess messages for post-round cleanup
        table.round_messages.append(message)

        player = table.players[uid]

        if check_answer(guess, table.current_answers):
            # Winner!
            now = time.monotonic()
            player.answer = guess
            player.answer_time = now
            player.rounds_won += 1
            table.round_winner = uid

            _fire_stat(str(uid), table, correct=True)

            try:
                await message.add_reaction("\u2705")
            except discord.HTTPException:
                pass

            table.round_solved.set()
        else:
            # Only record a miss on the first wrong guess this round
            if player.answer is None:
                _fire_stat(str(uid), table, correct=False)
            player.answer = "__wrong__"

            try:
                await message.add_reaction("\u274c")
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GeographyCog(bot))
