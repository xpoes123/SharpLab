"""
Unit tests for activity helper logic.
These test pure functions with no API calls or DB access.
"""
import pytest
from temporal.activities import _extract_payload
from shared.odds_utils import prob_to_american, american_to_prob, american_to_decimal


# ── _extract_payload ───────────────────────────────────────────────────────────

_BOOKMAKER = {
    "key": "draftkings",
    "markets": [
        {
            "key": "spreads",
            "outcomes": [
                {"name": "Boston Celtics", "price": -110, "point": -4.5},
                {"name": "Los Angeles Lakers", "price": -110, "point": 4.5},
            ],
        },
        {
            "key": "h2h",
            "outcomes": [
                {"name": "Boston Celtics", "price": -200},
                {"name": "Los Angeles Lakers", "price": +165},
            ],
        },
        {
            "key": "totals",
            "outcomes": [
                {"name": "Over", "price": -110, "point": 224.5},
                {"name": "Under", "price": -110, "point": 224.5},
            ],
        },
    ],
}


def test_extract_payload_spread():
    payload = _extract_payload(_BOOKMAKER, home_team="Boston Celtics")
    assert payload["spread"] == -4.5
    assert payload["spread_odds"] == -110
    assert payload["spread_away"] == 4.5


def test_extract_payload_moneyline():
    payload = _extract_payload(_BOOKMAKER, home_team="Boston Celtics")
    assert payload["ml_home"] == -200
    assert payload["ml_away"] == 165


def test_extract_payload_total():
    payload = _extract_payload(_BOOKMAKER, home_team="Boston Celtics")
    assert payload["total"] == 224.5
    assert payload["total_over_odds"] == -110
    assert payload["total_under_odds"] == -110


def test_extract_payload_empty_bookmaker():
    payload = _extract_payload({"key": "draftkings", "markets": []}, home_team="Celtics")
    assert payload == {}


# ── odds_utils ─────────────────────────────────────────────────────────────────

def test_prob_to_american_favorite():
    # 60% implied prob → -150
    assert prob_to_american(0.60) == -150


def test_prob_to_american_underdog():
    # 40% implied prob → +150
    assert prob_to_american(0.40) == 150


def test_american_to_prob_roundtrip():
    for odds in [-110, -200, +150, +300]:
        prob = american_to_prob(odds)
        recovered = prob_to_american(prob)
        # Allow ±1 rounding tolerance
        assert abs(recovered - odds) <= 1, f"Roundtrip failed for {odds}: got {recovered}"


def test_american_to_decimal():
    assert american_to_decimal(-110) == pytest.approx(1.909, abs=0.01)
    assert american_to_decimal(+150) == pytest.approx(2.5, abs=0.01)
