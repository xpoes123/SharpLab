"""In-flight persistence — open hands survive a restart (persist → restore round-trip)."""
import asyncio
import os
import tempfile

from db import queries as q
from db import schema as sch
from web import blackjack, uth, paigow, inflight


def _run(c):
    return asyncio.run(c)


def _fresh():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")


def test_persist_then_restore_roundtrips():
    _fresh()

    async def go():
        await sch.init_db()
        # open hands mid-round (as they'd sit in memory)
        blackjack._ROUNDS.clear(); uth._ROUNDS.clear(); paigow._ROUNDS.clear()
        blackjack._ROUNDS["bj1"] = {"uid": "a", "bet": 300, "deck": ["A♠", "10♥"],
                                    "player": ["9♣", "7♦"], "dealer": ["K♠", "5♥"],
                                    "player_bj": False, "dealer_bj": False}
        uth._ROUNDS["u1"] = {"uid": "b", "ante": 100, "blind": 100, "trips": 20,
                             "hole": ["A♠", "A♥"], "dealer": ["2♣", "3♦"],
                             "community": ["A♦", "K♦", "7♣", "3♠", "2♥"], "play": 0, "phase": "flop"}

        n = await inflight.persist_all()
        assert n == 2

        # simulate a restart: wipe memory, then restore
        saved_bj = dict(blackjack._ROUNDS["bj1"])
        blackjack._ROUNDS.clear(); uth._ROUNDS.clear()
        restored = await inflight.restore_all()
        assert restored == 2
        assert blackjack._ROUNDS["bj1"] == saved_bj          # exact state back, hand resumes
        assert uth._ROUNDS["u1"]["phase"] == "flop" and uth._ROUNDS["u1"]["play"] == 0
        # table cleared after restore → a second restore brings back nothing
        assert await inflight.restore_all() == 0

    _run(go())


def test_games_registered():
    import web.api  # noqa: F401 — imports every game module so they register
    names = {name for name, _r in inflight._REGISTRY}
    assert {"blackjack", "videopoker", "craps", "crapless", "hilo",
            "threecardpoker", "uth", "paigow"} <= names
