"""Odds commands — live-poll The Odds API with a 10-min per-game cooldown."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from db import queries
from shared.models import OddsSnapshot

load_dotenv()

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
COOLDOWN_SECONDS = 600  # one live poll per game per 10 minutes


# ── Live fetch helpers ──────────────────────────────────────────────────────────

def _extract_payload(bookmaker: dict, home_team: str) -> dict[str, Any]:
    """Normalize one bookmaker's markets into our standard payload shape.
    Mirrors temporal/activities.py — keep in sync if API shape changes."""
    payload: dict[str, Any] = {}
    for market in bookmaker.get("markets", []):
        key = market["key"]
        outcomes = {o["name"]: o for o in market["outcomes"]}

        if key == "spreads":
            home = outcomes.get(home_team, {})
            away = [o for n, o in outcomes.items() if n != home_team]
            payload["spread"] = home.get("point")
            payload["spread_odds"] = home.get("price")
            if away:
                payload["spread_away"] = away[0].get("point")

        elif key == "h2h":
            home = outcomes.get(home_team, {})
            away = [o for n, o in outcomes.items() if n != home_team]
            payload["ml_home"] = home.get("price")
            payload["ml_away"] = away[0].get("price") if away else None

        elif key == "totals":
            over = outcomes.get("Over", {})
            under = outcomes.get("Under", {})
            payload["total"] = over.get("point")
            payload["total_over_odds"] = over.get("price")
            payload["total_under_odds"] = under.get("price")

    return payload


async def _live_poll_and_store(game_id: str) -> list[OddsSnapshot]:
    """Fetch fresh odds for one game from The Odds API and write to DB."""
    captured_at = datetime.now(timezone.utc).isoformat()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ODDS_API_BASE}/sports/basketball_nba/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "spreads,totals,h2h",
                "oddsFormat": "american",
                "eventIds": game_id,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        events = resp.json()

    snapshots: list[OddsSnapshot] = []
    for event in events:
        home_team = event["home_team"]
        for bookmaker in event.get("bookmakers", []):
            payload = _extract_payload(bookmaker, home_team)
            if not payload:
                continue
            snap = OddsSnapshot(
                snapshot_id=f"poll:{game_id}:{bookmaker['key']}:{captured_at}",
                game_id=game_id,
                kind="poll",
                source=bookmaker["key"],
                captured_at_utc_iso=captured_at,
                payload=payload,
            )
            await queries.upsert_odds_snapshot(snap)
            snapshots.append(snap)

    return snapshots


def _age_seconds(iso_str: str) -> float:
    """Seconds since an ISO 8601 timestamp. Returns inf on parse failure."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return float("inf")


async def _get_snapshots(game_id: str) -> tuple[list[OddsSnapshot], str]:
    """Return snapshots for a game, live-polling if data is stale.

    Returns (snapshots, source_label) where source_label is 'live' or 'cached · X min ago'.
    """
    existing = await queries.get_latest_snapshots_for_game(game_id)
    most_recent = max(existing, key=lambda s: s.captured_at_utc_iso) if existing else None
    age = _age_seconds(most_recent.captured_at_utc_iso) if most_recent else float("inf")
    cached_label = f"cached · {_staleness(most_recent.captured_at_utc_iso)}" if most_recent else "cached"

    if age < COOLDOWN_SECONDS:
        return existing, cached_label

    # Stale or missing — go live
    try:
        fresh = await _live_poll_and_store(game_id)
        if fresh:
            return fresh, "live"
        # API returned nothing (game may have started/closed)
        if existing:
            return existing, cached_label
        return [], "no data"
    except Exception:
        if existing:
            return existing, f"{cached_label} (poll failed)"
        return [], "poll failed"


# Books to display in order (skip obscure ones)
DISPLAY_BOOKS = [
    "draftkings", "fanduel", "betmgm", "caesars", "pointsbet",
    "bet365", "williamhill_us", "barstool", "betonlineag",
]

BOOK_LABELS = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "caesars": "Caesars",
    "pointsbet": "PointsBet",
    "bet365": "Bet365",
    "williamhill_us": "Caesars (WH)",
    "barstool": "Barstool",
    "betonlineag": "BetOnline",
}


def _fmt_american(odds: int | None) -> str:
    if odds is None:
        return "n/a"
    return f"+{odds}" if odds > 0 else str(odds)


def _staleness(captured_at_iso: str) -> str:
    """Human-readable staleness string, e.g. '12 min ago'."""
    try:
        captured = datetime.fromisoformat(captured_at_iso)
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - captured
        minutes = int(delta.total_seconds() // 60)
        if minutes < 1:
            return "just now"
        if minutes < 60:
            return f"{minutes} min ago"
        return f"{minutes // 60}h {minutes % 60}m ago"
    except Exception:
        return "?"


def _build_odds_table(snapshots: list[OddsSnapshot]) -> str:
    """Render a fixed-width table of odds across books."""
    # Prefer display order, then include any remaining books
    book_order = [s.source for s in snapshots if s.source in DISPLAY_BOOKS]
    book_order += [s.source for s in snapshots if s.source not in DISPLAY_BOOKS]
    snap_by_source = {s.source: s for s in snapshots}

    lines = [f"{'Book':<14} {'Spread':>12}  {'ML':>14}  {'Total':>18}"]
    lines.append("─" * 62)

    for source in book_order:
        snap = snap_by_source[source]
        p = snap.payload
        label = BOOK_LABELS.get(source, source)[:13]

        spread_str = (
            f"{p.get('spread', ''):>+.1f} ({_fmt_american(p.get('spread_odds'))})"
            if p.get("spread") is not None else "—"
        )
        ml_str = (
            f"{_fmt_american(p.get('ml_home'))} / {_fmt_american(p.get('ml_away'))}"
            if p.get("ml_home") is not None else "—"
        )
        total_str = (
            f"O/U {p.get('total', '—')} ({_fmt_american(p.get('total_over_odds'))}/{_fmt_american(p.get('total_under_odds'))})"
            if p.get("total") is not None else "—"
        )

        lines.append(f"{label:<14} {spread_str:>12}  {ml_str:>14}  {total_str}")

    return "\n".join(lines)


class OddsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /odds ──────────────────────────────────────────────────────────────────

    @app_commands.command(name="odds", description="Live lines for a game across all books (polls API, 10-min cooldown per game)")
    @app_commands.describe(game="Team name to search for (e.g. 'Lakers', 'Boston')")
    async def odds(self, interaction: discord.Interaction, game: str) -> None:
        await interaction.response.defer()

        games = await queries.find_games_by_team(game)
        if not games:
            await interaction.followup.send(
                f"No games found matching `{game}`. Is the pipeline running and the DB populated?"
            )
            return

        # If multiple matches, use the first upcoming one
        now_iso = datetime.now(timezone.utc).isoformat()
        upcoming = [g for g in games if g.start_time_utc_iso >= now_iso]
        target = upcoming[0] if upcoming else games[-1]

        snapshots, source_label = await _get_snapshots(target.game_id)
        if not snapshots:
            await interaction.followup.send(
                f"Found **{target.away_team} @ {target.home_team}** but no odds available. "
                f"({source_label})"
            )
            return

        table = _build_odds_table(snapshots)

        start_dt = datetime.fromisoformat(target.start_time_utc_iso)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        start_fmt = start_dt.strftime("%a %b %-d, %-I:%M %p UTC")

        embed = discord.Embed(
            title=f"{target.away_team} @ {target.home_team}",
            description=f"**{start_fmt}**\n*{source_label}*\n\n```\n{table}\n```",
            color=0x5865F2,
        )
        await interaction.followup.send(embed=embed)

    # ── /best-line ─────────────────────────────────────────────────────────────

    @app_commands.command(name="best-line", description="Best available number across all books for a game")
    @app_commands.describe(game="Team name to search for (e.g. 'Lakers', 'Boston')")
    async def best_line(self, interaction: discord.Interaction, game: str) -> None:
        await interaction.response.defer()

        games = await queries.find_games_by_team(game)
        if not games:
            await interaction.followup.send(f"No games found matching `{game}`.")
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        upcoming = [g for g in games if g.start_time_utc_iso >= now_iso]
        target = upcoming[0] if upcoming else games[-1]

        snapshots, source_label = await _get_snapshots(target.game_id)
        if not snapshots:
            await interaction.followup.send(
                f"Found **{target.away_team} @ {target.home_team}** but no odds available. "
                f"({source_label})"
            )
            return

        def best(key: str, snapshots: list[OddsSnapshot], reverse: bool) -> tuple[str, float | int] | None:
            candidates = [(s.source, s.payload[key]) for s in snapshots if s.payload.get(key) is not None]
            if not candidates:
                return None
            return sorted(candidates, key=lambda x: x[1], reverse=reverse)[0]

        home = target.home_team
        away = target.away_team

        fields = []

        b = best("spread", snapshots, reverse=True)
        if b:
            src, val = b
            snap = next(s for s in snapshots if s.source == src)
            odds_val = _fmt_american(snap.payload.get("spread_odds"))
            fields.append((f"Spread ({home})", f"`{val:+.1f}` ({odds_val}) — {BOOK_LABELS.get(src, src)}"))

        b = best("spread_away", snapshots, reverse=True)
        if b:
            src, val = b
            snap = next(s for s in snapshots if s.source == src)
            # away spread odds isn't stored separately — use spread_odds as proxy isn't right,
            # but The Odds API doesn't give us away odds separately in the current payload.
            fields.append((f"Spread ({away})", f"`{val:+.1f}` — {BOOK_LABELS.get(src, src)}"))

        b = best("ml_home", snapshots, reverse=True)
        if b:
            src, val = b
            fields.append((f"ML ({home})", f"`{_fmt_american(int(val))}` — {BOOK_LABELS.get(src, src)}"))

        b = best("ml_away", snapshots, reverse=True)
        if b:
            src, val = b
            fields.append((f"ML ({away})", f"`{_fmt_american(int(val))}` — {BOOK_LABELS.get(src, src)}"))

        # For totals: best over = lowest total, best under = highest total
        b = best("total", snapshots, reverse=False)  # lowest = best for over
        if b:
            src, val = b
            snap = next(s for s in snapshots if s.source == src)
            o_odds = _fmt_american(snap.payload.get("total_over_odds"))
            fields.append(("Best Over", f"`O {val}` ({o_odds}) — {BOOK_LABELS.get(src, src)}"))

        b = best("total", snapshots, reverse=True)  # highest = best for under
        if b:
            src, val = b
            snap = next(s for s in snapshots if s.source == src)
            u_odds = _fmt_american(snap.payload.get("total_under_odds"))
            fields.append(("Best Under", f"`U {val}` ({u_odds}) — {BOOK_LABELS.get(src, src)}"))

        embed = discord.Embed(
            title=f"Best Lines — {away} @ {home}",
            description=f"*{source_label}*",
            color=0x57F287,
        )
        for name, value in fields:
            embed.add_field(name=name, value=value, inline=True)

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OddsCog(bot))
