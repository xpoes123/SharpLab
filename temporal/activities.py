from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from temporalio import activity

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
    activity.logger.info("[fetch_games_for_today] returning stub game")
    return [Game(game_id="GAME123", start_time_utc_iso=start.isoformat())]

@activity.defn
async def fetch_odds_snapshot(inp: FetchPollSnapshotInput) -> OddsSnapshot:
    """
    Side effect boundary: eventually this hits an odds provider. Return a snapshot with required metadata: source + timestamp
    """
    captured_at = datetime.now(timezone.utc).isoformat()
    source = "stubbook"
    
    payload = {gid: {"spread": -4.5, "price": -110} for gid in inp.game_ids}

    activity.logger.info(f"[fetch_poll_odds_snapshot] {inp.snapshot_id=} game_count={len(inp.game_ids)}")

    return OddsSnapshot(
        snapshot_id=inp.snapshot_id,
        kind="poll",
        game_id="__BATCH__",
        source=source,
        captured_at_utc_iso=captured_at,
        payload=payload,
    )

@activity.defn
async def persist_odds_snapshot(snapshot: OddsSnapshot) -> None:
    """
    Side effect boundary: eventually inserts into odds_snapshots table. For now, just log
    """
    activity.logger.info(
        f"[persist_odds_snapshot] source={snapshot.source} ts={snapshot.captured_at_utc_iso}"
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