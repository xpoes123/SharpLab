"""Tests for line movement alert cog — embed building, grouping, and DB interactions."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.models import Game, LineAlertThresholds, LineMovement
from bot.cogs.alerts import (
    build_alert_embed,
    detect_movements_for_game,
    post_pending_alerts,
    _fmt_movement,
    _direction_arrow,
    THRESHOLDS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

GAME = Game(
    game_id="nba-20260421-bos-lal",
    home_team="Los Angeles Lakers",
    away_team="Boston Celtics",
    start_time_utc_iso="2026-04-21T02:00:00Z",
)

SPREAD_MOVEMENT = LineMovement(
    movement_id=1,
    game_id="nba-20260421-bos-lal",
    source="draftkings",
    market="spread",
    prev_value=-4.5,
    curr_value=-6.5,
    delta=-2.0,
    detected_at_utc_iso="2026-04-21T01:30:00Z",
)

TOTAL_MOVEMENT = LineMovement(
    movement_id=2,
    game_id="nba-20260421-bos-lal",
    source="fanduel",
    market="total",
    prev_value=224.5,
    curr_value=228.0,
    delta=3.5,
    detected_at_utc_iso="2026-04-21T01:30:00Z",
)

ML_MOVEMENT = LineMovement(
    movement_id=3,
    game_id="nba-20260421-bos-lal",
    source="draftkings",
    market="moneyline",
    prev_value=-150,
    curr_value=-180,
    delta=-30,
    detected_at_utc_iso="2026-04-21T01:30:00Z",
)


# ── Embed building ───────────────────────────────────────────────────────────

def test_build_alert_embed_single_spread():
    embed = build_alert_embed(GAME, [SPREAD_MOVEMENT])
    assert embed.title == "\U0001f514 Line Movement Alert"
    assert "Boston Celtics @ Los Angeles Lakers" in embed.description
    assert embed.color.value == 0xF39C12
    # Should have Game Time field + Spread field
    field_names = [f.name for f in embed.fields]
    assert "Game Time" in field_names
    assert "Spread" in field_names


def test_build_alert_embed_multiple_markets():
    embed = build_alert_embed(GAME, [SPREAD_MOVEMENT, TOTAL_MOVEMENT, ML_MOVEMENT])
    field_names = [f.name for f in embed.fields]
    assert "Spread" in field_names
    assert "Total" in field_names
    assert "Moneyline" in field_names


def test_build_alert_embed_footer_timestamp():
    embed = build_alert_embed(GAME, [SPREAD_MOVEMENT])
    assert "01:30 UTC" in embed.footer.text


def test_build_alert_embed_game_time():
    embed = build_alert_embed(GAME, [SPREAD_MOVEMENT])
    game_time_field = next(f for f in embed.fields if f.name == "Game Time")
    assert "02:00 UTC" in game_time_field.value


# ── Movement formatting ──────────────────────────────────────────────────────

def test_fmt_spread_movement():
    text = _fmt_movement(SPREAD_MOVEMENT)
    assert "Draftkings" in text
    assert "-4.5" in text
    assert "-6.5" in text
    assert "-2.0 pts" in text


def test_fmt_total_movement():
    text = _fmt_movement(TOTAL_MOVEMENT)
    assert "Fanduel" in text
    assert "224.5" in text
    assert "228.0" in text
    assert "+3.5 pts" in text


def test_fmt_moneyline_movement():
    text = _fmt_movement(ML_MOVEMENT)
    assert "Draftkings" in text
    assert "-150" in text
    assert "-180" in text


def test_direction_arrow_up():
    assert _direction_arrow(2.0) == "\U0001f4c8"


def test_direction_arrow_down():
    assert _direction_arrow(-2.0) == "\U0001f4c9"


# ── Grouping by game_id ──────────────────────────────────────────────────────

def test_grouping_by_game_id():
    """Verify that movements group correctly by game_id."""
    other_game_mov = LineMovement(
        movement_id=4,
        game_id="nba-20260421-gsw-phx",
        source="draftkings",
        market="spread",
        prev_value=-2.0,
        curr_value=-4.0,
        delta=-2.0,
        detected_at_utc_iso="2026-04-21T01:30:00Z",
    )
    all_movements = [SPREAD_MOVEMENT, TOTAL_MOVEMENT, other_game_mov]
    by_game: dict[str, list[LineMovement]] = defaultdict(list)
    for mov in all_movements:
        by_game[mov.game_id].append(mov)

    assert len(by_game) == 2
    assert len(by_game["nba-20260421-bos-lal"]) == 2
    assert len(by_game["nba-20260421-gsw-phx"]) == 1


# ── Thresholds ────────────────────────────────────────────────────────────────

def test_default_thresholds():
    t = LineAlertThresholds()
    assert t.spread_points == 1.5
    assert t.total_points == 3.0
    assert t.moneyline_cents == 20


# ── Movement detection ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_detect_movements_spread_above_threshold():
    """Spread moving 2 pts should trigger (threshold 1.5)."""
    from shared.models import OddsSnapshot
    snaps = [
        OddsSnapshot(
            snapshot_id="s1", game_id="g1", kind="poll", source="draftkings",
            captured_at_utc_iso="2026-04-21T00:00:00Z",
            payload={"spread": -4.5, "spread_odds": -110, "ml_home": -150, "ml_away": 130, "total": 224.5},
        ),
        OddsSnapshot(
            snapshot_id="s2", game_id="g1", kind="poll", source="draftkings",
            captured_at_utc_iso="2026-04-21T00:15:00Z",
            payload={"spread": -6.5, "spread_odds": -110, "ml_home": -155, "ml_away": 135, "total": 224.5},
        ),
    ]
    with patch("bot.cogs.alerts.queries.get_snapshots_for_game_since", new_callable=AsyncMock, return_value=snaps):
        movements = await detect_movements_for_game("g1")

    spread_movs = [m for m in movements if m.market == "spread"]
    assert len(spread_movs) == 1
    assert spread_movs[0].delta == -2.0


@pytest.mark.asyncio
async def test_detect_movements_below_threshold():
    """Spread moving 1 pt should NOT trigger (threshold 1.5)."""
    from shared.models import OddsSnapshot
    snaps = [
        OddsSnapshot(
            snapshot_id="s1", game_id="g1", kind="poll", source="draftkings",
            captured_at_utc_iso="2026-04-21T00:00:00Z",
            payload={"spread": -4.5, "ml_home": -150, "ml_away": 130, "total": 224.5},
        ),
        OddsSnapshot(
            snapshot_id="s2", game_id="g1", kind="poll", source="draftkings",
            captured_at_utc_iso="2026-04-21T00:15:00Z",
            payload={"spread": -5.5, "ml_home": -155, "ml_away": 135, "total": 224.5},
        ),
    ]
    with patch("bot.cogs.alerts.queries.get_snapshots_for_game_since", new_callable=AsyncMock, return_value=snaps):
        movements = await detect_movements_for_game("g1")

    spread_movs = [m for m in movements if m.market == "spread"]
    assert len(spread_movs) == 0


@pytest.mark.asyncio
async def test_detect_movements_moneyline():
    """ML moving 30 cents should trigger (threshold 20)."""
    from shared.models import OddsSnapshot
    snaps = [
        OddsSnapshot(
            snapshot_id="s1", game_id="g1", kind="poll", source="draftkings",
            captured_at_utc_iso="2026-04-21T00:00:00Z",
            payload={"spread": -4.5, "ml_home": -150, "ml_away": 130, "total": 224.5},
        ),
        OddsSnapshot(
            snapshot_id="s2", game_id="g1", kind="poll", source="draftkings",
            captured_at_utc_iso="2026-04-21T00:15:00Z",
            payload={"spread": -4.5, "ml_home": -180, "ml_away": 155, "total": 224.5},
        ),
    ]
    with patch("bot.cogs.alerts.queries.get_snapshots_for_game_since", new_callable=AsyncMock, return_value=snaps):
        movements = await detect_movements_for_game("g1")

    ml_movs = [m for m in movements if m.market == "moneyline"]
    assert len(ml_movs) == 1
    assert ml_movs[0].delta == -30


@pytest.mark.asyncio
async def test_detect_movements_no_snapshots():
    """No snapshots should yield no movements."""
    with patch("bot.cogs.alerts.queries.get_snapshots_for_game_since", new_callable=AsyncMock, return_value=[]):
        movements = await detect_movements_for_game("g1")
    assert movements == []


# ── Cog behavior ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_pending_alerts_marks_notified():
    """After posting an embed, movements should be marked notified."""
    mock_channel = AsyncMock()

    with (
        patch("bot.cogs.alerts.queries.get_unnotified_line_movements", new_callable=AsyncMock, return_value=[SPREAD_MOVEMENT]),
        patch("bot.cogs.alerts.queries.get_game_by_id", new_callable=AsyncMock, return_value=GAME),
        patch("bot.cogs.alerts.queries.mark_movement_notified", new_callable=AsyncMock) as mock_mark,
    ):
        await post_pending_alerts(mock_channel)

        mock_mark.assert_called_once_with(1)
        mock_channel.send.assert_called_once()


@pytest.mark.asyncio
async def test_post_pending_alerts_no_movements():
    """When there are no unnotified movements, nothing is sent."""
    mock_channel = AsyncMock()

    with patch("bot.cogs.alerts.queries.get_unnotified_line_movements", new_callable=AsyncMock, return_value=[]):
        await post_pending_alerts(mock_channel)

    mock_channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_post_pending_alerts_handles_missing_game():
    """If a game_id doesn't resolve, movements should still be marked notified."""
    mock_channel = AsyncMock()

    with (
        patch("bot.cogs.alerts.queries.get_unnotified_line_movements", new_callable=AsyncMock, return_value=[SPREAD_MOVEMENT]),
        patch("bot.cogs.alerts.queries.get_game_by_id", new_callable=AsyncMock, return_value=None),
        patch("bot.cogs.alerts.queries.mark_movement_notified", new_callable=AsyncMock) as mock_mark,
    ):
        await post_pending_alerts(mock_channel)

        # Should mark notified even without a valid game
        mock_mark.assert_called_once_with(1)
        # Should NOT send an embed
        mock_channel.send.assert_not_called()
