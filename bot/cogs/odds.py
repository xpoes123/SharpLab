"""Odds commands — read from DB (Temporal pipeline writes every 30 min)."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from db import queries
from shared.models import OddsSnapshot
from shared.odds_utils import american_to_prob, prob_to_american

load_dotenv()

KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
BALLDONTLIE_API_KEY = os.getenv("BALLDONTLIE_API_KEY", "")
BALLDONTLIE_BASE = "https://api.balldontlie.io/v1"

# Sources that are always fetched fresh; exclude from DB staleness check.
LIVE_SOURCES = {"kalshi", "polymarket"}

# ── Display helpers ──────────────────────────────────────────────────────────

# Books shown in /odds and used for best-line comparisons.
# Pinnacle not available on current Odds API tier.
# Kalshi + Polymarket fetched live on demand.
TRACKED_BOOKS = ["draftkings", "fanduel", "betmgm", "pinnacle", "kalshi", "polymarket"]

BOOK_LABELS = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "pinnacle": "Pinnacle",
    "kalshi": "Kalshi",
    "polymarket": "Polymarket",
}


def _fmt_prob(odds: int | None) -> str:
    """Format American odds as implied probability (e.g. -110 → 52.4%)."""
    if odds is None:
        return "n/a"
    return f"{american_to_prob(odds) * 100:.1f}%"


def _fmt_game_time(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    h = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%a %b')} {dt.day}, {h}:{dt.strftime('%M')} {ampm} UTC"


def _staleness(captured_at_iso: str) -> str:
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
    ordered = [s for s in snapshots if s.source in TRACKED_BOOKS]
    ordered.sort(key=lambda s: TRACKED_BOOKS.index(s.source))

    if not ordered:
        return "(no data for tracked books)"

    lines = [f"{'Book':<14}{'Spread':<18}{'ML':<18}Total"]
    lines.append("─" * 62)

    for snap in ordered:
        p = snap.payload
        label = BOOK_LABELS.get(snap.source, snap.source)

        spread_str = (
            f"{p['spread']:+.1f} ({_fmt_prob(p.get('spread_odds'))})"
            if p.get("spread") is not None else "—"
        )
        ml_str = (
            f"{_fmt_prob(p.get('ml_home'))}/{_fmt_prob(p.get('ml_away'))}"
            if p.get("ml_home") is not None else "—"
        )
        total_str = (
            f"{p['total']} ({_fmt_prob(p.get('total_over_odds'))}/{_fmt_prob(p.get('total_under_odds'))})"
            if p.get("total") is not None else "—"
        )

        lines.append(f"{label:<14}{spread_str:<18}{ml_str:<18}{total_str}")

    return "\n".join(lines)


# ── Kalshi live fetch ────────────────────────────────────────────────────────

async def _fetch_kalshi_ml(home_team: str, away_team: str) -> tuple[int, int] | None:
    """
    Fetch Kalshi yes/no prices for an NBA game winner market.
    Searches open KXNBA markets for one matching both team names.
    Returns (home_american, away_american) or None if not found.
    """
    if not KALSHI_API_KEY:
        return None

    # Short names are most distinctive: "Celtics" from "Boston Celtics"
    home_short = home_team.split()[-1].lower()
    away_short = away_team.split()[-1].lower()

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{KALSHI_BASE}/markets",
                headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
                params={"limit": 200, "status": "open", "series_ticker": "KXNBA"},
                timeout=10.0,
            )
            if resp.status_code != 200:
                return None
            markets = resp.json().get("markets", [])
    except Exception:
        return None

    for market in markets:
        title = market.get("title", "").lower()
        if home_short not in title or away_short not in title:
            continue

        yes_bid = market.get("yes_bid", 0)
        yes_ask = market.get("yes_ask", 100)
        yes_mid = (yes_bid + yes_ask) / 2 / 100
        if not (0 < yes_mid < 1):
            continue

        # YES side = whichever team appears first in the title
        home_idx = title.find(home_short)
        away_idx = title.find(away_short)
        if home_idx < away_idx:
            home_prob, away_prob = yes_mid, 1 - yes_mid
        else:
            away_prob, home_prob = yes_mid, 1 - yes_mid

        try:
            return prob_to_american(home_prob), prob_to_american(away_prob)
        except ValueError:
            continue

    return None


# ── Polymarket live fetch ────────────────────────────────────────────────────

async def _fetch_polymarket_ml(home_team: str, away_team: str) -> tuple[int, int] | None:
    """
    Fetch Polymarket game winner market prices for an NBA game.
    Searches open markets via the Gamma API for one matching both team names.
    Returns (home_american, away_american) or None if not found.
    """
    home_short = home_team.split()[-1].lower()
    away_short = away_team.split()[-1].lower()

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{POLYMARKET_GAMMA}/markets",
                params={"q": f"{home_short} {away_short}", "active": "true", "closed": "false", "limit": 20},
                timeout=10.0,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
    except Exception:
        return None

    # Response may be a list or {"markets": [...]} or {"data": [...]}
    markets = data if isinstance(data, list) else data.get("markets", data.get("data", []))

    target = None
    for m in markets:
        title = (m.get("question") or m.get("title") or "").lower()
        if home_short in title and away_short in title:
            target = m
            break

    if not target:
        return None

    tokens = target.get("tokens", [])
    if isinstance(tokens, str):
        try:
            tokens = json.loads(tokens)
        except Exception:
            return None

    if not tokens:
        return None

    home_prob: float | None = None
    away_prob: float | None = None

    for token in tokens:
        outcome = (token.get("outcome") or "").lower()
        price = token.get("price")
        if price is None:
            continue
        price = float(price)
        if not (0 < price < 1):
            continue
        if home_short in outcome:
            home_prob = price
        elif away_short in outcome:
            away_prob = price

    if home_prob is None or away_prob is None:
        return None

    try:
        return prob_to_american(home_prob), prob_to_american(away_prob)
    except ValueError:
        return None


# ── balldontlie scores fetch ─────────────────────────────────────────────────

async def _fetch_today_scores() -> list[dict]:
    """Fetch today's NBA games with live scores from balldontlie."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    headers = {"Authorization": BALLDONTLIE_API_KEY} if BALLDONTLIE_API_KEY else {}
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BALLDONTLIE_BASE}/games",
            params={"dates[]": today, "per_page": 100},
            headers=headers,
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])


# ── Autocomplete ─────────────────────────────────────────────────────────────

async def game_autocomplete(
    _interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    games = await queries.get_upcoming_games(current)
    return [
        app_commands.Choice(
            name=f"{g.away_team} @ {g.home_team} — {_fmt_game_time(g.start_time_utc_iso)}"[:100],
            value=g.game_id,
        )
        for g in games
    ]


# ── Cog ──────────────────────────────────────────────────────────────────────

class OddsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /odds ───────────────────────────────────────────────────────────────

    @app_commands.command(name="odds", description="Lines for a game across all books")
    @app_commands.describe(game="Select a game")
    @app_commands.autocomplete(game=game_autocomplete)
    async def odds(self, interaction: discord.Interaction, game: str) -> None:
        await interaction.response.defer()

        target = await queries.get_game_by_id(game)
        if target is None:
            await interaction.followup.send(
                "Game not found. The Temporal pipeline polls every 30 min — DB may not be populated yet."
            )
            return

        snapshots = [
            s for s in await queries.get_latest_snapshots_for_game(game)
            if s.source in TRACKED_BOOKS
        ]
        if not snapshots:
            await interaction.followup.send(
                f"Found **{target.away_team} @ {target.home_team}** but no odds in DB yet. "
                "Temporal polls every 30 min — try again shortly."
            )
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        kalshi, polymarket = await asyncio.gather(
            _fetch_kalshi_ml(target.home_team, target.away_team),
            _fetch_polymarket_ml(target.home_team, target.away_team),
        )
        if kalshi:
            home_ml, away_ml = kalshi
            snapshots.append(OddsSnapshot(
                snapshot_id="kalshi-live",
                game_id=game,
                kind="poll",
                source="kalshi",
                captured_at_utc_iso=now_iso,
                payload={"ml_home": home_ml, "ml_away": away_ml},
            ))
        if polymarket:
            home_ml, away_ml = polymarket
            snapshots.append(OddsSnapshot(
                snapshot_id="polymarket-live",
                game_id=game,
                kind="poll",
                source="polymarket",
                captured_at_utc_iso=now_iso,
                payload={"ml_home": home_ml, "ml_away": away_ml},
            ))

        most_recent = max(
            (s for s in snapshots if s.source not in LIVE_SOURCES),
            key=lambda s: s.captured_at_utc_iso,
        )
        table = _build_odds_table(snapshots)

        embed = discord.Embed(
            title=f"{target.away_team} @ {target.home_team}",
            description=(
                f"**{_fmt_game_time(target.start_time_utc_iso)}**\n"
                f"*updated {_staleness(most_recent.captured_at_utc_iso)}*\n\n"
                f"```\n{table}\n```"
            ),
            color=0x5865F2,
        )
        await interaction.followup.send(embed=embed)

    # ── /best-line ──────────────────────────────────────────────────────────

    @app_commands.command(name="best-line", description="Best available number across all books for a game")
    @app_commands.describe(game="Select a game")
    @app_commands.autocomplete(game=game_autocomplete)
    async def best_line(self, interaction: discord.Interaction, game: str) -> None:
        await interaction.response.defer()

        target = await queries.get_game_by_id(game)
        if target is None:
            await interaction.followup.send("Game not found.")
            return

        snapshots = [
            s for s in await queries.get_latest_snapshots_for_game(game)
            if s.source in TRACKED_BOOKS
        ]
        if not snapshots:
            await interaction.followup.send(
                f"Found **{target.away_team} @ {target.home_team}** but no odds in DB yet. "
                "Temporal polls every 30 min — try again shortly."
            )
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        kalshi, polymarket = await asyncio.gather(
            _fetch_kalshi_ml(target.home_team, target.away_team),
            _fetch_polymarket_ml(target.home_team, target.away_team),
        )
        if kalshi:
            home_ml, away_ml = kalshi
            snapshots.append(OddsSnapshot(
                snapshot_id="kalshi-live",
                game_id=game,
                kind="poll",
                source="kalshi",
                captured_at_utc_iso=now_iso,
                payload={"ml_home": home_ml, "ml_away": away_ml},
            ))
        if polymarket:
            home_ml, away_ml = polymarket
            snapshots.append(OddsSnapshot(
                snapshot_id="polymarket-live",
                game_id=game,
                kind="poll",
                source="polymarket",
                captured_at_utc_iso=now_iso,
                payload={"ml_home": home_ml, "ml_away": away_ml},
            ))

        most_recent = max(
            (s for s in snapshots if s.source not in LIVE_SOURCES),
            key=lambda s: s.captured_at_utc_iso,
        )

        def best(key: str, reverse: bool) -> tuple[str, float | int] | None:
            candidates = [(s.source, s.payload[key]) for s in snapshots if s.payload.get(key) is not None]
            if not candidates:
                return None
            return sorted(candidates, key=lambda x: x[1], reverse=reverse)[0]

        home = target.home_team
        away = target.away_team
        fields = []

        b = best("spread", reverse=True)
        if b:
            src, val = b
            snap = next(s for s in snapshots if s.source == src)
            fields.append((f"Spread ({home})", f"`{val:+.1f}` ({_fmt_prob(snap.payload.get('spread_odds'))}) — {BOOK_LABELS.get(src, src)}"))

        b = best("spread_away", reverse=True)
        if b:
            src, val = b
            fields.append((f"Spread ({away})", f"`{val:+.1f}` — {BOOK_LABELS.get(src, src)}"))

        b = best("ml_home", reverse=True)
        if b:
            src, val = b
            fields.append((f"ML ({home})", f"`{_fmt_prob(int(val))}` — {BOOK_LABELS.get(src, src)}"))

        b = best("ml_away", reverse=True)
        if b:
            src, val = b
            fields.append((f"ML ({away})", f"`{_fmt_prob(int(val))}` — {BOOK_LABELS.get(src, src)}"))

        b = best("total", reverse=False)
        if b:
            src, val = b
            snap = next(s for s in snapshots if s.source == src)
            fields.append(("Best Over", f"`O {val}` ({_fmt_prob(snap.payload.get('total_over_odds'))}) — {BOOK_LABELS.get(src, src)}"))

        b = best("total", reverse=True)
        if b:
            src, val = b
            snap = next(s for s in snapshots if s.source == src)
            fields.append(("Best Under", f"`U {val}` ({_fmt_prob(snap.payload.get('total_under_odds'))}) — {BOOK_LABELS.get(src, src)}"))

        embed = discord.Embed(
            title=f"Best Lines — {away} @ {home}",
            description=f"*updated {_staleness(most_recent.captured_at_utc_iso)}*",
            color=0x57F287,
        )
        for name, value in fields:
            embed.add_field(name=name, value=value, inline=True)

        await interaction.followup.send(embed=embed)


    # ── /scores ─────────────────────────────────────────────────────────────

    @app_commands.command(name="scores", description="Live scores for today's NBA games")
    async def scores(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        try:
            games = await _fetch_today_scores()
        except Exception as e:
            await interaction.followup.send(f"Could not fetch scores: {e}")
            return

        if not games:
            await interaction.followup.send("No NBA games scheduled today.")
            return

        def _sort_key(g: dict) -> int:
            # Live games first, then not-started, then final
            period = g.get("period", 0)
            status = g.get("status", "")
            if period > 0 and not status.startswith("Final"):
                return 0
            if status.startswith("Final"):
                return 2
            return 1

        games.sort(key=_sort_key)

        lines = []
        for g in games:
            away = g["visitor_team"]["abbreviation"]
            home = g["home_team"]["abbreviation"]
            away_score = g.get("visitor_team_score") or 0
            home_score = g.get("home_team_score") or 0
            period = g.get("period", 0)
            status = g.get("status", "")
            time_left = (g.get("time") or "").strip()

            if period > 0:
                score_str = f"{away} {away_score:>3} @ {home_score:<3} {home}"
                if status.startswith("Final"):
                    status_str = status
                    dot = " "
                else:
                    status_str = f"{status} {time_left}".strip()
                    dot = "●"
            else:
                # Not yet started — status contains the scheduled tip-off time
                score_str = f"{away}     @     {home}"
                status_str = status or "—"
                dot = " "

            lines.append(f"{dot} {score_str:<26} {status_str}")

        _now = datetime.now(timezone.utc)
        today_str = f"{_now.strftime('%a %b')} {_now.day}"
        embed = discord.Embed(
            title=f"NBA Scores — {today_str}",
            description="```\n" + "\n".join(lines) + "\n```",
            color=0xE67E22,
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OddsCog(bot))
