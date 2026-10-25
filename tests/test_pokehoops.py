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
        assert (await gp.season(_Req(), old_id)).status_code == 410
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
        gp.gameround.peek(start["token"])["started"] -= gp.DRAFT_TTL - 5   # nearly expired: a touch resets the clock
        assert (await gp.touch(_Req(), gp.TouchBody(token=start["token"]))) == {"ok": True}
        assert gp.gameround.peek(start["token"])["started"] > gp.time.monotonic() - 5
        picks = await _draft(start["seed"], choose=1)
        team = {"five": picks[3:8], "bench": picks[:3], "backs": [2, 2, 4]}   # a coach's own arrangement
        res = await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=picks, team=team))
        assert res["wins"] + res["losses"] == 82 and res["picks"] == picks and res["team"] == team
        assert "lineup_cards" not in res and "bench_cards" not in res
        # the client-side replay must reproduce the server's season from the returned seeds
        assert (await ph.sim_season(start["seed"], picks, team, res["season_seed"]))["strip"] == res["strip"]
        again = await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=picks, team=team))
        assert again.status_code == 400                     # single-use token
        assert (await gp.touch(_Req(), gp.TouchBody(token=start["token"]))).status_code == 400   # spent: nothing to keep alive
        lb = await gp.leaderboard(_Req())
        assert lb["top"][0]["me"] and lb["top"][0]["wins"] == res["wins"]
        share = await gp.season(_Req(), res["id"])
        assert share["season_seed"] == res["season_seed"] and share["lineup"] == team["five"] and share["team"] == team
        assert lb["top"][0]["team"] == team and "ovr" not in str(lb)
        assert (await gp.season(_Req(), 999999)).status_code == 404
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


def test_two_player_challenge_best_of_seven(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        user = {"id": "alice"}
        monkeypatch.setattr(gp.auth, "read_session", lambda r: user)
        start = await gp.start(_Req())
        picks = await _draft(start["seed"])
        season = await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=picks, team=_team(picks)))
        # alice can't challenge her own team
        start = await gp.start(_Req())
        mine = await _draft(start["seed"], choose=2)
        res = await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=mine, team=_team(mine), vs=season["id"]))
        assert res.status_code == 400 and "own team" in res.body.decode()
        # bob drafts his own eight and plays alice's team, best of seven
        user["id"] = "bob"
        start = await gp.start(_Req())
        bob = await _draft(start["seed"], choose=1)
        team = {"five": bob[3:8], "bench": bob[:3], "backs": [0, 4, 4]}
        x = await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=bob, team=team, vs=season["id"]))
        assert max(x["wins"], x["losses"]) == 4 and min(x["wins"], x["losses"]) < 4
        assert len(x["games"]) == x["wins"] + x["losses"] and [g["win"] for g in x["games"]].count(True) == x["wins"]
        assert x["team"] == team and x["vs"] == season["id"]
        # the share link replays the same series from the stored seeds + both teams
        got = await gp.series(x["id"])
        assert got["opp_team"] == season["team"] and got["series_seed"] == x["series_seed"]
        again = await ph._call({"op": "series", "seed": got["seed"], "picks": got["picks"], "team": got["team"],
                                "opp": got["opp_team"], "series_seed": got["series_seed"]})
        assert again["games"] == x["games"]
        # the challenged season lists it; challenges don't touch the season board
        seen = await gp.season(_Req(), season["id"])
        assert seen["challenges"][0]["id"] == x["id"] and not seen["me"]
        assert len((await gp.leaderboard(_Req()))["top"]) == 1
        assert (await gp.series(999999)).status_code == 404
        start = await gp.start(_Req())
        bob = await _draft(start["seed"])
        assert (await gp.submit(_Req(), gp.SubmitBody(token=start["token"], picks=bob, team=_team(bob), vs=999999))).status_code == 400
    _run(go())


def test_live_duel_room_draft_line_bets_and_series(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        who = {"s": {"id": "dave", "username": "Dave"}}
        monkeypatch.setattr(gp.auth, "read_session", lambda r: who["s"])
        host = await gp.duel_create(_Req(), gp.DuelBody())
        code, ka = host["code"], host["key"]
        who["s"] = None                                                     # the friend joins as a guest
        assert (await gp.duel_join(_Req(), code, gp.DuelBody())).status_code == 400   # needs a name
        kb = (await gp.duel_join(_Req(), code, gp.DuelBody(name="Ash")))["key"]
        assert (await gp.duel_join(_Req(), code, gp.DuelBody(name="Gary"))).status_code == 409
        assert (await gp.duel_state(code, "nope")).status_code == 403
        st = await gp.duel_state(code, kb)
        assert st["phase"] == "draft" and st["names"] == ["Dave", "Ash"] and not st["can_bet"]
        seed = st["seed"]
        while (packs := await ph.duel_packs(seed, st["picks"])) is not None:
            assert not set(packs[0]) & set(packs[1])                        # never offered the same Pokémon
            assert (await gp.duel_pick(code, gp.DuelBody(key=ka, n=packs[1][0]))).status_code == 400   # not your pack
            a = await gp.duel_pick(code, gp.DuelBody(key=ka, n=packs[0][0]))
            assert a["pending"] == packs[0][0] and len(a["picks"][0]) == len(st["picks"][0])         # waits for Ash
            assert (await gp.duel_pick(code, gp.DuelBody(key=ka, n=packs[0][1]))).status_code == 409
            st = await gp.duel_pick(code, gp.DuelBody(key=kb, n=packs[1][2]))
        assert st["phase"] == "lineup" and st["opp_team"] is None
        pa, pb = st["picks"]
        bad = {"five": [pa[0]] * 5, "bench": pa[5:], "backs": [0, 1, 2]}
        assert (await gp.duel_team(code, gp.DuelBody(key=ka, team=bad))).status_code == 400
        await gp.duel_team(code, gp.DuelBody(key=ka, team=_team(pa)))
        st = await gp.duel_team(code, gp.DuelBody(key=kb, team=_team(pb)))
        line = st["line"]
        assert st["phase"] == "tip" and st["opp_team"] == _team(pa) and 0 < line["series"] < 1
        assert abs(sum(line["exact"].values()) - 1) < 1e-9 and all(
            st["prices"][s] < 1 / pr or st["prices"][s] == 1.0 for s, pr in ((0, line["series"]), (1, 1 - line["series"])))
        h = gp.VIG / 2
        assert gp._price(0.999, h) == 1.0 and gp._price(0.5, h) < 2 and gp._price(0.01, h) < 100   # never above fair
        # the hold covers the Monte Carlo noise: ≥ 2 se of the series estimate per side, never less than VIG/2
        assert line["n"] >= 4000
        for n, wide in ((100, True), (10**7, False)):
            pr = gp._prices({"p": 0.5, "series": 0.5, "n": n, "se": (0.25 / n) ** 0.5})
            se_s = 140 / 64 * (0.25 / n) ** 0.5
            assert all(1 / x - 0.5 >= max(h, 2 * se_s) - 1e-3 for x in pr) and (1 / pr[0] - 0.5 > h + 0.01) == wide
        # the guest can't bet; Dave bets 300 on himself
        assert (await gp.duel_ready(_Req(), code, gp.DuelBody(key=kb, side=1, stake=50))).status_code == 401
        who["s"] = {"id": "dave", "username": "Dave"}
        bal0 = await q.get_or_create_casino_wallet("dave")
        assert (await gp.duel_ready(_Req(), code, gp.DuelBody(key=ka, side=0, stake=10**9))).status_code == 400
        await gp.duel_ready(_Req(), code, gp.DuelBody(key=ka, side=0, stake=300))
        assert await q.get_casino_balance("dave") == bal0          # nothing moves until tip-off
        who["s"] = None
        st = await gp.duel_ready(_Req(), code, gp.DuelBody(key=kb))
        res = st["result"]
        assert st["phase"] == "done" and max(res["wins"]) == 4
        bet = st["bets"][0]
        won = res["wins"][0] == 4
        assert bet["payout"] == (int(300 * st["prices"][0]) if won else 0)
        assert await q.get_casino_balance("dave") == bal0 - 300 + bet["payout"]
        rec = await gp.duel_record(res["id"])
        assert rec["names"] == ["Dave", "Ash"] and rec["wins"] == res["wins"] and rec["line"] == line
        again = await ph.duel_series(rec["seed"], rec["picks"], rec["teams"], rec["series_seed"])
        assert again["wins"] == res["wins"]
        assert (await gp.duel_record(424242)).status_code == 404
        assert (await gp.duel_join(_Req(), "ZZZZ", gp.DuelBody(name="x"))).status_code == 404
    _run(go())


def test_guest_mode_plays_without_discord(monkeypatch):
    _fresh_db()

    class _G:
        def __init__(self, cookie):
            self.cookies = {gp.GUEST_COOKIE: cookie} if cookie else {}

    async def go():
        await sch.init_db()
        monkeypatch.setattr(gp.auth, "read_session", lambda r: None)
        assert (await gp.guest(gp.GuestBody(name="  <> "))).status_code == 400
        res = await gp.guest(gp.GuestBody(name="Ash Ketchum"))
        cookie = res.headers["set-cookie"].split(";")[0].split("=", 1)[1].strip('"')
        req = _G(cookie)
        assert gp._uid(_G("forged")) is None and gp._uid(req).startswith("guest:")
        start = await gp.start(req)
        picks = await _draft(start["seed"])
        season = await gp.submit(req, gp.SubmitBody(token=start["token"], picks=picks, team=_team(picks)))
        assert season["coins"] == 0 and season["balance"] == 0          # no coins for guests
        lb = await gp.leaderboard(req)
        assert lb["top"][0]["name"] == "Ash Ketchum (guest)" and lb["top"][0]["me"]
        assert (await gp.share(req, season["id"])).status_code == 401  # no #bot posts
        # a guest's duel seat takes the cookie's name and can't bet
        room = await gp.duel_create(req, gp.DuelBody())
        st = await gp.duel_state(room["code"], room["key"])
        assert st["names"][0] == "Ash Ketchum" and not st["can_bet"]
    _run(go())
