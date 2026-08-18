"""Web NBA Sim — solo moneyline bet on a simulated NBA game. Two stateless steps:

  POST /new  → pick a matchup, generate ratings, compute home_prob / spread / total,
               price the two moneylines with a 5% vig, and sign the fixed facts
               (home_abbr, away_abbr, home_prob, spread, total) into a short-lived
               token. Odds are ALWAYS recomputed from the token server-side; anything
               the client sends is ignored.
  POST /bet  → decode the token, debit the stake atomically, draw the winner with a
               CSPRNG weighted on home_prob (so the 5% edge is exact), fabricate a
               flavor final score consistent with that winner, pay out, and log it.

Reuses the Discord cog's pure sim math (bot/cogs/nbasim.py) so the two stay in sync.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from bot.cogs.nbasim import (
    _compute_home_prob,
    _compute_spread,
    _compute_total,
    _generate_ratings,
    _pick_matchup,
)
from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/casino/nbasim")

MIN_BET, MAX_BET = 1, 1_000_000
VIG = 0.95          # 5% hold baked into the decimal price
TOKEN_TTL = 900     # signed matchup good for 15 minutes

_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="nbasim")


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _dec_to_american(dec: float) -> str:
    """Decimal (total-return) odds → American string, e.g. 1.9 → '-111', 2.6 → '+160'."""
    if dec <= 1:
        return "—"
    if dec >= 2:
        return f"+{round((dec - 1) * 100)}"
    return str(-round(100 / (dec - 1)))


def _price(home_prob: float) -> dict:
    """Two-way moneyline with a 5% vig on each side (decimal = total return)."""
    home_dec = round((1 / home_prob) * VIG, 3)
    away_dec = round((1 / (1 - home_prob)) * VIG, 3)
    return {
        "home_dec": home_dec,
        "away_dec": away_dec,
        "home_american": _dec_to_american(home_dec),
        "away_american": _dec_to_american(away_dec),
    }


@router.post("/new")
async def new_game(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)

    home, away = _pick_matchup()          # ((home_name, home_abbr), (away_name, away_abbr))
    home_name, home_abbr = home
    away_name, away_abbr = away
    h_off, h_def, h_coa = _generate_ratings(home_abbr)
    a_off, a_def, a_coa = _generate_ratings(away_abbr)
    home_prob = _compute_home_prob(h_off, h_def, h_coa, a_off, a_def, a_coa)
    spread = _compute_spread(home_prob)
    total = _compute_total(h_off, h_def, a_off, a_def)

    # Only these five facts are trusted at /bet time — everything below is derived.
    token = _signer.dumps([home_abbr, away_abbr, home_prob, spread, total])

    return {
        "token": token,
        "home": {"abbr": home_abbr, "name": home_name},
        "away": {"abbr": away_abbr, "name": away_name},
        "moneyline": _price(home_prob),
        "spread": spread,
        "total": total,
    }


class BetBody(BaseModel):
    token: str
    side: str   # "home" | "away"
    stake: int


def _final_score(winner: str, home_prob: float, total: float, spread: float) -> tuple[int, int]:
    """A plausible final consistent with the drawn winner — flavor only, never
    changes who won. Base each team around total/2, tilt by the spread, add noise,
    then force the winner strictly ahead."""
    half = total / 2
    # spread is home's margin (positive = home favored); split it across the two.
    home = half + spread / 2 + (secrets.randbelow(13) - 6)
    away = half - spread / 2 + (secrets.randbelow(13) - 6)
    home, away = max(70, round(home)), max(70, round(away))
    if winner == "home" and home <= away:
        home = away + 1 + secrets.randbelow(6)
    elif winner == "away" and away <= home:
        away = home + 1 + secrets.randbelow(6)
    return home, away


@router.post("/bet")
async def place_bet(request: Request, body: BetBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)

    try:
        home_abbr, away_abbr, home_prob, spread, total = _signer.loads(
            body.token, max_age=TOKEN_TTL,
        )
    except (BadSignature, SignatureExpired, ValueError):
        return JSONResponse({"error": "this game expired — start a new one"}, status_code=400)

    side = "home" if body.side == "home" else "away"
    stake = int(body.stake)
    if stake < MIN_BET or stake > MAX_BET:
        return JSONResponse({"error": f"bet must be {MIN_BET}–{MAX_BET:,}"}, status_code=400)

    # Recompute odds server-side from the trusted prob — never trust the client.
    prices = _price(home_prob)

    # 1) Debit atomically; rejects overdraw.
    try:
        await queries.update_casino_balance(uid, -stake)
    except ValueError:
        return JSONResponse({"error": "not enough coins"}, status_code=400)

    # 2) CSPRNG weighted draw — this drives the payout so the 5% edge is exact.
    winner = "home" if secrets.randbelow(10 ** 9) / 10 ** 9 < home_prob else "away"

    # 3) Flavor score consistent with the winner.
    home_score, away_score = _final_score(winner, home_prob, total, spread)

    # 4) Pay out.
    won = side == winner
    dec = prices["home_dec"] if side == "home" else prices["away_dec"]
    payout = round(stake * dec) if won else 0
    if payout:
        await queries.update_casino_balance(uid, payout)
    await queries.log_casino_result(uid, "nbasim", stake, payout)
    balance = await queries.get_casino_balance(uid) or 0

    return {
        "home_abbr": home_abbr,
        "away_abbr": away_abbr,
        "home_score": home_score,
        "away_score": away_score,
        "winner": winner,
        "side": side,
        "won": won,
        "payout": payout,
        "stake": stake,
        "balance": balance,
    }
