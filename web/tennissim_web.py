"""Web Tennis Sim — solo moneyline bet on a simulated tennis match. Two
stateless steps:

  POST /new  → pick a same-tour matchup, generate p1's win probability, price
               the two moneylines from that prob (fair multiplier = 1/prob, same
               as the Discord cog), and sign the fixed facts (p1_abbr, p2_abbr,
               p1_prob) into a short-lived token. Odds are ALWAYS recomputed from
               the token server-side; anything the client sends is ignored.
  POST /bet  → decode the token, debit the stake atomically, draw the winner with
               a CSPRNG weighted on p1_prob (so the priced edge is exact),
               fabricate a flavor sets line consistent with that winner, pay out,
               and log it.

Reuses the Discord cog's pure sim math (bot/cogs/tennissim.py) so the two stay in
sync. There is no spread/total in a tennis match sim — just the moneyline.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from bot.cogs.tennissim import (
    SETS_TO_WIN,
    _generate_win_prob,
    _payout_multiplier,
    _pick_matchup,
    _prob_to_american,
    _simulate_game,
    _simulate_tiebreak,
)
from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/casino/tennissim")

MIN_BET, MAX_BET = 1, 1_000_000
TOKEN_TTL = 900     # signed matchup good for 15 minutes

_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="tennissim")


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _simulate_set(p1_prob: float) -> tuple[int, int]:
    """Play one set game-by-game (reusing the cog's per-game/tiebreak math) and
    return (p1_games, p2_games). Flavor only — no live updates."""
    p1g = p2g = 0
    p1_serving = secrets.randbelow(2) == 0
    while True:
        server_prob = p1_prob if p1_serving else 1 - p1_prob
        held = _simulate_game(server_prob)
        if p1_serving:
            p1g += 1 if held else 0
            p2g += 0 if held else 1
        else:
            p2g += 1 if held else 0
            p1g += 0 if held else 1
        p1_serving = not p1_serving

        if p1g >= 6 and p1g - p2g >= 2:
            return p1g, p2g
        if p2g >= 6 and p2g - p1g >= 2:
            return p1g, p2g
        if p1g == 6 and p2g == 6:
            tb1, tb2 = _simulate_tiebreak(p1_prob)
            return (7, 6) if tb1 > tb2 else (6, 7)


def _final_sets(winner: str, p1_prob: float) -> tuple[int, int]:
    """A plausible sets line consistent with the drawn winner — flavor only,
    never changes who won. Simulate a best-of-3 for atmosphere, then force the
    drawn winner to hold more sets so there are no ties or contradictions."""
    p1_sets = p2_sets = 0
    while p1_sets < SETS_TO_WIN and p2_sets < SETS_TO_WIN:
        p1g, p2g = _simulate_set(p1_prob)
        if p1g > p2g:
            p1_sets += 1
        else:
            p2_sets += 1
    # Force the drawn winner to be the match winner (flavor only).
    if (winner == "p1") != (p1_sets > p2_sets):
        p1_sets, p2_sets = p2_sets, p1_sets
    return p1_sets, p2_sets


@router.post("/new")
async def new_game(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)

    p1, p2 = _pick_matchup()          # (TennisPlayerInfo, TennisPlayerInfo)
    p1_prob = _generate_win_prob(p1, p2)
    p2_prob = 1 - p1_prob

    # Only these three facts are trusted at /bet time — everything else is derived.
    token = _signer.dumps([p1.short, p2.short, p1_prob])

    return {
        "token": token,
        "p1": {"abbr": p1.short, "name": p1.name},
        "p2": {"abbr": p2.short, "name": p2.name},
        "p1_prob": p1_prob,
        "p1_odds": _prob_to_american(p1_prob),
        "p2_odds": _prob_to_american(p2_prob),
        "p1_mult": _payout_multiplier(p1_prob),
        "p2_mult": _payout_multiplier(p2_prob),
    }


class BetBody(BaseModel):
    token: str
    side: str   # "p1" | "p2"
    stake: int


@router.post("/bet")
async def place_bet(request: Request, body: BetBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)

    try:
        p1_abbr, p2_abbr, p1_prob = _signer.loads(body.token, max_age=TOKEN_TTL)
    except (BadSignature, SignatureExpired, ValueError):
        return JSONResponse({"error": "this game expired — start a new one"}, status_code=400)

    side = "p1" if body.side == "p1" else "p2"
    stake = int(body.stake)
    if stake < MIN_BET or stake > MAX_BET:
        return JSONResponse({"error": f"bet must be {MIN_BET}–{MAX_BET:,}"}, status_code=400)

    # Recompute the payout multiplier server-side from the trusted prob — never
    # trust the client. Fair multiplier = 1/prob (same as the Discord cog).
    mult = _payout_multiplier(p1_prob if side == "p1" else 1 - p1_prob)

    # 1) Debit atomically; rejects overdraw.
    try:
        await queries.update_casino_balance(uid, -stake)
    except ValueError:
        return JSONResponse({"error": "not enough coins"}, status_code=400)

    # 2) CSPRNG weighted draw — this drives the payout so the priced edge is exact.
    winner = "p1" if secrets.randbelow(10 ** 9) / 10 ** 9 < p1_prob else "p2"

    # 3) Flavor sets line consistent with the winner.
    p1_sets, p2_sets = _final_sets(winner, p1_prob)

    # 4) Pay out.
    won = side == winner
    payout = int(stake * mult) if won else 0
    if payout:
        await queries.update_casino_balance(uid, payout)
    await queries.log_casino_result(uid, "tennissim", stake, payout)
    balance = await queries.get_casino_balance(uid) or 0

    return {
        "p1_abbr": p1_abbr,
        "p2_abbr": p2_abbr,
        "p1_sets": p1_sets,
        "p2_sets": p2_sets,
        "winner": winner,
        "winner_abbr": p1_abbr if winner == "p1" else p2_abbr,
        "side": side,
        "won": won,
        "payout": payout,
        "stake": stake,
        "balance": balance,
    }
