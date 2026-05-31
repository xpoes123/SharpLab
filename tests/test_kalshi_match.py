"""Kalshi matching: team-code overrides + market-suffix event matching.

Regression for the ~4/9 match rate: MLB codes differ from The Odds API's
(TB vs TBR, AZ vs ARI, ATH vs OAK, …) and the old positional ticker slice
broke on variable-length codes.
"""
from __future__ import annotations

from shared.models import get_kalshi_code, get_team_abbr
from temporal.activities import _kalshi_ml_from_markets


def test_kalshi_code_overrides_mlb():
    assert get_kalshi_code("Tampa Bay Rays", "mlb") == "TB"
    assert get_kalshi_code("Arizona Diamondbacks", "mlb") == "AZ"
    assert get_kalshi_code("Oakland Athletics", "mlb") == "ATH"
    assert get_kalshi_code("San Diego Padres", "mlb") == "SD"
    assert get_kalshi_code("San Francisco Giants", "mlb") == "SF"
    assert get_kalshi_code("Kansas City Royals", "mlb") == "KC"
    assert get_kalshi_code("Washington Nationals", "mlb") == "WSH"


def test_kalshi_code_passthrough_when_no_override():
    # Teams whose Kalshi code matches the standard abbreviation are unchanged.
    assert get_kalshi_code("Boston Red Sox", "mlb") == "BOS"
    assert get_kalshi_code("Detroit Tigers", "mlb") == "DET"
    assert get_kalshi_code("Los Angeles Lakers", "nba") == get_team_abbr("Los Angeles Lakers", "nba")


def test_kalshi_code_unknown_team():
    assert get_kalshi_code("Nonexistent Team", "mlb") is None


def _mkt(event: str, team: str, bid: float, ask: float) -> dict:
    return {
        "event_ticker": event,
        "ticker": f"{event}-{team}",
        "yes_bid_dollars": bid,
        "yes_ask_dollars": ask,
    }


def test_ml_from_markets_uses_market_suffix():
    # DET @ TB — variable-length codes that broke the old positional slice.
    event = "KXMLBGAME-26JUN031310DETTB"
    markets = [_mkt(event, "TB", 0.55, 0.57), _mkt(event, "DET", 0.43, 0.45)]
    res = _kalshi_ml_from_markets(markets, home_abbr="TB", away_abbr="DET")
    assert res is not None
    ml_home, ml_away = res
    assert ml_home < 0  # TB favored (~0.56) → negative American
    assert ml_away > 0  # DET dog (~0.44) → positive American


def test_ml_from_markets_missing_side_returns_none():
    event = "KXMLBGAME-26JUN031310DETTB"
    markets = [_mkt(event, "TB", 0.55, 0.57)]  # only one side
    assert _kalshi_ml_from_markets(markets, home_abbr="TB", away_abbr="DET") is None


def test_event_teamset_matching():
    """Mirror the batch matcher: an event's team set comes from market suffixes,
    and a game matches by the unordered pair of Kalshi codes."""
    markets = [
        _mkt("KXMLBGAME-26JUN031310DETTB", "TB", 0.55, 0.57),
        _mkt("KXMLBGAME-26JUN031310DETTB", "DET", 0.43, 0.45),
        _mkt("KXMLBGAME-26JUN031305MIAWSH", "WSH", 0.60, 0.62),
        _mkt("KXMLBGAME-26JUN031305MIAWSH", "MIA", 0.38, 0.40),
    ]
    event_teams: dict[str, set[str]] = {}
    for m in markets:
        event_teams.setdefault(m["event_ticker"], set()).add(m["ticker"].split("-")[-1])
    teamset_to_event = {frozenset(t): e for e, t in event_teams.items() if len(t) == 2}

    # Marlins @ Nationals → the MIAWSH event (codes WSH/MIA via overrides).
    h, a = get_kalshi_code("Washington Nationals", "mlb"), get_kalshi_code("Miami Marlins", "mlb")
    assert teamset_to_event.get(frozenset({h, a})) == "KXMLBGAME-26JUN031305MIAWSH"
    # Tigers @ Rays → the DETTB event.
    h, a = get_kalshi_code("Tampa Bay Rays", "mlb"), get_kalshi_code("Detroit Tigers", "mlb")
    assert teamset_to_event.get(frozenset({h, a})) == "KXMLBGAME-26JUN031310DETTB"
