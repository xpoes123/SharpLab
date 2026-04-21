"""Unit tests for paper trading — payout math and resolution logic."""
import pytest
from shared.odds_utils import american_to_decimal
from bot.cogs.trading import _compute_payout, _resolve_paper_bet, _parse_pick


# ── Payout calculation ────────────────────────────────────────────────────────


class TestComputePayout:
    def test_minus_110(self):
        # 100 coins at -110 → decimal 1.909 → payout 191
        assert _compute_payout(100, -110) == 191

    def test_plus_150(self):
        # 100 coins at +150 → decimal 2.5 → payout 250
        assert _compute_payout(100, +150) == 250

    def test_minus_200(self):
        # 100 coins at -200 → decimal 1.5 → payout 150
        assert _compute_payout(100, -200) == 150

    def test_even_odds(self):
        # 100 coins at +100 → decimal 2.0 → payout 200
        assert _compute_payout(100, +100) == 200

    def test_heavy_favorite(self):
        # 50 coins at -500 → decimal 1.2 → payout 60
        assert _compute_payout(50, -500) == 60

    def test_big_underdog(self):
        # 10 coins at +1000 → decimal 11.0 → payout 110
        assert _compute_payout(10, +1000) == 110

    def test_rounding(self):
        # 75 coins at -110 → decimal 1.909... → 75 * 1.909 = 143.18 → 143
        assert _compute_payout(75, -110) == 143


# ── Parse pick helper ─────────────────────────────────────────────────────────


class TestParsePick:
    def test_bare_team(self):
        assert _parse_pick("Boston Celtics") == ("Boston Celtics", None)

    def test_team_with_line(self):
        assert _parse_pick("Boston Celtics:-4.5") == ("Boston Celtics", -4.5)

    def test_over_with_line(self):
        assert _parse_pick("over:224.5") == ("over", 224.5)

    def test_under_with_line(self):
        assert _parse_pick("under:224.5") == ("under", 224.5)

    def test_invalid_line(self):
        assert _parse_pick("Celtics:abc") == ("Celtics", None)


# ── Resolution logic ─────────────────────────────────────────────────────────


def _make_pb(market: str, side: str, line: float | None = None, odds: int = -110, wager: int = 100) -> dict:
    """Helper to build a paper bet dict for testing."""
    return {
        "paper_bet_id": 1,
        "game_id": "test-game",
        "discord_user": "123",
        "market": market,
        "side": side,
        "line": line,
        "odds": odds,
        "wager": wager,
        "potential_payout": _compute_payout(wager, odds),
    }


class TestResolveMoneyline:
    def test_home_win(self):
        pb = _make_pb("moneyline", "Boston Celtics")
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 110, 95)
        assert result == "won"

    def test_home_loss(self):
        pb = _make_pb("moneyline", "Boston Celtics")
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 95, 110)
        assert result == "lost"

    def test_away_win(self):
        pb = _make_pb("moneyline", "New York Knicks")
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 95, 110)
        assert result == "won"

    def test_away_loss(self):
        pb = _make_pb("moneyline", "New York Knicks")
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 110, 95)
        assert result == "lost"

    def test_last_word_match(self):
        pb = _make_pb("moneyline", "Celtics")
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 110, 95)
        assert result == "won"


class TestResolveSpread:
    def test_home_covers(self):
        # Home -4.5, wins by 10 → margin = (110-100) + (-4.5) = 5.5 > 0 → won
        pb = _make_pb("spread", "Boston Celtics", line=-4.5)
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 110, 100)
        assert result == "won"

    def test_home_fails_to_cover(self):
        # Home -4.5, wins by 3 → margin = (103-100) + (-4.5) = -1.5 < 0 → lost
        pb = _make_pb("spread", "Boston Celtics", line=-4.5)
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 103, 100)
        assert result == "lost"

    def test_push(self):
        # Home -4.5, wins by 4.5 → impossible for integers, but test with -5
        # Home -5, wins by 5 → margin = (105-100) + (-5) = 0 → push
        pb = _make_pb("spread", "Boston Celtics", line=-5.0)
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 105, 100)
        assert result == "push"

    def test_away_spread(self):
        # Away +4.5, home wins by 3 → margin = (100-103) + 4.5 = 1.5 > 0 → won
        pb = _make_pb("spread", "New York Knicks", line=4.5)
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 103, 100)
        assert result == "won"

    def test_no_line_voids(self):
        pb = _make_pb("spread", "Boston Celtics", line=None)
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 110, 100)
        assert result == "void"


class TestResolveTotal:
    def test_over_hits(self):
        # Total 220.5, actual = 225 → diff = 4.5 > 0 → won
        pb = _make_pb("total", "over", line=220.5)
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 115, 110)
        assert result == "won"

    def test_over_misses(self):
        # Total 220.5, actual = 215 → diff = -5.5 < 0 → lost
        pb = _make_pb("total", "over", line=220.5)
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 110, 105)
        assert result == "lost"

    def test_under_hits(self):
        # Total 220.5, actual = 215 → diff = -5.5 < 0 → won
        pb = _make_pb("total", "under", line=220.5)
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 110, 105)
        assert result == "won"

    def test_under_misses(self):
        # Total 220.5, actual = 225 → diff = 4.5 > 0 → lost
        pb = _make_pb("total", "under", line=220.5)
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 115, 110)
        assert result == "lost"

    def test_total_push(self):
        # Total 220.0, actual = 220 → push
        pb = _make_pb("total", "over", line=220.0)
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 110, 110)
        assert result == "push"

    def test_no_line_voids(self):
        pb = _make_pb("total", "over", line=None)
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 115, 110)
        assert result == "void"


class TestResolveEdgeCases:
    def test_unknown_market_voids(self):
        pb = _make_pb("exotic", "something")
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 110, 100)
        assert result == "void"

    def test_unknown_side_voids(self):
        pb = _make_pb("moneyline", "Chicago Bulls")
        result = _resolve_paper_bet(pb, "Boston Celtics", "New York Knicks", 110, 100)
        assert result == "void"
