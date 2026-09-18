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


def test_mastermind_online_flow_and_server_move_count(monkeypatch):
    """Mastermind: per-guess feedback via /mm-guess, and the move count is SERVER-counted so a
    client that replays the answer in a short list still scores its true (persisted) guess total."""
    _fresh()

    async def go():
        await sch.init_db()
        # Force today's game to Mastermind regardless of the rotation date.
        monkeypatch.setattr(web_daily.daily, "schedule", lambda day: ("mastermind", "easy"))
        day = web_daily.daily.puzzle_day()
        code = (await q.get_or_create_daily_puzzle(day))["payload"]["code"]
        colors = (await q.get_or_create_daily_puzzle(day))["payload"]["colors"]
        wrong = [(code[0] + 1) % colors] + list(code[1:])

        monkeypatch.setattr(web_daily.auth, "read_session", lambda r: {"id": "m1"})
        await q.get_or_create_casino_wallet("m1")
        s = await web_daily.start(_Req())
        assert "code" not in s["board"]          # secret is redacted
        assert s["mm_history"] == []

        g1 = await web_daily.mm_guess(_Req(), web_daily.GuessBody(start_token=s["start_token"], guess=wrong))
        assert g1["solved"] is False and g1["count"] == 1
        g2 = await web_daily.mm_guess(_Req(), web_daily.GuessBody(start_token=s["start_token"], guess=code))
        assert g2["solved"] is True and g2["count"] == 2

        res = await web_daily.submit(_Req(), web_daily.SubmitBody(
            start_token=s["start_token"], solution={"moves": [wrong, code]}))
        assert res["result"]["solved"] and res["result"]["primary"] == 2 and res["rank"] == 1

        # Anti-cheese: a second player grinds 3 guesses server-side, then tries to submit a
        # 1-guess "solution". The move score must be the server total (3), not the client's 1.
        monkeypatch.setattr(web_daily.auth, "read_session", lambda r: {"id": "m2"})
        await q.get_or_create_casino_wallet("m2")
        s2 = await web_daily.start(_Req())
        for _ in range(2):
            await web_daily.mm_guess(_Req(), web_daily.GuessBody(start_token=s2["start_token"], guess=wrong))
        await web_daily.mm_guess(_Req(), web_daily.GuessBody(start_token=s2["start_token"], guess=code))
        res2 = await web_daily.submit(_Req(), web_daily.SubmitBody(
            start_token=s2["start_token"], solution={"moves": [code]}))
        assert res2["result"]["primary"] == 3    # server-counted, cheese-proof

        # m1 (2 guesses) ranks above m2 (3 guesses) — move-count first.
        lb = await web_daily.leaderboard(_Req())
        assert lb["today"][0]["primary"] == 2 and lb["today"][1]["primary"] == 3

    _run(go())


def test_mm_history_is_capped(monkeypatch):
    """A flood of /mm-guess can't grow the stored history without bound."""
    _fresh()

    async def go():
        await sch.init_db()
        day = web_daily.daily.puzzle_day()
        await q.get_or_create_daily_start("capuser", "mastermind", day)
        n = 0
        for _ in range(q.MM_MAX_GUESSES + 5):
            n = await q.append_daily_mm_guess("capuser", day, [0, 0, 0, 0], 0, 0)
        assert n == q.MM_MAX_GUESSES
        assert len(await q.get_daily_mm_state("capuser", day)) == q.MM_MAX_GUESSES

    _run(go())
