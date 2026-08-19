"""Web Pai Gow Poker — single-player vs the house, server-authoritative, no-commission variant.

Coin safety: bet (+optional Fortune side bet) debited at /deal, payout credited once at /set,
round popped so it can't be replayed; the round is claimed with `_ROUNDS.pop` up front so two
concurrent /set requests can't double-pay. Hand evaluation, the dealer's house-way split, the
foul rule (high must outrank low) and the Fortune pay table are all reused from the Discord cog
(bot.cogs.paigow) so web and Discord pay identically; only the deck is CSPRNG-shuffled here."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bot.cogs.paigow import (
    JOKER,
    RANKS,
    SUITS,
    _evaluate_2,
    _evaluate_5,
    _fortune_payout,
    _hand_name_2,
    _hand_name_5,
    _house_way,
    _valid_setting,
)
from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/casino/paigow")
MIN_BET, MAX_BET = 1, 50_000
_ROUNDS: dict[str, dict] = {}


def _deck() -> list[str]:
    cards = [r + s for s in SUITS for r in RANKS]
    cards.append(JOKER)
    for i in range(len(cards) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        cards[i], cards[j] = cards[j], cards[i]
    return cards


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


class DealBody(BaseModel):
    bet: int
    fortune: int = 0


@router.post("/deal")
async def deal(request: Request, body: DealBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    bet = int(body.bet)
    fortune = max(0, int(body.fortune))
    if bet < MIN_BET or bet > MAX_BET:
        return JSONResponse({"error": f"bet must be {MIN_BET}–{MAX_BET:,}"}, status_code=400)
    if fortune > MAX_BET:
        return JSONResponse({"error": f"fortune bet too large (max {MAX_BET:,})"}, status_code=400)
    try:
        await queries.update_casino_balance(uid, -(bet + fortune))
    except ValueError:
        return JSONResponse({"error": "not enough coins"}, status_code=400)
    d = _deck()
    player = [d.pop() for _ in range(7)]
    dealer = [d.pop() for _ in range(7)]
    rid = secrets.token_urlsafe(12)
    _ROUNDS[rid] = {"uid": uid, "bet": bet, "fortune": fortune,
                    "player": player, "dealer": dealer}
    hi, lo = _house_way(player)  # a suggested split the player can accept or override
    return {"round_id": rid, "cards": player, "suggest_high": hi, "suggest_low": lo}


class SetBody(BaseModel):
    round_id: str
    low: list[str] | None = None  # the two cards for the low hand; None = use house way


@router.post("/set")
async def set_hand(request: Request, body: SetBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    # Claim atomically → concurrent /set can't both settle & double-pay.
    st = _ROUNDS.pop(body.round_id, None)
    if st is None or st["uid"] != uid:
        return JSONResponse({"error": "no active hand"}, status_code=400)
    player = st["player"]

    if body.low is None:
        high, low = _house_way(player)
    else:
        low = list(body.low)
        # Validate the split: exactly two cards, both from the dealt hand (multiset), 5 remain.
        if len(low) != 2 or not _is_submultiset(low, player):
            _ROUNDS[body.round_id] = st  # invalid input — hand still live, let them retry
            return JSONResponse({"error": "low hand must be two of your seven cards"}, status_code=400)
        high = _remove(player, low)
        if not _valid_setting(high, low):  # foul: low outranks high
            _ROUNDS[body.round_id] = st
            return JSONResponse({"error": "foul — your two-card hand can't beat your five-card hand"},
                                status_code=400)

    bet, fortune = st["bet"], st["fortune"]
    dealer_high, dealer_low = _house_way(st["dealer"])
    d_hi, d_lo = _evaluate_5(dealer_high), _evaluate_2(dealer_low)
    p_hi, p_lo = _evaluate_5(high), _evaluate_2(low)
    hi_win = p_hi > d_hi  # ties (copies) go to the dealer
    lo_win = p_lo > d_lo

    if hi_win and lo_win:
        outcome, main = "win", bet * 2
    elif not hi_win and not lo_win:
        if d_hi[0] == 0 and d_hi[1] == 14:  # dealer ace-high foul → push (no-commission rule)
            outcome, main = "push", bet
        else:
            outcome, main = "lose", 0
    else:
        outcome, main = "push", bet

    fortune_win, fortune_label = (0, "")
    if fortune > 0:
        fortune_win, fortune_label = _fortune_payout(player, fortune)

    credit = main + fortune_win
    if credit > 0:
        await queries.update_casino_balance(uid, credit)
    await queries.log_casino_result(uid, "paigow", bet + fortune, credit)
    balance = await queries.get_casino_balance(uid) or 0
    return {"done": True, "outcome": outcome, "payout": credit, "balance": balance,
            "player_high": high, "player_low": low,
            "dealer_high": dealer_high, "dealer_low": dealer_low,
            "player_high_name": _hand_name_5(p_hi), "player_low_name": _hand_name_2(p_lo),
            "dealer_high_name": _hand_name_5(d_hi), "dealer_low_name": _hand_name_2(d_lo),
            "fortune_win": fortune_win, "fortune_label": fortune_label}


def _is_submultiset(sub: list[str], whole: list[str]) -> bool:
    pool = list(whole)
    for c in sub:
        if c in pool:
            pool.remove(c)
        else:
            return False
    return True


def _remove(whole: list[str], sub: list[str]) -> list[str]:
    rest = list(whole)
    for c in sub:
        rest.remove(c)
    return rest
