"""Mastermind feedback + NBA Sim moneyline settlement (the coin path)."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db.queries as q  # noqa: E402
import db.schema as sch  # noqa: E402
import web.g_mastermind as mm  # noqa: E402
import web.nbasim_web as nba  # noqa: E402


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


def test_mastermind_score():
    assert mm._score(["red", "red", "green", "green"], ["red", "red", "green", "green"]) == (4, 0)
    assert mm._score(["red", "red", "green", "green"], ["green", "green", "red", "red"]) == (0, 4)
    assert mm._score(["red", "blue", "green", "green"], ["red", "red", "red", "red"]) == (1, 0)  # no double-count


def test_nbasim_win_pays_decimal_odds(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        await _fund("n", 1000)
        monkeypatch.setattr(nba.auth, "read_session", lambda r: {"id": "n"})
        # forge a token for a 60%-home matchup, then bet home and force home to win
        token = nba._signer.dumps(["LAL", "BOS", 0.60, -3.0, 224.0])
        monkeypatch.setattr(nba.secrets, "randbelow", lambda k: 0)  # → u≈0 < 0.60 → home wins
        home_dec = nba._price(0.60)["home_dec"]
        res = await nba.place_bet(_Req(), nba.BetBody(token=token, side="home", stake=100))
        assert res["won"] and res["winner"] == "home"
        assert res["payout"] == round(100 * home_dec)
        assert await q.get_casino_balance("n") == 1000 - 100 + res["payout"]

    _run(go())


def test_nbasim_overdraw_rejected(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        await _fund("broke", 50)
        monkeypatch.setattr(nba.auth, "read_session", lambda r: {"id": "broke"})
        token = nba._signer.dumps(["LAL", "BOS", 0.55, -2.0, 220.0])
        res = await nba.place_bet(_Req(), nba.BetBody(token=token, side="home", stake=100))
        assert getattr(res, "status_code", None) == 400
        assert await q.get_casino_balance("broke") == 50

    _run(go())
