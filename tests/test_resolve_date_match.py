"""Regression test: a final score must match the game on its DATE, not just the
matchup — otherwise yesterday's final marks today's game final prematurely."""
from __future__ import annotations

from shared.models import Game
from temporal.activities import _pick_game_for_date


def _g(gid: str, start: str) -> Game:
    return Game(game_id=gid, home_team="Tampa Bay Rays", away_team="Detroit Tigers",
                start_time_utc_iso=start, sport="mlb")


def test_picks_the_game_on_the_score_date():
    # Same matchup on consecutive days (recent-first, like the query returns).
    cands = [_g("today", "2026-06-01T22:41:00Z"), _g("yesterday", "2026-05-31T18:11:00Z")]
    # Yesterday's final must hit yesterday's game, NOT today's unplayed one.
    assert _pick_game_for_date(cands, "2026-05-31").game_id == "yesterday"
    assert _pick_game_for_date(cands, "2026-06-01").game_id == "today"


def test_skips_when_no_game_on_that_date():
    # Score is for a date with no tracked game nearby → don't mark anything final.
    cands = [_g("g", "2026-06-05T22:00:00Z")]
    assert _pick_game_for_date(cands, "2026-05-31") is None


def test_handles_late_et_game_crossing_utc_midnight():
    # NBA game 8:40pm ET = 00:40 UTC next day; score date is the ET date.
    cands = [_g("nba", "2026-06-04T00:40:00Z")]
    assert _pick_game_for_date(cands, "2026-06-03").game_id == "nba"


def test_fallback_when_no_date():
    cands = [_g("a", "2026-06-01T22:00:00Z"), _g("b", "2026-05-30T22:00:00Z")]
    assert _pick_game_for_date(cands, "").game_id == "a"  # most recent


def test_empty_candidates():
    assert _pick_game_for_date([], "2026-05-31") is None
