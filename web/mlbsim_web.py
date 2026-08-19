"""Web MLB Sim — solo moneyline bet on a simulated MLB game. Two stateless steps:

  POST /new  → pick a matchup, generate a home win probability, price the two
               moneylines from that prob (fair multiplier = 1/prob, same as the
               Discord cog), and sign the fixed facts (home_abbr, away_abbr,
               home_prob) into a short-lived token. Odds are ALWAYS recomputed
               from the token server-side; anything the client sends is ignored.
  POST /bet  → decode the token, debit the stake atomically, draw the winner with
               a CSPRNG weighted on home_prob (so the priced edge is exact),
               fabricate a flavor runs line consistent with that winner, pay out,
               and log it.

Reuses the Discord cog's pure sim math (bot/cogs/mlbsim.py) so the two stay in
sync. There is no spread/total in baseball sim — just the moneyline.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from bot.cogs.mlbsim import (
    _generate_win_prob,
    _payout_multiplier,
    _pick_matchup,
    _prob_to_american,
    _simulate_inning,
)
from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/casino/mlbsim")

MIN_BET, MAX_BET = 1, 1_000_000
TOKEN_TTL = 900     # signed matchup good for 15 minutes
NUM_INNINGS = 9

_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="mlbsim")


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _final_score(winner: str, home_prob: float) -> tuple[int, int]:
    """A plausible runs line consistent with the drawn winner — flavor only,
    never changes who won. Simulate 9 innings for atmosphere, then force the
    drawn winner strictly ahead so there are no ties or contradictions."""
    home = away = 0
    for _ in range(NUM_INNINGS):
        a_runs, h_runs = _simulate_inning(home_prob)
        away += a_runs
        home += h_runs
    if winner == "home" and home <= away:
        home = away + 1 + secrets.randbelow(3)
    elif winner == "away" and away <= home:
        away = home + 1 + secrets.randbelow(3)
    return home, away


@router.post("/new")
async def new_game(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)

    home, away = _pick_matchup()          # ((home_name, home_abbr), (away_name, away_abbr))
    home_name, home_abbr = home
    away_name, away_abbr = away
    home_prob = _generate_win_prob()
    away_prob = 1 - home_prob

    # Only these three facts are trusted at /bet time — everything else is derived.
    token = _signer.dumps([home_abbr, away_abbr, home_prob])

    return {
        "token": token,
        "home": {"abbr": home_abbr, "name": home_name},
        "away": {"abbr": away_abbr, "name": away_name},
        "home_prob": home_prob,
        "home_odds": _prob_to_american(home_prob),
        "away_odds": _prob_to_american(away_prob),
        "home_mult": _payout_multiplier(home_prob),
        "away_mult": _payout_multiplier(away_prob),
    }


class BetBody(BaseModel):
    token: str
    side: str   # "home" | "away"
    stake: int


@router.post("/bet")
async def place_bet(request: Request, body: BetBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)

    try:
        home_abbr, away_abbr, home_prob = _signer.loads(body.token, max_age=TOKEN_TTL)
    except (BadSignature, SignatureExpired, ValueError):
        return JSONResponse({"error": "this game expired — start a new one"}, status_code=400)

    side = "home" if body.side == "home" else "away"
    stake = int(body.stake)
    if stake < MIN_BET or stake > MAX_BET:
        return JSONResponse({"error": f"bet must be {MIN_BET}–{MAX_BET:,}"}, status_code=400)

    # Recompute the payout multiplier server-side from the trusted prob — never
    # trust the client. Fair multiplier = 1/prob (same as the Discord cog).
    mult = _payout_multiplier(home_prob if side == "home" else 1 - home_prob)

    # 1) Debit atomically; rejects overdraw.
    try:
        await queries.update_casino_balance(uid, -stake)
    except ValueError:
        return JSONResponse({"error": "not enough coins"}, status_code=400)

    # 2) CSPRNG weighted draw — this drives the payout so the priced edge is exact.
    winner = "home" if secrets.randbelow(10 ** 9) / 10 ** 9 < home_prob else "away"

    # 3) Flavor runs line consistent with the winner.
    home_score, away_score = _final_score(winner, home_prob)

    # 4) Pay out.
    won = side == winner
    payout = int(stake * mult) if won else 0
    if payout:
        await queries.update_casino_balance(uid, payout)
    await queries.log_casino_result(uid, "mlbsim", stake, payout)
    balance = await queries.get_casino_balance(uid) or 0

    return {
        "home_abbr": home_abbr,
        "away_abbr": away_abbr,
        "home_score": home_score,
        "away_score": away_score,
        "winner": winner,
        "winner_abbr": home_abbr if winner == "home" else away_abbr,
        "side": side,
        "won": won,
        "payout": payout,
        "stake": stake,
        "balance": balance,
    }
