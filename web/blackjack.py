"""Web Blackjack — single-player, multi-step, server-authoritative. Coin safety: the bet is
debited at deal, the payout credited once at resolve, and the round is deleted on resolve so a
final-state request can't be replayed to double-settle. Rounds live in-memory on the single web
process (a restart just drops any in-flight hand — the bet was already taken)."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/casino/blackjack")
MIN_BET, MAX_BET = 1, 100_000
_ROUNDS: dict[str, dict] = {}  # round_id -> state

_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
_SUITS = ["♠", "♥", "♦", "♣"]


def _fresh_deck() -> list[str]:
    deck = [r + s for s in _SUITS for r in _RANKS]
    # Fisher–Yates with a CSPRNG
    for i in range(len(deck) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        deck[i], deck[j] = deck[j], deck[i]
    return deck


def _card_value(card: str) -> int:
    rank = card[:-1]
    if rank in ("J", "Q", "K", "10"):
        return 10
    if rank == "A":
        return 11
    return int(rank)


def hand_total(cards: list[str]) -> int:
    """Best blackjack total: aces count 11 unless that busts, then 1."""
    total = sum(_card_value(c) for c in cards)
    aces = sum(1 for c in cards if c[:-1] == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _is_blackjack(cards: list[str]) -> bool:
    return len(cards) == 2 and hand_total(cards) == 21


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


async def _settle(rid: str, st: dict) -> dict:
    """Compute outcome, credit payout once, log, drop the round."""
    p, d = hand_total(st["player"]), hand_total(st["dealer"])
    bet = st["bet"]  # already includes a double
    if hand_total(st["player"]) > 21:
        result, mult = "bust", 0.0
    elif st.get("player_bj") and not st.get("dealer_bj"):
        result, mult = "blackjack", 2.5
    elif d > 21 or p > d:
        result, mult = "win", 2.0
    elif p < d:
        result, mult = "lose", 0.0
    else:
        result, mult = "push", 1.0
    payout = round(bet * mult)
    if payout:
        await queries.update_casino_balance(st["uid"], payout)
    await queries.log_casino_result(st["uid"], "blackjack", bet, payout)
    balance = await queries.get_casino_balance(st["uid"]) or 0
    _ROUNDS.pop(rid, None)
    return {"done": True, "result": result, "payout": payout, "balance": balance,
            "dealer": st["dealer"], "dealer_total": d, "player": st["player"], "player_total": p}


def _live_state(rid: str, st: dict) -> dict:
    return {"round_id": rid, "done": False, "player": st["player"],
            "player_total": hand_total(st["player"]),
            "dealer_up": [st["dealer"][0]], "can_double": len(st["player"]) == 2}


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
        await queries.update_casino_balance(uid, -bet)  # atomic; rejects overdraw
    except ValueError:
        return JSONResponse({"error": "not enough coins"}, status_code=400)
    deck = _fresh_deck()
    player, dealer = [deck.pop(), deck.pop()], [deck.pop(), deck.pop()]
    rid = secrets.token_urlsafe(12)
    st = {"uid": uid, "bet": bet, "deck": deck, "player": player, "dealer": dealer,
          "player_bj": _is_blackjack(player), "dealer_bj": _is_blackjack(dealer)}
    _ROUNDS[rid] = st
    if st["player_bj"] or st["dealer_bj"]:  # naturals resolve immediately
        return await _settle(rid, st)
    return _live_state(rid, st)


class ActionBody(BaseModel):
    round_id: str
    action: str  # hit | stand | double


@router.post("/action")
async def action(request: Request, body: ActionBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    st = _ROUNDS.get(body.round_id)
    if st is None or st["uid"] != uid:
        return JSONResponse({"error": "no active hand"}, status_code=400)
    act = body.action
    if act == "hit":
        st["player"].append(st["deck"].pop())
        if hand_total(st["player"]) >= 21:  # bust or 21 → stand automatically
            return await _dealer_play_and_settle(body.round_id, st)
        return _live_state(body.round_id, st)
    if act == "double":
        if len(st["player"]) != 2:
            return JSONResponse({"error": "can only double on your first two cards"}, status_code=400)
        try:
            await queries.update_casino_balance(uid, -st["bet"])  # match the bet
        except ValueError:
            return JSONResponse({"error": "not enough coins to double"}, status_code=400)
        st["bet"] *= 2
        st["player"].append(st["deck"].pop())
        return await _dealer_play_and_settle(body.round_id, st)
    if act == "stand":
        return await _dealer_play_and_settle(body.round_id, st)
    return JSONResponse({"error": "unknown action"}, status_code=400)


async def _dealer_play_and_settle(rid: str, st: dict) -> dict:
    if hand_total(st["player"]) <= 21:
        while hand_total(st["dealer"]) < 17:  # dealer hits to 17 (stands on all 17s)
            st["dealer"].append(st["deck"].pop())
    return await _settle(rid, st)
