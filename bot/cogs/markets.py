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
from shared.models import get_team_abbr, get_kalshi_code
from shared.odds_utils import prob_to_american, kalshi_exec_price, KALSHI_TAKER_FEE
from .odds import KALSHI_SERIES, game_autocomplete, mlb_game_autocomplete
import logging

log = logging.getLogger(__name__)
load_dotenv()

KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# Game-level Kalshi series by sport: moneyline, spread/run-line, total.
KALSHI_DERIV = {
    "nba": {"ml": "KXNBAGAME", "spread": "KXNBASPREAD", "total": "KXNBATOTAL"},
    "mlb": {"ml": "KXMLBGAME", "spread": "KXMLBSPREAD", "total": "KXMLBTOTAL"},
}


def _et_date_code(start_utc_iso: str) -> str:
    """Kalshi event-ticker date code (e.g. '26JUN01') for a game's ET date."""
    try:
        dt = datetime.fromisoformat(start_utc_iso.replace("Z", "+00:00")).astimezone(_ET)
        return dt.strftime("%y%b%d").upper()
    except (ValueError, AttributeError):
        return ""


def _event_key(event_ticker: str) -> str:
    """The '<date><time><teams>' middle segment of a Kalshi event ticker."""
    parts = event_ticker.split("-")
    return parts[1] if len(parts) > 1 else event_ticker


def _matches_game(event_ticker: str, h: str, a: str, date_code: str) -> bool:
    """True if a Kalshi event is this game: team codes concatenated at the end of
    the key, and (if known) the right date."""
    key = _event_key(event_ticker).upper()
    teams_ok = key.endswith(a + h) or key.endswith(h + a)
    return teams_ok and (not date_code or key.startswith(date_code))


async def _kalshi_markets(client: httpx.AsyncClient, series: str) -> list[dict]:
    try:
        r = await client.get(
            f"{KALSHI_BASE}/markets",
            headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
            params={"limit": 200, "status": "open", "series_ticker": series},
            timeout=10.0,
        )
        return r.json().get("markets", []) if r.status_code == 200 else []
    except Exception:
        return []


async def _orderbook(client: httpx.AsyncClient, ticker: str, depth: int = 6) -> dict | None:
    try:
        r = await client.get(
            f"{KALSHI_BASE}/markets/{ticker}/orderbook",
            headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
            params={"depth": depth}, timeout=10.0,
        )
        if r.status_code != 200:
            return None
        j = r.json()
        return j.get("orderbook_fp") or j.get("orderbook") or {}
    except Exception:
        return None


def _ask_ladder(ob: dict) -> list[tuple[float, float]]:
    """Cost-to-BUY-yes ladder from the NO-side bids: each resting no bid at price
    n (size q) lets you buy q yes contracts at (1 - n). Best (lowest) ask first."""
    levels = [(1 - float(p), float(q)) for p, q in (ob.get("no_dollars") or []) if 0 < float(p) < 1]
    return sorted(levels, key=lambda x: x[0])


def _am(american: int) -> str:
    return f"+{american}" if american > 0 else str(american)


def _fee_american(price: float | None) -> str:
    """Fee-inclusive American odds for a raw yes price (cost to back)."""
    if price is None or not (0 < price < 1):
        return "—"
    eff = min(price + KALSHI_TAKER_FEE * price * (1 - price), 0.99)
    try:
        return _am(prob_to_american(eff))
    except (ValueError, ZeroDivisionError):
        return "—"


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

        h_abbr = get_kalshi_code(target.home_team, sport)
        a_abbr = get_kalshi_code(target.away_team, sport)
        if not h_abbr or not a_abbr:
            await interaction.followup.send(
                f"No Kalshi code for `{target.home_team}` or `{target.away_team}`."
            )
            return

        date_code = _et_date_code(target.start_time_utc_iso)
        try:
            async with httpx.AsyncClient() as client:
                markets = await _kalshi_markets(client, KALSHI_SERIES.get(sport, "KXNBAGAME"))
        except Exception as e:
            await interaction.followup.send(f"Could not reach Kalshi: {e}")
            return

        # Find the two ML markets for this game by team-code pair (+ date).
        game_markets: dict[str, dict] = {}
        for m in markets:
            if not _matches_game(m.get("event_ticker", ""), h_abbr, a_abbr, date_code):
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

    async def _orderbook_impl(self, interaction: discord.Interaction, game: str, sport: str) -> None:
        await interaction.response.defer()
        target = await queries.get_game_by_id(game)
        if target is None:
            await interaction.followup.send("Game not found."); return
        if not KALSHI_API_KEY:
            await interaction.followup.send("Kalshi API key not configured."); return

        h, a = get_kalshi_code(target.home_team, sport), get_kalshi_code(target.away_team, sport)
        if not h or not a:
            await interaction.followup.send(f"No Kalshi code for `{target.home_team}`/`{target.away_team}`."); return
        date_code = _et_date_code(target.start_time_utc_iso)
        series = KALSHI_DERIV.get(sport, KALSHI_DERIV["nba"])

        async with httpx.AsyncClient() as client:
            ml_mkts = [m for m in await _kalshi_markets(client, series["ml"])
                       if _matches_game(m.get("event_ticker", ""), h, a, date_code)]
            sp_mkts = [m for m in await _kalshi_markets(client, series["spread"])
                       if _matches_game(m.get("event_ticker", ""), h, a, date_code)]
            tot_mkts = [m for m in await _kalshi_markets(client, series["total"])
                        if _matches_game(m.get("event_ticker", ""), h, a, date_code)]
            if not (ml_mkts or sp_mkts or tot_mkts):
                await interaction.followup.send(
                    f"No open Kalshi markets for **{target.away_team} @ {target.home_team}**."); return

            # ── Moneyline: full ask-ladder depth per team ──
            ml_by = {m.get("ticker", "").split("-")[-1].upper(): m for m in ml_mkts}
            ml_lines = []
            for code, name in [(a, target.away_team), (h, target.home_team)]:
                m = ml_by.get(code)
                if not m:
                    ml_lines.append(f"**{name}** — no market"); continue
                ob = await _orderbook(client, m["ticker"])
                ladder = _ask_ladder(ob) if ob else []
                best = ladder[0][0] if ladder else (float(m["yes_ask_dollars"]) if m.get("yes_ask_dollars") else None)
                depth = " · ".join(f"{int(p*100)}¢×{int(q)}" for p, q in ladder[:5]) or "—"
                ml_lines.append(f"**{name}** — back at **{_fee_american(best)}** *(w/ fee)*\n   ask depth: {depth}")

        def _line_rows(mkts, kind):
            rows = []
            for m in mkts:
                ask = float(m["yes_ask_dollars"]) if m.get("yes_ask_dollars") else None
                bid = float(m["yes_bid_dollars"]) if m.get("yes_bid_dollars") else None
                vol = m.get("volume") or 0
                sub = m.get("yes_sub_title") or m.get("ticker", "").split("-")[-1]
                px = f"{int(ask*100)}¢" if ask else "—"
                rows.append((sub, f"{sub} — bid {int(bid*100) if bid else '—'}¢ / ask {px} → **{_fee_american(ask)}**"
                                  + (f"  ·  {vol:,} vol" if vol else "")))
            rows.sort(key=lambda r: r[0])
            return [r[1] for r in rows]

        embed = discord.Embed(
            title=f"📖 Kalshi orderbook — {target.away_team} @ {target.home_team}",
            description="Prices are **fee-inclusive** (Kalshi taker fee added to the ask).",
            color=0x5865F2,
        )
        if ml_lines:
            embed.add_field(name="💰 Moneyline", value="\n".join(ml_lines)[:1024], inline=False)
        sp_rows = _line_rows(sp_mkts, "spread")
        if sp_rows:
            embed.add_field(name=("⚾ Run line" if sport == "mlb" else "🏀 Spread"),
                            value="\n".join(sp_rows)[:1024], inline=False)
        tot_rows = _line_rows(tot_mkts, "total")
        if tot_rows:
            embed.add_field(name="📊 Total", value="\n".join(tot_rows)[:1024], inline=False)
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

    @app_commands.command(name="kalshi-orderbook", description="Full Kalshi orderbook + spread/total markets for an NBA game")
    @app_commands.describe(game="Select a game")
    @app_commands.autocomplete(game=game_autocomplete)
    async def kalshi_orderbook(self, interaction: discord.Interaction, game: str) -> None:
        await self._orderbook_impl(interaction, game, "nba")

    @app_commands.command(name="mlb-kalshi-orderbook", description="Full Kalshi orderbook + run line/total markets for an MLB game")
    @app_commands.describe(game="Select a game")
    @app_commands.autocomplete(game=mlb_game_autocomplete)
    async def mlb_kalshi_orderbook(self, interaction: discord.Interaction, game: str) -> None:
        await self._orderbook_impl(interaction, game, "mlb")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MarketsCog(bot))
