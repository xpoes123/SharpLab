"""Trap the Pig plugin — deterministic generation, pig AI, validate, par."""
from shared.daily_games import trappig as tp


def test_generate_is_deterministic():
    a = tp.generate(12345, "medium")
    b = tp.generate(12345, "medium")
    assert a == b
    assert a["rows"] == 11 and a["cols"] == 11
    assert a["pig"] == [5, 5]
    assert len(a["fences"]) == 7
    # different seed → (almost surely) different board
    assert tp.generate(999, "medium")["fences"] != a["fences"]


def test_difficulty_params():
    assert tp.generate(1, "easy")["rows"] == 9
    assert tp.generate(1, "hard")["rows"] == 13
    assert len(tp.generate(1, "hard")["fences"]) == 5


def test_pig_escapes_on_open_board():
    # no player moves → the pig should reach the border (escape)
    puz = {"rows": 11, "cols": 11, "pig": [5, 5], "fences": []}
    outcome, used = tp._run(puz, [])
    assert outcome == "escaped" and used == 0


def test_one_move_trap_validates():
    # 5 of 6 neighbours pre-fenced; fencing the last one traps the pig immediately (it can't move).
    ring = tp._neighbours(5, 5, 11, 11)
    puz = {"rows": 11, "cols": 11, "pig": [5, 5], "fences": [list(m) for m in ring[:5]]}
    res = tp.validate(puz, {"moves": [list(ring[5])], "elapsed_ms": 4200})
    assert res and res["solved"] and res["primary"] == 1 and res["secondary"] == 4200


def test_greedy_solution_actually_traps():
    # the pig moves after every fence, so trapping takes a real sequence — greedy finds one.
    puz = tp.generate(7, "easy")
    used, trapped = tp._greedy_solve(puz)
    moves = tp.greedy_solution(puz)
    if trapped:
        res = tp.validate(puz, {"moves": moves, "elapsed_ms": 1000})
        assert res and res["solved"] and res["primary"] == used


def test_validate_rejects_non_solutions():
    puz = {"rows": 11, "cols": 11, "pig": [5, 5], "fences": []}
    # one fence far away — pig still escapes → not a solution
    assert tp.validate(puz, {"moves": [[0, 0]], "elapsed_ms": 100}) is None
    # illegal: fencing the pig's own cell
    assert tp.validate(puz, {"moves": [[5, 5]], "elapsed_ms": 100}) is None
    # illegal: out of bounds
    assert tp.validate(puz, {"moves": [[99, 99]], "elapsed_ms": 100}) is None


def test_par_on_near_trapped_board():
    # 5 of 6 neighbours fenced → greedy fences the last one → par 1.
    ring = tp._neighbours(5, 5, 11, 11)
    puz = {"rows": 11, "cols": 11, "pig": [5, 5], "fences": [list(m) for m in ring[:5]]}
    p, approx = tp.par(puz)
    assert p == 1


def test_par_is_positive():
    for seed in (42, 7, 100, 2024):
        p, approx = tp.par(tp.generate(seed, "easy"))
        assert p >= 1


def test_share_grid():
    g = tp.share_grid({"solved": True, "primary": 5, "secondary": 48000},
                      {"difficulty": "medium", "par": 4})
    assert "Trap the Pig" in g and "5 fences" in g and "par 4" in g and "0:48" in g


def test_build_solvable_always_has_a_witness():
    from shared import daily
    # every scheduled daily over 60 days must be provably solvable, and the witness must trap
    for offset in range(60):
        day = f"2026-{7 + offset // 30:02d}-{(offset % 30) + 1:02d}"
        seed = daily.seed_for("trappig", day)
        _, difficulty = daily.schedule(day)
        puz = tp.build_solvable(seed, difficulty)
        ok, witness = tp.is_solvable(puz)
        assert ok, f"{day} board not solvable"
        res = tp.validate(puz, {"moves": witness, "elapsed_ms": 1})
        assert res and res["solved"], f"{day} witness didn't trap"


def test_build_solvable_is_deterministic():
    from shared import daily
    seed = daily.seed_for("trappig", "2026-09-09")
    assert tp.build_solvable(seed, "hard") == tp.build_solvable(seed, "hard")
