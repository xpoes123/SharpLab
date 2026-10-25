"""Poké Hoops Discord cog: button draft + select-menu lineup end-to-end (real engine via node), embeds, registration."""

import asyncio
import os
import re
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


LEGENDS = {144, 145, 146, 150, 151, 243, 244, 245, 249, 250, 251}


def _interaction(uid=42):
    it = MagicMock()
    it.user.id = uid
    it.response.edit_message = AsyncMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.edit_original_response = AsyncMock()
    it.followup.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    return it


def _all_text(embeds) -> str:
    return " ".join(str(e.to_dict()) for e in embeds)


async def _draft_to_lineup(cog, seed=31337):
    user = MagicMock(id=42, display_name="dj")
    cog._active.add(42)
    view = cogmod.DraftView(cog, user, seed, await ph.next_pack(seed, []))
    view.message = MagicMock(edit=AsyncMock())
    assert len(view.children) == 3 and {c["n"] for c in view.pack} <= LEGENDS
    embeds = cogmod.pick_embeds("dj", [], view.pack)
    assert len(embeds) == 4 and "Legendary" in embeds[0].title and "of 8" in embeds[0].title
    assert "OVR" not in _all_text(embeds) and all("plays" in e.title for e in embeds[1:])
    assert all(not re.search(r"\d\d", b.label) for b in view.children)  # no ratings on the buttons
    assert await view.interaction_check(_interaction(uid=7)) is False   # someone else can't click
    assert await view.interaction_check(_interaction(uid=42)) is True
    for _ in range(7):
        it = _interaction()
        await view.choose(it, view.pack[0])
        assert it.response.edit_message.await_args.kwargs["view"] is view
    last = _interaction()
    await view.choose(last, view.pack[1])
    starters = last.response.edit_message.await_args.kwargs["view"]
    assert isinstance(starters, cogmod.StartersView) and len(starters.children) == 5
    bench = last.followup.send.await_args.kwargs["view"]
    assert isinstance(bench, cogmod.BenchView) and starters.bench is bench
    bench.message = last.followup.send.return_value
    return view, starters, bench


def test_draft_then_coach_then_season_is_recorded():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")

    async def go():
        await sch.init_db()
        cog = cogmod.PokeHoopsCog(MagicMock())
        draft, starters, bench = await _draft_to_lineup(cog)
        picks = [c["n"] for c in draft.picks]
        coach = starters.coach
        # untouched lineup = draft order, bench backs PG/SG/SF (nothing auto-optimized)
        assert coach.team() == {"five": picks[:5], "bench": picks[5:], "backs": [0, 1, 2]}
        # each starter select lists all eight, current starter preselected; no ratings anywhere
        for spot, sel in enumerate(starters.children):
            assert len(sel.options) == 8 and [o.value for o in sel.options if o.default] == [str(picks[spot])]
            assert all("OVR" not in o.label for o in sel.options)
        assert [len(c.options) for c in bench.children[:3]] == [5, 5, 5] and bench.children[3].label == "Lock in lineup"

        # put a reserve (picks[6]) at C: the old C (picks[4]) drops into that bench slot
        it = _interaction()
        await starters.set_starter(it, 4, picks[6])
        assert coach.five == picks[:4] + [picks[6]] and coach.bench == [picks[5], picks[4], picks[7]]
        bench.message.edit.assert_awaited()                  # the bench message re-rendered with the new reserves
        # picking someone already starting swaps the two spots (no duplicates possible)
        await starters.set_starter(_interaction(), 0, picks[1])
        assert coach.five[:2] == [picks[1], picks[0]] and coach.problem() is None
        # reserves choose their spots
        await bench.set_backs(_interaction(), 0, 4)
        await bench.set_backs(_interaction(), 2, 4)
        assert coach.backs == [4, 1, 4]
        embed = cogmod.lineup_embed("dj", coach)
        assert "Bench:" in embed.description and "(backs C)" in embed.description

        # a broken arrangement is re-prompted, not submitted
        coach.five[1] = coach.five[0]
        it = _interaction()
        await starters.lock_in(it)
        assert it.response.send_message.await_args.kwargs.get("ephemeral") and not starters.finished
        coach.five[1] = picks[0]
        team = coach.team()

        lock = _interaction()
        await starters.lock_in(lock)
        final = lock.edit_original_response.await_args.kwargs["embed"]
        assert "dj's season" in final.title and 42 not in cog._active
        assert "Bench:" in final.description and "OVR" not in str(final.to_dict())

        lb = await q.get_pokehoops_leaderboard()
        assert len(lb) == 1 and lb[0]["discord_user"] == "42" and lb[0]["source"] == "discord"
        assert lb[0]["lineup"] == team and f"{lb[0]['wins']}–{lb[0]['losses']}" in final.title
        # the recorded season replays identically (what the web share link shows)
        again = await ph.sim_season(31337, lb[0]["picks"], team, lb[0]["season_seed"])
        assert again["strip"] == lb[0]["summary"]["strip"]

    asyncio.run(go())


def test_coach_rejects_bad_arrangements():
    cards = [{"n": n, "name": str(n), "plays": ["PG"], "archetype": ""} for n in range(1, 9)]
    c = cogmod.Coach(cards)
    assert c.problem() is None
    c.put(0, 99)                                    # not drafted: ignored
    assert c.five == [1, 2, 3, 4, 5]
    c.backs = [0, 1, 5]
    assert "back up" in c.problem()
    c.backs = [0, 1, 2]
    c.bench[0] = 1
    assert "exactly once" in c.problem()


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
