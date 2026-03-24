"""Odds commands — read from DB (Temporal pipeline writes every 30 min)."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from db import queries
from shared.models import TEAM_ABBR, OddsSnapshot
from shared.odds_utils import american_to_prob, prob_to_american

load_dotenv()

KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
BALLDONTLIE_API_KEY = os.getenv("BALLDONTLIE_API_KEY", "")
BALLDONTLIE_BASE = "https://api.balldontlie.io/v1"

# Sources fetched live on demand; exclude from DB staleness calculation.
# Kalshi is now written to DB by the pipeline — only polymarket remains live-only.
LIVE_SOURCES = {"polymarket"}

# ── Display helpers ──────────────────────────────────────────────────────────

# Books shown in /odds and used for best-line comparisons.
# Pinnacle not available on current Odds API tier.
# Kalshi + Polymarket fetched live on demand.
TRACKED_BOOKS = ["draftkings", "fanduel", "betmgm", "pinnacle", "kalshi", "polymarket"]

# Prediction market sources shown in /line-move. Polymarket added later.
PREDICTION_MARKET_SOURCES = ["kalshi", "polymarket"]

BOOK_LABELS = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "pinnacle": "Pinnacle",
    "kalshi": "Kalshi",
    "polymarket": "Polymarket",
}


def _fmt_move(open_odds: int | None, curr_odds: int | None) -> str:
    """Format probability change between two American odds as pp movement."""
    if open_odds is None or curr_odds is None:
        return "—"
    open_p = american_to_prob(open_odds) * 100
    curr_p = american_to_prob(curr_odds) * 100
    delta = curr_p - open_p
    if abs(delta) < 0.05:
        return "no change"
    arrow = "↑" if delta > 0 else "↓"
    return f"{delta:+.1f}pp {arrow}"


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
    Live Kalshi ML fallback — used only when the pipeline hasn't written Kalshi to DB yet.
    Matches by 3-char team abbreviation embedded in the KXNBAGAME event ticker
    (last 6 chars = {away_abbr}{home_abbr}).  Titles truncate for LA teams so
    we cannot use title-based matching.
    """
    if not KALSHI_API_KEY:
        return None

    h_abbr = TEAM_ABBR.get(home_team)
    a_abbr = TEAM_ABBR.get(away_team)
    if not h_abbr or not a_abbr:
        return None

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{KALSHI_BASE}/markets",
                headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
                params={"limit": 200, "status": "open", "series_ticker": "KXNBAGAME"},
                timeout=10.0,
            )
            if resp.status_code != 200:
                return None
            markets = resp.json().get("markets", [])
    except Exception:
        return None

    home_prob: float | None = None
    away_prob: float | None = None

    for m in markets:
        et = m.get("event_ticker", "")
        team_part = et.split("-")[-1]           # e.g. "26MAR24DENPHX"
        if team_part[-3:].upper() != h_abbr or team_part[-6:-3].upper() != a_abbr:
            continue

        suffix = m.get("ticker", "").split("-")[-1].upper()
        yes_bid = m.get("yes_bid_dollars") or 0
        yes_ask = m.get("yes_ask_dollars") or 0
        mid = (float(yes_bid) + float(yes_ask)) / 2 if (yes_bid or yes_ask) else float(m.get("last_price_dollars") or 0)
        if not (0 < mid < 1):
            continue

        if suffix == h_abbr:
            home_prob = mid
        elif suffix == a_abbr:
            away_prob = mid

    if home_prob is None or away_prob is None:
        return None
    try:
        return prob_to_american(home_prob), prob_to_american(away_prob)
    except ValueError:
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

_ET = ZoneInfo("America/New_York")


def _fmt_tipoff_et(status: str) -> str:
    """Convert an ISO tipoff timestamp (balldontlie not-started status) to '7:30 PM EDT'."""
    try:
        dt = datetime.fromisoformat(status.replace("Z", "+00:00"))
        dt_et = dt.astimezone(_ET)
        h = dt_et.hour % 12 or 12
        ampm = "AM" if dt_et.hour < 12 else "PM"
        return f"{h}:{dt_et.strftime('%M')} {ampm} {dt_et.strftime('%Z')}"
    except (ValueError, AttributeError):
        return status


async def _fetch_scores(dates: list[str]) -> list[dict]:
    """Fetch NBA games for one or more dates from balldontlie."""
    headers = {"Authorization": BALLDONTLIE_API_KEY} if BALLDONTLIE_API_KEY else {}
    params: list[tuple[str, str | int]] = [("per_page", 100)]
    for d in dates:
        params.append(("dates[]", d))
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BALLDONTLIE_BASE}/games",
            params=params,
            headers=headers,
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])


async def _preload_game_odds(dates: list[str]) -> dict[tuple[str, str], dict]:
    """
    Returns {(home_last_word, away_last_word): {spread, ml_home_prob, ml_away_prob}}
    for all DB games in the window covered by `dates`.

    Window: start of earliest date to noon UTC of the day after latest date.
    The +1 day buffer catches late West Coast games whose UTC date rolls over.
    """
    start_utc = min(dates) + "T00:00:00"
    end_utc = (
        datetime.strptime(max(dates), "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%dT12:00:00")

    result: dict[tuple[str, str], dict] = {}
    games = await queries.get_games_in_window(start_utc, end_utc)
    for game in games:
        snapshots = await queries.get_latest_snapshots_for_game(game.game_id)
        home_key = game.home_team.split()[-1].lower()
        away_key = game.away_team.split()[-1].lower()
        # Prefer Kalshi for ML (no vig, closer to true probability).
        # Fall back to any book with ML, then layer in spread from any book.
        by_source = {s.source: s for s in snapshots}
        ml_snap = by_source.get("kalshi") or next(
            (s for s in snapshots if s.payload.get("ml_home")), None
        )
        spread_snap = next(
            (s for s in snapshots if s.payload.get("spread") is not None), None
        )
        if ml_snap or spread_snap:
            ml_p = ml_snap.payload if ml_snap else {}
            sp_p = spread_snap.payload if spread_snap else {}
            result[(home_key, away_key)] = {
                "spread": sp_p.get("spread"),
                "ml_home_prob": american_to_prob(ml_p["ml_home"]) * 100 if ml_p.get("ml_home") else None,
                "ml_away_prob": american_to_prob(ml_p["ml_away"]) * 100 if ml_p.get("ml_away") else None,
            }
    return result


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


async def historical_game_autocomplete(
    _interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for /line-move: searches all games (past + upcoming).
    If the typed value looks like a game ID prefix (hex/dash, no spaces),
    search by ID prefix; otherwise search by team name.
    """
    stripped = current.strip().lower()
    if stripped and " " not in stripped and all(c in "0123456789abcdef-" for c in stripped):
        games = await queries.get_games_by_id_prefix(stripped)
    else:
        games = await queries.get_recent_games(current)
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
        has_kalshi = any(s.source == "kalshi" for s in snapshots)
        # Fetch Kalshi live only if pipeline hasn't written it to DB yet
        if not has_kalshi:
            kalshi_live = await _fetch_kalshi_ml(target.home_team, target.away_team)
            if kalshi_live:
                home_ml, away_ml = kalshi_live
                snapshots.append(OddsSnapshot(
                    snapshot_id="kalshi-live",
                    game_id=game,
                    kind="poll",
                    source="kalshi",
                    captured_at_utc_iso=now_iso,
                    payload={"ml_home": home_ml, "ml_away": away_ml},
                ))
        polymarket = await _fetch_polymarket_ml(target.home_team, target.away_team)
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
        has_kalshi = any(s.source == "kalshi" for s in snapshots)
        if not has_kalshi:
            kalshi_live = await _fetch_kalshi_ml(target.home_team, target.away_team)
            if kalshi_live:
                home_ml, away_ml = kalshi_live
                snapshots.append(OddsSnapshot(
                    snapshot_id="kalshi-live",
                    game_id=game,
                    kind="poll",
                    source="kalshi",
                    captured_at_utc_iso=now_iso,
                    payload={"ml_home": home_ml, "ml_away": away_ml},
                ))
        polymarket = await _fetch_polymarket_ml(target.home_team, target.away_team)
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
            fields.append((f"Spread ({away})", f"`{-val:+.1f}` ({_fmt_prob(snap.payload.get('spread_odds'))}) — {BOOK_LABELS.get(src, src)}"))

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


    # ── /line-move ──────────────────────────────────────────────────────────

    @app_commands.command(name="line-move", description="How Kalshi odds have moved since open")
    @app_commands.describe(game="Select a game (type team name or paste ID from /db)")
    @app_commands.autocomplete(game=historical_game_autocomplete)
    async def line_move(self, interaction: discord.Interaction, game: str) -> None:
        await interaction.response.defer()

        target = await queries.get_game_by_id(game)
        if target is None:
            await interaction.followup.send("Game not found.")
            return

        all_snaps = await queries.get_snapshots_for_game_since(game, "2000-01-01T00:00:00Z")
        ml_snaps = [s for s in all_snaps if s.source in PREDICTION_MARKET_SOURCES]
        dk_snaps = [
            s for s in all_snaps
            if s.source == "draftkings" and s.payload.get("spread") is not None
        ]

        if not ml_snaps and not dk_snaps:
            await interaction.followup.send(
                f"No line movement data yet for **{target.away_team} @ {target.home_team}**. "
                "The pipeline polls every 30 min — check back soon."
            )
            return

        home_name = target.home_team.split()[-1]
        away_name = target.away_team.split()[-1]
        W = 15
        header = f"{'Side':<12}{'Open':<{W}}{'Now':<{W}}Move"
        divider = "─" * (12 + W + W + 10)

        def _fmt_ml(o: int | None) -> str:
            if o is None:
                return "—"
            return f"{o:+d} ({american_to_prob(o) * 100:.0f}%)"

        def _fmt_spread_cell(spread: float | None, odds: int | None) -> str:
            if spread is None:
                return "—"
            odds_str = f" ({odds:+d})" if odds is not None else ""
            return f"{spread:+.1f}{odds_str}"

        sections: list[str] = []

        # ── ML sections (Kalshi / Polymarket) ─────────────────────────────────
        for source in PREDICTION_MARKET_SOURCES:
            source_snaps = [s for s in ml_snaps if s.source == source]
            if not source_snaps:
                continue

            label = BOOK_LABELS.get(source, source.title())
            open_s = source_snaps[0]
            curr_s = source_snaps[-1]
            count = len(source_snaps)

            open_home = open_s.payload.get("ml_home")
            open_away = open_s.payload.get("ml_away")
            curr_home = curr_s.payload.get("ml_home")
            curr_away = curr_s.payload.get("ml_away")

            count_str = f"{count} snapshot{'s' if count != 1 else ''}"
            sections.append("\n".join([
                f"{label} ML · {count_str} · opened {_staleness(open_s.captured_at_utc_iso)}",
                header,
                divider,
                f"{home_name:<12}{_fmt_ml(open_home):<{W}}{_fmt_ml(curr_home):<{W}}{_fmt_move(open_home, curr_home)}",
                f"{away_name:<12}{_fmt_ml(open_away):<{W}}{_fmt_ml(curr_away):<{W}}{_fmt_move(open_away, curr_away)}",
            ]))

        # ── Spread section (DraftKings) ────────────────────────────────────────
        if dk_snaps:
            open_s = dk_snaps[0]
            curr_s = dk_snaps[-1]
            count = len(dk_snaps)

            o_spread = open_s.payload.get("spread")
            c_spread = curr_s.payload.get("spread")
            o_odds   = open_s.payload.get("spread_odds")
            c_odds   = curr_s.payload.get("spread_odds")

            def _spread_move(open_val: float | None, curr_val: float | None) -> str:
                if open_val is None or curr_val is None:
                    return "—"
                delta = curr_val - open_val
                if abs(delta) < 0.05:
                    return "no change"
                arrow = "↑" if delta > 0 else "↓"
                return f"{delta:+.1f} {arrow}"

            count_str = f"{count} snapshot{'s' if count != 1 else ''}"
            o_spread_away = open_s.payload.get("spread_away")
            c_spread_away = curr_s.payload.get("spread_away")
            sections.append("\n".join([
                f"Spread · DraftKings · {count_str} · opened {_staleness(open_s.captured_at_utc_iso)}",
                header,
                divider,
                f"{home_name:<12}{_fmt_spread_cell(o_spread, o_odds):<{W}}{_fmt_spread_cell(c_spread, c_odds):<{W}}{_spread_move(o_spread, c_spread)}",
                f"{away_name:<12}{_fmt_spread_cell(o_spread_away, None):<{W}}{_fmt_spread_cell(c_spread_away, None):<{W}}{_spread_move(o_spread_away, c_spread_away)}",
            ]))

        description = (
            f"**{_fmt_game_time(target.start_time_utc_iso)}**\n\n"
            "```\n"
            + "\n\n".join(sections)
            + "\n```"
        )
        embed = discord.Embed(
            title=f"Line Movement — {target.away_team} @ {target.home_team}",
            description=description,
            color=0x5865F2,
        )
        await interaction.followup.send(embed=embed)

    # ── /scores ─────────────────────────────────────────────────────────────

    @app_commands.command(name="scores", description="Live scores for today's NBA games")
    async def scores(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        _now = datetime.now(timezone.utc)
        # NBA day rolls over at ~11 AM UTC (7 AM ET). Before that we're still
        # in yesterday's slate — fetch both so late games aren't missing.
        post_midnight = _now.hour < 11
        nba_day = (_now - timedelta(days=1)) if post_midnight else _now
        dates = [nba_day.strftime("%Y-%m-%d")]
        if post_midnight:
            dates.append(_now.strftime("%Y-%m-%d"))

        try:
            games, odds_lookup = await asyncio.gather(
                _fetch_scores(dates),
                _preload_game_odds(dates),
            )
        except Exception as e:
            await interaction.followup.send(f"Could not fetch scores: {e}")
            return

        if not games:
            await interaction.followup.send("No NBA games found.")
            return

        def _sort_key(g: dict) -> int:
            period = g.get("period", 0)
            status = g.get("status", "")
            if period > 0 and not status.startswith("Final"):
                return 0   # live — always first
            if status.startswith("Final"):
                return 1 if post_midnight else 3
            return 2   # not started

        games.sort(key=_sort_key)

        lines: list[str] = []
        prev_section: str | None = None
        for g in games:
            away = g["visitor_team"]["abbreviation"]
            home = g["home_team"]["abbreviation"]
            away_score = g.get("visitor_team_score") or 0
            home_score = g.get("home_team_score") or 0
            period = g.get("period", 0)
            status = g.get("status", "")
            time_left = (g.get("time") or "").strip()

            home_key = g["home_team"]["full_name"].split()[-1].lower()
            away_key = g["visitor_team"]["full_name"].split()[-1].lower()
            odds = odds_lookup.get((home_key, away_key))

            if period > 0:
                score_str = f"{away} {away_score:>3} @ {home_score:<3} {home}"
                if status.startswith("Final"):
                    section = "final"
                    spread_str = ""
                    if odds and odds["spread"] is not None:
                        margin = (home_score - away_score) + odds["spread"]
                        cover = "➖" if abs(margin) < 0.1 else ("✅" if margin > 0 else "❌")
                        spread_str = f"  {home} {odds['spread']:+.1f} {cover}"
                    line = f"  {score_str:<20} {status}{spread_str}"
                else:
                    section = "live"
                    line = f"● {score_str:<20} {status} {time_left}".rstrip()
            else:
                section = "upcoming"
                score_str = f"{away}     @     {home}"
                time_str = _fmt_tipoff_et(status) if status else "—"
                ml_str = ""
                if odds and odds["ml_home_prob"] and odds["ml_away_prob"]:
                    ml_str = f"  {odds['ml_away_prob']:>2.0f}%/{odds['ml_home_prob']:>2.0f}%"
                line = f"  {score_str:<20} {time_str:<14}{ml_str}"

            if prev_section is not None and section != prev_section:
                lines.append("")
            prev_section = section
            lines.append(line)

        day_str = f"{nba_day.strftime('%a %b')} {nba_day.day}"
        embed = discord.Embed(
            title=f"NBA Scores — {day_str}",
            description="```\n" + "\n".join(lines) + "\n```",
            color=0xE67E22,
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OddsCog(bot))
