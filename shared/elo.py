"""ELO rating calculations. Pure math, no side effects."""

# ── K-factor ─────────────────────────────────────────────────────────────────

RATING_FLOOR = 100.0
DEFAULT_RATING = 1000.0


def k_factor(games_played: int) -> int:
    """Dynamic K-factor: 32 provisional, 24 developing, 16 established."""
    if games_played < 10:
        return 32
    if games_played < 30:
        return 24
    return 16


# ── Core formulas ────────────────────────────────────────────────────────────


def expected_score(my_rating: float, opp_rating: float) -> float:
    """Standard ELO expected score."""
    return 1.0 / (1.0 + 10.0 ** ((opp_rating - my_rating) / 400.0))


def rating_change(
    my_rating: float,
    opp_rating: float,
    actual: float,
    games_played: int,
) -> float:
    """Rating delta for one result. actual: 1.0=win, 0.5=draw, 0.0=loss."""
    k = k_factor(games_played)
    e = expected_score(my_rating, opp_rating)
    return k * (actual - e)


# ── 1v1 ──────────────────────────────────────────────────────────────────────


def compute_1v1(
    r1: float,
    r2: float,
    gp1: int,
    gp2: int,
    winner: int,
) -> tuple[float, float]:
    """New ratings for a 1v1 match.

    winner: 1 = player 1, 2 = player 2, 0 = draw.
    Returns (new_r1, new_r2).
    """
    if winner == 1:
        a1, a2 = 1.0, 0.0
    elif winner == 2:
        a1, a2 = 0.0, 1.0
    else:
        a1, a2 = 0.5, 0.5

    d1 = rating_change(r1, r2, a1, gp1)
    d2 = rating_change(r2, r1, a2, gp2)
    return max(RATING_FLOOR, r1 + d1), max(RATING_FLOOR, r2 + d2)


# ── Multiplayer ──────────────────────────────────────────────────────────────


def compute_multiplayer(
    ratings: dict[int, float],
    games_played: dict[int, int],
    finish_order: list[int],
) -> dict[int, float]:
    """New ratings for a multiplayer game (e.g. Pokemon).

    Pairwise comparison: player ranked *i* "beats" player ranked *j* if i < j.
    Total delta normalised by (N-1).
    """
    n = len(finish_order)
    if n < 2:
        return dict(ratings)

    deltas: dict[int, float] = {uid: 0.0 for uid in finish_order}

    for i, uid_a in enumerate(finish_order):
        for j, uid_b in enumerate(finish_order):
            if i == j:
                continue
            actual = 1.0 if i < j else 0.0
            delta = rating_change(
                ratings[uid_a], ratings[uid_b], actual, games_played[uid_a],
            )
            deltas[uid_a] += delta

    return {
        uid: max(RATING_FLOOR, ratings[uid] + deltas[uid] / (n - 1))
        for uid in finish_order
    }


# ── F1 championship points ──────────────────────────────────────────────────

F1_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]


def championship_points(position: int) -> int:
    """F1-style points for a 1-indexed leaderboard position. 11+ → 0."""
    if 1 <= position <= len(F1_POINTS):
        return F1_POINTS[position - 1]
    return 0
