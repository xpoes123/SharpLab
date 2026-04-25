"""Shared async helpers for ELO updates. Used by duels, tournaments, pokemon."""

import logging

from db import queries
from shared import elo

log = logging.getLogger(__name__)

# ── Label mapping ────────────────────────────────────────────────────────────

ELO_GAME_LABELS: dict[str, str] = {
    # Duel/tournament mini-games (skill-based)
    "speed_math": "Speed Math",
    "trivia": "Trivia",
    "guess_number": "Guess the Number",
    "tictactoe": "Tic Tac Toe",
    "word_scramble": "Word Scramble",
    "nim": "Nim",
    "battleship": "Battleship",
    "rps": "Rock Paper Scissors",
    # Standalone multiplayer games
    "pokemon": "Pokemon",
    "valorant": "Valorant Guess",
    "math24": "Math 24",
    "mathsprint": "Math Sprint",
    "geography": "Geography",
    "wordle": "Wordle",
    "countdown": "Countdown",
    "quizbowl": "Quiz Bowl",
    "mastermind": "Mastermind",
    "liarsdice": "Liar's Dice",
    "stockguess": "Stock Guess",
    "nba-trivia": "NBA Trivia",
    "nfl-trivia": "NFL Trivia",
    # Web games
    "sudoku": "Sudoku Sprint",
    "figgie": "Figgie",
    "tradingfloor": "Trading Floor",
    "bingo": "Bingo",
    "nbaguess": "NBA Player Guess",
    "minesweeper": "Minesweeper Race",
    "sequence": "Sequence",
    "prisoner": "Prisoner's Dilemma",
    "indian_poker": "Indian Poker",
}

MIN_GAMES_FOR_LEADERBOARD = 5


# ── 1v1 update ───────────────────────────────────────────────────────────────


async def update_elo_1v1(
    winner_id: str,
    loser_id: str,
    game_key: str,
    context: str,
) -> tuple[float, float, float, float]:
    """Update ELO for a 1v1 result.

    Returns (winner_old, winner_new, loser_old, loser_new).
    """
    w_data = await queries.get_elo_rating(winner_id, game_key)
    l_data = await queries.get_elo_rating(loser_id, game_key)

    new_w, new_l = elo.compute_1v1(
        w_data["rating"], l_data["rating"],
        w_data["games_played"], l_data["games_played"],
        winner=1,
    )

    await queries.update_elo_rating(
        winner_id, game_key, new_w, "win",
        max(new_w, w_data["peak_rating"]),
    )
    await queries.update_elo_rating(
        loser_id, game_key, new_l, "loss",
        l_data["peak_rating"],  # can't be a new peak on a loss
    )

    await queries.log_elo_match(
        winner_id, loser_id, game_key, 1.0,
        w_data["rating"], new_w, context,
    )
    await queries.log_elo_match(
        loser_id, winner_id, game_key, 0.0,
        l_data["rating"], new_l, context,
    )

    return w_data["rating"], new_w, l_data["rating"], new_l


async def update_elo_draw(
    p1_id: str,
    p2_id: str,
    game_key: str,
    context: str,
) -> tuple[float, float, float, float]:
    """Update ELO for a draw.

    Returns (p1_old, p1_new, p2_old, p2_new).
    """
    d1 = await queries.get_elo_rating(p1_id, game_key)
    d2 = await queries.get_elo_rating(p2_id, game_key)

    new_1, new_2 = elo.compute_1v1(
        d1["rating"], d2["rating"],
        d1["games_played"], d2["games_played"],
        winner=0,
    )

    await queries.update_elo_rating(
        p1_id, game_key, new_1, "draw",
        max(new_1, d1["peak_rating"]),
    )
    await queries.update_elo_rating(
        p2_id, game_key, new_2, "draw",
        max(new_2, d2["peak_rating"]),
    )

    await queries.log_elo_match(
        p1_id, p2_id, game_key, 0.5,
        d1["rating"], new_1, context,
    )
    await queries.log_elo_match(
        p2_id, p1_id, game_key, 0.5,
        d2["rating"], new_2, context,
    )

    return d1["rating"], new_1, d2["rating"], new_2


# ── Multiplayer update ───────────────────────────────────────────────────────


async def update_elo_multiplayer(
    finish_order: list[int],
    game_key: str,
    context: str,
) -> dict[int, tuple[float, float]]:
    """Update ELO for a multiplayer game.

    finish_order: user IDs from 1st to last place.
    Returns {user_id: (old_rating, new_rating)}.
    """
    if len(finish_order) < 2:
        return {}

    ratings: dict[int, float] = {}
    games_played: dict[int, int] = {}
    for uid in finish_order:
        data = await queries.get_elo_rating(str(uid), game_key)
        ratings[uid] = data["rating"]
        games_played[uid] = data["games_played"]

    new_ratings = elo.compute_multiplayer(ratings, games_played, finish_order)

    changes: dict[int, tuple[float, float]] = {}
    for i, uid in enumerate(finish_order):
        old_r = ratings[uid]
        new_r = new_ratings[uid]
        changes[uid] = (old_r, new_r)

        result_str = "win" if i == 0 else "loss"
        peak_data = await queries.get_elo_rating(str(uid), game_key)
        peak = max(new_r, peak_data["peak_rating"])

        await queries.update_elo_rating(str(uid), game_key, new_r, result_str, peak)
        await queries.log_elo_match(
            str(uid), None, game_key,
            1.0 if i == 0 else 0.0,
            old_r, new_r, context,
        )

    return changes


def fmt_elo_change(old: float, new: float) -> str:
    """Format an ELO change like '1000 → 1016 (+16)'."""
    delta = new - old
    sign = "+" if delta >= 0 else ""
    return f"{old:.0f} → {new:.0f} ({sign}{delta:.0f})"
