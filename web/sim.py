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

import asyncio
import secrets
import statistics
import time

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


# ── Manual mode: live in-game betting on the NFL simcast ──────────────────────
#
# The game is fully simulated server-side at /start but the client only pulls one play
# at a time via /advance; the rest stays hidden here, so a client that already knows the
# outcome can't live-bet on it (betting is only ever at the current reveal frontier).
# Live odds are priced by Monte-Carlo continuation of the engine (web/nfl_engine.live_probs),
# which is calibrated by construction, so the LIVE_VIG hold is positive at every state.

LIVE_GAMES: dict[str, dict] = {}
LIVE_TTL = 1800          # drop abandoned games after 30 min
LIVE_MAX = 400           # hard cap on concurrent live games
# Live betting is only offered on competitive states (band). Lopsided games suspend —
# which is realistic AND avoids the longshot-pricing danger, where a small calibration
# error explodes into a player edge at long odds. A wide hold covers residual noise.
LIVE_VIG = 0.90          # 10% live hold
LIVE_BAND = (0.30, 0.70)  # suspend a market side priced outside this probability
LIVE_K = 220             # continuations per live price
LIVE_MAX_STAKE = 25_000  # per live bet (bounds exposure to any residual pricing leak)


def _live_gc() -> None:
    now = time.time()
    for gid in [g for g, v in LIVE_GAMES.items() if now - v["created"] > LIVE_TTL]:
        LIVE_GAMES.pop(gid, None)
    if len(LIVE_GAMES) > LIVE_MAX:  # evict oldest
        for gid in sorted(LIVE_GAMES, key=lambda g: LIVE_GAMES[g]["created"])[:len(LIVE_GAMES) - LIVE_MAX]:
            LIVE_GAMES.pop(gid, None)


def _sh_receiver(plays: list, home_abbr: str) -> str | None:
    """Which side received the 2nd-half kickoff (first real snap with <=1800s left)."""
    for pl in plays:
        if pl["secs_left"] <= 1800 and pl["type"] in ("run", "pass", "sack", "punt", "fg"):
            return "home" if pl["off"] == home_abbr else "away"
    return None


def _state_from_play(pl: dict, home_abbr: str, sh_recv: str | None) -> dict:
    sl = pl["secs_left"]
    half, clock = (1, sl - 1800) if sl > 1800 else (2, sl)
    off_home = pl["off"] == home_abbr
    if pl["type"] in ("kickoff", "xp", "2pt") or pl["fieldpos"] is None:
        poss, ownyl = ("away" if off_home else "home"), 25
    else:
        poss = "home" if off_home else "away"
        fp = pl["fieldpos"]
        ownyl = max(1, min(99, fp if off_home else 100 - fp))
    return {"half": half, "clock": max(0, clock), "home_score": pl["home"],
            "away_score": pl["away"], "possessing": poss, "ownyl": ownyl,
            "second_half_receiver": sh_recv}


def _side(p: float) -> dict | None:
    """Price one market side at LIVE_VIG, or None (suspended) outside the band."""
    if not (LIVE_BAND[0] <= p <= LIVE_BAND[1]):
        return None
    dec = round((1 / p) * LIVE_VIG, 3)
    return {"dec": dec, "american": _dec_to_american(dec)}


def _live_odds(g: dict, p_win: float, p_cover: float, p_over: float) -> dict:
    return {
        "ml": {"home": _side(p_win), "away": _side(1 - p_win)},
        "spread": {"line": g["spread"], "home": _side(p_cover), "away": _side(1 - p_cover)},
        "total": {"line": g["total"], "over": _side(p_over), "under": _side(1 - p_over)},
    }


def _pregame_odds(g: dict) -> dict:
    prices = _price(g["home_prob"])
    ou = {"dec": OU_DEC, "american": _dec_to_american(OU_DEC)}
    return {
        "ml": {"home": {"dec": prices["home_dec"], "american": prices["home_american"]},
               "away": {"dec": prices["away_dec"], "american": prices["away_american"]}},
        "spread": {"line": g["spread"], "home": dict(ou), "away": dict(ou)},
        "total": {"line": g["total"], "over": dict(ou), "under": dict(ou)},
    }


def _price_live(g: dict, cursor: int) -> dict:
    """Live odds for the state AFTER play[cursor-1] (the current reveal frontier)."""
    if cursor <= 0 or cursor > len(g["plays"]):
        return _pregame_odds(g)
    st = _state_from_play(g["plays"][cursor - 1], g["home_abbr"], g["sh_recv"])
    if st["half"] == 2 and st["clock"] <= 0:  # game essentially over → suspend
        return {"ml": {"home": None, "away": None},
                "spread": {"line": g["spread"], "home": None, "away": None},
                "total": {"line": g["total"], "over": None, "under": None}}
    p_win, p_cover, p_over = nfl_engine.live_probs(
        g["home"], g["away"], st, g["spread"], g["total"], k=LIVE_K)
    return _live_odds(g, p_win, p_cover, p_over)


class StartBody(BaseModel):
    sport: str = "nfl"


@router.post("/start")
async def start_live(request: Request, body: StartBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    if body.sport != "nfl":
        return JSONResponse({"error": "live mode is NFL only"}, status_code=400)
    _live_gc()
    home, away = _nfl_matchup()
    home_prob, spread, total = _nfl_lines(home, away)
    home_prob = min(0.92, max(0.08, home_prob))
    g = nfl_engine.simulate(home, away)
    gid = secrets.token_urlsafe(12)
    LIVE_GAMES[gid] = {
        "uid": uid, "home": home, "away": away,
        "home_abbr": home["abbr"], "away_abbr": away["abbr"],
        "home_name": home["name"], "away_name": away["name"],
        "plays": g.plays, "drives": g.drives,
        "home_final": g.home_score, "away_final": g.away_score, "winner": g.winner,
        "spread": spread, "total": total, "home_prob": home_prob,
        "sh_recv": _sh_receiver(g.plays, home["abbr"]),
        "cursor": 0, "bets": [], "odds": None,
        "created": time.time(), "settled": False,
    }
    LIVE_GAMES[gid]["odds"] = _pregame_odds(LIVE_GAMES[gid])
    return {
        "gid": gid, "sport": "nfl", "simcast": True, "total_plays": len(g.plays),
        "home": {"abbr": home["abbr"], "name": home["name"]},
        "away": {"abbr": away["abbr"], "name": away["name"]},
        "odds": LIVE_GAMES[gid]["odds"], "cursor": 0, "done": False,
    }


def _owned(request: Request, gid: str) -> dict | None:
    uid = _uid(request)
    g = LIVE_GAMES.get(gid)
    return g if (g and uid and g["uid"] == uid) else None


class GidBody(BaseModel):
    gid: str


@router.post("/advance")
async def advance_live(request: Request, body: GidBody):
    g = _owned(request, body.gid)
    if not g:
        return JSONResponse({"error": "game not found — start a new one"}, status_code=404)
    plays = g["plays"]
    if g["cursor"] >= len(plays):
        return await _settle_live(request, body.gid)
    play = plays[g["cursor"]]
    g["cursor"] += 1
    done = g["cursor"] >= len(plays)
    if done:
        res = await _settle_live(request, body.gid)
        res["play"] = play
        return res
    g["odds"] = await asyncio.to_thread(_price_live, g, g["cursor"])  # CPU-bound off the loop
    return {"play": play, "cursor": g["cursor"], "done": False,
            "home_score": play["home"], "away_score": play["away"], "odds": g["odds"]}


class LiveBetBody(BaseModel):
    gid: str
    market: str   # "ml" | "spread" | "total"
    side: str     # "home"|"away" | "over"|"under"
    stake: int


@router.post("/livebet")
async def live_bet(request: Request, body: LiveBetBody):
    uid = _uid(request)
    g = _owned(request, body.gid)
    if not g:
        return JSONResponse({"error": "game not found"}, status_code=404)
    if g["settled"] or g["cursor"] >= len(g["plays"]):
        return JSONResponse({"error": "game is over"}, status_code=400)
    stake = int(body.stake)
    if stake < MIN_BET or stake > LIVE_MAX_STAKE:
        return JSONResponse({"error": f"live bet must be {MIN_BET}–{LIVE_MAX_STAKE:,}"}, status_code=400)
    market = body.market if body.market in ("ml", "spread", "total") else "ml"
    sides = {"ml": ("home", "away"), "spread": ("home", "away"), "total": ("over", "under")}[market]
    side = body.side if body.side in sides else sides[0]
    quote = (g["odds"].get(market) or {}).get(side)  # cached frontier odds — no re-rolling
    if not quote:
        return JSONResponse({"error": "that market is suspended right now"}, status_code=400)
    try:
        await queries.update_casino_balance(uid, -stake)
    except ValueError:
        return JSONResponse({"error": "not enough coins"}, status_code=400)
    g["bets"].append({"market": market, "side": side, "stake": stake, "dec": quote["dec"],
                      "cursor": g["cursor"]})
    balance = await queries.get_casino_balance(uid) or 0
    return {"accepted": True, "market": market, "side": side,
            "dec": quote["dec"], "american": quote["american"], "stake": stake, "balance": balance}


async def _settle_live(request: Request, gid: str) -> dict:
    g = LIVE_GAMES.pop(gid, None)  # claim-by-pop so a double /advance can't double-settle
    if g is None:
        return JSONResponse({"error": "game not found"}, status_code=404)
    uid = g["uid"]
    margin = g["home_final"] - g["away_final"]
    total_pts = g["home_final"] + g["away_final"]
    results = []
    for bt in g["bets"]:
        push = False
        if bt["market"] == "ml":
            if g["winner"] == "tie":
                push, won = True, False
            else:
                won = bt["side"] == g["winner"]
        elif bt["market"] == "spread":
            cover = "home" if margin > g["spread"] else "away"
            won = bt["side"] == cover
        else:
            res = "over" if total_pts > g["total"] else "under"
            won = bt["side"] == res
        payout = bt["stake"] if push else (round(bt["stake"] * bt["dec"]) if won else 0)
        if payout:
            await queries.update_casino_balance(uid, payout)
        await queries.log_casino_result(uid, "nflsim", bt["stake"], payout)
        results.append({**bt, "won": won, "push": push, "payout": payout})
    balance = await queries.get_casino_balance(uid) or 0
    return {
        "done": True, "settled": True,
        "home": {"abbr": g["home_abbr"], "name": g["home_name"]},
        "away": {"abbr": g["away_abbr"], "name": g["away_name"]},
        "home_score": g["home_final"], "away_score": g["away_final"], "winner": g["winner"],
        "line": {"spread": g["spread"], "total": g["total"]},
        "bets": results, "balance": balance, "cursor": g["cursor"],
    }


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
