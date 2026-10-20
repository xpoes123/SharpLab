"""Unified Sports Sim — money paths across all five sports + server-side odds recompute."""
import asyncio
import os
import tempfile

from db import queries as q
from db import schema as sch
from web import sim


class _Req:
    cookies: dict = {}
    headers: dict = {}


def _run(c):
    return asyncio.run(c)


def _tok(sport, home, away, home_prob, spread=None, total=None):
    # Token layout since #453: [sport, home, away, home_prob, spread, total] (spread/total NFL-only).
    return sim._signer.dumps([sport, home, away, home_prob, spread, total])


def _fresh():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")


def test_new_returns_normalized_shape_all_sports(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(sim.auth, "read_session", lambda r: {"id": "u"})
        for sport in ("nba", "nfl", "mlb", "tennis", "soccer"):
            res = await sim.new_game(_Req(), sim.NewBody(sport=sport))
            assert res["sport"] == sport
            assert res["home"]["abbr"] and res["away"]["abbr"]
            assert 0.0 < res["home_prob"] < 1.0
            assert res["home_american"] and res["away_american"]

    _run(go())


def test_winning_bet_pays_and_losing_pays_zero(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(sim.auth, "read_session", lambda r: {"id": "w"})
        await q.get_or_create_casino_wallet("w")
        # force the home team to win
        monkeypatch.setattr(sim.secrets, "randbelow", lambda n: 0)  # draw < prob → home
        tok = _tok("nba", "LAL", "BOS", 0.6)
        r = await sim.place_bet(_Req(), sim.BetBody(token=tok, side="home", stake=100))
        assert r["won"] and r["winner"] == "home" and r["payout"] > 100
        assert r["home_score"] > r["away_score"]
        assert r["timeline"] and r["timeline"][-1]["home"] == r["home_score"]
        # a home-winner game, bet away → lose
        tok2 = _tok("nba", "LAL", "BOS", 0.6)
        r2 = await sim.place_bet(_Req(), sim.BetBody(token=tok2, side="away", stake=100))
        assert not r2["won"] and r2["payout"] == 0

    _run(go())


def test_odds_recomputed_from_token_not_client(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(sim.auth, "read_session", lambda r: {"id": "t"})
        await q.get_or_create_casino_wallet("t")
        monkeypatch.setattr(sim.secrets, "randbelow", lambda n: 0)  # home wins
        # token says prob 0.5 → home_dec ≈ 1.9; payout must follow the token, not any client value
        tok = _tok("mlb", "NYY", "BOS", 0.5)
        r = await sim.place_bet(_Req(), sim.BetBody(token=tok, side="home", stake=100))
        assert r["payout"] == round(100 * round((1 / 0.5) * sim.VIG, 3))

    _run(go())


def test_bad_token_and_overdraw_rejected(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(sim.auth, "read_session", lambda r: {"id": "x"})
        await q.get_or_create_casino_wallet("x")
        bad = await sim.place_bet(_Req(), sim.BetBody(token="garbage", side="home", stake=10))
        assert bad.status_code == 400
        tok = _tok("nba", "LAL", "BOS", 0.5)
        over = await sim.place_bet(_Req(), sim.BetBody(token=tok, side="home", stake=10 ** 12))
        assert over.status_code == 400  # stake over MAX_BET

    _run(go())


def test_all_sports_settle_without_error(monkeypatch):
    _fresh()

    async def go():
        await sch.init_db()
        monkeypatch.setattr(sim.auth, "read_session", lambda r: {"id": "a"})
        await q.get_or_create_casino_wallet("a")
        for sport in ("nba", "mlb", "tennis", "soccer"):
            for w in (0, 10 ** 9 - 1):  # both winners
                monkeypatch.setattr(sim.secrets, "randbelow", lambda n, _w=w: min(_w, n - 1))
                r = await sim.place_bet(_Req(), sim.BetBody(token=_tok(sport, "AAA", "BBB", 0.5),
                                                            side="home", stake=1))
                assert "balance" in r and isinstance(r["timeline"], list)
        # NFL (#453) runs the down-by-down engine and returns a play-by-play simcast, with
        # moneyline / spread / total markets resolved off the final score.
        tok = _tok("nfl", "AAA", "BBB", 0.5, spread=-3.5, total=44.5)
        for market, side in (("ml", "home"), ("spread", "away"), ("total", "over")):
            r = await sim.place_bet(_Req(), sim.BetBody(token=tok, side=side, stake=1, market=market))
            assert "balance" in r and r["market"] == market and isinstance(r["plays"], list)

    _run(go())
