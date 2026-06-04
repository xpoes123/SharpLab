"""Blunder detection for the live-chess cog: PGN replay, the swing classifier, formatting."""
from __future__ import annotations

from bot.cogs.chessblunder import (
    classify_blunder,
    fmt_eval,
    parse_broadcast_pgn,
)

# A two-game broadcast round PGN with [%clk] comments (live games carry no [%eval]).
SAMPLE = """[Event "2026 Norway Chess Open"]
[White "Carlsen, Magnus"]
[Black "Gukesh D"]
[Result "*"]
[Round "9"]
[GameURL "https://lichess.org/broadcast/norway-chess-2026--open/round-9/m4NlATsf/AbCd1234"]

1. e4 { [%clk 1:59:57] } 1... c5 { [%clk 1:59:55] } 2. Nf3 { [%clk 1:59:52] } 2... d6 *

[Event "2026 Norway Chess Open"]
[White "Firouzja, Alireza"]
[Black "Keymer, Vincent"]
[Result "*"]
[Round "9"]
[GameURL "https://lichess.org/broadcast/norway-chess-2026--open/round-9/m4NlATsf/Wxyz9876"]

1. d4 { [%clk 1:59:58] } 1... Nf6 *
"""


def test_parse_broadcast_pgn_fields_and_replay():
    games = parse_broadcast_pgn(SAMPLE)
    assert len(games) == 2
    g = games[0]
    assert g["white"] == "Carlsen, Magnus" and g["black"] == "Gukesh D"
    assert g["id"] == "AbCd1234"            # last path segment of GameURL
    assert g["round"] == "9"
    # 4 plies played → 4 SANs and 5 positions (start + after each ply).
    assert g["sans"] == ["e4", "c5", "Nf3", "d6"]
    assert len(g["positions"]) == 5
    assert g["positions"][0].startswith(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR")  # start position


def test_parse_id_falls_back_without_url():
    pgn = '[White "A"]\n[Black "B"]\n[Result "*"]\n\n1. e4 *\n'
    g = parse_broadcast_pgn(pgn)[0]
    assert g["id"] == "A-B"


def test_parse_garbage_is_safe():
    assert parse_broadcast_pgn("not a pgn") == []
    assert parse_broadcast_pgn("") == []


# ── classify_blunder (evals are White-POV pawns) ────────────────────────────────

def test_white_blunder_fires():
    # White to move at +0.4, plays into −3.8 → mover lost 4.2 pawns.
    drop = classify_blunder(white_moved=True, before=0.4, after=-3.8, threshold=3.0)
    assert drop is not None and round(drop, 1) == 4.2


def test_black_blunder_fires():
    # Black to move; White-POV eval rises from −0.2 to +3.5 → Black lost 3.7.
    drop = classify_blunder(white_moved=False, before=-0.2, after=3.5, threshold=3.0)
    assert drop is not None and round(drop, 1) == 3.7


def test_sub_threshold_does_not_fire():
    assert classify_blunder(white_moved=True, before=0.5, after=-1.5, threshold=3.0) is None


def test_already_lost_is_not_a_blunder():
    # White already at −6.0 drifting to −9.5: big drop, but no longer news.
    assert classify_blunder(white_moved=True, before=-6.0, after=-9.5, threshold=3.0) is None


def test_walking_into_mate_fires():
    # White at +0.5 hangs mate (folded to a large negative White-POV number).
    drop = classify_blunder(white_moved=True, before=0.5, after=-1000.0, threshold=3.0)
    assert drop is not None and drop > 3.0


def test_good_move_for_black_no_fire():
    # Black improves their position (White-POV eval drops) — never a Black blunder.
    assert classify_blunder(white_moved=False, before=1.0, after=-0.5, threshold=3.0) is None


# ── fmt_eval ────────────────────────────────────────────────────────────────────

def test_fmt_eval_uses_minus_sign_and_sign_prefix():
    assert fmt_eval(0.4) == "+0.4"
    assert fmt_eval(-3.8) == "−3.8"          # U+2212 minus, not hyphen
    assert fmt_eval(1000.0) == "+M"
    assert fmt_eval(-1000.0) == "−M"
