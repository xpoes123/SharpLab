"""Rush Hour plugin — BFS solver, guaranteed-solvable generation, validate."""
from shared.daily_games import rushhour as rh


def test_trivial_one_move():
    # target at cols 3-4 on the exit row, nothing blocking → one slide to the edge (col 4-5)
    board = {"size": 6, "exit_row": 2, "target": "X",
             "cars": [{"id": "X", "r": 2, "c": 3, "len": 2, "orient": "h"}]}
    assert rh.solve(board) == 1
    res = rh.validate(board, {"moves": [["X", 1]]})
    assert res and res["solved"] and res["primary"] == 1


def test_blocked_requires_moving_blocker():
    # a vertical car sits at col 5 crossing the exit row; must move it up/down first
    board = {"size": 6, "exit_row": 2, "target": "X", "cars": [
        {"id": "X", "r": 2, "c": 0, "len": 2, "orient": "h"},
        {"id": "A", "r": 1, "c": 5, "len": 2, "orient": "v"},   # occupies (1,5),(2,5) — blocks exit
    ]}
    opt, path = rh.solve(board, want_path=True)
    assert opt is not None and opt >= 2
    res = rh.validate(board, {"moves": path})
    assert res and res["solved"] and res["primary"] == opt


def test_validate_rejects_illegal_and_incomplete():
    board = {"size": 6, "exit_row": 2, "target": "X", "cars": [
        {"id": "X", "r": 2, "c": 0, "len": 2, "orient": "h"},
        {"id": "A", "r": 0, "c": 3, "len": 3, "orient": "v"},
    ]}
    assert rh.validate(board, {"moves": [["X", 1]]}) is None       # doesn't reach the edge
    assert rh.validate(board, {"moves": [["X", 9]]}) is None       # out of bounds
    assert rh.validate(board, {"moves": [["Z", 1]]}) is None       # no such car
    assert rh.validate(board, {"moves": [["A", 1]]}) is None       # legal move but not solved


def test_generation_is_solvable_and_par_exact():
    from shared import daily
    for difficulty in ("easy", "medium", "hard"):
        seed = daily.seed_for("rushhour", "2026-09-01") + hash(difficulty) % 1000
        board = rh.build_solvable(seed, difficulty)
        ok, path = rh.is_solvable(board)
        assert ok, f"{difficulty} board unsolvable"
        p, approx = rh.par(board)
        assert p >= 1 and not approx
        # the BFS witness path solves it and matches par length
        res = rh.validate(board, {"moves": path})
        assert res and res["solved"] and res["primary"] == p


def test_build_solvable_deterministic():
    from shared import daily
    s = daily.seed_for("rushhour", "2026-09-09")
    a, b = rh.build_solvable(s, "easy"), rh.build_solvable(s, "easy")
    assert a["cars"] == b["cars"]


def test_easy_par_in_range():
    from shared import daily
    for off in range(8):
        board = rh.build_solvable(daily.seed_for("rushhour", f"2026-09-1{off}"), "easy")
        p, _ = rh.par(board)
        assert 3 <= p <= 13    # easy stays gentle
