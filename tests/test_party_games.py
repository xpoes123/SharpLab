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
