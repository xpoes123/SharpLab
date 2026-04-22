"""Casino cog — multiplayer /geography speed race.

Given a country, first to type its capital wins the round.
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
MIN_PLAYERS = 2
ROUND_TIME = 30  # seconds per round
ROUND_DELAY = 4  # seconds between rounds
WINS_TO_WIN = 3  # first to N wins
MAX_ROUNDS = 15  # safety cap

# Paytable: fraction of prize pool by finishing position, keyed by player count
PAYTABLE: dict[int, list[float]] = {
    2: [1.0],
    3: [0.70, 0.30],
    4: [0.55, 0.30, 0.15],
    5: [0.45, 0.25, 0.18, 0.12],
    6: [0.40, 0.24, 0.16, 0.12, 0.08],
    7: [0.36, 0.22, 0.16, 0.12, 0.08, 0.06],
    8: [0.33, 0.21, 0.16, 0.12, 0.08, 0.06, 0.04],
}

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


# ── Payout helpers ──────────────────────────────────────────────────────────


def _compute_payouts(
    players: dict[int, "GeoPlayer"], prize_pool: int, n_players: int,
) -> dict[int, int]:
    """Compute per-player payouts using the paytable.

    Only players with rounds_won > 0 are in the money.
    Ties split the combined shares for occupied positions.
    Unused paid positions roll up to first place.
    """
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

    # Unused positions roll up to first place
    total_paid = sum(payouts.values())
    leftover = prize_pool - total_paid
    if leftover > 0 and in_money:
        top_wins = in_money[0].rounds_won
        top_group = [p for p in in_money if p.rounds_won == top_wins]
        extra = leftover // len(top_group)
        for p in top_group:
            payouts[p.user_id] += extra

    return payouts


# ── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class GeoPlayer:
    user_id: int
    display_name: str
    bet: int
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
    current_country: str = ""
    current_answers: list[str] = field(default_factory=list)
    round_start_time: float = 0.0
    round_winner: int | None = None
    race_task: asyncio.Task | None = field(default=None, repr=False)
    round_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    total_rounds_played: int = 0
    used_countries: list[str] = field(default_factory=list)


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


def _betting_embed(table: GeoTable) -> discord.Embed:
    pot = sum(p.bet for p in table.players.values())
    n = len(table.players)

    embed = discord.Embed(
        title="\U0001f30d Speed Geography",
        description=(
            f"Name the capital city! **First to {WINS_TO_WIN} wins** takes the pot.\n"
            "Type your answer directly in chat \u2014 fastest correct answer wins each round!"
        ),
        colour=discord.Colour.blue(),
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
            f"\U0001f30e **{p.display_name}** \u2014 {p.bet}c"
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
        f"# What is the capital of **{table.current_country}**?\n\n"
        "**Type your answer in chat!**"
    )

    secs = remaining if remaining is not None else ROUND_TIME
    embed.add_field(name="\u23f1\ufe0f Time", value=f"**{secs}s**", inline=True)

    pot = sum(p.bet for p in table.players.values())
    embed.add_field(name="Pot", value=f"{pot}c", inline=True)

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
        f"\U0001f1fa\U0001f1f3 {table.current_country} \u2192 **{canonical}**"
    )
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
        f"\U0001f1fa\U0001f1f3 {table.current_country} \u2192 **{canonical}**"
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round complete \u2014 calculating results\u2026")
    return embed


def _final_embed(
    table: GeoTable,
    *,
    payouts: dict[int, int],
    balances: dict[int, int],
) -> discord.Embed:
    max_wins = max((p.rounds_won for p in table.players.values()), default=0)
    is_refund = max_wins == 0

    embed = discord.Embed(
        title="\U0001f30d Speed Geography \u2014 Results",
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


# ── Modals ──────────────────────────────────────────────────────────────────


class JoinGeoModal(ui.Modal):
    amount = ui.TextInput(
        label="Bet amount (coins)",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
    )

    def __init__(
        self, table: GeoTable, view: "GeoTableView", balance: int,
    ) -> None:
        super().__init__(title="Join Speed Geography")
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

        self.table.players[uid] = GeoPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
            bet=amt,
        )

        self.table_view._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self.table_view,
        )


# ── View ────────────────────────────────────────────────────────────────────


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
        self.rebet_btn.disabled = not betting or not self.table.last_bets
        self.leave_btn.disabled = not betting
        self.close_btn.disabled = racing

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
        bal = await queries.get_or_create_casino_wallet(str(uid))
        await interaction.response.send_modal(
            JoinGeoModal(self.table, self, bal),
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
                "Race in progress!", ephemeral=True,
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
        self.table.players[uid] = GeoPlayer(
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
                "Can't leave during a race!", ephemeral=True,
            )
            return
        await queries.update_casino_balance(str(uid), player.bet)
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

    # ── Race logic ──────────────────────────────────────────────────────

    def _pick_country(self) -> tuple[str, list[str]]:
        """Pick a random country that hasn't been used yet."""
        available = [c for c in CAPITALS if c not in self.table.used_countries]
        if not available:
            # All used — reset
            available = list(CAPITALS.keys())
            self.table.used_countries.clear()
        country = random.choice(available)
        self.table.used_countries.append(country)
        return country, CAPITALS[country]

    async def _start_race(self, interaction: discord.Interaction) -> None:
        """Start the race: set up round 1, show it, then launch the race loop."""
        table = self.table

        # Save last bets for re-bet
        for uid, p in table.players.items():
            table.last_bets[uid] = (p.display_name, p.bet)

        # Set up round 1
        country, answers = self._pick_country()
        table.current_country = country
        table.current_answers = answers
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

    async def _race_loop(self) -> None:
        """Main race loop. Round 1 is already dealt by _start_race."""
        table = self.table
        try:
            rnd = 0
            while True:
                rnd += 1

                # Round 1 was set up by _start_race; rounds 2+ dealt here
                if rnd > 1:
                    country, answers = self._pick_country()
                    table.current_country = country
                    table.current_answers = answers
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

                # Wait for solve or timeout
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

                # Check if someone hit the win target or safety cap
                if any(p.rounds_won >= WINS_TO_WIN for p in table.players.values()):
                    break
                if rnd >= MAX_ROUNDS:
                    break

                # Delay before next round
                table.phase = "between_rounds"
                await asyncio.sleep(ROUND_DELAY)

            # Race complete — pay out
            await self._end_game()

        except asyncio.CancelledError:
            pass
        except Exception:
            table.phase = "closed"
            self.active_tables.pop(table.channel_id, None)

    async def _compute_and_apply_payouts(
        self,
    ) -> tuple[dict[int, int], dict[int, int]]:
        """Compute payouts, apply balance changes, log results."""
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
            await queries.log_casino_result(str(uid), "geography", p.bet, payout)

        return payouts, balances

    async def _end_game(self) -> None:
        """End the race: compute payouts and show final results."""
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
        """Close from betting phase or after race ends."""
        table = self.table

        if table.total_rounds_played == 0:
            for p in table.players.values():
                try:
                    await queries.update_casino_balance(str(p.user_id), p.bet)
                except Exception:
                    pass
            embed = discord.Embed(
                title="\U0001f30d Geography Table \u2014 Closed",
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
                    title="\U0001f30d Geography Table \u2014 Timed Out",
                    description="Table timed out. All bets refunded.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                pass


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

        await queries.get_or_create_casino_wallet(str(interaction.user.id))

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

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message) -> None:
        """Listen for chat answers during active geography rounds."""
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
        # Ignore very short or numeric messages
        if len(guess) < 3 or guess.isdigit():
            return

        # Only react on things that look like geography answers (mostly letters)
        alpha_chars = sum(1 for c in guess if c.isalpha())
        if alpha_chars < len(guess) * 0.5:
            return

        if check_answer(guess, table.current_answers):
            # Winner!
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
    await bot.add_cog(GeographyCog(bot))
