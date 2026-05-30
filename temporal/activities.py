"""
All pipeline side effects live here. One activity per API call.

API quota notes (The Odds API free tier = 500 req/month):
- fetch_games_for_today: 1 call/cycle (lightweight /events endpoint)
- fetch_odds_batch:      1 call/cycle (full odds)
- fetch_close_odds_snapshot: 1 call/game at tip-off (filtered by eventId)
At 30-min intervals during a ~9-hour game window × 20 game days = ~360 calls/month.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv
from temporalio import activity

from db import queries, schema
from shared.models import Bet, Game, GameResult, InjuryAlert, OddsBatch, OddsSnapshot, get_team_abbr
from shared.odds_utils import fetch_polymarket_ml, prob_to_american

load_dotenv()

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

BALLDONTLIE_API_KEY = os.getenv("BALLDONTLIE_API_KEY", "")
BALLDONTLIE_BASE = "https://api.balldontlie.io/v1"

ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"


# ── Sport config maps ────────────────────────────────────────────────────────

ODDS_API_SPORT_KEY = {"nba": "basketball_nba", "mlb": "baseball_mlb"}
KALSHI_SERIES = {"nba": "KXNBAGAME", "mlb": "KXMLBGAME"}
ESPN_SCORES_URL = {
    "nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "mlb": "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
}


# ── Input types ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FetchCloseSnapshotInput:
    snapshot_id: str
    game_id: str  # The Odds API event ID
    sport: str = "nba"


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
                payload["spread_away_odds"] = away[0].get("price")

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
async def fetch_games_for_today(sport: str = "nba") -> list[Game]:
    """
    Fetch today's schedule from The Odds API /events endpoint.
    Lightweight call — no odds data, minimal quota usage.
    Also upserts games to the DB so the rest of the pipeline can reference them.
    """
    await schema.init_db()
    sport_key = ODDS_API_SPORT_KEY.get(sport, "basketball_nba")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ODDS_API_BASE}/sports/{sport_key}/events",
            params={"apiKey": ODDS_API_KEY},
            timeout=15.0,
        )
        resp.raise_for_status()
        events = resp.json()
        remaining = resp.headers.get("x-requests-remaining", "?")
        used = resp.headers.get("x-requests-used", "?")

    activity.logger.info(
        f"[fetch_games_for_today] {sport} {len(events)} events | quota used={used} remaining={remaining}"
    )

    games: list[Game] = []
    for event in events:
        game = Game(
            game_id=event["id"],
            home_team=event["home_team"],
            away_team=event["away_team"],
            start_time_utc_iso=event["commence_time"],
            sport=sport,
        )
        await queries.upsert_game(game)
        games.append(game)

    return games


@activity.defn
async def fetch_odds_batch(game_ids: list[str], sport: str = "nba") -> OddsBatch:
    """
    Fetch live odds for the given games from The Odds API.
    Returns one OddsSnapshot per (game, bookmaker) pair.
    """
    captured_at = datetime.now(timezone.utc).isoformat()
    sport_key = ODDS_API_SPORT_KEY.get(sport, "basketball_nba")

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
            f"{ODDS_API_BASE}/sports/{sport_key}/odds",
            params=params,
            timeout=15.0,
        )
        resp.raise_for_status()
        events = resp.json()
        remaining = resp.headers.get("x-requests-remaining", "?")
        used = resp.headers.get("x-requests-used", "?")

    activity.logger.info(
        f"[fetch_odds_batch] {sport} {len(events)} events | quota used={used} remaining={remaining}"
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
    sport_key = ODDS_API_SPORT_KEY.get(inp.sport, "basketball_nba")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ODDS_API_BASE}/sports/{sport_key}/odds",
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
    Extract (ml_home, ml_away) American odds from a pair of Kalshi game markets.
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
async def fetch_kalshi_odds_batch(games: list[Game], sport: str = "nba") -> OddsBatch:
    """
    Fetch Kalshi winner market prices for today's games.
    Matches games to Kalshi events by 3-char team abbreviation embedded in the
    event ticker (last 6 chars = {away_abbr}{home_abbr}, e.g. DENPHX).
    Returns one OddsSnapshot per matched game with {ml_home, ml_away}.
    """
    captured_at = datetime.now(timezone.utc).isoformat()
    empty = OddsBatch(source="kalshi", captured_at_utc_iso=captured_at, snapshots=[])

    series_ticker = KALSHI_SERIES.get(sport)
    if not KALSHI_API_KEY or not series_ticker:
        return empty

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{KALSHI_BASE}/markets",
            headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
            params={"limit": 200, "status": "open", "series_ticker": series_ticker},
            timeout=10.0,
        )
        if resp.status_code != 200:
            activity.logger.warning(f"[fetch_kalshi_odds_batch] HTTP {resp.status_code}")
            return empty
        markets = resp.json().get("markets", [])

    # Group markets by event_ticker; build (away_abbr, home_abbr) → event_ticker lookup.
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
        h_abbr = get_team_abbr(game.home_team, sport)
        a_abbr = get_team_abbr(game.away_team, sport)
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
        f"[fetch_kalshi_odds_batch] {sport} matched {len(snapshots)}/{len(games)} games"
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
    record_id = ESPN athlete ID, extracted from player link URLs.
    """
    results = []
    for team_entry in data.get("injuries", []):
        team_name = team_entry.get("displayName", "")
        if not team_name:
            continue
        for inj in team_entry.get("injuries", []):
            athlete = inj.get("athlete", {})
            player_name = athlete.get("displayName", "")
            if not player_name:
                continue
            # Athlete ID is not a direct field — extract from player link URL
            athlete_id = ""
            for link in athlete.get("links", []):
                m = re.search(r"/id/(\d+)/", link.get("href", ""))
                if m:
                    athlete_id = m.group(1)
                    break
            if not athlete_id:
                continue
            status = inj.get("status", "")
            if not status:
                continue
            detail = _parse_espn_detail(inj.get("details") or {})
            results.append((athlete_id, player_name, team_name, status, detail))
    return results


# ── ESPN injuries activities ──────────────────────────────────────────────────

# Mirror of the threshold set in db/queries.py — statuses where a player is
# already known to be compromised (no new alert needed).
_PREVIOUSLY_INJURED = {"Out", "Doubtful", "Questionable", "Day-To-Day"}


@activity.defn
async def fetch_injuries() -> list[InjuryAlert]:
    """
    Poll ESPN unofficial API for NBA injury updates.
    Detects status changes vs. what's stored in the DB and returns only
    actionable InjuryAlert entries (new Out listings or healthy→Out transitions).

    This activity is READ-ONLY — it never writes to the DB.  Call
    record_injury_changes() afterward to persist the results.  Keeping reads
    and writes in separate activities makes each one safely idempotent: if
    fetch_injuries is retried after a transient failure the DB state is
    unchanged, so it returns the same result every time.
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
        current = await queries.get_injury_status(record_id)
        if current is None:
            # New player — alert only if Out (surprise listing); always track in DB
            changes.append(InjuryAlert(
                record_id=record_id,
                player_name=player_name,
                team=team,
                status=status,
                prev_status=None,
                detail=detail,
                updated_at_utc_iso=now_iso,
                should_notify=status == "Out",
            ))
        elif current != status:
            # Status changed — alert if transitioning to Out from a healthy state;
            # always persist so future Out detections see the correct previous status.
            should_notify = status == "Out" and current not in _PREVIOUSLY_INJURED
            changes.append(InjuryAlert(
                record_id=record_id,
                player_name=player_name,
                team=team,
                status=status,
                prev_status=current,
                detail=detail,
                updated_at_utc_iso=now_iso,
                should_notify=should_notify,
            ))

    activity.logger.info(f"[fetch_injuries] {len(changes)} status changes detected")
    return changes


@activity.defn
async def record_injury_changes(changes: list[InjuryAlert]) -> None:
    """
    Persist detected injury alerts to DB.
    Idempotent — safe to retry; a duplicate write for the same alert is a no-op
    that preserves whatever notified state the InjuryCog may have already set.
    """
    await schema.init_db()
    for alert in changes:
        await queries.record_injury_alert(alert)
    activity.logger.info(f"[record_injury_changes] {len(changes)} alerts persisted")


@activity.defn
async def fetch_kalshi_close_snapshot(inp: FetchCloseSnapshotInput) -> list[OddsSnapshot]:
    """
    Capture Kalshi ML close for a game at tip-off.
    Looks up team names from DB then finds the matching Kalshi event.
    Returns a one-item list on success, empty list on failure.
    (Same list-as-Optional convention as fetch_close_odds_snapshot.)
    """
    captured_at = datetime.now(timezone.utc).isoformat()

    series_ticker = KALSHI_SERIES.get(inp.sport)
    if not KALSHI_API_KEY or not series_ticker:
        return []

    game = await queries.get_game_by_id(inp.game_id)
    if game is None:
        activity.logger.warning(
            f"[fetch_kalshi_close_snapshot] game {inp.game_id} not found in DB"
        )
        return []

    h_abbr = get_team_abbr(game.home_team, inp.sport)
    a_abbr = get_team_abbr(game.away_team, inp.sport)
    if not h_abbr or not a_abbr:
        activity.logger.warning(
            f"[fetch_kalshi_close_snapshot] No abbreviation for {game.home_team!r}/{game.away_team!r}"
        )
        return []

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{KALSHI_BASE}/markets",
            headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
            params={"limit": 200, "status": "open", "series_ticker": series_ticker},
            timeout=10.0,
        )
        if resp.status_code != 200:
            activity.logger.warning(
                f"[fetch_kalshi_close_snapshot] HTTP {resp.status_code} for {game.game_id}"
            )
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


# ── Polymarket activities ────────────────────────────────────────────────────

@activity.defn
async def fetch_polymarket_odds_batch(games: list[Game]) -> OddsBatch:
    """
    Fetch Polymarket game winner market prices for today's games.
    Searches open markets via the Gamma API (no auth required).
    Returns one OddsSnapshot per matched game with {ml_home, ml_away}.
    """
    captured_at = datetime.now(timezone.utc).isoformat()
    snapshots: list[OddsSnapshot] = []

    async with httpx.AsyncClient() as client:
        for game in games:
            result = await fetch_polymarket_ml(client, game.home_team, game.away_team)
            if result is None:
                continue
            ml_home, ml_away = result
            snapshots.append(OddsSnapshot(
                snapshot_id=f"poll:{game.game_id}:polymarket:{captured_at}",
                game_id=game.game_id,
                kind="poll",
                source="polymarket",
                captured_at_utc_iso=captured_at,
                payload={"ml_home": ml_home, "ml_away": ml_away},
            ))

    activity.logger.info(
        f"[fetch_polymarket_odds_batch] matched {len(snapshots)}/{len(games)} games"
    )
    return OddsBatch(source="polymarket", captured_at_utc_iso=captured_at, snapshots=snapshots)


@activity.defn
async def fetch_polymarket_close_snapshot(inp: FetchCloseSnapshotInput) -> list[OddsSnapshot]:
    """
    Capture Polymarket ML close for a game at tip-off.
    Looks up team names from DB then searches Gamma API.
    Returns a one-item list on success, empty list on failure.
    (Same list-as-Optional convention as fetch_close_odds_snapshot.)
    """
    captured_at = datetime.now(timezone.utc).isoformat()

    game = await queries.get_game_by_id(inp.game_id)
    if game is None:
        activity.logger.warning(
            f"[fetch_polymarket_close_snapshot] game {inp.game_id} not found in DB"
        )
        return []

    async with httpx.AsyncClient() as client:
        result = await fetch_polymarket_ml(client, game.home_team, game.away_team)

    if result is None:
        activity.logger.warning(
            f"[fetch_polymarket_close_snapshot] No open market for "
            f"{game.away_team} @ {game.home_team}"
        )
        return []

    ml_home, ml_away = result
    return [OddsSnapshot(
        snapshot_id=inp.snapshot_id,
        game_id=inp.game_id,
        kind="close",
        source="polymarket",
        captured_at_utc_iso=captured_at,
        payload={"ml_home": ml_home, "ml_away": ml_away},
    )]


# ── Bet resolution activities ─────────────────────────────────────────────────

async def _fetch_nba_scores(dates: list[str]) -> list[GameResult]:
    """Fetch final scores from balldontlie (NBA only)."""
    headers = {"Authorization": BALLDONTLIE_API_KEY} if BALLDONTLIE_API_KEY else {}
    params: list[tuple[str, str | int]] = [("per_page", 100)]
    for d in dates:
        params.append(("dates[]", d))

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BALLDONTLIE_BASE}/games",
            params=params,
            headers=headers,
            timeout=15.0,
        )
        resp.raise_for_status()
        games_data = resp.json().get("data", [])

    results: list[GameResult] = []
    for g in games_data:
        status = g.get("status", "")
        period = g.get("period", 0)
        if not status.startswith("Final") or not period:
            continue
        home_score = g.get("home_team_score") or 0
        away_score = g.get("visitor_team_score") or 0
        if home_score == 0 and away_score == 0:
            continue
        results.append(GameResult(
            home_last=g["home_team"]["full_name"].split()[-1].lower(),
            away_last=g["visitor_team"]["full_name"].split()[-1].lower(),
            home_score=home_score,
            away_score=away_score,
        ))
    return results


async def _fetch_espn_scores(dates: list[str], sport: str) -> list[GameResult]:
    """Fetch final scores from ESPN scoreboard API."""
    url = ESPN_SCORES_URL.get(sport)
    if not url:
        return []

    results: list[GameResult] = []
    async with httpx.AsyncClient() as client:
        for date_str in dates:
            espn_date = date_str.replace("-", "")  # ESPN expects YYYYMMDD
            try:
                resp = await client.get(url, params={"dates": espn_date}, timeout=15.0)
                if resp.status_code != 200:
                    continue
                data = resp.json()
            except Exception as e:
                activity.logger.warning(f"[_fetch_espn_scores] {sport} {date_str} fetch failed: {e}")
                continue

            for event in data.get("events", []):
                status_type = event.get("status", {}).get("type", {}).get("name", "")
                if status_type != "STATUS_FINAL":
                    continue
                comp = event.get("competitions", [{}])[0]
                competitors = comp.get("competitors", [])
                home_name = away_name = ""
                home_score = away_score = 0
                for c in competitors:
                    team_name = c.get("team", {}).get("displayName", "")
                    try:
                        score = int(c.get("score", "0") or "0")
                    except (ValueError, TypeError):
                        score = 0
                    if c.get("homeAway") == "home":
                        home_name = team_name
                        home_score = score
                    else:
                        away_name = team_name
                        away_score = score
                if not home_name or not away_name:
                    continue
                if home_score == 0 and away_score == 0:
                    continue  # scores not yet populated despite STATUS_FINAL
                results.append(GameResult(
                    home_last=home_name.split()[-1].lower(),
                    away_last=away_name.split()[-1].lower(),
                    home_score=home_score,
                    away_score=away_score,
                ))
    return results


@activity.defn
async def fetch_final_scores(dates: list[str], sport: str = "nba") -> list[GameResult]:
    """
    Fetch final scores for the given dates.
    Routes to balldontlie for NBA, ESPN for MLB.
    Returns only completed games.
    """
    if sport == "nba":
        results = await _fetch_nba_scores(dates)
    else:
        results = await _fetch_espn_scores(dates, sport)

    activity.logger.info(f"[fetch_final_scores] {sport} {len(results)} final games on {dates}")
    return results


def _resolve_bet(
    bet: Bet, home_team: str, away_team: str, home_score: int, away_score: int
) -> str:
    """Return won/lost/push/void for a bet given the final score."""
    side = bet.side.lower()
    market = bet.market.lower()
    home_l = home_team.lower()
    away_l = away_team.lower()

    def _is_home(s: str) -> bool:
        return s in home_l and s not in away_l

    def _is_away(s: str) -> bool:
        return s in away_l and s not in home_l

    if market in ("moneyline", "kalshi"):
        if side == "yes" or _is_home(side):
            return "won" if home_score > away_score else "lost"
        if side == "no" or _is_away(side):
            return "won" if away_score > home_score else "lost"

    elif market == "spread":
        if bet.line is None:
            return "void"
        if _is_home(side):
            side_score, opp_score = home_score, away_score
        elif _is_away(side):
            side_score, opp_score = away_score, home_score
        else:
            return "void"
        margin = (side_score - opp_score) + bet.line
        if abs(margin) < 0.01:
            return "push"
        return "won" if margin > 0 else "lost"

    elif market == "total":
        if bet.line is None:
            return "void"
        total = home_score + away_score
        diff = total - bet.line
        if abs(diff) < 0.01:
            return "push"
        if side == "over":
            return "won" if diff > 0 else "lost"
        if side == "under":
            return "won" if diff < 0 else "lost"

    return "void"


@activity.defn
async def resolve_bets_for_game(result: GameResult) -> int:
    """
    Match a completed game to a DB game by team suffix, then resolve all open/graded bets.
    Marks the game 'final' so it won't be reprocessed. Returns the count of bets resolved.
    """
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

    game = await queries.get_game_by_team_suffixes(result.home_last, result.away_last, cutoff)
    if game is None:
        # Expected steady state: every already-final game in the score window is
        # re-reported each resolution cycle and matches no *unresolved* game, so
        # this is debug-level, not a warning. A genuinely unmatched game still
        # surfaces as a perpetually-open bet in /bet view.
        activity.logger.debug(
            f"[resolve_bets_for_game] No unresolved DB game for "
            f"{result.away_last} @ {result.home_last} (already final or not tracked)"
        )
        return 0

    bets = await queries.get_resolvable_bets_for_game(game.game_id)
    resolved = 0
    for bet in bets:
        outcome = _resolve_bet(
            bet, game.home_team, game.away_team, result.home_score, result.away_score
        )
        await queries.update_bet_result(bet.bet_id, outcome)
        activity.logger.info(
            f"[resolve_bets_for_game] bet {bet.bet_id} → {outcome} "
            f"({result.away_last} @ {result.home_last} "
            f"{result.away_score}-{result.home_score})"
        )
        resolved += 1

    await queries.update_game_scores(game.game_id, result.home_score, result.away_score)
    await queries.update_game_status(game.game_id, "final")
    activity.logger.info(
        f"[resolve_bets_for_game] {game.away_team} @ {game.home_team}: "
        f"{result.away_score}-{result.home_score}, {resolved} bets resolved"
    )
    return resolved
