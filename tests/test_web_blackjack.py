"""Web Blackjack — hand totals + the deal→action coin flow (debit, single settle, no replay)."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db.queries as q  # noqa: E402
import db.schema as sch  # noqa: E402
import web.blackjack as bj  # noqa: E402


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


def test_hand_total_aces_soften():
    assert bj.hand_total(["A♠", "K♥"]) == 21          # blackjack
    assert bj.hand_total(["A♠", "A♥", "9♦"]) == 21    # one ace hard, one soft
    assert bj.hand_total(["K♠", "Q♥", "2♦"]) == 22    # bust
    assert bj._is_blackjack(["A♠", "10♦"]) and not bj._is_blackjack(["A♠", "5♦", "5♣"])


def test_deal_debits_and_settle_is_single(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        await _fund("bj", 1000)
        monkeypatch.setattr(bj.auth, "read_session", lambda r: {"id": "bj"})
        res = await bj.deal(_Req(), bj.DealBody(bet=100))
        if res.get("done"):  # dealt a natural — settled during deal: bet debited then payout credited
            assert await q.get_casino_balance("bj") == 900 + res["payout"]
        else:
            assert await q.get_casino_balance("bj") == 900  # bet debited, hand still live
            rid = res["round_id"]
            done = await bj.action(_Req(), bj.ActionBody(round_id=rid, action="stand"))  # → settle
            assert done["done"] and done["result"] in ("win", "lose", "push", "blackjack", "bust")
            # replay the same round → rejected (round was deleted on settle, so no double-pay)
            replay = await bj.action(_Req(), bj.ActionBody(round_id=rid, action="stand"))
            assert getattr(replay, "status_code", None) == 400

    _run(go())


def test_overdraw_rejected(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        await _fund("poor", 50)
        monkeypatch.setattr(bj.auth, "read_session", lambda r: {"id": "poor"})
        res = await bj.deal(_Req(), bj.DealBody(bet=100))
        assert getattr(res, "status_code", None) == 400
        assert await q.get_casino_balance("poor") == 50

    _run(go())
