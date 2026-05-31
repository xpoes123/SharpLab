"""Smoke tests for /cluemaster and /imposter — categories, matchers, dataclasses."""

import asyncio

import pytest

from bot.cogs._party_categories import (
    CATEGORIES, DEFAULT_CATEGORY, category_options, check_answer, get_category,
)


def test_default_category_present() -> None:
    assert DEFAULT_CATEGORY in CATEGORIES
    label, emoji, items = get_category(DEFAULT_CATEGORY)
    assert label and emoji and items
    assert all(isinstance(name, str) and name for name, _alts in items)


def test_unknown_category_falls_back_to_default() -> None:
    assert get_category("nope") == get_category(DEFAULT_CATEGORY)


def test_category_options_marks_default() -> None:
    opts = category_options(DEFAULT_CATEGORY)
    defaults = [v for label, v, emoji, is_def in opts if is_def]
    assert defaults == [DEFAULT_CATEGORY]


def test_check_answer_exact_and_alias() -> None:
    item = ("LeBron James", ["LeBron", "King James"])
    assert check_answer("lebron james", item)
    assert check_answer("LeBron", item)
    assert check_answer("king james", item)


def test_check_answer_last_name_only() -> None:
    item = ("Stephen Curry", [])
    assert check_answer("curry", item)


def test_check_answer_rejects_short_or_unrelated() -> None:
    item = ("Kevin Durant", ["KD"])
    assert not check_answer("ab", item)
    assert not check_answer("xylophone", item)


def test_check_answer_fuzzy_typo() -> None:
    # 1-char typo on a long-enough name should still match
    item = ("Giannis Antetokounmpo", ["Giannis"])
    assert check_answer("giannis", item)
    assert check_answer("gianis", item)  # missing "n"


def test_cluemaster_imports_and_basic_flow() -> None:
    from bot.cogs.cluemaster import (
        CMTable, _betting_embed, _pick_item, MIN_PLAYERS, MAX_PLAYERS,
    )
    table = CMTable(channel_id=1, host_id=1, host_name="host")
    embed = _betting_embed(table)
    assert "Clue Master" in embed.title
    used: set[str] = set()
    item = _pick_item(DEFAULT_CATEGORY, used)
    assert item[0] in used
    assert MIN_PLAYERS >= 2
    assert MAX_PLAYERS >= MIN_PLAYERS


def test_qb_clean_answer_extracts_primary_and_aliases() -> None:
    from bot.cogs._party_categories import _clean_qb_answer

    name, alts = _clean_qb_answer(
        "<b><u>L-DOPA</u></b> [accept <b><u>levodopa</u></b>]",
        "L-DOPA [accept levodopa]",
    )
    assert name == "L-DOPA"
    assert "levodopa" in alts

    name, alts = _clean_qb_answer(
        "<b><u>Parkinson</u></b>'s disease [accept any answer containing \"Parkinson\"]",
        "Parkinson's disease [accept any answer containing \"Parkinson\"]",
    )
    assert name == "Parkinson's disease"
    assert "Parkinson" in alts  # surname alias makes it guessable


def test_qb_clean_answer_rejects_junk() -> None:
    from bot.cogs._party_categories import _clean_qb_answer

    # Bare number words make poor guess targets
    assert _clean_qb_answer("<b><u>two</u></b>", "two") is None
    # Too short
    assert _clean_qb_answer("<b><u>pi</u></b>", "pi") is None


@pytest.fixture()
def tmp_db(tmp_path):
    import db.schema as _schema
    import db.queries as _queries

    db_path = str(tmp_path / "test.db")
    orig_s, orig_q = _schema.DB_PATH, _queries.DB_PATH
    _schema.DB_PATH = _queries.DB_PATH = db_path
    asyncio.run(_schema.init_db())
    yield db_path
    _schema.DB_PATH, _queries.DB_PATH = orig_s, orig_q


def test_qb_answers_persist_and_accumulate(tmp_db) -> None:
    import db.queries as _queries

    async def go():
        await _queries.upsert_qb_answers("science", [("Entropy", ["disorder"]), ("Photon", [])])
        await _queries.upsert_qb_answers("science", [("Quark", ["quarks"])])  # accumulates
        return dict(await _queries.get_qb_answers("science"))

    pool = asyncio.run(go())
    assert set(pool) == {"Entropy", "Photon", "Quark"}  # pool grows, not replaced
    assert pool["Entropy"] == ["disorder"]


def test_qb_answers_upsert_refreshes_aliases(tmp_db) -> None:
    import db.queries as _queries

    async def go():
        await _queries.upsert_qb_answers("science", [("Entropy", ["disorder"])])
        await _queries.upsert_qb_answers("science", [("Entropy", ["disorder", "S"])])
        rows = await _queries.get_qb_answers("science")
        return rows

    rows = asyncio.run(go())
    assert len(rows) == 1  # same answer, no duplicate row
    assert dict(rows)["Entropy"] == ["disorder", "S"]  # aliases refreshed


def test_qb_load_cache_from_db_warms_pool(tmp_db) -> None:
    import db.queries as _queries
    from bot.cogs import _party_categories as pc

    async def go():
        await _queries.upsert_qb_answers("science", [("Boson", []), ("Lepton", ["leptons"])])
        pc._SCIENCE_QB_CACHE.clear()
        n = await pc._load_science_cache_from_db()
        return n, list(pc._SCIENCE_QB_CACHE)

    try:
        n, cache = asyncio.run(go())
        assert n == 2
        assert {name for name, _alts in cache} == {"Boson", "Lepton"}
    finally:
        pc._SCIENCE_QB_CACHE.clear()


def test_qb_science_falls_back_when_cache_empty() -> None:
    from bot.cogs import _party_categories as pc
    from bot.cogs._party_categories import get_category, SCIENCE_QB_KEY

    pc._SCIENCE_QB_CACHE.clear()
    _label, _emoji, items = get_category(SCIENCE_QB_KEY)
    assert items is pc._SCIENCE_BASIC  # fallback when nothing fetched


def test_qb_science_serves_cache_when_populated() -> None:
    from bot.cogs import _party_categories as pc
    from bot.cogs._party_categories import get_category, SCIENCE_QB_KEY

    pc._SCIENCE_QB_CACHE.clear()
    pc._SCIENCE_QB_CACHE.append(("Entropy", ["disorder"]))
    try:
        _label, _emoji, items = get_category(SCIENCE_QB_KEY)
        assert items == [("Entropy", ["disorder"])]
    finally:
        pc._SCIENCE_QB_CACHE.clear()


def test_cluemaster_total_rounds_scale_with_players() -> None:
    from bot.cogs.cluemaster import _compute_total_rounds, MAX_ROUNDS_CAP

    # Each player is clue master rounds_per_player times.
    assert _compute_total_rounds(2, 4) == 8
    assert _compute_total_rounds(1, 3) == 3
    assert _compute_total_rounds(3, 5) == 15
    # Capped for big tables.
    assert _compute_total_rounds(4, 8) == MAX_ROUNDS_CAP
    # Never zero, even with a degenerate table.
    assert _compute_total_rounds(2, 0) == 1


def test_imposter_total_rounds_scale_and_cap() -> None:
    from bot.cogs.imposter import _compute_total_rounds, MAX_ROUNDS_CAP

    assert _compute_total_rounds(1, 3) == 3   # each of 3 players imposter once
    assert _compute_total_rounds(2, 5) == 10
    assert _compute_total_rounds(3, 10) == MAX_ROUNDS_CAP  # capped
    assert _compute_total_rounds(1, 0) == 1   # never zero


def test_imposter_rotation_is_fair() -> None:
    """Over total_rounds, the rotated imposter role is spread evenly."""
    from bot.cogs.imposter import _compute_total_rounds

    order = [10, 20, 30, 40]
    total = _compute_total_rounds(2, len(order))  # 8 rounds
    counts: dict[int, int] = {uid: 0 for uid in order}
    for rnd in range(1, total + 1):
        imposter = order[(rnd - 1) % len(order)]
        counts[imposter] += 1
    assert set(counts.values()) == {2}  # each player imposter exactly twice


def test_imposter_setup_round_resets_state() -> None:
    from bot.cogs.imposter import ImpTable, ImpPlayer, ImposterLobbyView

    table = ImpTable(channel_id=1, host_id=1, host_name="h")
    for uid in (1, 2, 3):
        table.players[uid] = ImpPlayer(user_id=uid, display_name=f"p{uid}", score=5)
    table.imposter_order = [1, 2, 3]
    table.total_rounds = 3
    view = ImposterLobbyView.__new__(ImposterLobbyView)
    view.table = table
    view._setup_round(2)
    assert table.round_num == 2
    assert table.imposter_id == 2  # order[(2-1) % 3]
    assert table.answer
    assert all(not p.role_seen and p.hint is None and p.voted_for is None
               for p in table.players.values())
    assert all(p.score == 5 for p in table.players.values())  # scores carry over


def test_imposter_imports_and_setup_payouts() -> None:
    from bot.cogs.imposter import (
        ImpTable, ImpPlayer, _betting_embed, _pick_secret,
        MIN_PLAYERS, MAX_PLAYERS, PLAYER_WIN_POINTS, IMPOSTER_WIN_POINTS,
    )
    table = ImpTable(channel_id=1, host_id=1, host_name="host")
    embed = _betting_embed(table)
    assert "Imposter" in embed.title
    secret = _pick_secret(DEFAULT_CATEGORY)
    assert isinstance(secret, str) and secret
    assert MIN_PLAYERS >= 3
    assert MAX_PLAYERS >= MIN_PLAYERS
    assert PLAYER_WIN_POINTS > 0 and IMPOSTER_WIN_POINTS > 0


def test_imposter_replaceable_when_idle() -> None:
    """Stuck imposter tables must be replaceable after the idle window."""
    import time as _t
    from bot.cogs.imposter import ImpTable, ImposterCog, IDLE_REPLACEABLE_SECS

    cog = ImposterCog.__new__(ImposterCog)
    cog.active_tables = {}
    table = ImpTable(channel_id=42, host_id=1, host_name="host")
    table.last_activity_at = _t.monotonic() - (IDLE_REPLACEABLE_SECS + 5)
    replaceable, secs_left = cog._is_replaceable(table)
    assert replaceable
    assert secs_left == 0


def test_cluemaster_replaceable_when_closed() -> None:
    from bot.cogs.cluemaster import CMTable, CluemasterCog

    cog = CluemasterCog.__new__(CluemasterCog)
    cog.active_tables = {}
    table = CMTable(channel_id=42, host_id=1, host_name="host")
    table.phase = "closed"
    replaceable, _ = cog._is_replaceable(table)
    assert replaceable


def test_games_listing_shows_correct_invocation() -> None:
    """Most games launch via /game; only parameterized ones show a direct slash."""
    from bot.cogs.casino import _game_invocation, _DIRECT_SLASH_GAMES, CASINO_GAMES
    from bot.cogs.game_menu import PARAMETERIZED_SHORTCUTS

    # Keep the two sets in sync — they live in different cogs to avoid a circular import
    assert _DIRECT_SLASH_GAMES == PARAMETERIZED_SHORTCUTS

    for name, *_ in CASINO_GAMES:
        inv = _game_invocation(name)
        if name in _DIRECT_SLASH_GAMES:
            assert inv == f"/{name}", f"{name}: expected direct slash, got {inv}"
        else:
            assert inv == f"/game {name}", f"{name}: expected /game launcher, got {inv}"

    assert _game_invocation("cluemaster") == "/game cluemaster"
    assert _game_invocation("imposter") == "/game imposter"
    assert _game_invocation("penalties") == "/penalties"


def test_cluemaster_force_close_cancels_task() -> None:
    from bot.cogs.cluemaster import CMTable, CluemasterCog

    async def _run() -> None:
        cog = CluemasterCog.__new__(CluemasterCog)
        cog.active_tables = {}
        table = CMTable(channel_id=99, host_id=1, host_name="host")
        cog.active_tables[99] = table

        async def _never() -> None:
            await asyncio.sleep(60)

        table.game_task = asyncio.create_task(_never())
        await cog._force_close_table(table)
        # Task cancellation propagates on next loop tick
        await asyncio.sleep(0)
        assert table.game_task.cancelled() or table.game_task.done()
        assert 99 not in cog.active_tables
        assert table.phase == "closed"

    asyncio.run(_run())
