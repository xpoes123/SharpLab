"""Unit tests for shared/elo.py — pure ELO math."""

from shared.elo import (
    RATING_FLOOR,
    championship_points,
    compute_1v1,
    compute_multiplayer,
    expected_score,
    k_factor,
    rating_change,
)


# ── K-factor ─────────────────────────────────────────────────────────────────


def test_k_factor_provisional():
    assert k_factor(0) == 32
    assert k_factor(9) == 32


def test_k_factor_developing():
    assert k_factor(10) == 24
    assert k_factor(29) == 24


def test_k_factor_established():
    assert k_factor(30) == 16
    assert k_factor(100) == 16


# ── Expected score ───────────────────────────────────────────────────────────


def test_expected_score_equal():
    e = expected_score(1000, 1000)
    assert abs(e - 0.5) < 1e-9


def test_expected_score_symmetric():
    e1 = expected_score(1200, 1000)
    e2 = expected_score(1000, 1200)
    assert abs(e1 + e2 - 1.0) < 1e-9


def test_expected_score_higher_rated_favoured():
    e = expected_score(1400, 1000)
    assert e > 0.5


# ── Rating change ────────────────────────────────────────────────────────────


def test_rating_change_win_positive():
    delta = rating_change(1000, 1000, 1.0, 0)
    assert delta > 0


def test_rating_change_loss_negative():
    delta = rating_change(1000, 1000, 0.0, 0)
    assert delta < 0


def test_rating_change_draw_equal_ratings():
    delta = rating_change(1000, 1000, 0.5, 0)
    assert abs(delta) < 1e-9


# ── 1v1 ──────────────────────────────────────────────────────────────────────


def test_1v1_winner_gains():
    new1, new2 = compute_1v1(1000, 1000, 0, 0, winner=1)
    assert new1 > 1000
    assert new2 < 1000


def test_1v1_equal_ratings_winner_gets_half_k():
    new1, new2 = compute_1v1(1000, 1000, 0, 0, winner=1)
    # K=32 for 0 games, expected=0.5, so delta = 32 * 0.5 = 16
    assert abs(new1 - 1016) < 1e-6
    assert abs(new2 - 984) < 1e-6


def test_1v1_upset_larger_gain():
    # Lower-rated player wins
    new1, new2 = compute_1v1(800, 1200, 0, 0, winner=1)
    gain = new1 - 800
    # Expected score for 800 vs 1200 is very low, so gain should be large
    assert gain > 20


def test_1v1_draw():
    new1, new2 = compute_1v1(1000, 1000, 0, 0, winner=0)
    assert abs(new1 - 1000) < 1e-6
    assert abs(new2 - 1000) < 1e-6


def test_1v1_rating_floor():
    # Very low-rated player loses — shouldn't go below RATING_FLOOR
    new1, new2 = compute_1v1(RATING_FLOOR, 2000, 0, 0, winner=2)
    assert new1 >= RATING_FLOOR


# ── Multiplayer ──────────────────────────────────────────────────────────────


def test_multiplayer_2_players():
    ratings = {1: 1000.0, 2: 1000.0}
    gp = {1: 0, 2: 0}
    finish = [1, 2]  # player 1 wins
    new = compute_multiplayer(ratings, gp, finish)
    assert new[1] > 1000
    assert new[2] < 1000


def test_multiplayer_3_players():
    ratings = {1: 1000.0, 2: 1000.0, 3: 1000.0}
    gp = {1: 0, 2: 0, 3: 0}
    finish = [1, 2, 3]  # 1st, 2nd, 3rd
    new = compute_multiplayer(ratings, gp, finish)
    assert new[1] > new[2] > new[3]


def test_multiplayer_normalisation():
    """With equal ratings and 3 players, 2nd place should stay ~1000."""
    ratings = {1: 1000.0, 2: 1000.0, 3: 1000.0}
    gp = {1: 0, 2: 0, 3: 0}
    finish = [1, 2, 3]
    new = compute_multiplayer(ratings, gp, finish)
    # 2nd place beats 3rd but loses to 1st — should be near 1000
    assert abs(new[2] - 1000) < 5


def test_multiplayer_single_player():
    ratings = {1: 1000.0}
    gp = {1: 0}
    new = compute_multiplayer(ratings, gp, [1])
    assert new[1] == 1000.0


def test_multiplayer_floor():
    ratings = {1: 2000.0, 2: RATING_FLOOR}
    gp = {1: 0, 2: 0}
    finish = [1, 2]
    new = compute_multiplayer(ratings, gp, finish)
    assert new[2] >= RATING_FLOOR


# ── Championship points ─────────────────────────────────────────────────────


def test_championship_points_top_10():
    expected = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
    for pos, pts in enumerate(expected, 1):
        assert championship_points(pos) == pts


def test_championship_points_outside_top_10():
    assert championship_points(11) == 0
    assert championship_points(100) == 0


def test_championship_points_zero_or_negative():
    assert championship_points(0) == 0
    assert championship_points(-1) == 0
