"""Pure Bingo logic shared between the Discord cog and the web game engine."""

import random
from dataclasses import dataclass

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PLAYERS = 10
CARD_PRICE = 0
MAX_CARDS = 5
MIN_PLAYERS = 1
CALL_INTERVAL = 2.5

BINGO_RANGES: list[tuple[str, range]] = [
    ("B", range(1, 16)),
    ("I", range(16, 31)),
    ("N", range(31, 46)),
    ("G", range(46, 61)),
    ("O", range(61, 76)),
]
BINGO_LETTERS = ["B", "I", "N", "G", "O"]


# ── Patterns ─────────────────────────────────────────────────────────────────


def make_target(cells: list[tuple[int, int]]) -> list[list[bool]]:
    grid = [[False] * 5 for _ in range(5)]
    for r, c in cells:
        grid[r][c] = True
    return grid


@dataclass
class BingoPattern:
    name: str
    emoji: str
    description: str
    target: list[list[bool]]

    def check_card(self, grid_marked: list[list[bool]]) -> bool:
        for r in range(5):
            for c in range(5):
                if self.target[r][c] and not grid_marked[r][c]:
                    return False
        return True

    def progress(self, grid_marked: list[list[bool]]) -> tuple[int, int]:
        total = marked = 0
        for r in range(5):
            for c in range(5):
                if self.target[r][c]:
                    total += 1
                    if grid_marked[r][c]:
                        marked += 1
        return marked, total

    def target_as_list(self) -> list[list[bool]]:
        return self.target


BINGO_PATTERNS: list[BingoPattern] = [
    BingoPattern("Four Corners", "\U0001f4d0", "Mark all four corners",
                 make_target([(0, 0), (0, 4), (4, 0), (4, 4)])),
    BingoPattern("X", "\u274c", "Complete both diagonals",
                 make_target([(0, 0), (1, 1), (2, 2), (3, 3), (4, 4),
                              (0, 4), (1, 3), (3, 1), (4, 0)])),
    BingoPattern("Plus", "\u2795", "Fill the center row and center column",
                 make_target([(0, 2), (1, 2), (2, 0), (2, 1), (2, 2), (2, 3), (2, 4),
                              (3, 2), (4, 2)])),
    BingoPattern("Diamond", "\U0001f48e", "Complete the diamond shape",
                 make_target([(0, 2), (1, 1), (1, 3), (2, 0), (2, 2), (2, 4),
                              (3, 1), (3, 3), (4, 2)])),
    BingoPattern("T Shape", "\u2b06\ufe0f", "Fill the top row and center column",
                 make_target([(0, 0), (0, 1), (0, 2), (0, 3), (0, 4),
                              (1, 2), (2, 2), (3, 2), (4, 2)])),
    BingoPattern("L Shape", "\u2199\ufe0f", "Fill the left column and bottom row",
                 make_target([(0, 0), (1, 0), (2, 0), (3, 0), (4, 0),
                              (4, 1), (4, 2), (4, 3), (4, 4)])),
]


def pick_pattern(last_idx: int = -1) -> tuple[BingoPattern, int]:
    choices = list(range(len(BINGO_PATTERNS)))
    if last_idx >= 0 and len(choices) > 1:
        choices.remove(last_idx)
    idx = random.choice(choices)
    return BINGO_PATTERNS[idx], idx


# ── Card generation ──────────────────────────────────────────────────────────


def generate_card() -> tuple[list[list[int]], list[list[bool]]]:
    """Returns (grid, marked) — both 5x5. Center is free space."""
    grid: list[list[int]] = [[0] * 5 for _ in range(5)]
    for col_idx, (_, rng) in enumerate(BINGO_RANGES):
        nums = random.sample(list(rng), 5)
        for row in range(5):
            grid[row][col_idx] = nums[row]
    grid[2][2] = 0
    marked = [[False] * 5 for _ in range(5)]
    marked[2][2] = True
    return grid, marked


def mark_card(grid: list[list[int]], marked: list[list[bool]], number: int) -> bool:
    for row in range(5):
        for col in range(5):
            if grid[row][col] == number:
                marked[row][col] = True
                return True
    return False


def number_to_bingo(n: int) -> str:
    for letter, rng in BINGO_RANGES:
        if n in rng:
            return f"{letter}{n}"
    return str(n)
