"""
Unit tests for activity helper logic.
These test pure functions with no API calls or DB access.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from temporal.activities import (
    _extract_payload,
    _fetch_espn_scores,
    _kalshi_ml_from_markets,
    _parse_espn_detail,
    _parse_espn_injuries_response,
    _resolve_bet,
)
from shared.models import Bet, TEAM_ABBR_NBA, TEAM_ABBR_MLB, get_team_abbr
from shared.odds_utils import (
    prob_to_american, american_to_prob, american_to_decimal,
    kalshi_exec_price, KALSHI_TAKER_FEE,
)


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
    assert payload["spread_away_odds"] == -110


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


# ── kalshi_exec_price (executable ask + taker fee — guards against phantom arbs) ──

def test_kalshi_exec_uses_ask_plus_fee():
    p = kalshi_exec_price({"yes_ask_dollars": 0.60})
    assert p == pytest.approx(0.60 + KALSHI_TAKER_FEE * 0.60 * 0.40)
    assert p > 0.60   # you pay MORE than the ask (the fee) — never the cheap fair-value mid


def test_kalshi_exec_falls_back_to_last_then_mid():
    assert kalshi_exec_price({"yes_ask_dollars": 0, "last_price_dollars": 0.55}) == \
        pytest.approx(0.55 + KALSHI_TAKER_FEE * 0.55 * 0.45)
    # no ask, no last → bid/ask mid
    assert kalshi_exec_price({"yes_bid_dollars": 0.60, "yes_ask_dollars": 0}) == \
        pytest.approx(0.30 + KALSHI_TAKER_FEE * 0.30 * 0.70)


def test_kalshi_exec_none_when_no_usable_price():
    assert kalshi_exec_price({}) is None
    assert kalshi_exec_price({"yes_ask_dollars": 0, "last_price_dollars": 0}) is None


def test_kalshi_exec_capped_at_099():
    assert kalshi_exec_price({"yes_ask_dollars": 0.99}) <= 0.99


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


def test_kalshi_ml_from_markets_none_when_a_side_has_no_ask():
    """Thin market: away side has only a stray bid, no resting ask. Must NOT
    fabricate a lopsided line (the 5%/95% MLB pre-game bug) — return None so
    consumers fall back to a real book."""
    thin = [
        {**_KALSHI_MARKETS[0], "yes_bid_dollars": 0.90, "yes_ask_dollars": 0.95},  # PHX parked high
        {"event_ticker": _KALSHI_MARKETS[1]["event_ticker"],
         "ticker": _KALSHI_MARKETS[1]["ticker"],
         "yes_bid_dollars": 0.10, "yes_ask_dollars": None},  # DEN bid only, no ask
    ]
    assert _kalshi_ml_from_markets(thin, home_abbr="PHX", away_abbr="DEN") is None


# ── TEAM_ABBR sanity — NBA ────────────────────────────────────────────────────

def test_team_abbr_nba_has_30_teams():
    assert len(TEAM_ABBR_NBA) == 30


def test_team_abbr_nba_known_entries():
    assert get_team_abbr("Los Angeles Lakers", "nba") == "LAL"
    assert get_team_abbr("Los Angeles Clippers", "nba") == "LAC"
    assert get_team_abbr("Oklahoma City Thunder", "nba") == "OKC"
    assert get_team_abbr("Golden State Warriors", "nba") == "GSW"


def test_team_abbr_nba_all_values_are_3_chars():
    bad = {k: v for k, v in TEAM_ABBR_NBA.items() if len(v) != 3}
    assert bad == {}, f"Non-3-char abbreviations: {bad}"


# ── TEAM_ABBR sanity — MLB ────────────────────────────────────────────────────

def test_team_abbr_mlb_has_30_teams():
    assert len(TEAM_ABBR_MLB) == 30


def test_team_abbr_mlb_known_entries():
    assert get_team_abbr("New York Yankees", "mlb") == "NYY"
    assert get_team_abbr("Los Angeles Dodgers", "mlb") == "LAD"
    assert get_team_abbr("Chicago Cubs", "mlb") == "CHC"
    assert get_team_abbr("Chicago White Sox", "mlb") == "CWS"


def test_team_abbr_mlb_all_values_are_3_chars():
    bad = {k: v for k, v in TEAM_ABBR_MLB.items() if len(v) != 3}
    assert bad == {}, f"Non-3-char abbreviations: {bad}"


def test_team_abbr_cross_sport():
    """NBA and MLB both have Atlanta teams with different abbrs."""
    assert get_team_abbr("Atlanta Hawks", "nba") == "ATL"
    assert get_team_abbr("Atlanta Braves", "mlb") == "ATL"
    # NBA team not found in MLB
    assert get_team_abbr("Atlanta Hawks", "mlb") is None


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


# ── ESPN injury parsing ─────────────────────────────────────────────────────────

_ESPN_RESPONSE = {
    "injuries": [
        {
            "displayName": "Atlanta Hawks",
            "injuries": [
                {
                    "athlete": {
                        "displayName": "Trae Young",
                        "links": [{"href": "https://www.espn.com/nba/player/id/3136193/trae-young"}],
                    },
                    "status": "Questionable",
                    "details": {"type": "Ankle", "side": "Left", "detail": "Sprain"},
                },
                {
                    "athlete": {
                        "displayName": "Bogdan Bogdanovic",
                        "links": [{"href": "https://www.espn.com/nba/player/id/9999999/bogdan-bogdanovic"}],
                    },
                    "status": "Out",
                    "details": {"type": "Knee"},
                },
            ],
        },
        {
            "displayName": "Boston Celtics",
            "injuries": [],
        },
    ]
}


def test_parse_espn_injuries_response_basic():
    result = _parse_espn_injuries_response(_ESPN_RESPONSE)
    assert len(result) == 2
    r_id, r_name, r_team, r_status, r_detail = result[0]
    assert r_id == "3136193"
    assert r_name == "Trae Young"
    assert r_team == "Atlanta Hawks"
    assert r_status == "Questionable"
    assert r_detail == "Ankle - Left - Sprain"


def test_parse_espn_injuries_response_partial_detail():
    result = _parse_espn_injuries_response(_ESPN_RESPONSE)
    _, _, _, _, detail = result[1]
    assert detail == "Knee"


def test_parse_espn_injuries_response_empty_team():
    data = {"injuries": [{"displayName": "Boston Celtics", "injuries": []}]}
    assert _parse_espn_injuries_response(data) == []


def test_parse_espn_injuries_response_skips_missing_athlete_id():
    data = {
        "injuries": [{
            "displayName": "Dallas Mavericks",
            "injuries": [{"athlete": {"displayName": "Luka Doncic"}, "status": "Out"}],
        }]
    }
    assert _parse_espn_injuries_response(data) == []


def test_parse_espn_detail_full():
    assert _parse_espn_detail({"type": "Ankle", "side": "Left", "detail": "Sprain"}) == "Ankle - Left - Sprain"


def test_parse_espn_detail_partial():
    assert _parse_espn_detail({"type": "Knee"}) == "Knee"


def test_parse_espn_detail_none():
    assert _parse_espn_detail(None) is None
    assert _parse_espn_detail({}) is None


# ── _resolve_bet ───────────────────────────────────────────────────────────────

def _bet(**kwargs) -> Bet:
    defaults = dict(
        game_id="g1", placed_at="2026-01-01T00:00:00",
        discord_user="123", book="draftkings",
        market="moneyline", side="celtics", odds=-200, units=1.0,
    )
    return Bet(**{**defaults, **kwargs})

HOME = "Boston Celtics"
AWAY = "Los Angeles Lakers"

# Moneyline
def test_resolve_ml_home_win():
    assert _resolve_bet(_bet(market="moneyline", side="celtics"), HOME, AWAY, 110, 100) == "won"

def test_resolve_ml_home_loss():
    assert _resolve_bet(_bet(market="moneyline", side="celtics"), HOME, AWAY, 100, 110) == "lost"

def test_resolve_ml_away_win():
    assert _resolve_bet(_bet(market="moneyline", side="lakers"), HOME, AWAY, 100, 110) == "won"

def test_resolve_ml_yes_means_home():
    assert _resolve_bet(_bet(market="kalshi", side="yes"), HOME, AWAY, 110, 100) == "won"

def test_resolve_ml_no_means_away():
    assert _resolve_bet(_bet(market="kalshi", side="no"), HOME, AWAY, 100, 110) == "won"

# Spread
def test_resolve_spread_home_covers():
    assert _resolve_bet(_bet(market="spread", side="celtics", line=-4.5), HOME, AWAY, 110, 100) == "won"

def test_resolve_spread_home_fails_to_cover():
    assert _resolve_bet(_bet(market="spread", side="celtics", line=-4.5), HOME, AWAY, 103, 100) == "lost"

def test_resolve_spread_push():
    assert _resolve_bet(_bet(market="spread", side="celtics", line=-3.0), HOME, AWAY, 103, 100) == "push"

def test_resolve_spread_away_covers():
    assert _resolve_bet(_bet(market="spread", side="lakers", line=4.5), HOME, AWAY, 103, 100) == "won"

def test_resolve_spread_no_line_voids():
    assert _resolve_bet(_bet(market="spread", side="celtics", line=None), HOME, AWAY, 110, 100) == "void"

# Total
def test_resolve_total_over_hits():
    assert _resolve_bet(_bet(market="total", side="over", line=220.5), HOME, AWAY, 115, 110) == "won"

def test_resolve_total_over_misses():
    assert _resolve_bet(_bet(market="total", side="over", line=220.5), HOME, AWAY, 100, 110) == "lost"

def test_resolve_total_under_hits():
    assert _resolve_bet(_bet(market="total", side="under", line=220.5), HOME, AWAY, 100, 110) == "won"

def test_resolve_total_push():
    assert _resolve_bet(_bet(market="total", side="over", line=210.0), HOME, AWAY, 100, 110) == "push"

def test_resolve_total_no_line_voids():
    assert _resolve_bet(_bet(market="total", side="over", line=None), HOME, AWAY, 110, 100) == "void"

# Unknown side → void
def test_resolve_unknown_side_voids():
    assert _resolve_bet(_bet(market="moneyline", side="unknown_team"), HOME, AWAY, 110, 100) == "void"

# ── Shared-suffix team resolution (White Sox / Red Sox) ─────────────────────
# Regression tests for the `_is_home`/`_is_away` inverted-suffix bug.
# Before the fix, `home_l.split()[-1] in s` meant "sox" in "red sox" was True,
# so a bet side="red sox" would incorrectly match the White Sox home team.

WS_HOME = "Chicago White Sox"
RS_AWAY = "Boston Red Sox"


def test_resolve_shared_suffix_away_wins_ml():
    """Red Sox (away), side='red sox', Red Sox wins — must be 'won' not 'lost'."""
    assert (
        _resolve_bet(_bet(market="moneyline", side="red sox"), WS_HOME, RS_AWAY, 3, 5)
        == "won"
    )


def test_resolve_shared_suffix_away_loses_ml():
    """Red Sox (away), side='red sox', Red Sox loses — must be 'lost'."""
    assert (
        _resolve_bet(_bet(market="moneyline", side="red sox"), WS_HOME, RS_AWAY, 5, 3)
        == "lost"
    )


def test_resolve_shared_suffix_home_wins_ml():
    """White Sox (home), side='white sox', White Sox wins — must be 'won'."""
    assert (
        _resolve_bet(_bet(market="moneyline", side="white sox"), WS_HOME, RS_AWAY, 5, 3)
        == "won"
    )


def test_resolve_shared_suffix_spread_away_covers():
    """Red Sox +1.5 (away), Red Sox wins 5-3: margin=(5-3)+1.5=3.5 → won."""
    assert (
        _resolve_bet(_bet(market="spread", side="red sox", line=1.5), WS_HOME, RS_AWAY, 3, 5)
        == "won"
    )


def test_resolve_shared_suffix_spread_home_covers():
    """White Sox -1.5 (home), White Sox wins 5-3: margin=(5-3)+(-1.5)=0.5 → won."""
    assert (
        _resolve_bet(_bet(market="spread", side="white sox", line=-1.5), WS_HOME, RS_AWAY, 5, 3)
        == "won"
    )


def test_resolve_shared_suffix_bare_suffix_voids():
    """Bare 'sox' is ambiguous between White Sox and Red Sox — must void."""
    assert (
        _resolve_bet(_bet(market="moneyline", side="sox"), WS_HOME, RS_AWAY, 5, 3)
        == "void"
    )


# ── MLB-flavored bet resolution ─────────────────────────────────────────────

MLB_HOME = "New York Yankees"
MLB_AWAY = "Boston Red Sox"

def test_resolve_mlb_moneyline_home_win():
    assert _resolve_bet(_bet(market="moneyline", side="yankees"), MLB_HOME, MLB_AWAY, 5, 3) == "won"

def test_resolve_mlb_moneyline_away_win():
    assert _resolve_bet(_bet(market="moneyline", side="red sox"), MLB_HOME, MLB_AWAY, 3, 5) == "won"

def test_resolve_mlb_run_line_home_covers():
    # Yankees -1.5, win by 3 → margin = (5-3) + (-1.5) = 0.5 → won
    assert _resolve_bet(_bet(market="spread", side="yankees", line=-1.5), MLB_HOME, MLB_AWAY, 5, 2) == "won"

def test_resolve_mlb_run_line_home_fails():
    # Yankees -1.5, win by 1 → margin = (4-3) + (-1.5) = -0.5 → lost
    assert _resolve_bet(_bet(market="spread", side="yankees", line=-1.5), MLB_HOME, MLB_AWAY, 4, 3) == "lost"

def test_resolve_mlb_total_over():
    # O/U 8.5, final 5-4 = 9 → over hits
    assert _resolve_bet(_bet(market="total", side="over", line=8.5), MLB_HOME, MLB_AWAY, 5, 4) == "won"

def test_resolve_mlb_total_under():
    # O/U 8.5, final 3-2 = 5 → under hits
    assert _resolve_bet(_bet(market="total", side="under", line=8.5), MLB_HOME, MLB_AWAY, 3, 2) == "won"


# ── ESPN score parsing guard ───────────────────────────────────────────────────

def test_espn_score_none_guard():
    """Regression: ESPN sometimes returns None for score. int(None or '0') must not crash."""
    # Simulates the parse expression used in _fetch_espn_scores for each competitor
    competitor = {"score": None, "homeAway": "home", "team": {"displayName": "Los Angeles Lakers"}}
    score = int(competitor.get("score", "0") or "0")
    assert score == 0


def test_espn_score_empty_string_guard():
    """ESPN may return empty string for in-progress scores."""
    competitor = {"score": "", "homeAway": "away", "team": {"displayName": "Boston Celtics"}}
    score = int(competitor.get("score", "0") or "0")
    assert score == 0


def test_espn_score_normal():
    """Normal case still works after guard."""
    competitor = {"score": "112", "homeAway": "home", "team": {"displayName": "Miami Heat"}}
    score = int(competitor.get("score", "0") or "0")
    assert score == 112


# ── _fetch_espn_scores integration ────────────────────────────────────────────

def _espn_event(home_name: str, away_name: str,
                home_score: str | None, away_score: str | None,
                status: str = "STATUS_FINAL") -> dict:
    return {
        "status": {"type": {"name": status}},
        "competitions": [{
            "competitors": [
                {
                    "homeAway": "home",
                    "team": {"displayName": home_name},
                    "score": home_score,
                },
                {
                    "homeAway": "away",
                    "team": {"displayName": away_name},
                    "score": away_score,
                },
            ]
        }]
    }


def _mock_espn_client(data: dict):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = data
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.mark.asyncio
async def test_fetch_espn_scores_normal_game():
    """Normal final game is included in results."""
    espn_data = {"events": [_espn_event("New York Yankees", "Boston Red Sox", "5", "3")]}
    with patch("httpx.AsyncClient", return_value=_mock_espn_client(espn_data)):
        results = await _fetch_espn_scores(["2026-04-15"], "mlb")
    assert len(results) == 1
    assert results[0].home_last == "yankees"
    assert results[0].away_last == "sox"
    assert results[0].home_score == 5
    assert results[0].away_score == 3


@pytest.mark.asyncio
async def test_fetch_espn_scores_zero_zero_skipped():
    """STATUS_FINAL with 0-0 scores (postponed/unpopulated) must be skipped.

    Regression: without the zero-score guard, all open bets on the game
    would be incorrectly resolved as 'lost' for both sides.
    """
    espn_data = {"events": [_espn_event("New York Yankees", "Boston Red Sox", "0", "0")]}
    with patch("httpx.AsyncClient", return_value=_mock_espn_client(espn_data)):
        results = await _fetch_espn_scores(["2026-04-15"], "mlb")
    assert results == [], "0-0 STATUS_FINAL game must not produce a GameResult"


@pytest.mark.asyncio
async def test_fetch_espn_scores_none_score_skipped():
    """ESPN returning None for scores must not crash and must skip the game.

    Regression: without try/except, int(None or '0') works, but
    int('N/A' or '0') raises ValueError, crashing the activity.
    """
    espn_data = {"events": [_espn_event("New York Yankees", "Boston Red Sox", None, None)]}
    with patch("httpx.AsyncClient", return_value=_mock_espn_client(espn_data)):
        results = await _fetch_espn_scores(["2026-04-15"], "mlb")
    assert results == [], "None scores yield 0-0, must be skipped by zero-score guard"


@pytest.mark.asyncio
async def test_fetch_espn_scores_nonnumeric_score_skipped():
    """Non-numeric score string (e.g. 'N/A') must not raise and must skip the game."""
    espn_data = {"events": [_espn_event("New York Yankees", "Boston Red Sox", "N/A", "N/A")]}
    with patch("httpx.AsyncClient", return_value=_mock_espn_client(espn_data)):
        results = await _fetch_espn_scores(["2026-04-15"], "mlb")
    assert results == [], "Non-numeric scores must not raise ValueError"


@pytest.mark.asyncio
async def test_fetch_espn_scores_in_progress_skipped():
    """In-progress games (non-FINAL status) must not be included."""
    espn_data = {"events": [_espn_event("New York Yankees", "Boston Red Sox", "3", "2",
                                        status="STATUS_IN_PROGRESS")]}
    with patch("httpx.AsyncClient", return_value=_mock_espn_client(espn_data)):
        results = await _fetch_espn_scores(["2026-04-15"], "mlb")
    assert results == []
