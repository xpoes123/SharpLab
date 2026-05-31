"""Tests for the daily pick'em: scoring + DB lifecycle."""
from __future__ import annotations

import asyncio

import pytest

import db.schema as _schema
import db.queries as _queries
from bot.cogs.pickem import compute_pickem_standings, _winner_from_scores


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


# ── Scoring ───────────────────────────────────────────────────────────────────


def test_standings_correct_accuracy_and_streak():
    rows = [
        {"discord_user": "A", "correct": 1, "start_time": "1"},
        {"discord_user": "A", "correct": 1, "start_time": "2"},
        {"discord_user": "A", "correct": 0, "start_time": "3"},
        {"discord_user": "A", "correct": 1, "start_time": "4"},
        {"discord_user": "B", "correct": 1, "start_time": "1"},
        {"discord_user": "B", "correct": 1, "start_time": "2"},
        {"discord_user": "B", "correct": 1, "start_time": "3"},
    ]
    s = compute_pickem_standings(rows)
    assert s["A"]["correct"] == 3
    assert s["A"]["total"] == 4
    assert s["A"]["points"] == 4
    assert s["A"]["accuracy"] == 0.75
    # B and A tie on correct, but B's unbroken streak earns more points
    assert s["B"]["correct"] == 3
    assert s["B"]["points"] == 6  # 1 + 2 + 3
    assert s["B"]["points"] > s["A"]["points"]


def test_market_pnl_rewards_underdogs():
    # Both pick correctly once. U bet a 25% underdog; F bet a 80% favorite.
    rows = [
        {"discord_user": "U", "correct": 1, "pick": "away", "home_prob": 0.75, "away_prob": 0.25},
        {"discord_user": "F", "correct": 1, "pick": "home", "home_prob": 0.80, "away_prob": 0.20},
    ]
    s = compute_pickem_standings(rows)
    # underdog payout 1/0.25 - 1 = +3.0u ; favorite 1/0.80 - 1 = +0.25u
    assert round(s["U"]["units"], 2) == 3.0
    assert round(s["F"]["units"], 2) == 0.25
    assert s["U"]["units"] > s["F"]["units"]


def test_market_pnl_loss_costs_a_unit():
    rows = [{"discord_user": "X", "correct": 0, "pick": "home", "home_prob": 0.9, "away_prob": 0.1}]
    s = compute_pickem_standings(rows)
    assert s["X"]["units"] == -1.0


def test_standings_missing_probs_default_even():
    # No prob data → treated as a coin flip (1/0.5 - 1 = +1 on a correct pick)
    rows = [{"discord_user": "A", "correct": 1, "pick": "home"}]
    s = compute_pickem_standings(rows)
    assert s["A"]["units"] == 1.0


def test_streak_points_capped():
    rows = [{"discord_user": "X", "correct": 1, "start_time": str(i)} for i in range(10)]
    s = compute_pickem_standings(rows)
    # 1+2+3+4+5 then capped at 5 for the remaining five
    assert s["X"]["points"] == (1 + 2 + 3 + 4 + 5) + 5 * 5


def test_winner_from_scores():
    assert _winner_from_scores(110, 99) == "home"
    assert _winner_from_scores(2, 5) == "away"


# ── DB lifecycle ──────────────────────────────────────────────────────────────


def test_pick_record_change_remove(tmp_db):
    async def go():
        await _queries.add_pickem_game("m1", "g1", "nba", "Home", "Away", "2026-01-01T00:00:00Z", "2026-01-01")
        await _queries.record_pickem_pick("m1", "u1", "home")
        first = await _queries.get_pickem_pick("m1", "u1")
        await _queries.record_pickem_pick("m1", "u1", "away")  # change vote
        changed = await _queries.get_pickem_pick("m1", "u1")
        await _queries.remove_pickem_pick("m1", "u1")
        gone = await _queries.get_pickem_pick("m1", "u1")
        return first, changed, gone

    first, changed, gone = _run(go())
    assert first == "home"
    assert changed == "away"  # no duplicate row, vote updated
    assert gone is None


def test_resolve_scores_picks_and_counts(tmp_db):
    async def go():
        await _queries.add_pickem_game("m1", "g1", "nba", "Home", "Away", "2026-01-01T00:00:00Z", "2026-01-01")
        await _queries.record_pickem_pick("m1", "u1", "home")
        await _queries.record_pickem_pick("m1", "u2", "away")
        await _queries.record_pickem_pick("m1", "u3", "home")
        counts = await _queries.get_pickem_vote_counts("m1")
        await _queries.resolve_pickem_game("m1", "home")
        picks = {p["discord_user"]: p["correct"] for p in await _queries.get_pickem_picks_for_message("m1")}
        unresolved = await _queries.get_unresolved_pickem_games()
        return counts, picks, unresolved

    counts, picks, unresolved = _run(go())
    assert counts == {"home": 2, "away": 1}
    assert picks == {"u1": 1, "u2": 0, "u3": 1}  # home pickers correct
    assert unresolved == []  # marked resolved


def test_resolved_picks_ordered_for_streaks(tmp_db):
    async def go():
        # two games at different times; one user picks both
        await _queries.add_pickem_game("mA", "gA", "nba", "H", "A", "2026-01-01T02:00:00Z", "2026-01-01")
        await _queries.add_pickem_game("mB", "gB", "nba", "H", "A", "2026-01-01T01:00:00Z", "2026-01-01")
        await _queries.record_pickem_pick("mA", "u1", "home")
        await _queries.record_pickem_pick("mB", "u1", "away")
        await _queries.resolve_pickem_game("mA", "home")  # correct
        await _queries.resolve_pickem_game("mB", "home")  # u1 picked away -> wrong
        return await _queries.get_pickem_resolved_picks()

    rows = _run(go())
    # ordered by user, then start_time → mB (01:00) before mA (02:00)
    assert [r["correct"] for r in rows] == [0, 1]


def test_lock_lifecycle(tmp_db):
    async def go():
        await _queries.add_pickem_game("m1", "g1", "nba", "H", "A", "2026-01-01T00:00:00Z", "2026-01-01")
        before = await _queries.get_unlocked_pickem_games()
        await _queries.lock_pickem_game("m1")
        after = await _queries.get_unlocked_pickem_games()
        return len(before), len(after)

    before, after = _run(go())
    assert before == 1
    assert after == 0
