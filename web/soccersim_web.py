"""Web Soccer Sim — solo two-way moneyline bet on a simulated soccer match.
Ties in the sim are broken by extra time + penalties, so there is NO draw bet:
every match has a home or away winner. Two stateless steps:

  POST /new  → pick a pool (clubs or nationals), draw two DISTINCT teams from it
               with a CSPRNG, generate a home win probability from the two team
               ratings, price the two moneylines (fair multiplier = 1/prob, same
               as the Discord cog), and sign the fixed facts (home_abbr,
               away_abbr, home_prob) into a short-lived token. Odds are ALWAYS
               recomputed from the token server-side; the client is never trusted.
  POST /bet  → decode the token, debit the stake atomically, draw the winner with
               a CSPRNG weighted on home_prob (so the priced edge is exact),
               fabricate a flavor goals line consistent with that winner, pay out,
               and log it.

Reuses the Discord cog's pure sim math (bot/cogs/soccersim.py) so the two stay in
sync. There is no spread/total in soccer sim — just the two-way moneyline.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from bot.cogs.soccersim import (
    CLUB_TEAMS,
    NATIONAL_TEAMS,
    _generate_win_prob,
    _payout_multiplier,
    _prob_to_american,
    _simulate_half,
)
from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/casino/soccersim")

MIN_BET, MAX_BET = 1, 1_000_000
TOKEN_TTL = 900     # signed matchup good for 15 minutes

_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="soccersim")


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _pick_matchup():
    """Pick a pool (clubs or nationals) then draw two DISTINCT teams from it via a
    CSPRNG. Matchup choice doesn't affect the priced edge, but we use secrets
    throughout the web port to keep anything touching a coin game fair."""
    pool = secrets.choice([CLUB_TEAMS, NATIONAL_TEAMS])
    home = secrets.choice(pool)
    away = secrets.choice(pool)
    while away is home:
        away = secrets.choice(pool)
    return home, away


def _final_score(home, away, winner: str, home_prob: float) -> tuple[int, int]:
    """A plausible goals line consistent with the drawn winner — flavor only,
    never changes who won. Simulate two halves for atmosphere, then force the
    drawn winner strictly ahead (the real sim can end level, but the web game has
    no draw bet — ET/pens are abstracted away into the guaranteed winner)."""
    h1, a1, _ = _simulate_half(home, away, 1, home_prob)
    h2, a2, _ = _simulate_half(home, away, 2, home_prob)
    home_goals = h1 + h2
    away_goals = a1 + a2
    if winner == "home" and home_goals <= away_goals:
        home_goals = away_goals + 1 + secrets.randbelow(2)
    elif winner == "away" and away_goals <= home_goals:
        away_goals = home_goals + 1 + secrets.randbelow(2)
    return home_goals, away_goals


@router.post("/new")
async def new_game(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)

    home, away = _pick_matchup()
    home_prob = _generate_win_prob(home, away)
    away_prob = 1 - home_prob

    # Only these three facts are trusted at /bet time — everything else is derived.
    token = _signer.dumps([home.abbr, away.abbr, home_prob])

    return {
        "token": token,
        "home": {"abbr": home.abbr, "name": home.name},
        "away": {"abbr": away.abbr, "name": away.name},
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

    # 3) Flavor goals line consistent with the winner. Look up the drawn teams so
    #    the sim uses their real ratings; fall back gracefully if not found.
    home_team = next((t for t in CLUB_TEAMS + NATIONAL_TEAMS if t.abbr == home_abbr), None)
    away_team = next((t for t in CLUB_TEAMS + NATIONAL_TEAMS if t.abbr == away_abbr), None)
    if home_team and away_team:
        home_score, away_score = _final_score(home_team, away_team, winner, home_prob)
    else:
        home_score, away_score = (1, 0) if winner == "home" else (0, 1)

    # 4) Pay out.
    won = side == winner
    payout = int(stake * mult) if won else 0
    if payout:
        await queries.update_casino_balance(uid, payout)
    await queries.log_casino_result(uid, "soccersim", stake, payout)
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
