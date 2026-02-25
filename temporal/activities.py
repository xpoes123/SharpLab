from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from temporalio import activity
from typing import Any

@dataclass(frozen=True)
class Game:
    game_id: str
    start_time_utc_iso: str
    
@dataclass(frozen=True)
class OddsSnapshot:
    snapshot_id: str
    kind: str
    game_id: str
    source: str
    captured_at_utc_iso: str
    payload: dict
    
@dataclass(frozen=True)
class OddsBatch:
    source: str
    captured_at_utc_iso: str
    games: dict[str, dict[str, Any]]

@dataclass(frozen=True)
class FetchCloseSnapshotInput:
    snapshot_id: str
    game_id: str

@dataclass(frozen=True)
class FetchPollSnapshotInput:
    snapshot_id: str
    game_ids: list[str]
    
@activity.defn
async def fetch_games_for_today() -> list[Game]:
    """
    Side effect boundary: eventually this hits an NBA schedule API. For now, return a single fake game
    """
    now = datetime.now(timezone.utc)
    start = now.replace(minute=(now.minute + 2) % 60)
    activity.logger.info(f"[fetch_games_for_today] returning game_id=GAME123 start={start.isoformat()}")
    return [Game(game_id="GAME123", start_time_utc_iso=start.isoformat())]

@activity.defn
async def fetch_odds_batch(inp: FetchPollSnapshotInput) -> OddsBatch:
    """
    Side effect boundary: eventually this hits an odds provider. Return a snapshot with required metadata: source + timestamp
    """
    captured_at = datetime.now(timezone.utc).isoformat()
    source = "stubbook"
    
    games = {gid: {"spread": -4.5, "price": -110} for gid in inp.game_ids}
    return OddsBatch(
        source=source,
        captured_at_utc_iso=captured_at,
        games=games,
    )

@activity.defn
async def fetch_close_odds_snapshot(inp: FetchCloseSnapshotInput) -> OddsSnapshot:
    captured_at = datetime.now(timezone.utc).isoformat()
    source = "stubbook"
    
    payload = {"spread": -4.5, "price": -110}
    
    activity.logger.info(f"[fetch_close_odds_snapshot] {inp.snapshot_id=} {inp.game_id=}")
    return OddsSnapshot(
        snapshot_id=inp.snapshot_id,
        kind="close",
        game_id=inp.game_id,
        source=source,
        captured_at_utc_iso=captured_at,
        payload=payload,
    )

_SNAPSHOT_STORE: dict[str, OddsSnapshot] = {}

@activity.defn
async def upsert_odds_snapshot(snapshot: OddsSnapshot) -> None:
    existed = snapshot.snapshot_id in _SNAPSHOT_STORE
    _SNAPSHOT_STORE[snapshot.snapshot_id] = snapshot
    activity.logger.info(
        f"[upsert_odds_snapshot] snapshot_id={snapshot.snapshot_id} existed={existed}"
    )