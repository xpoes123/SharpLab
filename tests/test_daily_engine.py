"""Daily engine — rollover, rotation, seeding determinism, scoring, rewards."""
from datetime import datetime, timezone

from shared import daily


def _utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_puzzle_day_rolls_over_at_4am_et():
    # 08:00 UTC on 2026-03-01 = 03:00 EST → still the previous puzzle-day (Feb 28)
    assert daily.puzzle_day(_utc(2026, 3, 1, 8, 0)) == "2026-02-28"
    # 10:00 UTC = 05:00 EST → past the 4am rollover → March 1
    assert daily.puzzle_day(_utc(2026, 3, 1, 10, 0)) == "2026-03-01"


def test_next_rollover_is_in_the_future_and_at_4am_et():
    nr = daily.next_rollover(_utc(2026, 3, 1, 10, 0))
    assert nr > _utc(2026, 3, 1, 10, 0)
    assert nr.astimezone(daily.ET).hour == daily.ROLLOVER_HOUR


def test_schedule_cycles_difficulty():
    # with a single game, difficulty cycles easy→medium→hard across consecutive days
    diffs = [daily.schedule(d)[1] for d in
             ("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04")]
    assert diffs[:3] == ["easy", "medium", "hard"]
    assert diffs[3] == "easy"  # wraps
    assert daily.schedule("2026-01-01")[0] == "trappig"


def test_launch_ramp_is_easy_then_cycles():
    # first RAMP_EASY_DAYS from launch are easy; then the normal cycle resumes
    assert daily.schedule("2026-08-18")[1] == "easy"   # launch day
    assert daily.schedule("2026-08-22")[1] == "easy"   # still in ramp (day 5)
    after = daily.schedule("2026-08-23")[1]            # ramp over → normal cycle
    assert after in ("easy", "medium", "hard")
    # the ramp only affects the launch window, not arbitrary past/future dates
    assert daily.schedule("2026-01-02")[1] == "medium"


def test_seed_is_deterministic_and_day_specific():
    assert daily.seed_for("trappig", "2026-05-05") == daily.seed_for("trappig", "2026-05-05")
    assert daily.seed_for("trappig", "2026-05-05") != daily.seed_for("trappig", "2026-05-06")


def test_build_puzzle_is_reproducible():
    a = daily.build_puzzle("2026-06-10")
    b = daily.build_puzzle("2026-06-10")
    assert a == b
    assert a["par"] >= 1 and a["payload"]["pig"]


def test_placement_points_curve():
    assert daily.placement_points(1, True) == 100
    assert daily.placement_points(2, True) == 80
    assert daily.placement_points(5, True) == 50
    assert daily.placement_points(50, True) == 10   # floor
    assert daily.placement_points(1, False) == 3     # didn't solve


def test_streak_multiplier_caps():
    assert daily.streak_multiplier(0) == 1.0
    assert abs(daily.streak_multiplier(5) - 1.10) < 1e-9
    assert daily.streak_multiplier(100) == 1.30      # capped


def test_rank_results_orders_and_scores():
    rows = [
        {"discord_user": "a", "solved": 1, "primary_score": 5, "secondary_score": 9000},
        {"discord_user": "b", "solved": 1, "primary_score": 4, "secondary_score": 12000},
        {"discord_user": "c", "solved": 0, "primary_score": 8, "secondary_score": 3000},
        {"discord_user": "d", "solved": 1, "primary_score": 5, "secondary_score": 8000},
    ]
    ranked = daily.rank_results(rows, "trappig")
    order = [r["discord_user"] for r in ranked]
    # Trap the Pig ranks by TIME first: d(8000) < a(9000) < b(12000); non-solver c last.
    assert order == ["d", "a", "b", "c"]
    assert ranked[0]["points"] == 100 and ranked[-1]["points"] == 3


def test_placement_coins():
    assert daily.placement_coins(1, True) == 500
    assert daily.placement_coins(3, True) == 200
    assert daily.placement_coins(7, True) == 100
    assert daily.placement_coins(25, True) == 25
    assert daily.placement_coins(1, False) == 0
