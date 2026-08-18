"""Web Video Poker — Jacks-or-Better, 5-card draw. Two steps (deal → hold/draw) with an
in-memory round keyed by round_id: bet debited at deal, payout credited once at draw, round
deleted so a draw can't be replayed. Server-authoritative CSPRNG deck + hand evaluation."""

from __future__ import annotations

import secrets
from collections import Counter

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/casino/videopoker")
MIN_BET, MAX_BET = 1, 100_000
_ROUNDS: dict[str, dict] = {}

_RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
_RANK_VAL = {r: i + 2 for i, r in enumerate(_RANKS)}  # 2..14
_SUITS = ["♠", "♥", "♦", "♣"]

# Jacks-or-Better 9/6 paytable — total return per unit bet (bet already debited).
_PAYTABLE = {
    "royal_flush": 800, "straight_flush": 50, "four_kind": 25, "full_house": 9,
    "flush": 6, "straight": 4, "three_kind": 3, "two_pair": 2, "jacks_or_better": 1,
}
_LABELS = {
    "royal_flush": "Royal Flush", "straight_flush": "Straight Flush", "four_kind": "Four of a Kind",
    "full_house": "Full House", "flush": "Flush", "straight": "Straight",
    "three_kind": "Three of a Kind", "two_pair": "Two Pair", "jacks_or_better": "Jacks or Better",
    "none": "No Win",
}


def _fresh_deck() -> list[str]:
    deck = [r + s for s in _SUITS for r in _RANKS]
    for i in range(len(deck) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        deck[i], deck[j] = deck[j], deck[i]
    return deck


def evaluate(hand: list[str]) -> str:
    """Return the winning category key (or 'none'). hand = 5 cards like 'A♠'."""
    vals = sorted(_RANK_VAL[c[:-1]] for c in hand)
    suits = [c[-1] for c in hand]
    counts = Counter(vals)
    is_flush = len(set(suits)) == 1
    uniq = sorted(set(vals))
    is_straight = len(uniq) == 5 and uniq[-1] - uniq[0] == 4
    wheel = uniq == [2, 3, 4, 5, 14]  # A-2-3-4-5
    is_straight = is_straight or wheel
    freq = sorted(counts.values(), reverse=True)
    if is_straight and is_flush:
        return "royal_flush" if (min(vals) == 10 and not wheel) else "straight_flush"
    if freq[0] == 4:
        return "four_kind"
    if freq == [3, 2]:
        return "full_house"
    if is_flush:
        return "flush"
    if is_straight:
        return "straight"
    if freq[0] == 3:
        return "three_kind"
    if freq[:2] == [2, 2]:
        return "two_pair"
    # jacks or better: a pair of J(11), Q, K, or A
    for v, n in counts.items():
        if n == 2 and v >= 11:
            return "jacks_or_better"
    return "none"


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


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
    deck = _fresh_deck()
    hand = [deck.pop() for _ in range(5)]
    rid = secrets.token_urlsafe(12)
    _ROUNDS[rid] = {"uid": uid, "bet": bet, "deck": deck, "hand": hand}
    return {"round_id": rid, "hand": hand}


class DrawBody(BaseModel):
    round_id: str
    hold: list[bool]  # length 5, True = keep


@router.post("/draw")
async def draw(request: Request, body: DrawBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    st = _ROUNDS.pop(body.round_id, None)  # atomic claim → no double-settle race
    if st is None or st["uid"] != uid:
        return JSONResponse({"error": "no active hand"}, status_code=400)
    hold = (list(body.hold) + [False] * 5)[:5]
    hand = [c if hold[i] else st["deck"].pop() for i, c in enumerate(st["hand"])]
    cat = evaluate(hand)
    mult = _PAYTABLE.get(cat, 0)
    payout = st["bet"] * mult
    if payout:
        await queries.update_casino_balance(uid, payout)
    await queries.log_casino_result(uid, "videopoker", st["bet"], payout)
    balance = await queries.get_casino_balance(uid) or 0
    return {"hand": hand, "category": cat, "label": _LABELS[cat], "mult": mult,
            "payout": payout, "balance": balance}
