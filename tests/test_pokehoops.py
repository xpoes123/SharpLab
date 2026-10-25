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
LEGENDS = {144, 145, 146, 150, 151, 243, 244, 245, 249, 250, 251}


def _run(coro):
    return asyncio.run(coro)


def _fresh_db():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")


class _Req:
    pass


def _team(picks: list[int]) -> dict:
    return ph.default_team(picks)


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
        assert all(c["plays"] and set(c["plays"]) <= {"PG", "SG", "SF", "PF", "C"} for c in a)
        assert all("ovr" not in c for c in a)                       # overall ratings are never sent to players
        picks = await _draft(4242, choose=2)
        assert len(picks) == 8 and len(set(picks)) == 8
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
            legal[:7] + [legal[0]],                # duplicate
            legal[:7] + [151 if legal[0] != 151 else 150],  # second Legendary
            legal[:7] + [0],                       # not a dex number at all
        ]
        for picks in cases:
            with pytest.raises(ph.IllegalDraft):
                await ph.sim_season(99, picks, _team(picks), 1)
        with pytest.raises(ph.IllegalDraft):
            await ph.sim_season(99, legal[:7], _team(legal[:7]), 1)  # incomplete
        with pytest.raises(ph.IllegalDraft):
            await ph.next_pack(99, ["150"])         # wrong type
    _run(go())


def test_illegal_teams_rejected():
    async def go():
        picks = await _draft(99)
        ok = _team(picks)
        assert (await ph.sim_season(99, picks, ok, 1))["team"] == ok
        p = picks
        bad = [
            {"five": [p[0], p[0], p[2], p[3], p[4]], "bench": p[5:], "backs": [0, 1, 2]},    # duplicate starter
            {"five": p[:5], "bench": [p[5], p[6], p[0]], "backs": [0, 1, 2]},                # starter also on bench
            {"five": p[:4] + [1 if 1 not in p else 4], "bench": p[5:], "backs": [0, 1, 2]},  # someone not drafted
            {"five": p[:5], "bench": p[5:], "backs": [0, 1, 5]},                             # backs out of range
            {"five": p[:5], "bench": p[5:], "backs": [0, -1, 2]},                            # backs out of range
            {"five": p[:5], "bench": p[5:], "backs": [0, 1]},                                # a reserve with no spot
            {"five": p[:4], "bench": p[4:], "backs": [0, 1, 2, 3]},                          # four starters
            {"five": p[:5], "bench": p[5:7], "backs": [0, 1]},                               # a drafted player left out
        ]
        for team in bad:
            with pytest.raises(ph.IllegalDraft):
                await ph.sim_season(99, picks, team, 1)
        for team in (None, [1, 2, 3], {"five": p[:5], "bench": p[5:]}, {"five": p[:5], "bench": p[5:], "backs": ["0", 1, 2]}):
            with pytest.raises(ph.IllegalDraft):
                await ph.sim_season(99, picks, team, 1)
        # the coach's arrangement matters: a different lineup is a different season
        alt = {"five": [p[4], p[3], p[2], p[1], p[0]], "bench": p[5:], "backs": [4, 4, 0]}
        assert (await ph.sim_season(99, picks, alt, 1))["strip"] != (await ph.sim_season(99, picks, ok, 1))["strip"]
    _run(go())


def test_season_is_deterministic_and_well_formed():
    async def go():
        picks = await _draft(7)
        team = _team(picks)
        a = await ph.sim_season(7, picks, team, 123)
        b = await ph.sim_season(7, picks, team, 123)
        assert a == b
        assert a["wins"] + a["losses"] == 82 and len(a["strip"]) == 82
        assert a["strip"].count("W") == a["wins"]
        assert a["lineup"] == picks[:5] and a["team"] == team    # draft order until the coach moves anyone
        assert len(a["lineup_cards"]) == 5 and len(a["bench_cards"]) == 3
        assert a["grade"] in {"82-0!!", "A+", "A", "B+", "B", "C", "D", "F"}
        assert a["top_scorer"]["n"] in picks and a["top_scorer"]["led"] > 0
        c = await ph.sim_season(7, picks, team, 124)
        assert c["strip"] != a["strip"]             # the season seed matters
    _run(go())


def test_finish_season_records_and_ranks(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        seeds = iter([1001, 1002, 1003])
        monkeypatch.setattr(ph, "new_seed", lambda: next(seeds))
        picks = await _draft(5)
        team = _team(picks)
        r1 = await ph.finish_season("alice", 5, picks, team, "web")
        assert r1["is_new_best"] and r1["prev_best"] is None and r1["rank"] == 1
        assert r1["season_seed"] == 1001 and r1["coins"] == 25
        # server's numbers = a fresh replay of the same seeds (what the page shows)
        assert (await ph.sim_season(5, picks, team, 1001))["wins"] == r1["wins"]
        r2 = await ph.finish_season("alice", 5, picks, team, "discord")
        assert r2["prev_best"] == r1["wins"] and r2["is_new_best"] == (r2["wins"] > r1["wins"])
        await ph.finish_season("bob", 5, picks, team, "web")
        lb = await q.get_pokehoops_leaderboard()
        assert len(lb) == 2                              # one row per player: their best
        alice = next(r for r in lb if r["discord_user"] == "alice")
        assert alice["runs"] == 2 and alice["wins"] == max(r1["wins"], r2["wins"])
        assert lb[0]["wins"] >= lb[1]["wins"]
        assert {await q.get_pokehoops_rank("alice"), await q.get_pokehoops_rank("bob")} == {1, 2}
        assert await q.get_pokehoops_rank("nobody") is None
        row = await q.get_pokehoops_season(r1["id"])
        assert row["picks"] == picks and row["summary"]["strip"] == r1["strip"] and row["lineup"] == team
    _run(go())


def test_legacy_five_pick_seasons_are_off_the_board(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        # an old-rules row (5 picks, bare lineup) with a huge record must not rank or replay
        old = {"wins": 82, "losses": 0, "grade": "82-0!!", "lineup": [150, 1, 4, 7, 25], "team": [150, 1, 4, 7, 25]}
        old_id = await q.insert_pokehoops_season("carol", "web", 1, 2, [150, 1, 4, 7, 25], old)
        assert await q.get_pokehoops_leaderboard() == []
        assert await q.get_pokehoops_rank("carol") is None and await q.get_pokehoops_best("carol") is None
        assert (await gp.season(old_id)).status_code == 410
        monkeypatch.setattr(gp.auth, "read_session", lambda r: {"id": "carol"})
        assert (await gp.share(_Req(), old_id)).status_code == 410
        picks = await _draft(3)
        r = await ph.finish_season("carol", 3, picks, _team(picks), "web")
        assert r["prev_best"] is None and r["is_new_best"] and r["rank"] == 1
        lb = await q.get_pokehoops_leaderboard()
        assert len(lb) == 1 and lb[0]["id"] == r["id"] and lb[0]["runs"] == 1
    _run(go())


def test_web_start_submit_flow(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(gp.auth, "read_session", lambda r: {"id": "p1"})
        start = await gp.start(_Req())
        picks = await _draft(start["seed"], choose=1)
        team = {"five": picks[3:8], "bench": picks[:3], "backs": [2, 2, 4]}   # a coach's own arrangement
        res = await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=picks, team=team))
        assert res["wins"] + res["losses"] == 82 and res["picks"] == picks and res["team"] == team
        assert "lineup_cards" not in res and "bench_cards" not in res
        # the client-side replay must reproduce the server's season from the returned seeds
        assert (await ph.sim_season(start["seed"], picks, team, res["season_seed"]))["strip"] == res["strip"]
        again = await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=picks, team=team))
        assert again.status_code == 400                     # single-use token
        lb = await gp.leaderboard(_Req())
        assert lb["top"][0]["me"] and lb["top"][0]["wins"] == res["wins"]
        share = await gp.season(res["id"])
        assert share["season_seed"] == res["season_seed"] and share["lineup"] == team["five"] and share["team"] == team
        assert lb["top"][0]["team"] == team and "ovr" not in str(lb)
        assert (await gp.season(999999)).status_code == 404
    _run(go())


def test_web_rejects_bad_drafts(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(gp.auth, "read_session", lambda r: {"id": "p1"})
        start = await gp.start(_Req())
        junk = [150, 1, 4, 7, 25, 26, 27, 28]
        res = await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=junk, team=_team(junk)))
        assert res.status_code == 400                       # (almost surely) not what was offered
        assert await q.get_pokehoops_leaderboard() == []
        # someone else's token
        start = await gp.start(_Req())
        monkeypatch.setattr(gp.auth, "read_session", lambda r: {"id": "mallory"})
        picks = await _draft(start["seed"])
        assert (await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=picks, team=_team(picks)))).status_code == 400
        # a legal draft with an illegal lineup is rejected too
        monkeypatch.setattr(gp.auth, "read_session", lambda r: {"id": "p1"})
        start = await gp.start(_Req())
        picks = await _draft(start["seed"])
        dup = {"five": [picks[0]] * 5, "bench": picks[5:], "backs": [0, 1, 2]}
        res = await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=picks, team=dup))
        assert res.status_code == 400 and "lineup" in res.body.decode()
        assert await q.get_pokehoops_leaderboard() == []
        monkeypatch.setattr(gp.auth, "read_session", lambda r: {"id": "mallory"})
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
        res = await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=picks, team=_team(picks)))
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


def test_share_posts_once_and_only_your_own(monkeypatch):
    _fresh_db()
    sent = []

    class _Resp:
        def raise_for_status(self):
            pass

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, headers, json):
            sent.append((url, json))
            return _Resp()

    async def go():
        await sch.init_db()
        gp._shared.clear()
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "x")
        monkeypatch.setattr(gp.httpx, "AsyncClient", _Client)
        monkeypatch.setattr(gp.auth, "read_session", lambda r: {"id": "p1"})
        start = await gp.start(_Req())
        picks = await _draft(start["seed"])
        res = await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=picks, team=_team(picks)))
        assert (await gp.share(_Req(), res["id"]))["ok"]
        url, body = sent[0]
        assert url.endswith(f"/channels/{gp.SHARE_CHANNEL}/messages")
        emb = body["embeds"][0]
        assert f"{res['wins']}–{res['losses']}" in emb["title"] and emb["url"].endswith(f"?s={res['id']}")
        assert emb["description"].count("\n") == 5 and "#1" in str(emb["fields"])
        assert emb["description"].splitlines()[-1].startswith("Bench: ") and "(backs PG)" in emb["description"]
        assert (await gp.share(_Req(), res["id"])).status_code == 409      # once per season
        monkeypatch.setattr(gp.auth, "read_session", lambda r: {"id": "mallory"})
        assert (await gp.share(_Req(), res["id"])).status_code == 404      # not yours
        assert len(sent) == 1
        # Today board: today's season counts, an old one doesn't
        assert len((await gp.leaderboard(_Req(), period="day"))["top"]) == 1
        assert await q.get_pokehoops_leaderboard(since="2999-01-01") == []
    _run(go())


def test_no_overall_ratings_in_player_facing_code():
    # David: overall ratings must never be shown — the page and the bot only get skills / positions / moves
    import re
    for f in ("web/static/pokehoops.js", "bot/cogs/pokehoops.py", "web/g_pokehoops.py", "shared/pokehoops_cli.mjs"):
        src = (ROOT / f).read_text()
        assert not re.search(r"\bovr\b|OVR", src.replace("bestPos(n).pos", "")), f
