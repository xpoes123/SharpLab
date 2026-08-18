"""Web arcade — Who's That Pokémon? round-token flow + capped coin reward on a correct guess."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db.queries as q  # noqa: E402
import db.schema as sch  # noqa: E402
import web.arcade as arcade  # noqa: E402
from bot.cogs.pokemon import POKEMON  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _fresh_db():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")


class _Req:
    pass


def test_correct_guess_pays_and_reveals(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(arcade.auth, "read_session", lambda r: {"id": "p"})
        token = arcade._round_signer.dumps(0)  # POKEMON[0]
        entry = POKEMON[0]
        # wrong guess → no reveal, no reward
        wrong = await arcade.pokemon_guess(_Req(), arcade.GuessBody(token=token, guess="zzzznotamon"))
        assert wrong == {"correct": False}
        # correct guess → reveal + capped reward
        res = await arcade.pokemon_guess(_Req(), arcade.GuessBody(token=token, guess=entry[1]))
        assert res["correct"] is True and res["name"] == entry[1]
        assert res["reward"] == 15 and res["balance"] >= 15

    _run(go())


def test_wordle_scoring_handles_duplicates():
    import web.g_wordle as w  # score_guess(guess, answer)
    # guess LEVEL vs answer ALLEY: L→present, E→absent (only E is the correct one), V→absent,
    # E→correct, L→present
    assert w.score_guess("LEVEL", "ALLEY") == ["present", "absent", "absent", "correct", "present"]
    assert w.score_guess("ALLEY", "ALLEY") == ["correct"] * 5


def test_expired_or_bad_token_400(monkeypatch):
    _fresh_db()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(arcade.auth, "read_session", lambda r: {"id": "p"})
        res = await arcade.pokemon_guess(_Req(), arcade.GuessBody(token="garbage", guess="pikachu"))
        assert getattr(res, "status_code", None) == 400

    _run(go())
