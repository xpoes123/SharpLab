import pytest

from scripts.espn_close import payload_from_pick


def _pick(spread, ml_home, ml_away):
    return {
        "spread": spread, "overUnder": 39.5, "overOdds": -110.0, "underOdds": -110.0,
        "homeTeamOdds": {"moneyLine": ml_home, "spreadOdds": -102.0},
        "awayTeamOdds": {"moneyLine": ml_away, "spreadOdds": -119.0},
    }


def test_maps_home_perspective_payload():
    assert payload_from_pick(_pick(10.0, 400, -535)) == {
        "spread": 10.0, "spread_odds": -102, "ml_home": 400, "ml_away": -535,
        "total": 39.5, "total_over_odds": -110, "total_under_odds": -110,
    }


def test_rejects_spread_sign_that_contradicts_moneyline():
    with pytest.raises(ValueError):
        payload_from_pick(_pick(-10.0, 400, -535))
