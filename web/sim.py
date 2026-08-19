"""Unified Sports Sim — one page, pick a sport (NBA / NFL / MLB / Tennis / Soccer), bet the
moneyline, watch the game play out.

Two stateless steps mirroring the old per-sport sims, now behind ONE normalized contract:
  POST /new {sport} → pick a matchup for that sport, compute the true home win prob, price a
      two-way moneyline with a 5% house edge, and sign (sport, home_abbr, away_abbr, home_prob)
      into a short-lived token. Odds are ALWAYS recomputed from the token at /bet.
  POST /bet {token, side, stake} → debit atomically, draw the winner with a CSPRNG weighted on
      home_prob (so the 5% edge is exact), generate a cosmetic final score + play-by-play
      timeline (web/simplay.py), pay out, log under "<sport>sim" (keeps per-game stats history).

Per-sport rating math is reused from the Discord cogs so the sims stay in sync. Pricing is
unified to a single 5% vig here — the old MLB/Tennis/Soccer web sims used fair (zero-edge) odds,
which made them a slow coin faucet; a casino table should hold an edge.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from bot.cogs import mlbsim, nbasim, nflsim, soccersim, tennissim
from db import queries
from web import auth, simplay

router = APIRouter(prefix="/api/v1/casino/sim")

MIN_BET, MAX_BET = 1, 1_000_000
VIG = 0.95          # 5% hold baked into the decimal price
TOKEN_TTL = 900     # signed matchup good for 15 minutes

_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="sim")

SPORTS = {
    "nba": {"label": "NBA", "icon": "🏀"},
    "nfl": {"label": "NFL", "icon": "🏈"},
    "mlb": {"label": "MLB", "icon": "⚾"},
    "tennis": {"label": "Tennis", "icon": "🎾"},
    "soccer": {"label": "Soccer", "icon": "⚽"},
}


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _matchup(sport: str) -> tuple[str, str, str, str, float]:
    """Return (home_name, home_abbr, away_name, away_abbr, home_prob) for a sport."""
    if sport == "nba":
        (hn, ha), (an, aa) = nbasim._pick_matchup()
        ho, hd, hc = nbasim._generate_ratings(ha)
        ao, ad, ac = nbasim._generate_ratings(aa)
        return hn, ha, an, aa, nbasim._compute_home_prob(ho, hd, hc, ao, ad, ac)
    if sport == "nfl":
        (hn, ha), (an, aa) = nflsim._pick_matchup()
        ho, hd, hc = nflsim._generate_ratings(ha)
        ao, ad, ac = nflsim._generate_ratings(aa)
        return hn, ha, an, aa, nflsim._compute_home_prob(ho, hd, hc, ao, ad, ac)
    if sport == "mlb":
        (hn, ha), (an, aa) = mlbsim._pick_matchup()
        return hn, ha, an, aa, mlbsim._generate_win_prob()
    if sport == "tennis":
        p1, p2 = tennissim._pick_matchup()
        return p1.name, p1.short, p2.name, p2.short, tennissim._generate_win_prob(p1, p2)
    if sport == "soccer":
        pool = secrets.choice([soccersim.CLUB_TEAMS, soccersim.NATIONAL_TEAMS])
        home = secrets.choice(pool)
        away = secrets.choice(pool)
        while away is home:
            away = secrets.choice(pool)
        return home.name, home.abbr, away.name, away.abbr, soccersim._generate_win_prob(home, away)
    raise KeyError(sport)


def _dec_to_american(dec: float) -> str:
    if dec <= 1:
        return "—"
    if dec >= 2:
        return f"+{round((dec - 1) * 100)}"
    return str(-round(100 / (dec - 1)))


def _price(home_prob: float) -> dict:
    home_dec = round((1 / home_prob) * VIG, 3)
    away_dec = round((1 / (1 - home_prob)) * VIG, 3)
    return {"home_dec": home_dec, "away_dec": away_dec,
            "home_american": _dec_to_american(home_dec), "away_american": _dec_to_american(away_dec)}


class NewBody(BaseModel):
    sport: str = "nba"


@router.post("/new")
async def new_game(request: Request, body: NewBody):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    sport = body.sport if body.sport in SPORTS else "nba"
    hn, ha, an, aa, home_prob = _matchup(sport)
    # clamp so pricing never blows up on a near-certain favorite
    home_prob = min(0.92, max(0.08, home_prob))
    token = _signer.dumps([sport, ha, aa, home_prob])
    prices = _price(home_prob)
    return {
        "token": token, "sport": sport, "sport_label": SPORTS[sport]["label"],
        "home": {"abbr": ha, "name": hn}, "away": {"abbr": aa, "name": an},
        "home_prob": round(home_prob, 4),
        "home_american": prices["home_american"], "away_american": prices["away_american"],
        "home_dec": prices["home_dec"], "away_dec": prices["away_dec"],
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
        sport, home_abbr, away_abbr, home_prob = _signer.loads(body.token, max_age=TOKEN_TTL)
    except (BadSignature, SignatureExpired, ValueError):
        return JSONResponse({"error": "this game expired — start a new one"}, status_code=400)

    side = "home" if body.side == "home" else "away"
    stake = int(body.stake)
    if stake < MIN_BET or stake > MAX_BET:
        return JSONResponse({"error": f"bet must be {MIN_BET}–{MAX_BET:,}"}, status_code=400)

    prices = _price(home_prob)  # recomputed server-side; client odds are never trusted
    try:
        await queries.update_casino_balance(uid, -stake)
    except ValueError:
        return JSONResponse({"error": "not enough coins"}, status_code=400)

    # CSPRNG weighted draw drives the payout → exact 5% edge.
    winner = "home" if secrets.randbelow(10 ** 9) / 10 ** 9 < home_prob else "away"
    home_score, away_score, timeline = simplay.build(sport, winner)

    won = side == winner
    dec = prices["home_dec"] if side == "home" else prices["away_dec"]
    payout = round(stake * dec) if won else 0
    if payout:
        await queries.update_casino_balance(uid, payout)
    await queries.log_casino_result(uid, f"{sport}sim", stake, payout)
    balance = await queries.get_casino_balance(uid) or 0

    return {
        "sport": sport, "home_abbr": home_abbr, "away_abbr": away_abbr,
        "home_score": home_score, "away_score": away_score, "timeline": timeline,
        "winner": winner, "side": side, "won": won,
        "payout": payout, "stake": stake, "balance": balance,
    }
