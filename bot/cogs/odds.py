"""Odds commands — read from DB (Temporal pipeline writes every 30 min)."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from db import queries
from shared.models import OddsSnapshot, get_team_abbr
from shared.odds_utils import american_to_prob, fetch_polymarket_ml, prob_to_american
import logging

log = logging.getLogger(__name__)
load_dotenv()

KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
BALLDONTLIE_API_KEY = os.getenv("BALLDONTLIE_API_KEY", "")
BALLDONTLIE_BASE = "https://api.balldontlie.io/v1"

LIVE_SOURCES = {"polymarket"}

# ── Sport config ─────────────────────────────────────────────────────────────

KALSHI_SERIES = {"nba": "KXNBAGAME", "mlb": "KXMLBGAME"}

ESPN_SCORES_URL = {
    "nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "mlb": "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
}

SPORT_LABELS = {"nba": "NBA", "mlb": "MLB"}

# ── Display helpers ──────────────────────────────────────────────────────────

TRACKED_BOOKS = ["draftkings", "fanduel", "betmgm", "pinnacle", "kalshi", "polymarket"]
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
    if odds is None:
        return "n/a"
    return f"{american_to_prob(odds) * 100:.1f}%"


def _fmt_game_time(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(_ET)
    h = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%a %b')} {dt.day}, {h}:{dt.strftime('%M')} {ampm} {dt.strftime('%Z')}"


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
        spread_str = f"{p['spread']:+.1f} ({_fmt_prob(p.get('spread_odds'))})" if p.get("spread") is not None else "—"
        ml_str = f"{_fmt_prob(p.get('ml_home'))}/{_fmt_prob(p.get('ml_away'))}" if p.get("ml_home") is not None else "—"
        total_str = f"{p['total']} ({_fmt_prob(p.get('total_over_odds'))}/{_fmt_prob(p.get('total_under_odds'))})" if p.get("total") is not None else "—"
        lines.append(f"{label:<14}{spread_str:<18}{ml_str:<18}{total_str}")
    return "\n".join(lines)


# ── Kalshi live fetch ────────────────────────────────────────────────────────

async def _fetch_kalshi_ml(home_team: str, away_team: str, sport: str = "nba") -> tuple[int, int] | None:
    series_ticker = KALSHI_SERIES.get(sport)
    if not KALSHI_API_KEY or not series_ticker:
        return None
    h_abbr = get_team_abbr(home_team, sport)
    a_abbr = get_team_abbr(away_team, sport)
    if not h_abbr or not a_abbr:
        return None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{KALSHI_BASE}/markets",
                headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
                params={"limit": 200, "status": "open", "series_ticker": series_ticker},
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
        team_part = et.split("-")[-1]
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
    async with httpx.AsyncClient() as client:
        return await fetch_polymarket_ml(client, home_team, away_team)


# ── Scores fetch ───────────────────────────────────────────────────────────

def _fmt_tipoff_et(status: str) -> str:
    try:
        dt = datetime.fromisoformat(status.replace("Z", "+00:00"))
        dt_et = dt.astimezone(_ET)
        h = dt_et.hour % 12 or 12
        ampm = "AM" if dt_et.hour < 12 else "PM"
        return f"{h}:{dt_et.strftime('%M')} {ampm} {dt_et.strftime('%Z')}"
    except (ValueError, AttributeError):
        return status


async def _fetch_scores_nba(dates: list[str]) -> list[dict]:
    headers = {"Authorization": BALLDONTLIE_API_KEY} if BALLDONTLIE_API_KEY else {}
    params: list[tuple[str, str | int]] = [("per_page", 100)]
    for d in dates:
        params.append(("dates[]", d))
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BALLDONTLIE_BASE}/games", params=params, headers=headers, timeout=10.0)
        resp.raise_for_status()
        return resp.json().get("data", [])


async def _fetch_scores_espn(dates: list[str], sport: str) -> list[dict]:
    url = ESPN_SCORES_URL.get(sport)
    if not url:
        return []
    results: list[dict] = []
    async with httpx.AsyncClient() as client:
        for date_str in dates:
            espn_date = date_str.replace("-", "")
            try:
                resp = await client.get(url, params={"dates": espn_date}, timeout=15.0)
                if resp.status_code != 200:
                    continue
                data = resp.json()
            except Exception:
                continue
            for event in data.get("events", []):
                status_obj = event.get("status", {})
                status_type = status_obj.get("type", {}).get("name", "")
                short_detail = status_obj.get("type", {}).get("shortDetail", "")
                comp = event.get("competitions", [{}])[0]
                competitors = comp.get("competitors", [])
                game: dict = {"status": "", "period": 0, "time": ""}
                for c in competitors:
                    team = c.get("team", {})
                    abbr = team.get("abbreviation", "???")
                    full_name = team.get("displayName", abbr)
                    score = int(c.get("score", "0"))
                    if c.get("homeAway") == "home":
                        game["home_team"] = {"abbreviation": abbr, "full_name": full_name}
                        game["home_team_score"] = score
                    else:
                        game["visitor_team"] = {"abbreviation": abbr, "full_name": full_name}
                        game["visitor_team_score"] = score
                if status_type == "STATUS_FINAL":
                    game["status"] = "Final"
                    game["period"] = 9
                elif status_type == "STATUS_IN_PROGRESS":
                    game["status"] = short_detail or "Live"
                    game["period"] = 1
                else:
                    game["status"] = event.get("date", "")
                    game["period"] = 0
                if "home_team" in game and "visitor_team" in game:
                    results.append(game)
    return results


async def _preload_game_odds(dates: list[str], sport: str = "nba") -> dict[tuple[str, str], dict]:
    start_utc = min(dates) + "T00:00:00"
    end_utc = (datetime.strptime(max(dates), "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%dT12:00:00")
    result: dict[tuple[str, str], dict] = {}
    games = await queries.get_games_in_window(start_utc, end_utc, sport=sport)
    for game in games:
        snapshots = await queries.get_latest_snapshots_for_game(game.game_id)
        home_key = game.home_team.split()[-1].lower()
        away_key = game.away_team.split()[-1].lower()
        by_source = {s.source: s for s in snapshots}
        ml_snap = by_source.get("kalshi") or next((s for s in snapshots if s.payload.get("ml_home")), None)
        spread_snap = next((s for s in snapshots if s.payload.get("spread") is not None), None)
        if ml_snap or spread_snap:
            ml_p = ml_snap.payload if ml_snap else {}
            sp_p = spread_snap.payload if spread_snap else {}
            result[(home_key, away_key)] = {
                "spread": sp_p.get("spread"),
                "ml_home_prob": american_to_prob(ml_p["ml_home"]) * 100 if ml_p.get("ml_home") else None,
                "ml_away_prob": american_to_prob(ml_p["ml_away"]) * 100 if ml_p.get("ml_away") else None,
            }
    return result


# ── Autocomplete factories ───────────────────────────────────────────────────

def _make_game_autocomplete(sport: str):
    async def _autocomplete(_interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        games = await queries.get_upcoming_games(current, sport=sport)
        return [
            app_commands.Choice(
                name=f"{g.away_team} @ {g.home_team} — {_fmt_game_time(g.start_time_utc_iso)}"[:100],
                value=g.game_id,
            )
            for g in games
        ]
    return _autocomplete


def _make_historical_autocomplete(sport: str):
    async def _autocomplete(_interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        stripped = current.strip().lower()
        if stripped and " " not in stripped and all(c in "0123456789abcdef-" for c in stripped):
            games = await queries.get_games_by_id_prefix(stripped)
        else:
            games = await queries.get_recent_games(current, sport=sport)
        return [
            app_commands.Choice(
                name=f"{g.away_team} @ {g.home_team} — {_fmt_game_time(g.start_time_utc_iso)}"[:100],
                value=g.game_id,
            )
            for g in games
        ]
    return _autocomplete


# Exported for use by other cogs
game_autocomplete = _make_game_autocomplete("nba")
mlb_game_autocomplete = _make_game_autocomplete("mlb")
historical_game_autocomplete = _make_historical_autocomplete("nba")
mlb_historical_game_autocomplete = _make_historical_autocomplete("mlb")


# ── Cog ──────────────────────────────────────────────────────────────────────

class OddsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── shared impls ─────────────────────────────────────────────────────────

    async def _odds_impl(self, interaction: discord.Interaction, game: str, sport: str) -> None:
        await interaction.response.defer()
        target = await queries.get_game_by_id(game)
        if target is None:
            await interaction.followup.send("Game not found. The Temporal pipeline polls every 30 min — DB may not be populated yet.")
            return
        snapshots = [s for s in await queries.get_latest_snapshots_for_game(game) if s.source in TRACKED_BOOKS]
        if not snapshots:
            await interaction.followup.send(f"Found **{target.away_team} @ {target.home_team}** but no odds in DB yet. Temporal polls every 30 min — try again shortly.")
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        if not any(s.source == "kalshi" for s in snapshots):
            kalshi_live = await _fetch_kalshi_ml(target.home_team, target.away_team, sport)
            if kalshi_live:
                home_ml, away_ml = kalshi_live
                snapshots.append(OddsSnapshot(snapshot_id="kalshi-live", game_id=game, kind="poll", source="kalshi", captured_at_utc_iso=now_iso, payload={"ml_home": home_ml, "ml_away": away_ml}))
        if not any(s.source == "polymarket" for s in snapshots):
            polymarket = await _fetch_polymarket_ml(target.home_team, target.away_team)
            if polymarket:
                home_ml, away_ml = polymarket
                snapshots.append(OddsSnapshot(snapshot_id="polymarket-live", game_id=game, kind="poll", source="polymarket", captured_at_utc_iso=now_iso, payload={"ml_home": home_ml, "ml_away": away_ml}))
        _non_live = [s for s in snapshots if s.source not in LIVE_SOURCES]
        most_recent = max(_non_live or snapshots, key=lambda s: s.captured_at_utc_iso)
        table = _build_odds_table(snapshots)
        embed = discord.Embed(
            title=f"{target.away_team} @ {target.home_team}",
            description=f"**{_fmt_game_time(target.start_time_utc_iso)}**\n*updated {_staleness(most_recent.captured_at_utc_iso)}*\n\n```\n{table}\n```",
            color=0x5865F2,
        )
        await interaction.followup.send(embed=embed)

    async def _best_line_impl(self, interaction: discord.Interaction, game: str, sport: str) -> None:
        await interaction.response.defer()
        target = await queries.get_game_by_id(game)
        if target is None:
            await interaction.followup.send("Game not found.")
            return
        snapshots = [s for s in await queries.get_latest_snapshots_for_game(game) if s.source in TRACKED_BOOKS]
        if not snapshots:
            await interaction.followup.send(f"Found **{target.away_team} @ {target.home_team}** but no odds in DB yet. Temporal polls every 30 min — try again shortly.")
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        if not any(s.source == "kalshi" for s in snapshots):
            kalshi_live = await _fetch_kalshi_ml(target.home_team, target.away_team, sport)
            if kalshi_live:
                home_ml, away_ml = kalshi_live
                snapshots.append(OddsSnapshot(snapshot_id="kalshi-live", game_id=game, kind="poll", source="kalshi", captured_at_utc_iso=now_iso, payload={"ml_home": home_ml, "ml_away": away_ml}))
        if not any(s.source == "polymarket" for s in snapshots):
            polymarket = await _fetch_polymarket_ml(target.home_team, target.away_team)
            if polymarket:
                home_ml, away_ml = polymarket
                snapshots.append(OddsSnapshot(snapshot_id="polymarket-live", game_id=game, kind="poll", source="polymarket", captured_at_utc_iso=now_iso, payload={"ml_home": home_ml, "ml_away": away_ml}))
        _non_live = [s for s in snapshots if s.source not in LIVE_SOURCES]
        most_recent = max(_non_live or snapshots, key=lambda s: s.captured_at_utc_iso)
        def best(key: str, reverse: bool) -> tuple[str, float | int] | None:
            candidates = [(s.source, s.payload[key]) for s in snapshots if s.payload.get(key) is not None]
            return sorted(candidates, key=lambda x: x[1], reverse=reverse)[0] if candidates else None
        home, away = target.home_team, target.away_team
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
        embed = discord.Embed(title=f"Best Lines — {away} @ {home}", description=f"*updated {_staleness(most_recent.captured_at_utc_iso)}*", color=0x57F287)
        for name, value in fields:
            embed.add_field(name=name, value=value, inline=True)
        await interaction.followup.send(embed=embed)

    async def _line_move_impl(self, interaction: discord.Interaction, game: str) -> None:
        await interaction.response.defer()
        target = await queries.get_game_by_id(game)
        if target is None:
            await interaction.followup.send("Game not found.")
            return
        all_snaps = await queries.get_snapshots_for_game_since(game, "2000-01-01T00:00:00Z")
        ml_snaps = [s for s in all_snaps if s.source in PREDICTION_MARKET_SOURCES]
        dk_snaps = [s for s in all_snaps if s.source == "draftkings" and s.payload.get("spread") is not None]
        if not ml_snaps and not dk_snaps:
            await interaction.followup.send(f"No line movement data yet for **{target.away_team} @ {target.home_team}**. The pipeline polls every 30 min — check back soon.")
            return
        home_name, away_name = target.home_team.split()[-1], target.away_team.split()[-1]
        W = 15
        header = f"{'Side':<12}{'Open':<{W}}{'Now':<{W}}Move"
        divider = "─" * (12 + W + W + 10)
        def _fmt_ml(o: int | None) -> str:
            return "—" if o is None else _fmt_prob(o)
        def _fmt_spread_cell(spread: float | None, odds: int | None) -> str:
            if spread is None:
                return "—"
            return f"{spread:+.1f}" + (f" ({_fmt_prob(odds)})" if odds is not None else "")
        sections: list[str] = []
        for source in PREDICTION_MARKET_SOURCES:
            source_snaps = [s for s in ml_snaps if s.source == source]
            if not source_snaps:
                continue
            label = BOOK_LABELS.get(source, source.title())
            open_s, curr_s, count = source_snaps[0], source_snaps[-1], len(source_snaps)
            sections.append("\n".join([
                f"{label} ML · {count} snapshot{'s' if count != 1 else ''} · opened {_staleness(open_s.captured_at_utc_iso)}",
                header, divider,
                f"{home_name:<12}{_fmt_ml(open_s.payload.get('ml_home')):<{W}}{_fmt_ml(curr_s.payload.get('ml_home')):<{W}}{_fmt_move(open_s.payload.get('ml_home'), curr_s.payload.get('ml_home'))}",
                f"{away_name:<12}{_fmt_ml(open_s.payload.get('ml_away')):<{W}}{_fmt_ml(curr_s.payload.get('ml_away')):<{W}}{_fmt_move(open_s.payload.get('ml_away'), curr_s.payload.get('ml_away'))}",
            ]))
        if dk_snaps:
            open_s, curr_s, count = dk_snaps[0], dk_snaps[-1], len(dk_snaps)
            def _spread_move(ov: float | None, cv: float | None) -> str:
                if ov is None or cv is None:
                    return "—"
                d = cv - ov
                return "no change" if abs(d) < 0.05 else f"{d:+.1f} {'↑' if d > 0 else '↓'}"
            sections.append("\n".join([
                f"Spread · DraftKings · {count} snapshot{'s' if count != 1 else ''} · opened {_staleness(open_s.captured_at_utc_iso)}",
                header, divider,
                f"{home_name:<12}{_fmt_spread_cell(open_s.payload.get('spread'), open_s.payload.get('spread_odds')):<{W}}{_fmt_spread_cell(curr_s.payload.get('spread'), curr_s.payload.get('spread_odds')):<{W}}{_spread_move(open_s.payload.get('spread'), curr_s.payload.get('spread'))}",
                f"{away_name:<12}{_fmt_spread_cell(open_s.payload.get('spread_away'), None):<{W}}{_fmt_spread_cell(curr_s.payload.get('spread_away'), None):<{W}}{_spread_move(open_s.payload.get('spread_away'), curr_s.payload.get('spread_away'))}",
            ]))
        embed = discord.Embed(
            title=f"Line Movement — {target.away_team} @ {target.home_team}",
            description=f"**{_fmt_game_time(target.start_time_utc_iso)}**\n\n```\n" + "\n\n".join(sections) + "\n```",
            color=0x5865F2,
        )
        await interaction.followup.send(embed=embed)

    async def _scores_impl(self, interaction: discord.Interaction, sport: str) -> None:
        await interaction.response.defer()
        _now = datetime.now(timezone.utc)
        post_midnight = _now.hour < 11
        game_day = (_now - timedelta(days=1)) if post_midnight else _now
        dates = [game_day.strftime("%Y-%m-%d")]
        if post_midnight:
            dates.append(_now.strftime("%Y-%m-%d"))
        try:
            if sport == "nba":
                games, odds_lookup = await asyncio.gather(_fetch_scores_nba(dates), _preload_game_odds(dates, sport=sport))
            else:
                games, odds_lookup = await asyncio.gather(_fetch_scores_espn(dates, sport), _preload_game_odds(dates, sport=sport))
        except Exception as e:
            await interaction.followup.send(f"Could not fetch scores: {e}")
            return
        label = SPORT_LABELS.get(sport, sport.upper())
        if not games:
            await interaction.followup.send(f"No {label} games found.")
            return
        def _sort_key(g: dict) -> int:
            period, status = g.get("period", 0), g.get("status", "")
            if period > 0 and not status.startswith("Final"):
                return 0
            if status.startswith("Final"):
                return 1 if post_midnight else 3
            return 2
        games.sort(key=_sort_key)
        lines: list[str] = []
        prev_section: str | None = None
        for g in games:
            away, home = g["visitor_team"]["abbreviation"], g["home_team"]["abbreviation"]
            away_score, home_score = g.get("visitor_team_score") or 0, g.get("home_team_score") or 0
            period, status, time_left = g.get("period", 0), g.get("status", ""), (g.get("time") or "").strip()
            home_key, away_key = g["home_team"]["full_name"].split()[-1].lower(), g["visitor_team"]["full_name"].split()[-1].lower()
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
        day_str = f"{game_day.strftime('%a %b')} {game_day.day}"
        embed = discord.Embed(title=f"{label} Scores — {day_str}", description="```\n" + "\n".join(lines) + "\n```", color=0xE67E22)
        await interaction.followup.send(embed=embed)

    # ── NBA commands ─────────────────────────────────────────────────────────

    @app_commands.command(name="odds", description="NBA lines for a game across all books")
    @app_commands.describe(game="Select a game")
    @app_commands.autocomplete(game=game_autocomplete)
    async def odds(self, interaction: discord.Interaction, game: str) -> None:
        await self._odds_impl(interaction, game, "nba")

    @app_commands.command(name="best-line", description="Best available NBA number across all books")
    @app_commands.describe(game="Select a game")
    @app_commands.autocomplete(game=game_autocomplete)
    async def best_line(self, interaction: discord.Interaction, game: str) -> None:
        await self._best_line_impl(interaction, game, "nba")

    @app_commands.command(name="line-move", description="NBA line movement — Kalshi/Polymarket ML + DK spread")
    @app_commands.describe(game="Select a game (type team name or paste ID from /db)")
    @app_commands.autocomplete(game=historical_game_autocomplete)
    async def line_move(self, interaction: discord.Interaction, game: str) -> None:
        await self._line_move_impl(interaction, game)

    @app_commands.command(name="scores", description="Live NBA scores")
    async def scores(self, interaction: discord.Interaction) -> None:
        await self._scores_impl(interaction, "nba")

    # ── MLB commands ─────────────────────────────────────────────────────────

    @app_commands.command(name="mlb-odds", description="MLB lines for a game across all books")
    @app_commands.describe(game="Select a game")
    @app_commands.autocomplete(game=mlb_game_autocomplete)
    async def mlb_odds(self, interaction: discord.Interaction, game: str) -> None:
        await self._odds_impl(interaction, game, "mlb")

    @app_commands.command(name="mlb-best-line", description="Best available MLB number across all books")
    @app_commands.describe(game="Select a game")
    @app_commands.autocomplete(game=mlb_game_autocomplete)
    async def mlb_best_line(self, interaction: discord.Interaction, game: str) -> None:
        await self._best_line_impl(interaction, game, "mlb")

    @app_commands.command(name="mlb-line-move", description="MLB line movement — Kalshi/Polymarket ML + DK spread")
    @app_commands.describe(game="Select a game (type team name or paste ID from /mlb-db)")
    @app_commands.autocomplete(game=mlb_historical_game_autocomplete)
    async def mlb_line_move(self, interaction: discord.Interaction, game: str) -> None:
        await self._line_move_impl(interaction, game)

    @app_commands.command(name="mlb-scores", description="Live MLB scores")
    async def mlb_scores(self, interaction: discord.Interaction) -> None:
        await self._scores_impl(interaction, "mlb")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OddsCog(bot))
