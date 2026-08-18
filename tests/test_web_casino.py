"""Web casino: the shared _play() coin flow (atomic debit, payout credit, overdraw reject)
and per-game payout sanity. Uses a temp DB + a fake Request with a monkeypatched session."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db.queries as q  # noqa: E402
import db.schema as sch  # noqa: E402
import web.casino as casino  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _fresh_db():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")


class _Req:
    pass


async def _fund(uid, amount):
    import aiosqlite
    async with aiosqlite.connect(q.DB_PATH) as db:
        await db.execute(
            "INSERT INTO casino_wallets (discord_user, balance) VALUES (?, ?) "
            "ON CONFLICT(discord_user) DO UPDATE SET balance = ?", (uid, amount, amount))
        await db.commit()


def test_play_credits_win_and_logs(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        await _fund("w", 1000)
        monkeypatch.setattr(casino.auth, "read_session", lambda r: {"id": "w"})
        # forced win: resolver returns a 300 payout on a 100 bet
        res = await casino._play(_Req(), "coinflip", 100, lambda: (300, {"x": 1}))
        assert res["payout"] == 300 and res["won"] is True
        assert res["balance"] == 1000 - 100 + 300  # debit then credit
        # PnL logged
        import aiosqlite
        async with aiosqlite.connect(q.DB_PATH) as db:
            n = (await (await db.execute("SELECT COUNT(*) FROM casino_history WHERE discord_user='w'")).fetchone())[0]
        assert n == 1

    _run(go())


def test_play_rejects_overdraw(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        await _fund("b", 50)
        monkeypatch.setattr(casino.auth, "read_session", lambda r: {"id": "b"})
        res = await casino._play(_Req(), "slots", 100, lambda: (0, {}))  # bet > balance
        assert getattr(res, "status_code", None) == 400
        assert await q.get_casino_balance("b") == 50  # untouched

    _run(go())


def test_plinko_center_and_edge_payouts(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        await _fund("pl", 1000)
        monkeypatch.setattr(casino.auth, "read_session", lambda r: {"id": "pl"})
        monkeypatch.setattr(casino.secrets, "randbelow", lambda n: 1)  # all right → bucket 8 → 8.4x
        res = await casino.plinko(_Req(), casino.PlinkoBody(bet=10))
        assert res["detail"]["bucket"] == 8 and res["payout"] == round(10 * 8.4)

    _run(go())


def test_crash_win_when_point_beats_target(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        await _fund("cr", 1000)
        monkeypatch.setattr(casino.auth, "read_session", lambda r: {"id": "cr"})
        monkeypatch.setattr(casino.secrets, "randbelow", lambda n: 990_000_000)  # u=0.99 → point 99
        res = await casino.crash(_Req(), casino.CrashBody(bet=10, target=2.0))
        assert res["won"] and res["payout"] == 20 and res["detail"]["point"] >= 2.0

    _run(go())


def test_roulette_number_pays_36x(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        await _fund("r", 1000)
        monkeypatch.setattr(casino.auth, "read_session", lambda r: {"id": "r"})
        monkeypatch.setattr(casino.secrets, "randbelow", lambda n: 17)  # force spin = 17
        body = casino.RouletteBody(bet=10, kind="number", value=17)
        res = await casino.roulette(_Req(), body)
        assert res["payout"] == 360  # 10 × 36
        assert res["detail"]["color"] == "black"

    _run(go())


def test_horserace_win_pays_odds(monkeypatch):
    import web.horserace as hr
    _fresh_db()

    async def go():
        await sch.init_db()
        await _fund("hr", 1000)
        monkeypatch.setattr(hr._play.__globals__["auth"], "read_session", lambda r: {"id": "hr"})
        monkeypatch.setattr(hr, "_run_race", lambda: 3)  # horse 3 wins (odds 9.0)
        res = await hr.horserace(_Req(), hr.RaceBody(bet=10, horse=3))
        assert res["won"] and res["payout"] == round(10 * 9.0)
        assert res["detail"]["winner"] == 3
    _run(go())


def test_sicbo_number_triple_pays_4x(monkeypatch):
    import web.sicbo as sb
    _fresh_db()

    async def go():
        await sch.init_db()
        await _fund("sb", 1000)
        monkeypatch.setattr(sb._play.__globals__["auth"], "read_session", lambda r: {"id": "sb"})
        monkeypatch.setattr(sb.secrets, "randbelow", lambda n: 2)  # every die = 3 → triple threes
        # bet the number 3 → appears 3× → 4× payout
        res = await sb.sicbo(_Req(), sb.SicBoBody(bet=10, kind="num", value=3))
        assert res["won"] and res["payout"] == 40 and res["detail"]["triple"]
        # "small" loses on a triple even though sum 9 is in 4-10
        res2 = await sb.sicbo(_Req(), sb.SicBoBody(bet=10, kind="small"))
        assert res2["payout"] == 0
    _run(go())
