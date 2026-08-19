"""Pai Gow web — foul rejection, house-way settle, double-settle race. Money paths."""
import asyncio
import os
import tempfile

from db import queries as q
from db import schema as sch
from web import paigow as pg


class _Req:
    cookies: dict = {}
    headers: dict = {}


def _run(c):
    return asyncio.run(c)


def _fresh():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")


def _seed(uid, player, dealer, bet=100, fortune=0):
    rid = "rid"
    pg._ROUNDS[rid] = {"uid": uid, "bet": bet, "fortune": fortune,
                       "player": player, "dealer": dealer}
    return rid


def test_foul_split_rejected(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(pg.auth, "read_session", lambda r: {"id": "u"})
        await q.get_or_create_casino_wallet("u")
        # low = two aces (a pair) but high left as junk that can't beat it → foul.
        player = ["A♠", "A♥", "2♦", "3♣", "4♠", "5♥", "7♦"]
        rid = _seed("u", player, ["K♠", "Q♥", "J♦", "9♣", "8♠", "6♥", "3♦"])
        res = await pg.set_hand(_Req(), pg.SetBody(round_id=rid, low=["A♠", "A♥"]))
        assert res.status_code == 400  # foul
        assert rid in pg._ROUNDS  # still live for retry

    _run(go())


def test_low_not_from_hand_rejected(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(pg.auth, "read_session", lambda r: {"id": "u2"})
        await q.get_or_create_casino_wallet("u2")
        player = ["A♠", "K♥", "2♦", "3♣", "4♠", "5♥", "7♦"]
        rid = _seed("u2", player, ["K♠", "Q♥", "J♦", "9♣", "8♠", "6♥", "3♦"])
        res = await pg.set_hand(_Req(), pg.SetBody(round_id=rid, low=["A♦", "A♣"]))
        assert res.status_code == 400

    _run(go())


def test_houseway_settles_and_claims(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(pg.auth, "read_session", lambda r: {"id": "u3"})
        await q.get_or_create_casino_wallet("u3")
        player = ["A♠", "A♥", "K♦", "K♣", "Q♠", "J♥", "9♦"]  # two pair, strong
        rid = _seed("u3", player, ["7♠", "5♥", "4♦", "3♣", "2♠", "9♥", "8♦"])
        res = await pg.set_hand(_Req(), pg.SetBody(round_id=rid, low=None))  # house way
        assert res["done"] and res["outcome"] in ("win", "lose", "push")
        assert rid not in pg._ROUNDS

    _run(go())


def test_double_settle_race(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(pg.auth, "read_session", lambda r: {"id": "u4"})
        await q.get_or_create_casino_wallet("u4")
        player = ["A♠", "A♥", "K♦", "K♣", "Q♠", "J♥", "9♦"]
        rid = _seed("u4", player, ["7♠", "5♥", "4♦", "3♣", "2♠", "9♥", "8♦"])
        r1, r2 = await asyncio.gather(
            pg.set_hand(_Req(), pg.SetBody(round_id=rid, low=None)),
            pg.set_hand(_Req(), pg.SetBody(round_id=rid, low=None)),
            return_exceptions=True,
        )
        oks = [r for r in (r1, r2) if isinstance(r, dict) and r.get("done")]
        assert len(oks) == 1

    _run(go())
