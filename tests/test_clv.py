"""
Unit tests for number-adjusted CLV calculation.
"""
import pytest
from shared.odds_utils import (
    compute_clv,
    side_is_home,
    american_to_prob,
    prob_to_american,
    _PP_PER_HALF_POINT_SPREAD,
    _PP_PER_HALF_POINT_TOTAL,
)
from shared.prop_clv import prop_clv_at_line


class TestWnbaPropClv:
    """prop_clv.py is sport-agnostic; this exercises it on WNBA-shaped capture rows
    (the merged main + alt ladder that queries.get_prop_close_rows returns)."""

    def test_wnba_over_beats_close_at_exact_line(self):
        # Bet Over 18.5 pts at +100 (50% implied). Two books close the exact line
        # two-way; de-vigged fair Over prob > 50% → positive CLV.
        rows = [
            {"source": "williamhill_us", "line": 18.5, "over_odds": -130, "under_odds": +110},
            {"source": "fanatics", "line": 18.5, "over_odds": -125, "under_odds": +105},
        ]
        res = prop_clv_at_line("over", 18.5, +100, rows)
        assert res is not None
        assert res["devigged"] is True
        assert res["n_books"] == 2
        assert res["bet_implied"] == pytest.approx(0.5, abs=0.01)
        assert res["clv"] > 0  # fair Over ~55% vs 50% bet-implied

    def test_wnba_no_price_at_line_returns_none(self):
        # Only a 17.5 line captured; a bet at 18.5 has no real close → None (never faked).
        rows = [{"source": "fanatics", "line": 17.5, "over_odds": -110, "under_odds": -110}]
        assert prop_clv_at_line("over", 18.5, -110, rows) is None

    def test_wnba_alt_line_one_sided_uses_vigged_implied(self):
        # Alt ladder often prices only one side. Falls back to that side's vigged implied.
        rows = [{"source": "fanatics", "line": 24.5, "over_odds": +250, "under_odds": None}]
        res = prop_clv_at_line("over", 24.5, +300, rows)
        assert res is not None
        assert res["devigged"] is False
        assert res["close_prob"] == pytest.approx(american_to_prob(+250), abs=1e-6)


# ── side_is_home ──────────────────────────────────────────────────────────────

class TestSideIsHome:
    def test_home_full_name(self):
        assert side_is_home("Cleveland Cavaliers", "Cleveland Cavaliers", "Toronto Raptors") is True

    def test_away_full_name(self):
        assert side_is_home("Toronto Raptors", "Cleveland Cavaliers", "Toronto Raptors") is False

    def test_home_last_word(self):
        assert side_is_home("Cavaliers", "Cleveland Cavaliers", "Toronto Raptors") is True

    def test_away_last_word(self):
        assert side_is_home("Raptors", "Cleveland Cavaliers", "Toronto Raptors") is False

    def test_no_match(self):
        assert side_is_home("Lakers", "Cleveland Cavaliers", "Toronto Raptors") is None

    def test_case_insensitive(self):
        assert side_is_home("cavaliers", "Cleveland Cavaliers", "Toronto Raptors") is True

    def test_shared_suffix_home(self):
        assert side_is_home("White Sox", "Chicago White Sox", "Boston Red Sox") is True

    def test_shared_suffix_away(self):
        assert side_is_home("Red Sox", "Chicago White Sox", "Boston Red Sox") is False

    def test_shared_suffix_ambiguous(self):
        """Bare 'sox' matches both teams — must return None."""
        assert side_is_home("Sox", "Chicago White Sox", "Boston Red Sox") is None


# ── compute_clv: moneyline (juice-only) ──────────────────────────────────────

class TestComputeCLVMoneyline:
    def test_same_odds_zero_clv(self):
        clv = compute_clv(-110, -110, market="moneyline")
        assert clv == pytest.approx(0.0, abs=0.01)

    def test_got_better_odds(self):
        # Bet at +150 (40%), close at +130 (43.5%) — close tightened, you got better price
        clv = compute_clv(150, 130, market="moneyline")
        assert clv > 0

    def test_got_worse_odds(self):
        # Bet at -150 (60%), close at -130 (56.5%) — close loosened, you got worse price
        clv = compute_clv(-150, -130, market="moneyline")
        assert clv < 0


# ── compute_clv: spread with number movement ─────────────────────────────────

class TestComputeCLVSpread:
    def test_same_spread_same_juice(self):
        """No movement at all — CLV = 0."""
        clv = compute_clv(-110, -110, market="spread", bet_line=8.5, close_line=-8.5, is_home=False)
        # close_line=-8.5 (home perspective), is_home=False → close_for_bettor = +8.5
        # bet_line=8.5, diff=0, so just juice CLV = 0
        assert clv == pytest.approx(0.0, abs=0.01)

    def test_underdog_spread_moved_against(self):
        """User's case: bet Toronto (away) +8.5, close moved to +9.5. Got fewer points than close."""
        # close_line = -9.5 (home perspective, Cleveland -9.5)
        # is_home = False (bet Toronto = away)
        # close_for_bettor = +9.5
        # diff = 8.5 - 9.5 = -1.0 → 2 half-points worse
        clv = compute_clv(-110, -110, market="spread", bet_line=8.5, close_line=-9.5, is_home=False)
        expected = 0.0 + (-1.0 / 0.5) * _PP_PER_HALF_POINT_SPREAD  # -4.0 pp
        assert clv == pytest.approx(expected, abs=0.01)
        assert clv < 0  # should be clearly negative

    def test_underdog_spread_moved_in_favor(self):
        """Bet Toronto +9.5, close came back to +8.5. Got more points than close."""
        clv = compute_clv(-110, -110, market="spread", bet_line=9.5, close_line=-8.5, is_home=False)
        expected = 0.0 + (1.0 / 0.5) * _PP_PER_HALF_POINT_SPREAD  # +4.0 pp
        assert clv == pytest.approx(expected, abs=0.01)
        assert clv > 0

    def test_favorite_spread_moved_in_favor(self):
        """Bet Cleveland (home) -8.5, close moved to -9.5. Gave fewer points than close."""
        # close_line = -9.5 (home perspective), is_home = True → close_for_bettor = -9.5
        # diff = -8.5 - (-9.5) = +1.0 → better for favorite
        clv = compute_clv(-110, -110, market="spread", bet_line=-8.5, close_line=-9.5, is_home=True)
        expected = 0.0 + (1.0 / 0.5) * _PP_PER_HALF_POINT_SPREAD  # +4.0 pp
        assert clv == pytest.approx(expected, abs=0.01)
        assert clv > 0

    def test_favorite_spread_moved_against(self):
        """Bet Cleveland (home) -9.5, close came back to -8.5. Gave more points than close."""
        clv = compute_clv(-110, -110, market="spread", bet_line=-9.5, close_line=-8.5, is_home=True)
        expected = 0.0 + (-1.0 / 0.5) * _PP_PER_HALF_POINT_SPREAD  # -4.0 pp
        assert clv == pytest.approx(expected, abs=0.01)
        assert clv < 0

    def test_spread_number_plus_juice_change(self):
        """Both the number and the juice changed."""
        # Bet away +8.5 @ -112, close at +9.5 @ -108
        # close_line = -9.5 (home), is_home=False → close_for_bettor = +9.5
        # number diff = 8.5 - 9.5 = -1.0 → -4.0 pp
        # juice CLV = (prob(-108) - prob(-112)) * 100
        juice = (american_to_prob(-108) - american_to_prob(-112)) * 100
        number_adj = (-1.0 / 0.5) * _PP_PER_HALF_POINT_SPREAD
        expected = juice + number_adj
        clv = compute_clv(-112, -108, market="spread", bet_line=8.5, close_line=-9.5, is_home=False)
        assert clv == pytest.approx(expected, abs=0.01)

    def test_half_point_movement(self):
        """Half-point spread change = one half-point of adjustment."""
        clv = compute_clv(-110, -110, market="spread", bet_line=8.5, close_line=-9.0, is_home=False)
        # close_for_bettor = +9.0, diff = 8.5 - 9.0 = -0.5 → 1 half-point worse
        expected = (-0.5 / 0.5) * _PP_PER_HALF_POINT_SPREAD  # -2.0 pp
        assert clv == pytest.approx(expected, abs=0.01)

    def test_no_line_data_falls_back_to_juice(self):
        """If bet_line or close_line is None, just compute juice CLV."""
        clv = compute_clv(-110, -105, market="spread", bet_line=None, close_line=-9.5, is_home=False)
        juice = (american_to_prob(-105) - american_to_prob(-110)) * 100
        assert clv == pytest.approx(juice, abs=0.01)


# ── compute_clv: total with number movement ──────────────────────────────────

class TestComputeCLVTotal:
    def test_over_total_moved_up_good(self):
        """Bet over 221.5, close at 223.5. Lower threshold = easier to clear = positive CLV."""
        clv = compute_clv(-110, -110, market="total", bet_line=221.5, close_line=223.5, is_over=True)
        # diff = 223.5 - 221.5 = 2.0 → 4 half-points better for over
        expected = (2.0 / 0.5) * _PP_PER_HALF_POINT_TOTAL  # +4.0 pp
        assert clv == pytest.approx(expected, abs=0.01)
        assert clv > 0

    def test_over_total_moved_down_bad(self):
        """Bet over 223.5, close at 221.5. Higher threshold = harder to clear = negative CLV."""
        clv = compute_clv(-110, -110, market="total", bet_line=223.5, close_line=221.5, is_over=True)
        expected = (-2.0 / 0.5) * _PP_PER_HALF_POINT_TOTAL  # -4.0 pp
        assert clv == pytest.approx(expected, abs=0.01)
        assert clv < 0

    def test_under_total_moved_down_good(self):
        """Bet under 223.5, close at 221.5. Higher threshold = easier to stay under = positive CLV."""
        clv = compute_clv(-110, -110, market="total", bet_line=223.5, close_line=221.5, is_over=False)
        expected = (2.0 / 0.5) * _PP_PER_HALF_POINT_TOTAL  # +4.0 pp
        assert clv == pytest.approx(expected, abs=0.01)
        assert clv > 0

    def test_under_total_moved_up_bad(self):
        """Bet under 221.5, close at 223.5. Lower threshold = harder to stay under = negative CLV."""
        clv = compute_clv(-110, -110, market="total", bet_line=221.5, close_line=223.5, is_over=False)
        expected = (-2.0 / 0.5) * _PP_PER_HALF_POINT_TOTAL  # -4.0 pp
        assert clv == pytest.approx(expected, abs=0.01)
        assert clv < 0

    def test_same_total_zero_clv(self):
        clv = compute_clv(-110, -110, market="total", bet_line=221.5, close_line=221.5, is_over=True)
        assert clv == pytest.approx(0.0, abs=0.01)
