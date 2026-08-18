"""Three Card Poker — 3-card hand ranking + the ante/play coin resolution."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db.queries as q  # noqa: E402
import db.schema as sch  # noqa: E402
import web.threecardpoker as tcp  # noqa: E402


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


def test_rank3_ordering():
    r = tcp.rank3
    assert r(["5♠", "6♥", "7♦"]) > r(["2♣", "5♣", "9♣"])   # straight > flush
    assert r(["9♠", "9♥", "9♦"])[0] == 4                    # trips
    assert r(["5♣", "6♣", "7♣"])[0] == 5                    # straight flush
    assert r(["K♠", "K♥", "A♦"]) > r(["K♣", "K♦", "Q♠"])   # pair kicker


def test_fold_loses_ante_play_pays_out(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        await _fund("t", 1000)
        monkeypatch.setattr(tcp.auth, "read_session", lambda r: {"id": "t"})
        # fold: ante debited, nothing back → balance 900
        d = await tcp.deal(_Req(), tcp.DealBody(ante=100))
        rid = d["round_id"]
        res = await tcp.play(_Req(), tcp.PlayBody(round_id=rid, action="fold"))
        assert res["result"] == "fold" and res["payout"] == 0
        assert await q.get_casino_balance("t") == 900
        # replaying the settled round is rejected
        again = await tcp.play(_Req(), tcp.PlayBody(round_id=rid, action="play"))
        assert getattr(again, "status_code", None) == 400

    _run(go())


def test_play_resolution_conserves_coins(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        await _fund("t", 1000)
        monkeypatch.setattr(tcp.auth, "read_session", lambda r: {"id": "t"})
        d = await tcp.deal(_Req(), tcp.DealBody(ante=100))     # -100 → 900
        res = await tcp.play(_Req(), tcp.PlayBody(round_id=d["round_id"], action="play"))  # -100 → 800, +payout
        bal = await q.get_casino_balance("t")
        # net vs the 1000 start = payout - 200 (both bets staked); balance is internally consistent
        assert bal == 800 + res["payout"]
        assert res["result"] in ("win", "lose", "push", "dealer_no_qualify")

    _run(go())
