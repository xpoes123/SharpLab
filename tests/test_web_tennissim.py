"""Web Tennis Sim settlement — money paths. Forces a deterministic winner by
monkeypatching the module's CSPRNG draw (secrets.randbelow), and asserts the
server always recomputes the payout multiplier from the signed token."""
import asyncio
import json

from db import queries
from db import schema as sch
from web import tennissim_web


class _Req:
    cookies: dict = {}
    headers: dict = {}


def _run(c):
    return asyncio.run(c)


def _force_winner(monkeypatch, side):
    # /bet draws: secrets.randbelow(1e9)/1e9 < p1_prob → "p1".
    # randbelow→0 makes 0 < prob → p1; randbelow→n-1 makes ~1.0 → p2.
    # (The flavor-sets sim also calls randbelow(2); a constant is harmless there.)
    if side == "p1":
        monkeypatch.setattr(tennissim_web.secrets, "randbelow", lambda n: 0)
    else:
        monkeypatch.setattr(tennissim_web.secrets, "randbelow", lambda n: n - 1)


def test_winning_bet_credits_stake_times_mult_and_logs(monkeypatch, tmp_path):
    sch.DB_PATH = str(tmp_path / "t.db"); queries.DB_PATH = sch.DB_PATH

    async def go():
        await sch.init_db()
        monkeypatch.setattr(tennissim_web.auth, "read_session", lambda r: {"id": "u1"})
        await queries.get_or_create_casino_wallet("u1")
        await queries.update_casino_balance("u1", 100_000)  # ensure funds
        start = await queries.get_casino_balance("u1")

        new = await tennissim_web.new_game(_Req())
        token, p1_prob = new["token"], new["p1_prob"]
        mult = 1 / p1_prob  # server multiplier for the p1 side

        _force_winner(monkeypatch, "p1")
        stake = 500
        res = await tennissim_web.place_bet(
            _Req(), tennissim_web.BetBody(token=token, side="p1", stake=stake),
        )
        assert isinstance(res, dict)
        assert res["won"] is True
        assert res["winner"] == "p1"
        assert res["winner_abbr"] == new["p1"]["abbr"]
        expected_payout = int(stake * mult)
        assert res["payout"] == expected_payout
        # Balance conserved: start - stake + payout.
        assert res["balance"] == start - stake + expected_payout
        assert await queries.get_casino_balance("u1") == res["balance"]
        # Sets line is consistent with the winner.
        assert res["p1_sets"] > res["p2_sets"]

        # Logged exactly one tennissim round.
        stats = await queries.get_casino_stats("u1")
        assert stats["total_wagered"] == stake

    _run(go())


def test_losing_bet_pays_zero(monkeypatch, tmp_path):
    sch.DB_PATH = str(tmp_path / "t2.db"); queries.DB_PATH = sch.DB_PATH

    async def go():
        await sch.init_db()
        monkeypatch.setattr(tennissim_web.auth, "read_session", lambda r: {"id": "u2"})
        await queries.get_or_create_casino_wallet("u2")
        await queries.update_casino_balance("u2", 100_000)
        start = await queries.get_casino_balance("u2")

        new = await tennissim_web.new_game(_Req())
        _force_winner(monkeypatch, "p2")
        stake = 750
        res = await tennissim_web.place_bet(
            _Req(), tennissim_web.BetBody(token=new["token"], side="p1", stake=stake),
        )
        assert res["won"] is False
        assert res["winner"] == "p2"
        assert res["payout"] == 0
        assert res["balance"] == start - stake
        assert res["p2_sets"] > res["p1_sets"]

    _run(go())


def test_payout_recomputed_server_side_and_forged_token_rejected(monkeypatch, tmp_path):
    sch.DB_PATH = str(tmp_path / "t3.db"); queries.DB_PATH = sch.DB_PATH

    async def go():
        await sch.init_db()
        monkeypatch.setattr(tennissim_web.auth, "read_session", lambda r: {"id": "u3"})
        await queries.get_or_create_casino_wallet("u3")
        await queries.update_casino_balance("u3", 100_000)

        new = await tennissim_web.new_game(_Req())
        p1_prob = new["p1_prob"]

        # The client body carries only token/side/stake — no odds. Payout on a win
        # is ALWAYS int(stake * 1/prob) from the signed token, whatever the client does.
        _force_winner(monkeypatch, "p1")
        stake = 300
        res = await tennissim_web.place_bet(
            _Req(), tennissim_web.BetBody(token=new["token"], side="p1", stake=stake),
        )
        assert res["payout"] == int(stake * (1 / p1_prob))

        # A tampered token can't be decoded → rejected (no payout math runs).
        forged = new["token"][:-3] + "zzz"
        bad = await tennissim_web.place_bet(
            _Req(), tennissim_web.BetBody(token=forged, side="p1", stake=stake),
        )
        assert getattr(bad, "status_code", None) == 400
        assert "expired" in json.loads(bad.body)["error"]

    _run(go())


def test_overdraw_rejected(monkeypatch, tmp_path):
    sch.DB_PATH = str(tmp_path / "t4.db"); queries.DB_PATH = sch.DB_PATH

    async def go():
        await sch.init_db()
        monkeypatch.setattr(tennissim_web.auth, "read_session", lambda r: {"id": "u4"})
        await queries.get_or_create_casino_wallet("u4")
        bal = await queries.get_casino_balance("u4")

        new = await tennissim_web.new_game(_Req())
        res = await tennissim_web.place_bet(
            _Req(), tennissim_web.BetBody(token=new["token"], side="p1", stake=bal + 1),
        )
        assert getattr(res, "status_code", None) == 400
        assert "not enough coins" in json.loads(res.body)["error"]
        # Balance untouched.
        assert await queries.get_casino_balance("u4") == bal

    _run(go())
