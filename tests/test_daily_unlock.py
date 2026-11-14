"""Unlock plugin — Android pattern rules, solution counting, generation, validate."""
from shared import daily
from shared.daily_games import unlock as ul


def test_replay_android_rules():
    assert ul.replay(3, [0, 2]) == ([0, 1, 2], {(0, 1), (1, 2)})       # crossing unused dot selects it
    assert ul.replay(3, [1, 0, 2]) == ([1, 0, 2], {(0, 1), (1, 2)})    # crossing a used dot is fine
    assert ul.replay(3, [0, 1, 0]) is None                              # can't land on a used dot
    assert ul.replay(3, [0, 9]) is None                                 # off-grid
    assert ul.replay(3, []) is None


def test_count_solutions_line():
    # a 3-dot line: 0→2, 2→0, 1→0→2, 1→2→0
    assert ul.count_solutions(3, {(0, 1), (1, 2)}) == 4


def test_generation_solvable_and_hidden_witness(monkeypatch):
    monkeypatch.setitem(daily.TRIAL_DAYS, "2026-11-16", ("unlock", "medium"))
    for diff in ul.DIFFICULTIES:
        p = ul.build_solvable(daily.seed_for("unlock", "2026-11-16"), diff)
        assert ul.validate(p, {"moves": p["_witness"]})["solved"]
        assert ul.count_solutions(p["n"], {tuple(e) for e in p["edges"]}) >= 1
    b = daily.build_puzzle("2026-11-16")
    assert b["game_id"] == "unlock" and "_witness" not in b["payload"]


def test_validate_rejects_wrong_picture():
    p = {"n": 3, "edges": [[0, 1], [1, 2]]}
    assert ul.validate(p, {"moves": [0, 2]})["primary"] == 3
    assert ul.validate(p, {"moves": [0, 1]}) is None                    # missing a line
    assert ul.validate(p, {"moves": [0, 2, 5]}) is None                 # extra line
    assert ul.validate(p, {"moves": "nope"}) is None


def test_trial_day_is_out_of_rotation(monkeypatch):
    monkeypatch.setitem(daily.TRIAL_DAYS, "2026-11-15", ("unlock", "medium"))   # a Sunday
    assert daily.schedule("2026-11-15") == ("unlock", "medium")         # beats the Sunday override
    assert all(daily.schedule(f"2026-12-{d:02d}")[0] != "unlock" for d in range(1, 29))


def test_difficulty_tracks_solver_effort():
    eff = {d: ul.build_solvable(daily.seed_for("unlock", "2026-11-20"), d)["_effort"] for d in ("easy", "medium")}
    assert eff["easy"] < eff["medium"]
    assert ul.build_solvable(1, "easy")["n"] == 4                       # 3×3 is too trivial to ship
