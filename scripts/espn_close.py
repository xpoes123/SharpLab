"""Capture a DraftKings close from ESPN's keyless feed when The Odds API is down.

Run just before start: PYTHONPATH=. venv/bin/python scripts/espn_close.py <game_id>
Writes a kind='close' snapshot; the CLV cog picks it up and posts as usual.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from db import queries
from shared.models import OddsSnapshot
from temporal.activities import ESPN_SCORES_URL


def payload_from_pick(pick: dict) -> dict:
    """Map an ESPN pickcenter entry to the odds_snapshots payload (spread = home)."""
    home, away = pick["homeTeamOdds"], pick["awayTeamOdds"]
    spread, ml_home, ml_away = pick["spread"], home["moneyLine"], away["moneyLine"]
    if spread and (spread < 0) != (ml_home < ml_away):
        raise ValueError(f"spread {spread} disagrees with ML {ml_home}/{ml_away}")
    return {
        "spread": spread,
        "spread_odds": int(home["spreadOdds"]),
        "ml_home": ml_home,
        "ml_away": ml_away,
        "total": pick["overUnder"],
        "total_over_odds": int(pick["overOdds"]),
        "total_under_odds": int(pick["underOdds"]),
    }


async def capture(game_id: str) -> None:
    game = await queries.get_game_by_id(game_id)
    if game is None:
        raise SystemExit(f"no game {game_id}")
    url = ESPN_SCORES_URL[game.sport]
    start = datetime.fromisoformat(game.start_time_utc_iso.replace("Z", "+00:00"))
    date = start.astimezone(ZoneInfo("America/New_York")).strftime("%Y%m%d")
    async with httpx.AsyncClient(timeout=15.0) as client:
        events = (await client.get(url, params={"dates": date})).raise_for_status().json()["events"]
        event = next(e for e in events if e["name"] == f"{game.away_team} at {game.home_team}")
        if event["status"]["type"]["state"] != "pre":
            raise SystemExit(f"{event['name']} already started; a live line is not a close")
        summary = await client.get(url.replace("scoreboard", "summary"), params={"event": event["id"]})
    pick = next(p for p in summary.raise_for_status().json()["pickcenter"]
                if p["provider"]["name"] == "DraftKings")
    now = datetime.now(timezone.utc).isoformat()
    payload = payload_from_pick(pick)
    await queries.upsert_odds_snapshot(OddsSnapshot(
        snapshot_id=f"close:{game_id}:draftkings:{now}", game_id=game_id, kind="close",
        source="draftkings", captured_at_utc_iso=now, payload=payload,
    ))
    print(event["name"], payload)


if __name__ == "__main__":
    asyncio.run(capture(sys.argv[1]))
