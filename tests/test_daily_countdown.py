"""Countdown daily plugin — evaluator, deterministic solvable generation, validation, rotation."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import daily  # noqa: E402
from shared.daily_games import countdown as cd  # noqa: E402


def test_evaluator_countdown_rules():
    assert cd.evaluate_expression("(100 + 25) * 3", [100, 25, 3, 7, 2, 50]) == 375
    assert cd.evaluate_expression("100 / 25", [100, 25, 1, 2, 3, 4]) == 4
    for bad, nums in [("100 / 3", [100, 3, 1, 2, 4, 5]),   # non-exact division
                      ("9 * 9", [1, 2, 3, 4, 5, 6]),        # 9 not available
                      ("2 +", [1, 2, 3]),                    # incomplete
                      ("2 ** 3", [2, 3])]:                   # illegal token
        try:
            cd.evaluate_expression(bad, nums)
            assert False, bad
        except cd.ExprError:
            pass


def test_solver_finds_and_proves():
    ok, used, expr = cd.solve([100, 25, 3, 7, 2, 50], 375)
    assert ok and used >= 1
    assert cd.evaluate_expression(expr, [100, 25, 3, 7, 2, 50]) == 375
    # an unreachable target on tiny numbers is reported unsolvable
    ok2, _u, _e = cd.solve([1, 1], 999)
    assert not ok2


def test_generate_is_deterministic():
    assert cd.generate(42, "medium") == cd.generate(42, "medium")
    assert cd.build_solvable(42, "medium") == cd.build_solvable(42, "medium")


def test_build_solvable_boards_are_always_solvable_and_public():
    # every scheduled Countdown board over a stretch of days must have an exact solution,
    # and the payload must NEVER carry a witness (it's handed straight to the browser).
    for offset in range(40):
        day = f"2026-09-{19 + (offset % 11):02d}" if offset < 11 else f"2026-10-{(offset - 10):02d}"
        seed = daily.seed_for(cd.ID, day)
        board = cd.build_solvable(seed, "hard")
        assert set(board.keys()) == {"numbers", "target", "difficulty"}, board.keys()
        ok, used, expr = cd.solve(board["numbers"], board["target"])
        assert ok, f"{day} board {board} not solvable"
        # the witness really hits the target under the shared evaluator
        assert cd.evaluate_expression(expr, board["numbers"]) == board["target"]
        # validate accepts the witness as a genuine win
        res = cd.validate(board, {"moves": [expr]})
        assert res and res["solved"] and res["primary"] == used


def test_validate_rejects_non_solutions():
    board = {"numbers": [100, 25, 3, 7, 2, 50], "target": 375}
    assert cd.validate(board, {"moves": ["100 + 25"]}) is None       # wrong value
    assert cd.validate(board, {"moves": ["9 * 9"]}) is None           # invalid numbers
    assert cd.validate(board, {"moves": [""]}) is None                # empty
    assert cd.validate(board, {"moves": []}) is None                  # nothing
    assert cd.validate(board, {"expression": "(100 + 25) * 3"}) == {  # exact via expression key
        "solved": True, "primary": 3, "secondary": 0}


def test_par_is_achievable():
    board = cd.build_solvable(7, "easy")
    p, approx = cd.par(board)
    assert p >= 1 and approx is True


def test_rotation_countdown_live_and_mastermind_retired():
    # era 2 begins 2026-09-19 → that day is Countdown; era 3 (2026-09-24) re-anchors on Countdown.
    # Across everything from era 2 onward, Mastermind is retired from the pool but still resolves.
    assert daily.schedule("2026-09-19")[0] == "countdown"
    assert daily.schedule("2026-09-24")[0] == "countdown"   # today, forced by era 3 re-anchor
    post_retire = {daily.schedule(f"2026-09-{d:02d}")[0] for d in range(19, 30)}
    assert "mastermind" not in post_retire
    assert post_retire <= {"countdown", "rushhour", "trappig"}
    # earlier days are untouched (history intact)
    assert daily.schedule("2026-09-18")[0] != "countdown"
    # mastermind stays registered so historical days still resolve
    assert "mastermind" in daily.DAILY_GAMES


def test_share_grid_smoke():
    s = cd.share_grid({"primary": 4, "secondary": 63000}, {"difficulty": "easy", "par": 5, "number": 12})
    assert "Countdown #12" in s and "1:03" in s
