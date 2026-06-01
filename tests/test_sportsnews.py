"""Unit tests for the sports-news cog's pure helpers (no network / no Haiku)."""
from datetime import datetime, timezone

from bot.cogs.sportsnews import _merge, _parse_ts, _ping_leagues, _primary


def _art(aid, league, headline="h", typ="HeadlineNews"):
    return {"id": aid, "league": league, "headline": headline,
            "description": "", "type": typ, "published": "", "url": ""}


def test_merge_dedupes_by_id_and_unions_leagues():
    # Same article id appears in both the NFL and NBA feeds (ESPN cross-posts).
    batches = [
        [_art("1", "nfl"), _art("2", "nfl")],
        [_art("1", "nba"), _art("3", "nba")],
    ]
    merged = _merge(batches)
    assert set(merged) == {"1", "2", "3"}
    assert merged["1"]["leagues"] == {"nfl", "nba"}   # unioned, not duplicated
    assert merged["2"]["leagues"] == {"nfl"}
    assert merged["3"]["leagues"] == {"nba"}


def test_primary_follows_league_order():
    # LEAGUES order is nba, nfl, mlb — nba wins when present.
    assert _primary({"mlb", "nba"}) == "nba"
    assert _primary({"mlb", "nfl"}) == "nfl"
    assert _primary({"mlb"}) == "mlb"


def test_ping_only_single_feed_articles():
    assert _ping_leagues({"nfl"}) == {"nfl"}          # league-specific → ping
    assert _ping_leagues({"nba", "nfl", "mlb"}) == set()  # cross-promo → no ping
    assert _ping_leagues({"nba", "mlb"}) == set()


def test_parse_ts_handles_z_suffix_and_naive():
    dt = _parse_ts("2026-06-01T17:41:58Z")
    assert dt == datetime(2026, 6, 1, 17, 41, 58, tzinfo=timezone.utc)
    assert _parse_ts("") is None
    assert _parse_ts("not-a-date") is None
