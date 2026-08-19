"""Sequence guesser — reward on correct, single-use, answer stays server-side."""
import asyncio
import os
import tempfile

from db import queries as q
from db import schema as sch
from web import g_sequence as seq


class _Req:
    cookies: dict = {}
    headers: dict = {}


def _run(c):
    return asyncio.run(c)


def _fresh():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")


def test_correct_pays_once_and_is_single_use(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(seq.auth, "read_session", lambda r: {"id": "s1"})
        await q.get_or_create_casino_wallet("s1")
        # stash a known round directly
        token = seq.gameround.stash({"answer": 42, "name": "test"})
        wrong = await seq.sequence_guess(_Req(), seq.GuessBody(token=token, guess=41))
        assert wrong == {"correct": False}
        r1 = await seq.sequence_guess(_Req(), seq.GuessBody(token=token, guess=42))
        assert r1["correct"] and r1["reward"] == 20 and r1["balance"] >= 20
        # replay → round consumed, no second payout
        r2 = await seq.sequence_guess(_Req(), seq.GuessBody(token=token, guess=42))
        assert r2.status_code == 400

    _run(go())


def test_new_hides_answer(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(seq.auth, "read_session", lambda r: {"id": "s2"})
        res = await seq.sequence_new(_Req())
        # only the visible terms + opaque token leave the server — no answer/name
        assert set(res.keys()) == {"token", "terms"}
        assert isinstance(res["terms"], list) and len(res["terms"]) >= 3

    _run(go())


def test_bank_answers_are_ints():
    from bot.cogs.sequence import SEQUENCE_BANK
    assert all(isinstance(a, int) for _, a, _ in SEQUENCE_BANK)
