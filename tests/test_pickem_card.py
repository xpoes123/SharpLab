"""Unit tests for the /pickem today card: payout math + per-game formatting."""
from __future__ import annotations

import pytest

from shared.pickem_scoring import pick_prob, pick_units, potential_units
from bot.cogs.pickem import (
    _build_score_map,
    _format_game,
    _pair_key,
    _pickem_win_coins,
    _today_board,
)


def test_pickem_win_coins():
    assert _pickem_win_coins(2.0) == 100      # 2 units won × 50 coins/unit
    assert _pickem_win_coins(3.4) == 170
    assert _pickem_win_coins(0) == 0
    assert _pickem_win_coins(-1) == 0         # a loss never pays

FUTURE = "2999-01-01T00:00:00+00:00"
PAST = "2000-01-01T00:00:00+00:00"
NOW = "2026-06-07T20:00:00+00:00"


# ── payout math ───────────────────────────────────────────────────────────────

def test_potential_and_pick_units():
    assert potential_units(1, 0.5) == pytest.approx(1.0)
    assert potential_units(2, 0.25) == pytest.approx(6.0)  # 2 * (4 - 1)
    assert pick_units(3, 0.5, won=True) == pytest.approx(3.0)
    assert pick_units(3, 0.5, won=False) == pytest.approx(-3.0)


def test_payout_handles_missing_or_zero_prob():
    # None / 0 prob fall back to even money rather than dividing by zero.
    assert potential_units(1, None) == pytest.approx(1.0)
    assert potential_units(1, 0.0) == pytest.approx(1.0)


def test_pick_prob_picks_the_right_side():
    assert pick_prob("home", 0.6, 0.4) == 0.6
    assert pick_prob("away", 0.6, 0.4) == 0.4


# ── score map ─────────────────────────────────────────────────────────────────

def _fetched(home, away, hs, as_, status, period):
    return {
        "home_team": {"full_name": home, "abbreviation": home[:3].upper()},
        "visitor_team": {"full_name": away, "abbreviation": away[:3].upper()},
        "home_team_score": hs, "visitor_team_score": as_,
        "status": status, "period": period,
    }


def test_build_score_map_states_and_lookup():
    fetched = [
        _fetched("New York Yankees", "Boston Red Sox", 7, 4, "Final", 9),
        _fetched("Colorado Rockies", "Milwaukee Brewers", 2, 1, "Top 5th", 1),
        _fetched("Chicago Cubs", "San Francisco Giants", 0, 0, "2026-06-07T23:00Z", 0),
    ]
    m = _build_score_map(fetched)
    fin = m[_pair_key("New York Yankees", "Boston Red Sox")]
    assert (fin["state"], fin["hs"], fin["as_"]) == ("final", 7, 4)
    assert m[_pair_key("Milwaukee Brewers", "Colorado Rockies")]["state"] == "live"
    assert m[_pair_key("Chicago Cubs", "San Francisco Giants")]["state"] == "pre"


# ── per-game formatting ─────────────────────────────────────────────────────────

def _game(**kw):
    base = {
        "sport": "mlb", "message_id": "m1", "game_id": "g1",
        "away_team": "Boston Red Sox", "home_team": "New York Yankees",
        "home_prob": 0.5, "away_prob": 0.5, "start_time": PAST,
        "resolved": 0, "winner": None,
    }
    base.update(kw)
    return base


def test_resolved_win_pays_out():
    g = _game(resolved=1, winner="home")
    mine = {"pick": "home", "stake": 2, "correct": 1}
    score = {"hs": 7, "as_": 4, "state": "final", "detail": "Final"}
    e = _format_game(g, mine, score, NOW)
    assert e["settled_delta"] == pytest.approx(2.0)  # even money, stake 2
    assert "✅" in e["line"] and "+2.0u" in e["line"]


def test_resolved_loss_costs_stake():
    g = _game(resolved=1, winner="away")
    mine = {"pick": "home", "stake": 3, "correct": 0}
    score = {"hs": 1, "as_": 5, "state": "final", "detail": "Final"}
    e = _format_game(g, mine, score, NOW)
    assert e["settled_delta"] == pytest.approx(-3.0)
    assert "❌" in e["line"]


def test_live_game_with_pick_shows_lean_and_exposure():
    g = _game(start_time=PAST)
    mine = {"pick": "away", "stake": 2, "correct": None}
    score = {"hs": 1, "as_": 4, "state": "live", "detail": "Top 6th"}  # away ahead
    e = _format_game(g, mine, score, NOW)
    assert e["settled_delta"] is None
    assert e["live_risk"] == pytest.approx(2.0)
    assert e["live_pot"] == pytest.approx(2.0)
    assert "🔵" in e["line"] and "🟢" in e["line"]  # picked away, away leading


def test_upcoming_pick_shows_tip_time_not_live():
    g = _game(start_time=FUTURE)
    mine = {"pick": "home", "stake": 1, "correct": None}
    e = _format_game(g, mine, None, NOW)
    assert e["settled_delta"] is None
    assert e["live_risk"] == pytest.approx(1.0)
    assert "🕒" in e["line"] and "🔵" not in e["line"]


def test_unpicked_resolved_game_names_the_winner():
    g = _game(resolved=1, winner="home")
    score = {"hs": 7, "as_": 4, "state": "final", "detail": "Final"}
    e = _format_game(g, None, score, NOW)
    assert e["settled_delta"] is None
    assert "won" in e["line"]


# ── daily board ─────────────────────────────────────────────────────────────────

def test_today_board_aggregates_only_resolved():
    picks = [
        {"discord_user": "u1", "pick": "home", "stake": 2, "correct": 1,
         "home_prob": 0.5, "away_prob": 0.5, "resolved": 1},
        {"discord_user": "u1", "pick": "away", "stake": 1, "correct": 0,
         "home_prob": 0.5, "away_prob": 0.5, "resolved": 1},
        {"discord_user": "u2", "pick": "home", "stake": 3, "correct": None,
         "home_prob": 0.5, "away_prob": 0.5, "resolved": 0},  # unresolved → ignored
    ]
    board = _today_board(picks)
    assert board["u1"] == {"net": pytest.approx(1.0), "w": 1, "l": 1}  # +2 then -1
    assert "u2" not in board
