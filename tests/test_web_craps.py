"""Web Craps — come-out naturals/craps, point phase, and single-settle."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db.queries as q  # noqa: E402
import db.schema as sch  # noqa: E402
import web.craps as cr  # noqa: E402


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


def test_comeout_natural_and_craps(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        await _fund("c", 1000)
        monkeypatch.setattr(cr.auth, "read_session", lambda r: {"id": "c"})
        monkeypatch.setattr(cr, "_roll", lambda: (4, 3))  # 7 → natural win, pays 2×
        res = await cr.comeout(_Req(), cr.ComeOutBody(bet=100))
        assert res["done"] and res["result"] == "win" and res["payout"] == 200
        assert await q.get_casino_balance("c") == 1000 - 100 + 200
        monkeypatch.setattr(cr, "_roll", lambda: (1, 1))  # 2 → craps, lose
        res2 = await cr.comeout(_Req(), cr.ComeOutBody(bet=100))
        assert res2["done"] and res2["result"] == "craps" and res2["payout"] == 0

    _run(go())


def test_point_phase_win_and_replay_rejected(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        await _fund("c", 1000)
        monkeypatch.setattr(cr.auth, "read_session", lambda r: {"id": "c"})
        monkeypatch.setattr(cr, "_roll", lambda: (3, 3))  # 6 → point set
        res = await cr.comeout(_Req(), cr.ComeOutBody(bet=100))
        assert not res["done"] and res["point"] == 6
        rid = res["round_id"]
        monkeypatch.setattr(cr, "_roll", lambda: (2, 4))  # 6 again → hit the point, win
        done = await cr.roll(_Req(), cr.RollBody(round_id=rid))
        assert done["done"] and done["result"] == "win" and done["payout"] == 200
        # replay the settling roll → rejected (round deleted)
        replay = await cr.roll(_Req(), cr.RollBody(round_id=rid))
        assert getattr(replay, "status_code", None) == 400

    _run(go())
