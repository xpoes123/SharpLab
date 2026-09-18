"""Mastermind daily plugin — scoring, validation, redaction, determinism."""
from shared.daily_games import mastermind as mm


def test_generate_deterministic_and_in_range():
    for diff, (length, colors) in mm._PARAMS.items():
        p = mm.generate(999, diff)
        assert p["len"] == length and p["colors"] == colors
        assert len(p["code"]) == length
        assert all(0 <= c < colors for c in p["code"])
        assert mm.generate(999, diff)["code"] == p["code"]   # same seed → same code


def test_redact_hides_the_code():
    p = mm.generate(1, "easy")
    r = mm.redact(p)
    assert "code" not in r
    assert r["len"] == p["len"] and r["colors"] == p["colors"]


def test_feedback_black_and_white():
    p = {"len": 4, "colors": 6, "dup": True, "code": [0, 1, 2, 3]}
    assert mm.feedback(p, [0, 1, 2, 3]) == (4, 0)           # perfect
    assert mm.feedback(p, [3, 2, 1, 0]) == (0, 4)           # all present, all misplaced
    assert mm.feedback(p, [0, 1, 5, 5]) == (2, 0)           # two exact, rest absent
    # min-count rule: code has one 1, guess has two 1s → only one white credited
    p2 = {"len": 4, "colors": 6, "dup": True, "code": [0, 1, 1, 2]}
    assert mm.feedback(p2, [1, 0, 3, 3]) == (0, 2)


def test_feedback_rejects_malformed():
    p = mm.generate(2, "easy")
    assert mm.feedback(p, [0, 0, 0]) is None                # wrong length
    assert mm.feedback(p, [0, 0, 0, 99]) is None            # out of range
    assert mm.feedback(p, "abcd") is None                   # wrong type


def test_validate_requires_last_guess_equals_code():
    p = {"len": 4, "colors": 6, "dup": True, "code": [4, 2, 0, 1]}
    ok = mm.validate(p, {"moves": [[0, 0, 0, 0], [4, 2, 0, 1]]})
    assert ok == {"solved": True, "primary": 2, "secondary": 0}
    assert mm.validate(p, {"moves": [[4, 2, 0, 0]]}) is None   # last guess wrong
    assert mm.validate(p, {"moves": []}) is None              # no guesses
    assert mm.validate(p, {"moves": [[4, 2, 0]]}) is None     # malformed guess


def test_par_is_approximate():
    for diff in mm.DIFFICULTIES:
        par_v, approx = mm.par(mm.generate(3, diff))
        assert par_v > 0 and approx is True


def test_rank_order_is_move_first():
    assert mm.RANK_ORDER == ("primary_score", "secondary_score")
    assert getattr(mm, "ONLINE", False) is True


def test_seed_salt_makes_online_code_unguessable():
    from shared import daily
    # A server secret must change the seed (so the hidden code can't be recomputed offline)...
    assert daily.seed_for("mastermind", "2026-09-19", "") != daily.seed_for("mastermind", "2026-09-19", "s3cret")
    # ...while offline games (secret="") are unchanged.
    assert daily.seed_for("trappig", "2026-09-19") == daily.seed_for("trappig", "2026-09-19", "")
