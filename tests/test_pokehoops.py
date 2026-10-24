"""Poké Hoops: seeded draft replay, server-side season verification, leaderboard, web endpoints.
Runs the real vendored JS engine under node (shared/pokehoops_cli.mjs)."""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db.queries as q  # noqa: E402
import db.schema as sch  # noqa: E402
import web.g_pokehoops as gp  # noqa: E402
from shared import pokehoops as ph  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

ROOT = Path(__file__).resolve().parent.parent
LEGENDS = {144, 145, 146, 150, 151}


def _run(coro):
    return asyncio.run(coro)


def _fresh_db():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")


class _Req:
    pass


async def _draft(seed: int, choose=0) -> list[int]:
    picks: list[int] = []
    while (pack := await ph.next_pack(seed, picks)) is not None:
        picks.append(pack[choose if isinstance(choose, int) else choose(len(picks))]["n"])
    return picks


def test_packs_are_seeded_and_legend_first():
    async def go():
        a = await ph.next_pack(4242, [])
        b = await ph.next_pack(4242, [])
        assert [c["n"] for c in a] == [c["n"] for c in b]          # same seed → same offers
        assert len(a) == 3 and {c["n"] for c in a} <= LEGENDS        # pick 1: three Legendaries
        assert all(c["move"] and c["pos"] in ("PG", "SG", "SF", "PF", "C") for c in a)
        picks = await _draft(4242, choose=2)
        assert len(picks) == 5 and len(set(picks)) == 5
        assert picks[0] in LEGENDS and not (set(picks[1:]) & LEGENDS)  # one Legendary, then the rest
        assert await ph.next_pack(4242, picks) is None
        # different seeds give different drafts (overwhelmingly)
        assert len({tuple(await _draft(s)) for s in range(1, 6)}) > 1
    _run(go())


def test_illegal_picks_rejected():
    async def go():
        legal = await _draft(99)
        offered = {c["n"] for c in await ph.next_pack(99, [])}
        bad_first = next(n for n in sorted(LEGENDS) if n not in offered)
        cases = [
            [bad_first] + legal[1:],               # a Legendary that wasn't in the first pack
            legal[:4] + [legal[0]],                # duplicate
            legal[:4] + [151 if legal[0] != 151 else 150],  # second Legendary
            legal[:4] + [0],                       # not a dex number at all
        ]
        for picks in cases:
            with pytest.raises(ph.IllegalDraft):
                await ph.sim_season(99, picks, 1)
        with pytest.raises(ph.IllegalDraft):
            await ph.sim_season(99, legal[:4], 1)  # incomplete
        with pytest.raises(ph.IllegalDraft):
            await ph.next_pack(99, ["150"])         # wrong type
    _run(go())


def test_season_is_deterministic_and_well_formed():
    async def go():
        picks = await _draft(7)
        a = await ph.sim_season(7, picks, 123)
        b = await ph.sim_season(7, picks, 123)
        assert a == b
        assert a["wins"] + a["losses"] == 82 and len(a["strip"]) == 82
        assert a["strip"].count("W") == a["wins"]
        assert sorted(a["lineup"]) == sorted(picks)
        assert a["grade"] in {"82-0!!", "A+", "A", "B+", "B", "C", "D", "F"}
        assert a["top_scorer"]["n"] in picks and a["top_scorer"]["led"] > 0
        c = await ph.sim_season(7, picks, 124)
        assert c["strip"] != a["strip"]             # the season seed matters
    _run(go())


def test_finish_season_records_and_ranks(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        seeds = iter([1001, 1002, 1003])
        monkeypatch.setattr(ph, "new_seed", lambda: next(seeds))
        picks = await _draft(5)
        r1 = await ph.finish_season("alice", 5, picks, "web")
        assert r1["is_new_best"] and r1["prev_best"] is None and r1["rank"] == 1
        assert r1["season_seed"] == 1001 and r1["coins"] == 25
        # server's numbers = a fresh replay of the same seeds (what the page shows)
        assert (await ph.sim_season(5, picks, 1001))["wins"] == r1["wins"]
        r2 = await ph.finish_season("alice", 5, picks, "discord")
        assert r2["prev_best"] == r1["wins"] and r2["is_new_best"] == (r2["wins"] > r1["wins"])
        await ph.finish_season("bob", 5, picks, "web")
        lb = await q.get_pokehoops_leaderboard()
        assert len(lb) == 2                              # one row per player: their best
        alice = next(r for r in lb if r["discord_user"] == "alice")
        assert alice["runs"] == 2 and alice["wins"] == max(r1["wins"], r2["wins"])
        assert lb[0]["wins"] >= lb[1]["wins"]
        assert {await q.get_pokehoops_rank("alice"), await q.get_pokehoops_rank("bob")} == {1, 2}
        assert await q.get_pokehoops_rank("nobody") is None
        row = await q.get_pokehoops_season(r1["id"])
        assert row["picks"] == picks and row["summary"]["strip"] == r1["strip"]
    _run(go())


def test_web_start_submit_flow(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(gp.auth, "read_session", lambda r: {"id": "p1"})
        start = await gp.start(_Req())
        picks = await _draft(start["seed"], choose=1)
        res = await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=picks))
        assert res["wins"] + res["losses"] == 82 and res["picks"] == picks
        # the client-side replay must reproduce the server's season from the returned seeds
        assert (await ph.sim_season(start["seed"], picks, res["season_seed"]))["strip"] == res["strip"]
        again = await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=picks))
        assert again.status_code == 400                     # single-use token
        lb = await gp.leaderboard(_Req())
        assert lb["top"][0]["me"] and lb["top"][0]["wins"] == res["wins"]
        share = await gp.season(res["id"])
        assert share["season_seed"] == res["season_seed"] and share["lineup"] == res["lineup"]
        assert (await gp.season(999999)).status_code == 404
    _run(go())


def test_web_rejects_bad_drafts(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(gp.auth, "read_session", lambda r: {"id": "p1"})
        start = await gp.start(_Req())
        res = await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=[150, 1, 4, 7, 25]))
        assert res.status_code == 400                       # (almost surely) not what was offered
        assert await q.get_pokehoops_leaderboard() == []
        # someone else's token
        start = await gp.start(_Req())
        monkeypatch.setattr(gp.auth, "read_session", lambda r: {"id": "mallory"})
        picks = await _draft(start["seed"])
        assert (await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=picks))).status_code == 400
        monkeypatch.setattr(gp.auth, "read_session", lambda r: None)
        assert (await gp.start(_Req())).status_code == 401
    _run(go())


def test_engine_missing_is_503(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(gp.auth, "read_session", lambda r: {"id": "p1"})
        start = await gp.start(_Req())
        picks = await _draft(start["seed"])
        monkeypatch.setenv("POKEHOOPS_NODE", "/nonexistent/node")
        res = await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=picks))
        assert res.status_code == 503
    _run(go())


TV = Path.home() / "code/tv"


@pytest.mark.skipif(not (TV / ".git").exists(), reason="TV repo not checked out here")
def test_vendored_engine_is_unmodified_tv_revision():
    # vendored files must be byte-identical to the TV commit recorded in web/static/pokehoops/VERSION
    r = subprocess.run([str(ROOT / "scripts/sync-pokehoops.sh"), "--check"], capture_output=True, text=True,
                       env={**os.environ, "TV_REPO": str(TV)})
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(not (TV / ".git").exists(), reason="TV repo not checked out here")
def test_tierpack_matches_tv_game_js():
    # pack generation must be the TV's tierPack, character for character, at the vendored revision
    import re
    rev = (ROOT / "web/static/pokehoops/VERSION").read_text().strip()
    tv = subprocess.run(["git", "-C", str(TV), "show", f"{rev}:games/pokehoops/game.js"],
                        capture_output=True, text=True, check=True).stdout
    grab = lambda src: re.search(r"^function tierPack\(.*?^}$", src, re.S | re.M).group(0)
    assert grab((ROOT / "web/static/pokehoops-draft.js").read_text()) == grab(tv)
