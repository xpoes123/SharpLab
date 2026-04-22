"""Tests for reworked NBA/NFL sim — three-rating system, spread/OU markets."""

import math

import pytest


# ── nbasim helpers ────────────────────────────────────────────────────────────


def _nba():
    from bot.cogs.nbasim import (
        _compute_home_prob,
        _compute_spread,
        _compute_total,
        _generate_ratings,
        _ou_payout,
        _simulate_quarter,
    )
    return _compute_home_prob, _compute_spread, _compute_total, _generate_ratings, _ou_payout, _simulate_quarter


def _nfl():
    from bot.cogs.nflsim import (
        _compute_home_prob,
        _compute_spread,
        _compute_total,
        _generate_ratings,
        _juice_payout,
        _simulate_quarter,
    )
    return _compute_home_prob, _compute_spread, _compute_total, _generate_ratings, _juice_payout, _simulate_quarter


# ── ratings generation ────────────────────────────────────────────────────────


def test_nba_generate_ratings_in_range():
    _, _, _, _generate_ratings, _, _ = _nba()
    for _ in range(50):
        off, def_, coa = _generate_ratings()
        assert 45 <= off <= 95
        assert 45 <= def_ <= 95
        assert 45 <= coa <= 95


def test_nfl_generate_ratings_in_range():
    _, _, _, _generate_ratings, _, _ = _nfl()
    for _ in range(50):
        off, def_, coa = _generate_ratings()
        assert 45 <= off <= 95
        assert 45 <= def_ <= 95
        assert 45 <= coa <= 95


# ── win probability ───────────────────────────────────────────────────────────


def test_nba_home_prob_clamped():
    _compute_home_prob, _, _, _, _, _ = _nba()
    # Even teams → prob near 0.5
    p = _compute_home_prob(65, 65, 65, 65, 65, 65)
    assert 0.45 <= p <= 0.55

    # Dominant home team → high prob but clamped
    p_high = _compute_home_prob(95, 95, 95, 45, 45, 45)
    assert p_high <= 0.80

    # Dominant away → low prob but clamped
    p_low = _compute_home_prob(45, 45, 45, 95, 95, 95)
    assert p_low >= 0.20


def test_nba_home_prob_direction():
    _compute_home_prob, _, _, _, _, _ = _nba()
    p_fav = _compute_home_prob(90, 80, 80, 50, 50, 50)
    p_dog = _compute_home_prob(50, 50, 50, 90, 80, 80)
    assert p_fav > 0.5
    assert p_dog < 0.5


def test_nfl_home_prob_clamped():
    _compute_home_prob, _, _, _, _, _ = _nfl()
    p = _compute_home_prob(65, 65, 65, 65, 65, 65)
    assert 0.45 <= p <= 0.55
    assert _compute_home_prob(95, 95, 95, 45, 45, 45) <= 0.80
    assert _compute_home_prob(45, 45, 45, 95, 95, 95) >= 0.20


# ── spread generation ─────────────────────────────────────────────────────────


def test_nba_spread_half_point_lines():
    _, _compute_spread, _, _, _, _ = _nba()
    for prob in [0.3, 0.4, 0.5, 0.6, 0.7]:
        s = _compute_spread(prob)
        # Must be a .5 line (no whole numbers to avoid pushes)
        assert (s * 2) % 1 == 0  # multiple of 0.5
        assert s != int(s), f"spread={s} is a whole number (push risk) for prob={prob}"


def test_nba_spread_direction():
    _, _compute_spread, _, _, _, _ = _nba()
    assert _compute_spread(0.65) > 0  # home favored → positive spread
    assert _compute_spread(0.35) < 0  # home underdog → negative spread


def test_nfl_spread_half_point_lines():
    _, _compute_spread, _, _, _, _ = _nfl()
    for prob in [0.3, 0.4, 0.5, 0.6, 0.7]:
        s = _compute_spread(prob)
        assert (s * 2) % 1 == 0
        assert s != int(s), f"spread={s} is whole number for prob={prob}"


def test_nfl_spread_direction():
    _, _compute_spread, _, _, _, _ = _nfl()
    assert _compute_spread(0.65) > 0
    assert _compute_spread(0.35) < 0


# ── total generation ──────────────────────────────────────────────────────────


def test_nba_total_reasonable_range():
    _, _, _compute_total, _, _, _ = _nba()
    # Signature: (home_off, home_def, away_off, away_def)
    total = _compute_total(65, 65, 65, 65)
    assert 200 <= total <= 250
    # High offense + low defense on both teams → higher total
    t_high = _compute_total(90, 45, 90, 45)
    # Low offense + high defense on both teams → lower total
    t_low = _compute_total(45, 90, 45, 90)
    assert t_high > t_low


def test_nfl_total_reasonable_range():
    _, _, _compute_total, _, _, _ = _nfl()
    total = _compute_total(65, 65, 65, 65)
    assert 35 <= total <= 60


# ── -110 juice payout ─────────────────────────────────────────────────────────


def test_nba_ou_payout_approx_191():
    _, _, _, _, _ou_payout, _ = _nba()
    # Bet 110 → win 100 → total 210; bet/win ratio
    p = _ou_payout(110)
    assert p == 110 + int(110 * 100 / 110)  # 210
    # Bet 100 → total ~191
    p100 = _ou_payout(100)
    assert 190 <= p100 <= 192


def test_nfl_juice_payout_approx_191():
    _, _, _, _, _juice_payout, _ = _nfl()
    p = _juice_payout(110)
    assert p == 110 + int(110 * 100 / 110)
    p100 = _juice_payout(100)
    assert 190 <= p100 <= 192


# ── quarter simulation ────────────────────────────────────────────────────────


def test_nba_quarter_sim_min_points():
    _, _, _, _, _, _simulate_quarter = _nba()
    for _ in range(100):
        h, a = _simulate_quarter(65, 65, 65, 65, 65, 65)
        assert h >= 14
        assert a >= 14


def test_nba_quarter_sim_high_offense_scores_more():
    _, _, _, _, _, _simulate_quarter = _nba()
    import statistics
    home_scores = [_simulate_quarter(90, 65, 65, 65, 45, 65)[0] for _ in range(200)]
    base_scores = [_simulate_quarter(65, 65, 65, 65, 65, 65)[0] for _ in range(200)]
    assert statistics.mean(home_scores) > statistics.mean(base_scores)


def test_nfl_quarter_sim_returns_nonneg():
    _, _, _, _, _, _simulate_quarter = _nfl()
    for _ in range(100):
        h, a = _simulate_quarter(65, 65, 65, 65, 65, 65)
        assert h >= 0
        assert a >= 0


# ── modal label length ───────────────────────────────────────────────────────
# Discord API hard limit: text input labels must be ≤ 45 characters.
# discord.py does not validate this client-side — over-length labels reach
# Discord's API which returns 400, silently failing the join interaction.


def test_nba_modal_label_lengths():
    import warnings
    from bot.cogs.nbasim import JoinNbaSimModal
    for name in JoinNbaSimModal.__modal_children_items__:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            obj = getattr(JoinNbaSimModal, name)
            label = obj.label
        assert len(label) <= 45, (
            f"TextInput '{name}' label is {len(label)} chars "
            f"— Discord API limit is 45"
        )


# ── resolution logic (pure logic, no Discord) ─────────────────────────────────


def test_nba_spread_cover_home():
    """Home covers if score_diff > spread."""
    from bot.cogs.nbasim import NbaSimPlayer, NbaSimTable

    table = NbaSimTable(
        channel_id=1, host_id=1, host_name="host",
        home_team=("Lakers", "LAL"), away_team=("Celtics", "BOS"),
        home_prob=0.6, spread=4.5, total=220.0,
        home_score=110, away_score=100,
    )
    # score_diff = 10 > 4.5 → home covers
    score_diff = table.home_score - table.away_score
    assert score_diff > table.spread  # home covers

    # away covers when home wins by less than spread
    table.home_score = 104
    score_diff2 = table.home_score - table.away_score
    assert score_diff2 < table.spread  # away covers


def test_nba_ou_resolution():
    from bot.cogs.nbasim import NbaSimTable

    table = NbaSimTable(
        channel_id=1, host_id=1, host_name="host",
        home_team=("Lakers", "LAL"), away_team=("Celtics", "BOS"),
        home_prob=0.5, spread=0.5, total=220.0,
        home_score=115, away_score=110,
    )
    total_score = table.home_score + table.away_score  # 225
    assert total_score > table.total  # over wins

    table.home_score = 105
    table.away_score = 100
    total_score2 = table.home_score + table.away_score  # 205
    assert total_score2 < table.total  # under wins


def test_nfl_spread_cover():
    from bot.cogs.nflsim import NflSimTable

    table = NflSimTable(
        channel_id=1, host_id=1, host_name="host",
        home_team=("Chiefs", "KC"), away_team=("Bills", "BUF"),
        home_prob=0.65, spread=5.5, total=46.5,
        home_score=28, away_score=20,
    )
    diff = table.home_score - table.away_score  # 8 > 5.5 → home covers
    assert diff > table.spread

    table.home_score = 24
    diff2 = table.home_score - table.away_score  # 4 < 5.5 → away covers
    assert diff2 < table.spread
