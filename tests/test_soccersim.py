"""Unit tests for the soccer sim cog."""

import pytest

from bot.cogs.soccersim import (
    ALL_TEAMS,
    CLUB_TEAMS,
    NATIONAL_TEAMS,
    MatchEvent,
    SoccerTeam,
    _event_line,
    _generate_win_prob,
    _payout_multiplier,
    _prob_to_american,
    _simulate_half,
    _simulate_penalties,
    _team_strength,
)


class TestTeamStrength:
    def test_returns_float(self):
        team = CLUB_TEAMS[0]  # Real Madrid
        result = _team_strength(team)
        assert isinstance(result, float)

    def test_in_expected_range(self):
        for team in ALL_TEAMS:
            s = _team_strength(team)
            # All ratings 1-99, so strength should be in that range
            assert 1.0 <= s <= 99.0, f"{team.name} strength {s} out of range"

    def test_stronger_team_higher_strength(self):
        # Real Madrid should be stronger than Australia
        rma = next(t for t in ALL_TEAMS if t.abbr == "RMA")
        aus = next(t for t in ALL_TEAMS if t.abbr == "AUS")
        assert _team_strength(rma) > _team_strength(aus)


class TestWinProbability:
    def test_clamped_range(self):
        for _ in range(100):
            home, away = ALL_TEAMS[0], ALL_TEAMS[-1]
            prob = _generate_win_prob(home, away)
            assert 0.15 <= prob <= 0.85, f"prob {prob} out of clamped range"

    def test_home_boost(self):
        # Same team vs itself: home should get slight edge
        team = CLUB_TEAMS[0]
        prob = _generate_win_prob(team, team)
        assert prob > 0.50  # home boost

    def test_stronger_team_favored(self):
        rma = next(t for t in ALL_TEAMS if t.abbr == "RMA")
        aus = next(t for t in ALL_TEAMS if t.abbr == "AUS")
        prob = _generate_win_prob(rma, aus)
        assert prob > 0.5  # RMA at home vs AUS should be favored


class TestSimulateHalf:
    def test_returns_valid_goals(self):
        home = CLUB_TEAMS[0]
        away = CLUB_TEAMS[1]
        for _ in range(20):
            hg, ag, events = _simulate_half(home, away, 1, 0.55)
            assert hg >= 0
            assert ag >= 0

    def test_events_have_valid_minutes(self):
        home = CLUB_TEAMS[0]
        away = CLUB_TEAMS[1]
        for _ in range(20):
            _, _, events = _simulate_half(home, away, 1, 0.5)
            for e in events:
                assert 1 <= e.minute <= 45, f"1st half minute {e.minute} out of range"

    def test_second_half_minutes(self):
        home = CLUB_TEAMS[0]
        away = CLUB_TEAMS[1]
        for _ in range(20):
            _, _, events = _simulate_half(home, away, 2, 0.5)
            for e in events:
                assert 46 <= e.minute <= 90, f"2nd half minute {e.minute} out of range"

    def test_returns_match_events(self):
        home = CLUB_TEAMS[0]
        away = CLUB_TEAMS[1]
        _, _, events = _simulate_half(home, away, 1, 0.5)
        assert isinstance(events, list)
        for e in events:
            assert isinstance(e, MatchEvent)
            assert e.event_type in ("goal", "yellow", "red", "sub")

    def test_events_sorted_by_minute(self):
        home = CLUB_TEAMS[0]
        away = CLUB_TEAMS[1]
        for _ in range(10):
            _, _, events = _simulate_half(home, away, 2, 0.5)
            minutes = [e.minute for e in events]
            assert minutes == sorted(minutes)


class TestSimulatePenalties:
    def test_always_produces_winner(self):
        home = CLUB_TEAMS[0]
        away = CLUB_TEAMS[1]
        for _ in range(50):
            ph, pa, log = _simulate_penalties(home, away)
            assert ph != pa, f"Penalties tied: {ph}-{pa}"

    def test_returns_log_lines(self):
        home = CLUB_TEAMS[0]
        away = CLUB_TEAMS[1]
        _, _, log = _simulate_penalties(home, away)
        assert len(log) >= 10  # At least 5 rounds * 2 shots


class TestTeamData:
    def test_all_teams_unique_abbreviations(self):
        abbrs = [t.abbr for t in ALL_TEAMS]
        assert len(abbrs) == len(set(abbrs)), f"Duplicate abbrs: {[a for a in abbrs if abbrs.count(a) > 1]}"

    def test_all_ratings_1_to_99(self):
        for team in ALL_TEAMS:
            for attr in ("attack", "midfield", "defense", "goalkeeper"):
                val = getattr(team, attr)
                assert 1 <= val <= 99, f"{team.name}.{attr} = {val} out of range"

    def test_all_teams_have_players(self):
        for team in ALL_TEAMS:
            assert len(team.players) >= 10, f"{team.name} has only {len(team.players)} players"

    def test_club_and_national_counts(self):
        assert len(CLUB_TEAMS) == 20
        assert len(NATIONAL_TEAMS) == 20
        assert len(ALL_TEAMS) == 40

    def test_team_types(self):
        for t in CLUB_TEAMS:
            assert t.team_type == "club"
        for t in NATIONAL_TEAMS:
            assert t.team_type == "national"


class TestProbToAmerican:
    def test_favorite(self):
        result = _prob_to_american(0.7)
        assert result.startswith("-")
        assert int(result) < 0

    def test_underdog(self):
        result = _prob_to_american(0.3)
        assert result.startswith("+")
        assert int(result) > 0

    def test_even(self):
        result = _prob_to_american(0.5)
        assert result == "-100"


class TestPayoutMultiplier:
    def test_even_money(self):
        assert _payout_multiplier(0.5) == pytest.approx(2.0)

    def test_heavy_favorite(self):
        mult = _payout_multiplier(0.8)
        assert mult == pytest.approx(1.25)

    def test_underdog(self):
        mult = _payout_multiplier(0.25)
        assert mult == pytest.approx(4.0)


class TestEventLine:
    def test_goal_format(self):
        e = MatchEvent(minute=23, event_type="goal", team_name="RMA", player="Vinicius")
        line = _event_line(e)
        assert "23'" in line
        assert "\u26bd" in line
        assert "RMA" in line
        assert "Vinicius" in line

    def test_yellow_format(self):
        e = MatchEvent(minute=55, event_type="yellow", team_name="BAR", player="Gavi")
        line = _event_line(e)
        assert "\U0001f7e8" in line

    def test_sub_with_detail(self):
        e = MatchEvent(minute=70, event_type="sub", team_name="MCI", player="Foden", detail="on")
        line = _event_line(e)
        assert "\U0001f504" in line
        assert "on" in line
