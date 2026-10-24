"""Poké Hoops Discord cog: button draft end-to-end (real engine via node), embeds, registration."""

import asyncio
import os
import shutil
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db.queries as q  # noqa: E402
import db.schema as sch  # noqa: E402
from bot.cogs import pokehoops as cogmod  # noqa: E402
from shared import pokehoops as ph  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _interaction(uid=42):
    it = MagicMock()
    it.user.id = uid
    it.response.edit_message = AsyncMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.edit_original_response = AsyncMock()
    return it


def test_button_draft_plays_and_records_a_season():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")

    async def go():
        await sch.init_db()
        cog = cogmod.PokeHoopsCog(MagicMock())
        user = MagicMock(id=42, display_name="dj")
        cog._active.add(42)
        seed = 31337
        view = cogmod.DraftView(cog, user, seed, await ph.next_pack(seed, []))
        assert len(view.children) == 3 and {c["n"] for c in view.pack} <= {144, 145, 146, 150, 151}
        embeds = cogmod.pick_embeds("dj", [], view.pack)
        assert len(embeds) == 4 and "Legendary" in embeds[0].title

        # someone else can't click
        assert await view.interaction_check(_interaction(uid=7)) is False
        assert await view.interaction_check(_interaction(uid=42)) is True

        for _ in range(4):
            it = _interaction()
            await view.choose(it, view.pack[0])
            assert it.response.edit_message.await_args.kwargs["view"] is view
        last = _interaction()
        await view.choose(last, view.pack[1])
        final = last.edit_original_response.await_args.kwargs["embed"]
        assert "dj's season" in final.title and 42 not in cog._active

        lb = await q.get_pokehoops_leaderboard()
        assert len(lb) == 1 and lb[0]["discord_user"] == "42" and lb[0]["source"] == "discord"
        assert f"{lb[0]['wins']}–{lb[0]['losses']}" in final.title
        # the recorded season replays identically (what the web share link shows)
        again = await ph.sim_season(seed, lb[0]["picks"], lb[0]["season_seed"])
        assert again["strip"] == lb[0]["summary"]["strip"]

    asyncio.run(go())


def test_strip_lines_wraps_82_games():
    out = cogmod.strip_lines("W" * 41 + "L" * 41)
    assert out.split("\n") == ["🟩" * 41, "🟥" * 41]


def test_registered_everywhere():
    from bot.cogs.casino import CASINO_GAMES, GAME_LABELS, GAME_RULES
    from bot.cogs.game_menu import GAME_DISPATCH
    from pathlib import Path
    main_src = (Path(__file__).resolve().parent.parent / "bot/main.py").read_text()  # importing it needs Discord env
    assert GAME_DISPATCH["pokehoops"] == ("PokeHoopsCog", "pokehoops")
    assert any(g[0] == "pokehoops" for g in CASINO_GAMES)
    assert "pokehoops" in GAME_LABELS and "pokehoops" in GAME_RULES
    assert '"bot.cogs.pokehoops"' in main_src
