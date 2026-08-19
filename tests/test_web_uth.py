"""UTH web settlement — money paths. Forces known hands by seeding _ROUNDS directly."""
import asyncio

import pytest

from db import queries
from db import schema as sch
from web import uth


class _Req:
    cookies: dict = {}
    headers: dict = {}


def _run(c):
    return asyncio.run(c)


def _seed(uid, hole, dealer, community, ante=100, trips=0, play=400, phase="turn"):
    rid = "test-rid"
    uth._ROUNDS[rid] = {
        "uid": uid, "ante": ante, "blind": ante, "trips": trips,
        "hole": hole, "dealer": dealer, "community": community,
        "play": play, "phase": phase,
    }
    return rid


def test_player_win_pays_play_ante_blind(monkeypatch, tmp_path):
    sch.DB_PATH = str(tmp_path / "t.db"); queries.DB_PATH = sch.DB_PATH

    async def go():
        await sch.init_db()
        monkeypatch.setattr(uth.auth, "read_session", lambda r: {"id": "u1"})
        await queries.get_or_create_casino_wallet("u1")
        # Player: pair of aces on board vs dealer 7-high (dealer DNQ would be no pair;
        # give dealer a pair too so ante pays 1:1). Player two pair beats dealer pair.
        hole = ["A♠", "A♥"]
        dealer = ["K♠", "Q♥"]
        community = ["A♦", "K♦", "7♣", "3♠", "2♥"]  # player: trip aces; dealer: aces+kings? no
        rid = _seed("u1", hole, dealer, community, ante=100, play=400)
        res = await uth.action(_Req(), uth.ActionBody(round_id=rid, action="bet"))
        # player has trips (aces) — beats dealer. play 400*2 + ante 200 + blind(+bonus)
        assert res["payout"] > 0 and res["done"]
        assert res["round_id"] not in uth._ROUNDS if "round_id" in res else True
        assert rid not in uth._ROUNDS  # claimed

    _run(go())


def test_fold_loses_ante_blind_but_trips_can_pay(monkeypatch, tmp_path):
    sch.DB_PATH = str(tmp_path / "t2.db"); queries.DB_PATH = sch.DB_PATH

    async def go():
        await sch.init_db()
        monkeypatch.setattr(uth.auth, "read_session", lambda r: {"id": "u2"})
        await queries.get_or_create_casino_wallet("u2")
        # Fold with a straight flush in hole+community → trips side bet still pays big.
        hole = ["5♠", "6♠"]
        dealer = ["K♦", "2♣"]
        community = ["7♠", "8♠", "9♠", "3♦", "2♥"]  # player 5-9 straight flush
        rid = _seed("u2", hole, dealer, community, ante=100, trips=10, play=0, phase="turn")
        res = await uth.action(_Req(), uth.ActionBody(round_id=rid, action="fold"))
        assert res["folded"] and res["payout"] >= 10 * 40  # trips SF pays 40:1 + stake
        assert rid not in uth._ROUNDS

    _run(go())


def test_double_settle_race_pays_once(monkeypatch, tmp_path):
    sch.DB_PATH = str(tmp_path / "t3.db"); queries.DB_PATH = sch.DB_PATH

    async def go():
        await sch.init_db()
        monkeypatch.setattr(uth.auth, "read_session", lambda r: {"id": "u3"})
        await queries.get_or_create_casino_wallet("u3")
        rid = _seed("u3", ["A♠", "A♥"], ["2♣", "3♦"], ["A♦", "K♦", "7♣", "3♠", "2♥"])
        r1, r2 = await asyncio.gather(
            uth.action(_Req(), uth.ActionBody(round_id=rid, action="bet")),
            uth.action(_Req(), uth.ActionBody(round_id=rid, action="bet")),
            return_exceptions=True,
        )
        oks = [r for r in (r1, r2) if isinstance(r, dict) and r.get("done")]
        assert len(oks) == 1  # exactly one settle won the claim

    _run(go())
