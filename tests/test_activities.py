"""
Unit tests for activity helper logic.
These test pure functions with no API calls or DB access.
"""
import pytest
from temporal.activities import _extract_payload, _kalshi_ml_from_markets, _kalshi_mid
from shared.models import TEAM_ABBR
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


# ── _kalshi_mid ────────────────────────────────────────────────────────────────

def test_kalshi_mid_from_bid_ask():
    m = {"yes_bid_dollars": 0.60, "yes_ask_dollars": 0.64}
    assert _kalshi_mid(m) == pytest.approx(0.62)


def test_kalshi_mid_falls_back_to_last_price():
    m = {"yes_bid_dollars": 0, "yes_ask_dollars": 0, "last_price_dollars": 0.55}
    assert _kalshi_mid(m) == pytest.approx(0.55)


def test_kalshi_mid_returns_none_when_zero():
    m = {"yes_bid_dollars": 0, "yes_ask_dollars": 0}
    assert _kalshi_mid(m) is None


def test_kalshi_mid_rejects_out_of_range():
    assert _kalshi_mid({"yes_bid_dollars": 1.0, "yes_ask_dollars": 1.0}) is None
    assert _kalshi_mid({"yes_bid_dollars": 0.0, "yes_ask_dollars": 0.0}) is None


# ── _kalshi_ml_from_markets ────────────────────────────────────────────────────

_KALSHI_MARKETS = [
    # DEN @ PHX  — home=PHX (abbr PHX), away=DEN
    {
        "event_ticker": "KXNBAGAME-26MAR24DENPHX",
        "ticker": "KXNBAGAME-26MAR24DENPHX-PHX",
        "yes_bid_dollars": 0.36,
        "yes_ask_dollars": 0.40,
    },
    {
        "event_ticker": "KXNBAGAME-26MAR24DENPHX",
        "ticker": "KXNBAGAME-26MAR24DENPHX-DEN",
        "yes_bid_dollars": 0.62,
        "yes_ask_dollars": 0.66,
    },
]


def test_kalshi_ml_from_markets_basic():
    result = _kalshi_ml_from_markets(_KALSHI_MARKETS, home_abbr="PHX", away_abbr="DEN")
    assert result is not None
    ml_home, ml_away = result
    # PHX mid = 0.38 → underdog → positive American odds
    assert ml_home > 0
    # DEN mid = 0.64 → favourite → negative American odds
    assert ml_away < 0


def test_kalshi_ml_from_markets_home_is_favourite():
    """Flip so home is the heavy favourite."""
    flipped = [
        {**_KALSHI_MARKETS[0], "yes_bid_dollars": 0.72, "yes_ask_dollars": 0.76},  # PHX = favourite
        {**_KALSHI_MARKETS[1], "yes_bid_dollars": 0.26, "yes_ask_dollars": 0.30},  # DEN = underdog
    ]
    result = _kalshi_ml_from_markets(flipped, home_abbr="PHX", away_abbr="DEN")
    assert result is not None
    ml_home, ml_away = result
    assert ml_home < 0   # favourite
    assert ml_away > 0   # underdog


def test_kalshi_ml_from_markets_returns_none_when_missing_side():
    only_home = [_KALSHI_MARKETS[0]]  # only PHX market
    assert _kalshi_ml_from_markets(only_home, home_abbr="PHX", away_abbr="DEN") is None


def test_kalshi_ml_from_markets_returns_none_on_empty():
    assert _kalshi_ml_from_markets([], home_abbr="PHX", away_abbr="DEN") is None


# ── TEAM_ABBR sanity ───────────────────────────────────────────────────────────

def test_team_abbr_has_all_30_teams():
    assert len(TEAM_ABBR) == 30


def test_team_abbr_known_entries():
    assert TEAM_ABBR["Los Angeles Lakers"] == "LAL"
    assert TEAM_ABBR["Los Angeles Clippers"] == "LAC"
    assert TEAM_ABBR["Oklahoma City Thunder"] == "OKC"
    assert TEAM_ABBR["Golden State Warriors"] == "GSW"


def test_team_abbr_all_values_are_3_chars():
    bad = {k: v for k, v in TEAM_ABBR.items() if len(v) != 3}
    assert bad == {}, f"Non-3-char abbreviations: {bad}"


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
