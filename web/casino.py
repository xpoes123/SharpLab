"""Web casino — single-player, instant-resolve gambling games (coinflip, dice, slots,
roulette) playable in the browser on the same casino-coin economy as Discord.

Coin safety is centralized in _play(): the bet is debited atomically (rejecting overdraw)
before the outcome is resolved, the payout is credited, and the round is logged for PnL/XP —
exactly like the Discord cogs. Games only provide pure resolvers returning (payout, detail)."""

from __future__ import annotations

import secrets  # CSPRNG — every value here decides a payout, so no predictable random.*

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/casino")

MIN_BET = 1
MAX_BET = 100_000


async def _play(request: Request, game: str, bet: int, resolver):
    """Auth, debit the bet atomically, resolve, credit the payout, log. resolver() -> (payout, detail)."""
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    uid = sess["id"]
    bet = int(bet)
    if bet < MIN_BET or bet > MAX_BET:
        return JSONResponse({"error": f"bet must be {MIN_BET}–{MAX_BET:,} coins"}, status_code=400)
    try:
        await queries.update_casino_balance(uid, -bet)  # atomic; raises if insufficient
    except ValueError:
        return JSONResponse({"error": "not enough coins"}, status_code=400)
    payout, detail = resolver()
    if payout:
        await queries.update_casino_balance(uid, payout)
    await queries.log_casino_result(uid, game, bet, payout)
    balance = await queries.get_casino_balance(uid) or 0
    return {"detail": detail, "payout": payout, "won": payout > bet, "bet": bet, "balance": balance}


@router.get("/balance")
async def balance(request: Request):
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"authenticated": False, "balance": 0}, status_code=401)
    return {"authenticated": True, "balance": await queries.get_casino_balance(sess["id"]) or 0}


# ── Coinflip — 50/50, pays 1.95× (a small house edge) ──
class FlipBody(BaseModel):
    bet: int
    side: str  # "heads" | "tails"


@router.post("/coinflip")
async def coinflip(request: Request, body: FlipBody):
    side = body.side if body.side in ("heads", "tails") else "heads"

    def resolve():
        flip = secrets.choice(("heads", "tails"))
        won = flip == side
        return (round(body.bet * 1.95) if won else 0), {"flip": flip, "side": side}

    return await _play(request, "coinflip", body.bet, resolve)


# ── Dice — crypto-style: pick a target 2–98 and over/under; payout scales with the odds ──
class DiceBody(BaseModel):
    bet: int
    target: int
    direction: str  # "over" | "under"


@router.post("/dice")
async def dice(request: Request, body: DiceBody):
    target = max(2, min(98, int(body.target)))
    over = body.direction != "under"
    win_chance = (100 - target) if over else target  # roll is 0..99

    def resolve():
        roll = secrets.randbelow(100)
        won = (roll > target) if over else (roll < target)
        mult = 0.98 * 100 / win_chance  # 2% house edge
        return (round(body.bet * mult) if won else 0), {
            "roll": roll, "target": target, "direction": "over" if over else "under",
            "win_chance": round(win_chance * 0.98, 1),
        }

    return await _play(request, "dice", body.bet, resolve)


# ── Slots — 3 reels, weighted symbols, simple paytable ──
_SLOT_SYMBOLS = [
    ("🍒", 28), ("🍋", 24), ("🔔", 18), ("⭐", 14), ("💎", 9), ("7️⃣", 5), ("🃏", 2),
]
_SLOT_PAY3 = {"🍒": 5, "🍋": 8, "🔔": 12, "⭐": 20, "💎": 40, "7️⃣": 100, "🃏": 250}  # 3 of a kind (× bet)
_SLOT_PAY2 = {"💎": 2, "7️⃣": 3, "🃏": 5}  # any two of these (× bet)


def _spin_reel() -> str:
    total = sum(w for _, w in _SLOT_SYMBOLS)
    r = secrets.randbelow(total) + 1
    acc = 0
    for sym, w in _SLOT_SYMBOLS:
        acc += w
        if r <= acc:
            return sym
    return _SLOT_SYMBOLS[0][0]


class SlotsBody(BaseModel):
    bet: int


@router.post("/slots")
async def slots(request: Request, body: SlotsBody):
    def resolve():
        reels = [_spin_reel() for _ in range(3)]
        mult = 0
        if reels[0] == reels[1] == reels[2]:
            mult = _SLOT_PAY3.get(reels[0], 3)
        else:
            for sym, m in _SLOT_PAY2.items():
                if reels.count(sym) == 2:
                    mult = max(mult, m)
        return (body.bet * mult), {"reels": reels, "mult": mult}

    return await _play(request, "slots", body.bet, resolve)


# ── Roulette — single-zero (European): number 0–36, standard payouts ──
_RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


class RouletteBody(BaseModel):
    bet: int
    kind: str            # "number" | "red" | "black" | "even" | "odd" | "low" | "high" | "dozen"
    value: int | None = None  # number (0–36) for "number", dozen index 1–3 for "dozen"


@router.post("/roulette")
async def roulette(request: Request, body: RouletteBody):
    kind, value = body.kind, body.value

    def resolve():
        n = secrets.randbelow(37)
        color = "green" if n == 0 else ("red" if n in _RED else "black")
        win, mult = False, 0
        if kind == "number":
            win = value == n
            mult = 36  # 35:1 + stake
        elif kind in ("red", "black"):
            win = color == kind
            mult = 2
        elif kind == "even":
            win = n != 0 and n % 2 == 0
            mult = 2
        elif kind == "odd":
            win = n % 2 == 1
            mult = 2
        elif kind == "low":
            win = 1 <= n <= 18
            mult = 2
        elif kind == "high":
            win = 19 <= n <= 36
            mult = 2
        elif kind == "dozen" and value in (1, 2, 3):
            win = (value - 1) * 12 < n <= value * 12
            mult = 3
        return (body.bet * mult if win else 0), {"n": n, "color": color, "kind": kind, "value": value}

    return await _play(request, "roulette", body.bet, resolve)
