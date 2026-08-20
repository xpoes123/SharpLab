"""Web Craps — pass-line only. Two phases: a come-out roll, then (if a point is set) rolls until
the point repeats (win) or a 7 (lose). In-memory round keyed by round_id: bet debited at come-out,
payout credited once at settle, round deleted so a settling roll can't be replayed. Pays 1:1."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/casino/craps")
MIN_BET, MAX_BET = 1, 100_000
_ROUNDS: dict[str, dict] = {}


def _roll() -> tuple[int, int]:
    return secrets.randbelow(6) + 1, secrets.randbelow(6) + 1


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


async def _settle(uid: str, bet: int, won: bool) -> tuple[int, int]:
    payout = bet * 2 if won else 0  # pass line pays 1:1 (stake + winnings)
    if payout:
        await queries.update_casino_balance(uid, payout)
    await queries.log_casino_result(uid, "craps", bet, payout)
    return payout, (await queries.get_casino_balance(uid) or 0)


class ComeOutBody(BaseModel):
    bet: int


@router.post("/comeout")
async def comeout(request: Request, body: ComeOutBody):
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
    d = _roll()
    total = sum(d)
    if total in (7, 11):
        payout, balance = await _settle(uid, bet, True)
        return {"done": True, "dice": d, "total": total, "result": "win", "payout": payout, "balance": balance}
    if total in (2, 3, 12):
        payout, balance = await _settle(uid, bet, False)
        return {"done": True, "dice": d, "total": total, "result": "craps", "payout": payout, "balance": balance}
    rid = secrets.token_urlsafe(12)
    _ROUNDS[rid] = {"uid": uid, "bet": bet, "point": total}
    return {"done": False, "round_id": rid, "dice": d, "total": total, "point": total}


class RollBody(BaseModel):
    round_id: str


@router.post("/roll")
async def roll(request: Request, body: RollBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    st = _ROUNDS.pop(body.round_id, None)  # atomic claim → no double-settle race
    if st is None or st["uid"] != uid:
        return JSONResponse({"error": "no active round"}, status_code=400)
    d = _roll()
    total = sum(d)
    if total == st["point"]:
        payout, balance = await _settle(uid, st["bet"], True)
        return {"done": True, "dice": d, "total": total, "point": st["point"], "result": "win", "payout": payout, "balance": balance}
    if total == 7:
        payout, balance = await _settle(uid, st["bet"], False)
        return {"done": True, "dice": d, "total": total, "point": st["point"], "result": "seven-out", "payout": payout, "balance": balance}
    _ROUNDS[body.round_id] = st  # not resolved this roll — restore the claim
    return {"done": False, "dice": d, "total": total, "point": st["point"]}

# Refund the up-front bet if the process restarts mid-hand (see web/inflight.py).
from web import inflight as _inflight  # noqa: E402
_inflight.register("craps", _ROUNDS)
