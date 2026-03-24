"""
All pipeline side effects live here. One activity per API call.

API quota notes (The Odds API free tier = 500 req/month):
- fetch_games_for_today: 1 call/cycle (lightweight /events endpoint)
- fetch_odds_batch:      1 call/cycle (full odds)
- fetch_close_odds_snapshot: 1 call/game at tip-off (filtered by eventId)
At 30-min intervals during a ~9-hour game window × 20 game days = ~360 calls/month.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv
from temporalio import activity

from db import queries, schema
from shared.models import TEAM_ABBR, Game, InjuryAlert, OddsBatch, OddsSnapshot
from shared.odds_utils import prob_to_american

load_dotenv()

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"


# ── Input types ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FetchCloseSnapshotInput:
    snapshot_id: str
    game_id: str  # The Odds API event ID


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_payload(bookmaker: dict, home_team: str) -> dict[str, Any]:
    """Normalize one bookmaker's markets into our standard payload shape."""
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


# ── Activities ─────────────────────────────────────────────────────────────────

@activity.defn
async def fetch_games_for_today() -> list[Game]:
    """
    Fetch today's NBA schedule from The Odds API /events endpoint.
    Lightweight call — no odds data, minimal quota usage.
    Also upserts games to the DB so the rest of the pipeline can reference them.
    """
    await schema.init_db()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ODDS_API_BASE}/sports/basketball_nba/events",
            params={"apiKey": ODDS_API_KEY},
            timeout=15.0,
        )
        resp.raise_for_status()
        events = resp.json()
        remaining = resp.headers.get("x-requests-remaining", "?")
        used = resp.headers.get("x-requests-used", "?")

    activity.logger.info(
        f"[fetch_games_for_today] {len(events)} events | quota used={used} remaining={remaining}"
    )

    games: list[Game] = []
    for event in events:
        game = Game(
            game_id=event["id"],
            home_team=event["home_team"],
            away_team=event["away_team"],
            start_time_utc_iso=event["commence_time"],
        )
        await queries.upsert_game(game)
        games.append(game)

    return games


@activity.defn
async def fetch_odds_batch(game_ids: list[str]) -> OddsBatch:
    """
    Fetch live odds for the given games from The Odds API.
    Returns one OddsSnapshot per (game, bookmaker) pair.
    """
    captured_at = datetime.now(timezone.utc).isoformat()

    params: dict[str, Any] = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "spreads,totals,h2h",
        "oddsFormat": "american",
    }
    if game_ids:
        params["eventIds"] = ",".join(game_ids)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ODDS_API_BASE}/sports/basketball_nba/odds",
            params=params,
            timeout=15.0,
        )
        resp.raise_for_status()
        events = resp.json()
        remaining = resp.headers.get("x-requests-remaining", "?")
        used = resp.headers.get("x-requests-used", "?")

    activity.logger.info(
        f"[fetch_odds_batch] {len(events)} events | quota used={used} remaining={remaining}"
    )

    snapshots: list[OddsSnapshot] = []
    for event in events:
        game_id = event["id"]
        home_team = event["home_team"]
        for bookmaker in event.get("bookmakers", []):
            payload = _extract_payload(bookmaker, home_team)
            if not payload:
                continue
            snapshots.append(
                OddsSnapshot(
                    snapshot_id=f"poll:{game_id}:{bookmaker['key']}:{captured_at}",
                    game_id=game_id,
                    kind="poll",
                    source=bookmaker["key"],
                    captured_at_utc_iso=captured_at,
                    payload=payload,
                )
            )

    return OddsBatch(
        source="the-odds-api",
        captured_at_utc_iso=captured_at,
        snapshots=snapshots,
    )


@activity.defn
async def upsert_odds_snapshot(snapshot: OddsSnapshot) -> None:
    await queries.upsert_odds_snapshot(snapshot)
    activity.logger.info(f"[upsert_odds_snapshot] {snapshot.snapshot_id}")


@activity.defn
async def fetch_close_odds_snapshot(inp: FetchCloseSnapshotInput) -> list[OddsSnapshot]:
    """
    Called at tip-off by CloseCaptureWorkflow.
    Captures the final pre-game line for a specific game (DraftKings preferred).
    Returns a one-item list on success, empty list if lines have already closed.
    (Temporal can't handle Optional return types, so we use list as the container.)
    """
    captured_at = datetime.now(timezone.utc).isoformat()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ODDS_API_BASE}/sports/basketball_nba/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "spreads,totals,h2h",
                "oddsFormat": "american",
                "eventIds": inp.game_id,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        events = resp.json()

    if not events:
        activity.logger.warning(
            f"[fetch_close_odds_snapshot] {inp.game_id} not in API — lines may have already closed"
        )
        return []

    event = events[0]
    home_team = event["home_team"]
    bookmakers = event.get("bookmakers", [])

    # Prefer DraftKings as canonical close source; fall back to first available
    canonical = next(
        (b for b in bookmakers if b["key"] == "draftkings"),
        bookmakers[0] if bookmakers else None,
    )
    if canonical is None:
        return []

    payload = _extract_payload(canonical, home_team)
    return [
        OddsSnapshot(
            snapshot_id=inp.snapshot_id,
            game_id=inp.game_id,
            kind="close",
            source=canonical["key"],
            captured_at_utc_iso=captured_at,
            payload=payload,
        )
    ]


# ── Kalshi helpers ──────────────────────────────────────────────────────────────

def _kalshi_mid(market: dict) -> float | None:
    """Return mid price (0–1) from a Kalshi market dict, or None if unavailable."""
    yes_bid = market.get("yes_bid_dollars") or 0
    yes_ask = market.get("yes_ask_dollars") or 0
    if yes_bid or yes_ask:
        mid = (float(yes_bid) + float(yes_ask)) / 2
    else:
        last = market.get("last_price_dollars")
        mid = float(last) if last else 0.0
    return mid if 0 < mid < 1 else None


def _kalshi_ml_from_markets(
    game_markets: list[dict], home_abbr: str, away_abbr: str
) -> tuple[int, int] | None:
    """
    Extract (ml_home, ml_away) American odds from a pair of Kalshi KXNBAGAME markets.
    Returns None if prices are missing or not in (0, 1).
    """
    home_prob: float | None = None
    away_prob: float | None = None

    for m in game_markets:
        ticker = m.get("ticker", "")
        suffix = ticker.split("-")[-1].upper()
        mid = _kalshi_mid(m)
        if mid is None:
            continue
        if suffix == home_abbr:
            home_prob = mid
        elif suffix == away_abbr:
            away_prob = mid

    if home_prob is None or away_prob is None:
        return None
    try:
        return prob_to_american(home_prob), prob_to_american(away_prob)
    except (ValueError, ZeroDivisionError):
        return None


# ── Kalshi activities ────────────────────────────────────────────────────────────

@activity.defn
async def fetch_kalshi_odds_batch(games: list[Game]) -> OddsBatch:
    """
    Fetch Kalshi KXNBAGAME winner market prices for today's games.
    Matches games to Kalshi events by 3-char team abbreviation embedded in the
    event ticker (last 6 chars = {away_abbr}{home_abbr}, e.g. DENPHX).
    Returns one OddsSnapshot per matched game with {ml_home, ml_away}.
    """
    captured_at = datetime.now(timezone.utc).isoformat()
    empty = OddsBatch(source="kalshi", captured_at_utc_iso=captured_at, snapshots=[])

    if not KALSHI_API_KEY:
        return empty

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{KALSHI_BASE}/markets",
            headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
            params={"limit": 200, "status": "open", "series_ticker": "KXNBAGAME"},
            timeout=10.0,
        )
        if resp.status_code != 200:
            activity.logger.warning(f"[fetch_kalshi_odds_batch] HTTP {resp.status_code}")
            return empty
        markets = resp.json().get("markets", [])

    # Group markets by event_ticker; build (away_abbr, home_abbr) → event_ticker lookup.
    # Event ticker format: KXNBAGAME-{date}{away3}{home3}  e.g. KXNBAGAME-26MAR24DENPHX
    event_markets: dict[str, list[dict]] = {}
    abbr_pair_to_event: dict[tuple[str, str], str] = {}
    for m in markets:
        et = m.get("event_ticker", "")
        event_markets.setdefault(et, []).append(m)
        team_part = et.split("-")[-1]           # e.g. "26MAR24DENPHX"
        home_abbr = team_part[-3:].upper()      # "PHX"
        away_abbr = team_part[-6:-3].upper()    # "DEN"
        abbr_pair_to_event[(away_abbr, home_abbr)] = et

    snapshots: list[OddsSnapshot] = []
    for game in games:
        h_abbr = TEAM_ABBR.get(game.home_team)
        a_abbr = TEAM_ABBR.get(game.away_team)
        if not h_abbr or not a_abbr:
            activity.logger.debug(
                f"[fetch_kalshi_odds_batch] No abbreviation for {game.home_team!r} or {game.away_team!r}"
            )
            continue

        et = abbr_pair_to_event.get((a_abbr, h_abbr))
        if et is None:
            continue

        result = _kalshi_ml_from_markets(event_markets[et], h_abbr, a_abbr)
        if result is None:
            continue
        ml_home, ml_away = result

        snapshots.append(OddsSnapshot(
            snapshot_id=f"poll:{game.game_id}:kalshi:{captured_at}",
            game_id=game.game_id,
            kind="poll",
            source="kalshi",
            captured_at_utc_iso=captured_at,
            payload={"ml_home": ml_home, "ml_away": ml_away},
        ))

    activity.logger.info(
        f"[fetch_kalshi_odds_batch] matched {len(snapshots)}/{len(games)} games"
    )
    return OddsBatch(source="kalshi", captured_at_utc_iso=captured_at, snapshots=snapshots)


# ── ESPN injuries helpers ─────────────────────────────────────────────────────

def _parse_espn_detail(details: dict) -> str | None:
    """Build a readable detail string from an ESPN injury details dict."""
    if not details:
        return None
    parts = [str(details[k]) for k in ("type", "side", "detail") if details.get(k)]
    return " - ".join(parts) if parts else None


def _parse_espn_injuries_response(
    data: dict,
) -> list[tuple[str, str, str, str, str | None]]:
    """
    Parse ESPN injury report API response.
    Returns list of (record_id, player_name, team, status, detail).
    """
    results = []
    for team_entry in data.get("injuries", []):
        team_name = team_entry.get("team", {}).get("displayName", "")
        if not team_name:
            continue
        for inj in team_entry.get("injuries", []):
            athlete = inj.get("athlete", {})
            athlete_id = str(athlete.get("id", ""))
            player_name = athlete.get("displayName", "")
            if not athlete_id or not player_name:
                continue
            status = inj.get("status", "")
            if not status:
                continue
            detail = _parse_espn_detail(inj.get("details") or {})
            results.append((athlete_id, player_name, team_name, status, detail))
    return results


# ── ESPN injuries activity ────────────────────────────────────────────────────

@activity.defn
async def fetch_injuries() -> list[InjuryAlert]:
    """
    Poll ESPN unofficial API for NBA injury updates.
    Detects status changes vs. what's stored in the DB.
    Returns InjuryAlert entries only for players whose status changed (or new
    significant listings). The bot's InjuryCog handles Discord notifications.
    """
    await schema.init_db()
    now_iso = datetime.now(timezone.utc).isoformat()

    async with httpx.AsyncClient() as client:
        resp = await client.get(ESPN_INJURIES_URL, timeout=15.0)
        if resp.status_code != 200:
            activity.logger.warning(f"[fetch_injuries] ESPN returned HTTP {resp.status_code}")
            return []
        data = resp.json()

    entries = _parse_espn_injuries_response(data)
    activity.logger.info(f"[fetch_injuries] {len(entries)} players in ESPN report")

    changes: list[InjuryAlert] = []
    for record_id, player_name, team, status, detail in entries:
        prev = await queries.upsert_injury_status(record_id, player_name, team, status, detail, now_iso)
        if prev is not None:
            changes.append(InjuryAlert(
                record_id=record_id,
                player_name=player_name,
                team=team,
                status=status,
                prev_status=prev if prev else None,
                detail=detail,
                updated_at_utc_iso=now_iso,
            ))

    activity.logger.info(f"[fetch_injuries] {len(changes)} status changes detected")
    return changes


@activity.defn
async def fetch_kalshi_close_snapshot(inp: FetchCloseSnapshotInput) -> list[OddsSnapshot]:
    """
    Capture Kalshi ML close for a game at tip-off.
    Looks up team names from DB then finds the matching KXNBAGAME event.
    Returns a one-item list on success, empty list on failure.
    (Same list-as-Optional convention as fetch_close_odds_snapshot.)
    """
    captured_at = datetime.now(timezone.utc).isoformat()

    if not KALSHI_API_KEY:
        return []

    game = await queries.get_game_by_id(inp.game_id)
    if game is None:
        activity.logger.warning(
            f"[fetch_kalshi_close_snapshot] game {inp.game_id} not found in DB"
        )
        return []

    h_abbr = TEAM_ABBR.get(game.home_team)
    a_abbr = TEAM_ABBR.get(game.away_team)
    if not h_abbr or not a_abbr:
        activity.logger.warning(
            f"[fetch_kalshi_close_snapshot] No abbreviation for {game.home_team!r}/{game.away_team!r}"
        )
        return []

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{KALSHI_BASE}/markets",
            headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
            params={"limit": 200, "status": "open", "series_ticker": "KXNBAGAME"},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return []
        markets = resp.json().get("markets", [])

    # Find markets for this specific game
    game_markets = [
        m for m in markets
        if (lambda tp: tp[-3:].upper() == h_abbr and tp[-6:-3].upper() == a_abbr)(
            m.get("event_ticker", "").split("-")[-1]
        )
    ]

    if not game_markets:
        activity.logger.warning(
            f"[fetch_kalshi_close_snapshot] No open market for {a_abbr}@{h_abbr}"
        )
        return []

    result = _kalshi_ml_from_markets(game_markets, h_abbr, a_abbr)
    if result is None:
        return []

    ml_home, ml_away = result
    return [OddsSnapshot(
        snapshot_id=inp.snapshot_id,
        game_id=inp.game_id,
        kind="close",
        source="kalshi",
        captured_at_utc_iso=captured_at,
        payload={"ml_home": ml_home, "ml_away": ml_away},
    )]
