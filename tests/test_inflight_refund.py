"""Graceful-shutdown refund — an interrupted hand returns exactly what was debited."""
import asyncio
import os
import tempfile

from db import queries as q
from db import schema as sch
from web import blackjack, uth, paigow, threecardpoker, inflight


def _run(c):
    return asyncio.run(c)


def _fresh():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")


def test_refund_returns_exact_debit_and_clears():
    _fresh()

    async def go():
        await sch.init_db()
        for uid in ("bj", "u", "pg", "tcp"):
            await q.get_or_create_casino_wallet(uid)
        start = {u: await q.get_casino_balance(u) for u in ("bj", "u", "pg", "tcp")}

        # simulate open rounds (as if the bet was already debited and the hand is unfinished)
        blackjack._ROUNDS["r1"] = {"uid": "bj", "bet": 300}
        uth._ROUNDS["r2"] = {"uid": "u", "ante": 100, "blind": 100, "trips": 20, "play": 400}
        paigow._ROUNDS["r3"] = {"uid": "pg", "bet": 250, "fortune": 50}
        threecardpoker._ROUNDS["r4"] = {"uid": "tcp", "ante": 75}

        refunded = await inflight.refund_all()
        assert refunded == 300 + (100 + 100 + 20 + 400) + (250 + 50) + 75

        assert await q.get_casino_balance("bj") == start["bj"] + 300
        assert await q.get_casino_balance("u") == start["u"] + 620      # ante+blind+trips+play
        assert await q.get_casino_balance("pg") == start["pg"] + 300     # bet+fortune
        assert await q.get_casino_balance("tcp") == start["tcp"] + 75
        # stores cleared → a second call refunds nothing (no double-pay)
        assert not blackjack._ROUNDS and not uth._ROUNDS
        assert await inflight.refund_all() == 0

    _run(go())


def test_all_eight_games_registered():
    import web.api  # noqa: F401 — ensures every game module is imported + registered
    names = {name for name, _r, _f in inflight._REGISTRY}
    assert {"blackjack", "videopoker", "craps", "crapless", "hilo",
            "threecardpoker", "uth", "paigow"} <= names
    # no game registered twice (module import is cached, so exactly one entry each)
    assert len(inflight._REGISTRY) == len(names)
