"""Daily Games web API — today, submit (witness solves), one-submit, coins/streak, leaderboard."""
import asyncio
import os
import tempfile

from db import queries as q
from db import schema as sch
from shared.daily_games import trappig
from web import daily as web_daily


class _Req:
    cookies: dict = {}
    headers: dict = {}


def _run(c):
    return asyncio.run(c)


def _fresh():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")


def test_today_shape_has_no_board(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(web_daily.auth, "read_session", lambda r: None)  # signed out
        t = await web_daily.today(_Req())
        assert t["game"]["id"] == "trappig" and t["game"]["howto"]
        assert "board" not in t                      # board is withheld until /start
        assert t["number"] >= 1 and t["par"] >= 1 and t["signed_in"] is False

    _run(go())


def test_start_then_submit_witness_solves_and_server_times(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(web_daily.auth, "read_session", lambda r: {"id": "p1"})
        await q.get_or_create_casino_wallet("p1")
        s = await web_daily.start(_Req())
        assert s["board"]["pig"] and s["start_token"]
        _, witness = trappig.is_solvable(s["board"])
        res = await web_daily.submit(_Req(), web_daily.SubmitBody(
            start_token=s["start_token"], solution={"moves": witness}))
        assert res["result"]["solved"] and res["rank"] == 1
        assert res["result"]["secondary"] >= 0        # server-timed, near-zero here
        assert res["coins"] == 25 and res["streak"] == 1 and "Trap the Pig #" in res["share"]
        # one-submit: a fresh start + resubmit 409s
        s2 = await web_daily.start(_Req())
        again = await web_daily.submit(_Req(), web_daily.SubmitBody(
            start_token=s2["start_token"], solution={"moves": witness}))
        assert again.status_code == 409

    _run(go())


def test_submit_rejects_non_solution_and_bad_token(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(web_daily.auth, "read_session", lambda r: {"id": "p2"})
        await q.get_or_create_casino_wallet("p2")
        s = await web_daily.start(_Req())
        bad = await web_daily.submit(_Req(), web_daily.SubmitBody(
            start_token=s["start_token"], solution={"moves": [[0, 0]]}))
        assert bad.status_code == 400
        # forged/garbage token rejected
        forged = await web_daily.submit(_Req(), web_daily.SubmitBody(
            start_token="garbage", solution={"moves": []}))
        assert forged.status_code == 400
        # a rejected attempt is not recorded → they can still play
        assert await q.get_daily_result("trappig", web_daily.daily.puzzle_day(), "p2") is None

    _run(go())


def test_leaderboard_ranks_two_players(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        t_day = web_daily.daily.puzzle_day()
        puz = await q.get_or_create_daily_puzzle(t_day)
        _, witness = trappig.is_solvable(puz["payload"])
        # p1 solves in `witness` fences slow; p2 same fences faster → p2 ranks first
        await q.record_daily_result("trappig", t_day, "p1", solved=True,
                                    primary=len(witness), secondary=20000)
        await q.record_daily_result("trappig", t_day, "p2", solved=True,
                                    primary=len(witness), secondary=8000)
        monkeypatch.setattr(web_daily.auth, "read_session", lambda r: {"id": "p1"})
        lb = await web_daily.leaderboard(_Req())
        assert [row["rank"] for row in lb["today"]] == [1, 2]
        assert lb["today"][0]["secondary"] == 8000   # faster first
        assert lb["season"][0]["days"] == 1

    _run(go())
