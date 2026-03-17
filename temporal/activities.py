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
from shared.models import Game, OddsBatch, OddsSnapshot

load_dotenv()

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"


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
