"""Tests for the popularity-ordered ELO leaderboard query."""
from __future__ import annotations

import asyncio

import pytest

import db.schema as _schema
import db.queries as _queries


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    orig_s, orig_q = _schema.DB_PATH, _queries.DB_PATH
    _schema.DB_PATH = _queries.DB_PATH = db_path
    _run(_schema.init_db())
    yield db_path
    _schema.DB_PATH, _queries.DB_PATH = orig_s, orig_q


async def _seed(game: str, user: str, n_games: int) -> None:
    await _queries.get_elo_rating(user, game)  # create row
    for _ in range(n_games):
        await _queries.update_elo_rating(user, game, 1000.0, "win", 1000.0)


def test_popularity_orders_by_total_plays_then_players(tmp_db) -> None:
    async def go():
        # chess: 1 player × 5 games = 5 plays
        await _seed("chess", "A", 5)
        # wordle: 3 players × 1 game = 3 plays, 3 players
        for u in ("A", "B", "C"):
            await _seed("wordle", u, 1)
        # pokemon: 3 plays total but only 2 players
        await _seed("pokemon", "A", 2)
        await _seed("pokemon", "B", 1)
        return await _queries.get_elo_game_popularity()

    pop = _run(go())
    games = [p["game"] for p in pop]
    # chess most plays; wordle & pokemon tie on plays → more players first
    assert games == ["chess", "wordle", "pokemon"]
    by_game = {p["game"]: p for p in pop}
    assert by_game["chess"]["total_plays"] == 5
    assert by_game["chess"]["players"] == 1
    assert by_game["wordle"]["total_plays"] == 3
    assert by_game["wordle"]["players"] == 3
    assert by_game["pokemon"]["total_plays"] == 3
    assert by_game["pokemon"]["players"] == 2


def test_popularity_empty_when_no_games(tmp_db) -> None:
    assert _run(_queries.get_elo_game_popularity()) == []
