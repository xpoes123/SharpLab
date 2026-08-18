"""Web Three Card Poker — Ante/Play vs the dealer. Two steps (deal → play|fold) with an
in-memory round: the ante is debited at deal, the Play bet at play, the total payout credited
once at resolve, round deleted (no replay). Dealer qualifies on Queen-high or better. Note the
3-card quirk: a straight beats a flush."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/casino/threecardpoker")
MIN_BET, MAX_BET = 1, 100_000
_ROUNDS: dict[str, dict] = {}

_RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
_RVAL = {r: i + 2 for i, r in enumerate(_RANKS)}  # 2..14
_SUITS = ["♠", "♥", "♦", "♣"]
# ante bonus (paid on your hand regardless of the dealer) — × ante
_ANTE_BONUS = {"straight_flush": 5, "trips": 4, "straight": 1}


def _deck() -> list[str]:
    d = [r + s for s in _SUITS for r in _RANKS]
    for i in range(len(d) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        d[i], d[j] = d[j], d[i]
    return d


def rank3(cards: list[str]) -> tuple:
    """(category_rank, tiebreak…) — higher compares better. Categories high→low:
    straight_flush 5, trips 4, straight 3, flush 2, pair 1, high 0."""
    vals = sorted((_RVAL[c[:-1]] for c in cards), reverse=True)
    flush = len({c[-1] for c in cards}) == 1
    # straight (A-2-3 wheel allowed; A-K-Q is just 14-13-12)
    uniq = sorted(set(vals))
    straight = len(uniq) == 3 and uniq[2] - uniq[0] == 2
    wheel = uniq == [2, 3, 14]
    straight = straight or wheel
    hi = 3 if wheel else vals[0]  # wheel's high card is the 3
    if vals[0] == vals[1] == vals[2]:
        return (4, vals[0])
    if straight and flush:
        return (5, hi)
    if straight:
        return (3, hi)
    if flush:
        return (2, *vals)
    if vals[0] == vals[1] or vals[1] == vals[2]:  # a pair
        pair = vals[1]  # middle is always in the pair for sorted 3 cards
        kicker = vals[2] if vals[0] == vals[1] else vals[0]
        return (1, pair, kicker)
    return (0, *vals)


def _cat_name(r: tuple) -> str:
    return {5: "straight_flush", 4: "trips", 3: "straight", 2: "flush", 1: "pair", 0: "high"}[r[0]]


def _dealer_qualifies(cards: list[str]) -> bool:
    r = rank3(cards)
    return r[0] > 0 or r[1] >= 12  # any made hand, or Queen-high (12) or better


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


class DealBody(BaseModel):
    ante: int


@router.post("/deal")
async def deal(request: Request, body: DealBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    ante = int(body.ante)
    if ante < MIN_BET or ante > MAX_BET:
        return JSONResponse({"error": f"ante must be {MIN_BET}–{MAX_BET:,}"}, status_code=400)
    try:
        await queries.update_casino_balance(uid, -ante)
    except ValueError:
        return JSONResponse({"error": "not enough coins"}, status_code=400)
    d = _deck()
    player, dealer = [d.pop(), d.pop(), d.pop()], [d.pop(), d.pop(), d.pop()]
    rid = secrets.token_urlsafe(12)
    _ROUNDS[rid] = {"uid": uid, "ante": ante, "player": player, "dealer": dealer}
    return {"round_id": rid, "player": player, "ante": ante,
            "player_rank": _cat_name(rank3(player))}


class PlayBody(BaseModel):
    round_id: str
    action: str  # "play" | "fold"


@router.post("/play")
async def play(request: Request, body: PlayBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    # Claim the round atomically (pop, not get) so two concurrent /play requests can't both
    # settle it and double-pay. In asyncio the pop is synchronous → exactly one caller wins.
    st = _ROUNDS.pop(body.round_id, None)
    if st is None or st["uid"] != uid:
        return JSONResponse({"error": "no active hand"}, status_code=400)
    ante = st["ante"]

    if body.action == "fold":
        await queries.log_casino_result(uid, "threecardpoker", ante, 0)
        bal = await queries.get_casino_balance(uid) or 0
        return {"done": True, "result": "fold", "payout": 0, "balance": bal,
                "dealer": st["dealer"], "player": st["player"]}

    # play: match the ante with a Play bet
    try:
        await queries.update_casino_balance(uid, -ante)
    except ValueError:
        return JSONResponse({"error": "not enough coins to play"}, status_code=400)

    pr, dr = rank3(st["player"]), rank3(st["dealer"])
    qualifies = _dealer_qualifies(st["dealer"])
    bonus = ante * _ANTE_BONUS.get(_cat_name(pr), 0)  # paid regardless of dealer

    if not qualifies:
        result, ret = "dealer_no_qualify", ante * 2 + ante  # ante wins 1:1, play pushes
    elif pr > dr:
        result, ret = "win", ante * 4  # ante + play both 1:1
    elif pr < dr:
        result, ret = "lose", 0
    else:
        result, ret = "push", ante * 2  # both push
    payout = ret + bonus
    if payout:
        await queries.update_casino_balance(uid, payout)
    await queries.log_casino_result(uid, "threecardpoker", ante * 2, payout)
    _ROUNDS.pop(body.round_id, None)
    bal = await queries.get_casino_balance(uid) or 0
    return {"done": True, "result": result, "payout": payout, "balance": bal,
            "dealer": st["dealer"], "dealer_qualifies": qualifies,
            "player": st["player"], "player_rank": _cat_name(pr),
            "dealer_rank": _cat_name(dr), "ante_bonus": bonus}
