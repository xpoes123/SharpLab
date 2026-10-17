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
import statistics

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from bot.cogs import mlbsim, nbasim, nflsim, soccersim, tennissim
from db import queries
from web import auth, nfl_engine, nfl_teams_2026, simplay

router = APIRouter(prefix="/api/v1/casino/sim")

MIN_BET, MAX_BET = 1, 1_000_000
VIG = 0.95          # 5% hold baked into the moneyline decimal price
OU_DEC = 1.909      # -110 spread/total price (~4.5% hold)
NFL_PRESIM = 700    # engine sims at /new to set the NFL lines from its own distribution
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


def _nfl_matchup() -> tuple[dict, dict]:
    """Pick two distinct real 2026 teams. Returns (home_params, away_params)."""
    abbrs = secrets.SystemRandom().sample(list(nfl_teams_2026.NFL_TEAMS_2026), 2)
    return nfl_teams_2026.team(abbrs[0]), nfl_teams_2026.team(abbrs[1])


def _fair_line(samples: list[int]) -> float:
    """Pick the half-point line (x.5, so never a push) whose empirical cover rate is
    closest to 50/50 — no directional bias, so neither side carries a free edge."""
    import math
    n = len(samples)
    base = math.floor(statistics.median(samples))
    best = None
    for L in (base - 2.5, base - 1.5, base - 0.5, base + 0.5, base + 1.5, base + 2.5):
        over = sum(1 for v in samples if v > L) / n
        d = abs(over - 0.5)
        if best is None or d < best[0]:
            best = (d, L)
    return best[1]


def _nfl_lines(home: dict, away: dict, n: int = NFL_PRESIM) -> tuple[float, float, float]:
    """Monte-Carlo the engine to set fair lines: (home_prob, spread, total).

    spread is the home margin line (positive = home favored); total is the O/U. Both are
    the half-point that splits the sampled outcomes closest to 50/50, so each side prices
    at -110 with no skew. home_prob (ties count as half) prices the moneyline.
    """
    margins, totals, hw = [], [], 0.0
    for _ in range(n):
        g = nfl_engine.simulate(home, away)
        margins.append(g.home_score - g.away_score)
        totals.append(g.home_score + g.away_score)
        hw += 1.0 if g.winner == "home" else 0.5 if g.winner == "tie" else 0.0
    return hw / n, _fair_line(margins), _fair_line(totals)


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

    spread = total = None
    if sport == "nfl":
        home, away = _nfl_matchup()
        hn, ha = home["name"], home["abbr"]
        an, aa = away["name"], away["abbr"]
        home_prob, spread, total = _nfl_lines(home, away)
    else:
        hn, ha, an, aa, home_prob = _matchup(sport)

    # clamp so pricing never blows up on a near-certain favorite
    home_prob = min(0.92, max(0.08, home_prob))
    token = _signer.dumps([sport, ha, aa, home_prob, spread, total])
    prices = _price(home_prob)
    out = {
        "token": token, "sport": sport, "sport_label": SPORTS[sport]["label"],
        "home": {"abbr": ha, "name": hn}, "away": {"abbr": aa, "name": an},
        "home_prob": round(home_prob, 4),
        "home_american": prices["home_american"], "away_american": prices["away_american"],
        "home_dec": prices["home_dec"], "away_dec": prices["away_dec"],
    }
    if sport == "nfl":
        out.update({"simcast": True, "spread": spread, "total": total,
                    "spread_dec": OU_DEC, "total_dec": OU_DEC})
    return out


class BetBody(BaseModel):
    token: str
    side: str            # "home"|"away" (ml/spread) or "over"|"under" (total)
    stake: int
    market: str = "ml"   # "ml" | "spread" | "total" (NFL only)


@router.post("/bet")
async def place_bet(request: Request, body: BetBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    try:
        sport, home_abbr, away_abbr, home_prob, spread, total = _signer.loads(
            body.token, max_age=TOKEN_TTL)
    except (BadSignature, SignatureExpired, ValueError):
        return JSONResponse({"error": "this game expired — start a new one"}, status_code=400)

    stake = int(body.stake)
    if stake < MIN_BET or stake > MAX_BET:
        return JSONResponse({"error": f"bet must be {MIN_BET}–{MAX_BET:,}"}, status_code=400)

    prices = _price(home_prob)  # recomputed server-side; client odds are never trusted
    try:
        await queries.update_casino_balance(uid, -stake)
    except ValueError:
        return JSONResponse({"error": "not enough coins"}, status_code=400)

    if sport == "nfl":
        result = _resolve_nfl(home_abbr, away_abbr, prices, spread, total, body.market,
                              body.side, stake)
    else:
        result = _resolve_simple(sport, home_abbr, away_abbr, home_prob, prices,
                                 body.side, stake)

    if result["payout"]:
        await queries.update_casino_balance(uid, result["payout"])
    await queries.log_casino_result(uid, f"{sport}sim", stake, result["payout"])
    result["balance"] = await queries.get_casino_balance(uid) or 0
    result["stake"] = stake
    return result


def _resolve_simple(sport, home_abbr, away_abbr, home_prob, prices, side, stake) -> dict:
    """Non-NFL: CSPRNG winner weighted on home_prob → cosmetic timeline → exact 5% edge."""
    side = "home" if side == "home" else "away"
    winner = "home" if secrets.randbelow(10 ** 9) / 10 ** 9 < home_prob else "away"
    home_score, away_score, timeline = simplay.build(sport, winner)
    won = side == winner
    dec = prices["home_dec"] if side == "home" else prices["away_dec"]
    return {
        "sport": sport, "home_abbr": home_abbr, "away_abbr": away_abbr,
        "home_score": home_score, "away_score": away_score, "timeline": timeline,
        "winner": winner, "side": side, "won": won, "push": False,
        "payout": round(stake * dec) if won else 0,
    }


def _resolve_nfl(home_abbr, away_abbr, prices, spread, total, market, side, stake) -> dict:
    """NFL: run the real down-by-down engine, resolve the chosen market off the score."""
    home, away = nfl_teams_2026.team(home_abbr), nfl_teams_2026.team(away_abbr)
    g = nfl_engine.simulate(home, away)
    margin = g.home_score - g.away_score
    tot = g.home_score + g.away_score
    market = market if market in ("ml", "spread", "total") else "ml"

    push = False
    if market == "spread":
        side = "home" if side == "home" else "away"
        cover = "home" if margin > spread else "away"  # .5 line → no push
        won, dec = side == cover, OU_DEC
    elif market == "total":
        side = "over" if side == "over" else "under"
        res = "over" if tot > total else "under"       # .5 line → no push
        won, dec = side == res, OU_DEC
    else:  # moneyline
        side = "home" if side == "home" else "away"
        if g.winner == "tie":
            push, won = True, False
        else:
            won = side == g.winner
        dec = prices["home_dec"] if side == "home" else prices["away_dec"]

    payout = stake if push else (round(stake * dec) if won else 0)
    return {
        "sport": "nfl", "simcast": True,
        "home": {"abbr": home.get("abbr", home_abbr), "name": home.get("name", home_abbr)},
        "away": {"abbr": away.get("abbr", away_abbr), "name": away.get("name", away_abbr)},
        "home_abbr": home_abbr, "away_abbr": away_abbr,
        "home_score": g.home_score, "away_score": g.away_score, "winner": g.winner,
        "market": market, "side": side, "line": {"spread": spread, "total": total},
        "won": won, "push": push, "payout": payout,
        "plays": g.plays, "drives": g.drives,
    }
