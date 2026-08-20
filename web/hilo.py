"""Web Hi-Lo — draw a card, bet whether the next is higher or lower. Two steps (deal → guess)
with an in-memory round: bet debited at deal, payout credited once at guess, round deleted.
Payout scales with the true odds (ties lose — that's the house edge)."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/casino/hilo")
MIN_BET, MAX_BET = 1, 100_000
_ROUNDS: dict[str, dict] = {}

_RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]  # 2..14
_SUITS = ["♠", "♥", "♦", "♣"]
_EDGE = 0.95  # 5% house edge baked into the payout multiplier


def _draw() -> tuple[str, int]:
    r = secrets.randbelow(13)
    return _RANKS[r] + secrets.choice(_SUITS), r + 2  # (label, rank 2..14)


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _odds(current: int) -> dict:
    """Winning-rank counts (of 13) and fair multipliers for each direction. Tie loses."""
    higher = 14 - current  # ranks strictly greater
    lower = current - 2    # ranks strictly lower
    return {
        "higher": {"count": higher, "mult": round(_EDGE * 13 / higher, 2) if higher else 0},
        "lower": {"count": lower, "mult": round(_EDGE * 13 / lower, 2) if lower else 0},
    }


class DealBody(BaseModel):
    bet: int


@router.post("/deal")
async def deal(request: Request, body: DealBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    bet = int(body.bet)
    if bet < MIN_BET or bet > MAX_BET:
        return JSONResponse({"error": f"bet must be {MIN_BET}–{MAX_BET:,}"}, status_code=400)
    try:
        await queries.update_casino_balance(uid, -bet)
    except ValueError:
        return JSONResponse({"error": "not enough coins"}, status_code=400)
    card, rank = _draw()
    rid = secrets.token_urlsafe(12)
    _ROUNDS[rid] = {"uid": uid, "bet": bet, "rank": rank}
    return {"round_id": rid, "card": card, "rank": rank, "odds": _odds(rank)}


class GuessBody(BaseModel):
    round_id: str
    direction: str  # "higher" | "lower"


@router.post("/guess")
async def guess(request: Request, body: GuessBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    st = _ROUNDS.pop(body.round_id, None)  # atomic claim → no double-settle race
    if st is None or st["uid"] != uid:
        return JSONResponse({"error": "no active round"}, status_code=400)
    direction = "lower" if body.direction == "lower" else "higher"
    card, rank = _draw()
    cur = st["rank"]
    won = (rank > cur) if direction == "higher" else (rank < cur)  # tie loses
    mult = _odds(cur)[direction]["mult"]
    payout = round(st["bet"] * mult) if won else 0
    if payout:
        await queries.update_casino_balance(uid, payout)
    await queries.log_casino_result(uid, "hilo", st["bet"], payout)
    balance = await queries.get_casino_balance(uid) or 0
    return {"card": card, "rank": rank, "prev": cur, "won": won, "direction": direction,
            "payout": payout, "balance": balance}

# Refund the up-front bet if the process restarts mid-hand (see web/inflight.py).
from web import inflight as _inflight  # noqa: E402
_inflight.register("hilo", _ROUNDS)
