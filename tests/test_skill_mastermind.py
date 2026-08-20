"""Mastermind timed gauntlet + skill leaderboard."""
import asyncio
import os
import tempfile

from db import queries as q
from db import schema as sch
from web import g_mastermind as mm, gameround


class _Req:
    cookies: dict = {}
    headers: dict = {}


def _run(c):
    return asyncio.run(c)


def _fresh():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")


def test_skill_best_keeps_lowest_and_ranks():
    _fresh()

    async def go():
        await sch.init_db()
        assert await q.record_skill_best("mastermind", "a", 40000) == (40000, True)
        assert await q.record_skill_best("mastermind", "a", 55000) == (40000, False)  # slower ignored
        assert await q.record_skill_best("mastermind", "a", 30000) == (30000, True)   # new best
        await q.record_skill_best("mastermind", "b", 35000)
        lb = await q.get_skill_leaderboard("mastermind")
        assert [r["discord_user"] for r in lb] == ["a", "b"]     # 30s < 35s
        assert lb[0]["runs"] == 3
        assert await q.get_skill_rank("mastermind", "b") == 2

    _run(go())


def test_gauntlet_run_completes_and_records(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(mm, "_uid", lambda r: "runner")
        await q.get_or_create_casino_wallet("runner")
        start = await mm.run_start(_Req())
        tok = start["run_token"]
        assert start["target"] == mm.GAUNTLET_N
        # solve every code by peeking the server-side run state for the current secret
        res = None
        for _ in range(mm.GAUNTLET_N):
            st = gameround.peek(tok)
            code = list(st["codes"][st["index"]])
            res = await mm.run_guess(_Req(), mm.RunGuessBody(run_token=tok, guess=code))
            assert res.get("code_solved")
        assert res["done"] and not res.get("dnf")
        assert res["elapsed_ms"] >= 0 and res["is_new_best"] and res["rank"] == 1
        assert res["reward"] == 30           # timed run also pays the capped coins
        assert (await q.get_skill_leaderboard("mastermind"))[0]["discord_user"] == "runner"

    _run(go())


def test_gauntlet_dnf_on_blown_budget(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(mm, "_uid", lambda r: "bad")
        start = await mm.run_start(_Req())
        tok = start["run_token"]
        st = gameround.peek(tok)
        wrong = ["red" if st["codes"][0][0] != "red" else "blue"] * mm.CODE_LEN
        res = None
        for _ in range(mm.MAX_GUESSES):
            res = await mm.run_guess(_Req(), mm.RunGuessBody(run_token=tok, guess=wrong))
        assert res["done"] and res["dnf"] and "code" in res
        assert await q.get_skill_leaderboard("mastermind") == []   # DNF → no leaderboard entry

    _run(go())
