"""Dataclasses shared between the pipeline and the bot."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Game:
    game_id: str
    home_team: str
    away_team: str
    start_time_utc_iso: str  # UTC ISO 8601


@dataclass(frozen=True)
class OddsSnapshot:
    snapshot_id: str
    game_id: str
    kind: str               # 'poll' | 'close'
    source: str             # 'draftkings' | 'fanduel' | 'kalshi' | 'polymarket' | ...
    captured_at_utc_iso: str
    payload: dict[str, Any]  # {spread, spread_odds, ml_home, ml_away, total, total_over_odds, total_under_odds}


@dataclass(frozen=True)
class OddsBatch:
    source: str
    captured_at_utc_iso: str
    snapshots: list[OddsSnapshot]
