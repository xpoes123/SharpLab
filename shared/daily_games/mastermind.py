"""Mastermind — the Daily Games platform's first ONLINE plugin (hidden information).

Crack a secret code of N colored pegs. After each guess the server returns feedback:
  ● black = right color, right position
  ○ white = right color, wrong position (min-count rule, colors never double-counted)
You keep guessing until every peg is black.

Unlike Trap the Pig / Rush Hour (fully offline — the client is handed the whole board and just
replays its solution), Mastermind hides the code, so the client CANNOT compute feedback itself:
guesses go to the server (web/daily.py POST /mm-guess) which holds the code and counts every guess.
Because guesses are counted server-side and persist across refreshes, the move-count leaderboard
can't be gamed by replaying a memorised code in "1 guess".

RANK_ORDER is move-first (fewest guesses wins; time breaks ties) per the community preference.

Board payload (stored in daily_puzzles.payload):
  {"len": 4, "colors": 6, "dup": true, "code": [c0, c1, c2, c3]}   # code ints in 0..colors-1
`redact` strips `code` before it's ever sent to a browser.
Solution (validated by /submit): {"moves": [[g0,g1,g2,g3], ...]}  — each move is one full guess.
"""

from __future__ import annotations

import random

ID = "mastermind"
NAME = "Mastermind"
ICON = "🎯"
SURFACE = "web"
ONLINE = True                    # web/daily.py: hide the code + serve per-guess feedback
DIFFICULTIES = ["easy", "medium", "hard"]
RANK_ORDER = ("primary_score", "secondary_score")   # guesses first, time breaks ties
HOWTO = (
    "Crack the secret code of colored pegs. After each guess you get feedback: a **black** peg ● "
    "for every peg that's the **right color in the right spot**, and a **white** peg ○ for a right "
    "color in the **wrong** spot. Colors can repeat. Solve it in as **few guesses** as you can — "
    "guesses are your rank, time breaks ties. Every guess counts (even across a refresh), so think "
    "before you lock one in. The clock starts on Start."
)

# difficulty → (code length, number of colors). Duplicates are always allowed.
_PARAMS = {
    "easy":   (4, 6),
    "medium": (5, 7),
    "hard":   (6, 8),
}
# Rough optimal-ish guess counts (approximate — Mastermind par isn't a clean closed form).
_PAR = {"easy": 5, "medium": 6, "hard": 7}
_MAX_GUESSES = 1000             # hard ceiling on stored history — a sanity bound, not a game limit


def _dims(difficulty: str) -> tuple[int, int]:
    return _PARAMS.get(difficulty, _PARAMS["medium"])


def generate(seed: int, difficulty: str) -> dict:
    """Deterministic secret code for (seed, difficulty). Always solvable by construction, so no
    build_solvable is needed."""
    length, colors = _dims(difficulty)
    rng = random.Random(seed)
    code = [rng.randrange(colors) for _ in range(length)]
    return {"len": length, "colors": colors, "dup": True, "code": code}


def redact(payload: dict) -> dict:
    """The client-safe board — everything except the secret code."""
    return {k: v for k, v in payload.items() if k != "code"}


def par(puzzle: dict) -> tuple[int, bool]:
    """Approximate par for the difficulty. approx=True — there's no exact single-number par."""
    length = puzzle.get("len")
    for diff, (l, _c) in _PARAMS.items():
        if l == length:
            return _PAR[diff], True
    return length + 1, True


def feedback(payload: dict, guess: list[int]) -> tuple[int, int] | None:
    """(black, white) for a guess, or None if the guess is malformed. Standard scoring: black =
    exact matches; white = right-color/wrong-place counted with the min-count rule so a color is
    never credited more times than it appears."""
    code = payload["code"]
    if not _valid_guess(guess, payload):
        return None
    black = sum(1 for s, g in zip(code, guess) if s == g)
    white = 0
    for color in range(payload["colors"]):
        in_code = sum(1 for i, s in enumerate(code) if s == color and guess[i] != color)
        in_guess = sum(1 for i, g in enumerate(guess) if g == color and code[i] != color)
        white += min(in_code, in_guess)
    return black, white


def _valid_guess(guess, payload: dict) -> bool:
    length, colors = payload["len"], payload["colors"]
    return (
        isinstance(guess, (list, tuple))
        and len(guess) == length
        and all(isinstance(g, int) and 0 <= g < colors for g in guess)
    )


def validate(puzzle: dict, solution: dict) -> dict | None:
    """A win = a non-empty guess list whose LAST guess equals the code (every peg black). primary is
    the guess count here as a floor; the web layer overrides it with the server-counted total (which
    survives refreshes), so the client can't shrink its move count by replaying the answer."""
    moves = solution.get("moves") or []
    if not isinstance(moves, list) or not (1 <= len(moves) <= _MAX_GUESSES):
        return None
    if any(not _valid_guess(g, puzzle) for g in moves):
        return None
    if list(moves[-1]) != list(puzzle["code"]):
        return None
    return {"solved": True, "primary": len(moves), "secondary": 0}


def share_grid(result: dict, meta: dict) -> str:
    num = meta.get("number")
    par_v = meta.get("par")
    secs = result["secondary"] // 1000
    t = f"{secs // 60}:{secs % 60:02d}"
    head = f"{ICON} {NAME} #{num}" if num else f"{ICON} {NAME}"
    par_str = f" (par {par_v})" if par_v is not None else ""
    blocks = "🟩" * min(result["primary"], 12)
    return f"{head} · {meta.get('difficulty', '')} · {result['primary']} guesses{par_str} · {t}\n{blocks}"


if __name__ == "__main__":
    # ponytail: one runnable self-check — scoring, validation, determinism.
    p = generate(12345, "easy")
    assert p["len"] == 4 and p["colors"] == 6 and "code" in p
    assert "code" not in redact(p)
    assert generate(12345, "easy")["code"] == p["code"]      # deterministic
    code = p["code"]
    assert feedback(p, list(code)) == (4, 0)                  # exact
    # all-black wins; a wrong final guess doesn't
    assert validate(p, {"moves": [[0, 0, 0, 0], list(code)]}) == {"solved": True, "primary": 2, "secondary": 0}
    wrong = [(code[0] + 1) % 6] + list(code[1:])
    assert validate(p, {"moves": [wrong]}) is None
    assert validate(p, {"moves": []}) is None
    assert feedback(p, [0, 0, 0]) is None                     # wrong length
    assert feedback(p, [0, 0, 0, 99]) is None                 # out of range
    # white-peg min-count: code [0,1,1,2], guess [1,0,3,3] → 0 black, 2 white (one 0, one 1)
    p2 = {"len": 4, "colors": 6, "dup": True, "code": [0, 1, 1, 2]}
    assert feedback(p2, [1, 0, 3, 3]) == (0, 2)
    print("mastermind self-check OK", p["code"])
