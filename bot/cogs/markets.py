"""Prediction market commands — /kalshi."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from db import queries
from shared.models import get_team_abbr
from shared.odds_utils import prob_to_american
from .odds import KALSHI_SERIES, game_autocomplete, mlb_game_autocomplete
import logging

log = logging.getLogger(__name__)
load_dotenv()

KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"


def _fmt_prob(p: float) -> str:
    return f"{p * 100:.1f}%"


def _mid_cell(bid: float | None, ask: float | None, last: float | None) -> tuple[str, str, str]:
    """Return (bid_str, ask_str, mid_str) for display."""
    bid_str = _fmt_prob(bid) if bid else "—"
    ask_str = _fmt_prob(ask) if ask else "—"

    if bid and ask:
        mid = (bid + ask) / 2
    elif last:
        mid = last
    else:
        return bid_str, ask_str, "—"

    if not (0 < mid < 1):
        return bid_str, ask_str, "—"

    try:
        american = prob_to_american(mid)
        sign = "+" if american > 0 else ""
        mid_str = f"{_fmt_prob(mid)} ({sign}{american})"
    except (ValueError, ZeroDivisionError):
        mid_str = _fmt_prob(mid)

    return bid_str, ask_str, mid_str


class MarketsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _kalshi_impl(self, interaction: discord.Interaction, game: str, sport: str) -> None:
        await interaction.response.defer()

        target = await queries.get_game_by_id(game)
        if target is None:
            await interaction.followup.send("Game not found.")
            return

        if not KALSHI_API_KEY:
            await interaction.followup.send("Kalshi API key not configured.")
            return

        h_abbr = get_team_abbr(target.home_team, sport)
        a_abbr = get_team_abbr(target.away_team, sport)
        if not h_abbr or not a_abbr:
            await interaction.followup.send(
                f"No Kalshi abbreviation for `{target.home_team}` or `{target.away_team}`."
            )
            return

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{KALSHI_BASE}/markets",
                    headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
                    params={"limit": 200, "status": "open", "series_ticker": KALSHI_SERIES.get(sport, "KXNBAGAME")},
                    timeout=10.0,
                )
        except Exception as e:
            await interaction.followup.send(f"Could not reach Kalshi: {e}")
            return

        if resp.status_code != 200:
            await interaction.followup.send(f"Kalshi API error: HTTP {resp.status_code}")
            return

        markets = resp.json().get("markets", [])

        # Find the two markets for this game by matching abbr pair in event ticker
        game_markets: dict[str, dict] = {}
        for m in markets:
            et = m.get("event_ticker", "")
            team_part = et.split("-")[-1]
            if team_part[-3:].upper() != h_abbr or team_part[-6:-3].upper() != a_abbr:
                continue
            suffix = m.get("ticker", "").split("-")[-1].upper()
            game_markets[suffix] = m

        if not game_markets:
            await interaction.followup.send(
                f"No open Kalshi market found for **{target.away_team} @ {target.home_team}**. "
                "The market may not be open yet or has already closed."
            )
            return

        fetched_at = datetime.now(timezone.utc).astimezone(_ET)

        # Build one row per side
        table_rows: list[tuple[str, str, str, str, str]] = []
        for abbr, team_name in [(a_abbr, target.away_team), (h_abbr, target.home_team)]:
            m = game_markets.get(abbr)
            if m is None:
                table_rows.append((team_name.split()[-1], "—", "—", "—", "—"))
                continue
            bid = float(m["yes_bid_dollars"]) if m.get("yes_bid_dollars") else None
            ask = float(m["yes_ask_dollars"]) if m.get("yes_ask_dollars") else None
            last = float(m["last_price_dollars"]) if m.get("last_price_dollars") else None
            volume = m.get("volume") or 0
            bid_str, ask_str, mid_str = _mid_cell(bid, ask, last)
            table_rows.append((team_name.split()[-1], bid_str, ask_str, mid_str, f"{volume:,}" if volume else "—"))

        # Column widths
        name_w = max(len(r[0]) for r in table_rows) + 2
        C = [name_w, 8, 8, 22, 8]
        header = f"{'Team':<{C[0]}}{'Bid':<{C[1]}}{'Ask':<{C[2]}}{'Mid':<{C[3]}}Volume"
        divider = "─" * (sum(C) + 2)
        lines = [header, divider]
        for name, bid_s, ask_s, mid_s, vol_s in table_rows:
            lines.append(f"{name:<{C[0]}}{bid_s:<{C[1]}}{ask_s:<{C[2]}}{mid_s:<{C[3]}}{vol_s}")

        h = fetched_at.hour % 12 or 12
        ampm = "AM" if fetched_at.hour < 12 else "PM"
        fetched_str = f"{fetched_at.strftime('%b')} {fetched_at.day}, {h}:{fetched_at.strftime('%M')} {ampm} {fetched_at.strftime('%Z')}"

        embed = discord.Embed(
            title=f"Kalshi — {target.away_team} @ {target.home_team}",
            description=(
                f"*fetched {fetched_str}*\n\n"
                "```\n" + "\n".join(lines) + "\n```"
            ),
            color=0x5865F2,
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="kalshi", description="Kalshi market depth for an NBA game")
    @app_commands.describe(game="Select a game")
    @app_commands.autocomplete(game=game_autocomplete)
    async def kalshi(self, interaction: discord.Interaction, game: str) -> None:
        await self._kalshi_impl(interaction, game, "nba")

    @app_commands.command(name="mlb-kalshi", description="Kalshi market depth for an MLB game")
    @app_commands.describe(game="Select a game")
    @app_commands.autocomplete(game=mlb_game_autocomplete)
    async def mlb_kalshi(self, interaction: discord.Interaction, game: str) -> None:
        await self._kalshi_impl(interaction, game, "mlb")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MarketsCog(bot))
