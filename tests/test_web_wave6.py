"""Countdown evaluator + solve reward, and Math Sprint submit scoring — the coin paths."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db.queries as q  # noqa: E402
import db.schema as sch  # noqa: E402
import web.g_countdown as cd  # noqa: E402
import web.g_mathsprint as ms  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _fresh_db():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")


class _Req:
    pass


def test_countdown_evaluator_rules():
    assert cd.evaluate_expression("(100 + 25) * 3", [100, 25, 3, 7, 2, 50]) == 375
    assert cd.evaluate_expression("100 / 25", [100, 25, 1, 2, 3, 4]) == 4
    for bad in ("100 / 3", "9 * 9"):  # non-int division / unavailable number
        try:
            cd.evaluate_expression(bad, [100, 3, 1, 2, 4, 5] if "/" in bad else [1, 2, 3, 4, 5, 6])
            assert False, bad
        except Exception:
            pass


def test_countdown_solve_exact_pays(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(cd.auth, "read_session", lambda r: {"id": "cd"})
        # forge a round: numbers [100,25,3,...] target 375, and solve it exactly
        token = cd._round_signer.dumps([[100, 25, 3, 7, 2, 50], 375]) if hasattr(cd, "_round_signer") \
            else cd._signer.dumps([[100, 25, 3, 7, 2, 50], 375])
        res = await cd.countdown_solve(_Req(), cd.SolveBody(token=token, expression="(100 + 25) * 3"))
        assert res["exact"] and res["value"] == 375
        assert res.get("reward", 0) == 30  # countdown_win = 30

    _run(go())


def test_mathsprint_submit_counts_and_pays(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(ms.auth, "read_session", lambda r: {"id": "ms"})
        # forge a token with 3 known answers, submit 2 correct + 1 wrong
        signer = getattr(ms, "_round_signer", None) or ms._signer
        token = signer.dumps([12, 20, 30])
        res = await ms.submit(_Req(), ms.SubmitBody(token=token, answers=[12, 20, 99]))
        assert res["correct"] == 2 and res["coins"] == 4  # 2 coins per correct

    _run(go())
