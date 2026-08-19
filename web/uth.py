"""Web Ultimate Texas Hold'em — single-player vs the house, server-authoritative.

Coin safety: ante+blind (+optional trips) debited at /deal, each Play bet debited when placed,
the whole payout credited once at settle, round popped so it can't be replayed. The round is
claimed with `_ROUNDS.pop` up front (never get-then-pop) so two concurrent settle requests can't
double-pay. Hand evaluation + payout tables are reused from the Discord cog (bot.cogs.uth) so the
web and bot tables pay identically; only the deck is re-shuffled here with a CSPRNG."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bot.cogs.uth import (
    RANKS,
    SUITS,
    _best_5_from_7,
    _blind_payout,
    _hand_name,
    _trips_payout,
)
from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/casino/uth")
MIN_BET, MAX_BET = 1, 50_000  # ante; total exposure can reach ~8x this
_ROUNDS: dict[str, dict] = {}


def _deck() -> list[str]:
    cards = [r + s for s in SUITS for r in RANKS]
    for i in range(len(cards) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        cards[i], cards[j] = cards[j], cards[i]
    return cards


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


class DealBody(BaseModel):
    ante: int
    trips: int = 0


@router.post("/deal")
async def deal(request: Request, body: DealBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    ante = int(body.ante)
    trips = max(0, int(body.trips))
    if ante < MIN_BET or ante > MAX_BET:
        return JSONResponse({"error": f"ante must be {MIN_BET}–{MAX_BET:,}"}, status_code=400)
    if trips > MAX_BET:
        return JSONResponse({"error": f"trips bet too large (max {MAX_BET:,})"}, status_code=400)
    upfront = ante * 2 + trips  # ante + blind (=ante) + trips
    try:
        await queries.update_casino_balance(uid, -upfront)
    except ValueError:
        return JSONResponse({"error": "not enough coins"}, status_code=400)
    d = _deck()
    st = {
        "uid": uid, "ante": ante, "blind": ante, "trips": trips,
        "hole": [d.pop(), d.pop()], "dealer": [d.pop(), d.pop()],
        "community": [d.pop(), d.pop(), d.pop(), d.pop(), d.pop()],
        "play": 0, "phase": "preflop",
    }
    rid = secrets.token_urlsafe(12)
    _ROUNDS[rid] = st
    return {"round_id": rid, "phase": "preflop", "hole": st["hole"], "ante": ante, "trips": trips}


class ActionBody(BaseModel):
    round_id: str
    action: str  # "bet" | "check" | "fold"
    mult: int = 4  # preflop Play multiplier: 3 or 4 (ignored otherwise)


# Play multiplier available to bet in each phase.
_PHASE_MULT = {"preflop": (3, 4), "flop": (2,), "turn": (1,)}
# Community cards visible in each phase (after the decision that entered it).
_PHASE_REVEAL = {"preflop": 0, "flop": 3, "turn": 5}


@router.post("/action")
async def action(request: Request, body: ActionBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    # Claim atomically so concurrent requests can't both settle & double-pay; a still-live
    # branch (a check that advances a street) restores the claim before returning.
    st = _ROUNDS.pop(body.round_id, None)
    if st is None or st["uid"] != uid:
        return JSONResponse({"error": "no active hand"}, status_code=400)
    phase = st["phase"]

    if body.action == "bet":
        allowed = _PHASE_MULT[phase]
        mult = body.mult if (phase == "preflop" and body.mult in allowed) else allowed[0]
        play = st["ante"] * mult
        try:
            await queries.update_casino_balance(uid, -play)
        except ValueError:
            _ROUNDS[body.round_id] = st  # bet failed — hand still live
            return JSONResponse({"error": "not enough coins for that bet"}, status_code=400)
        st["play"] = play
        return await _settle(uid, st)

    if body.action == "check":
        if phase == "turn":
            _ROUNDS[body.round_id] = st
            return JSONResponse({"error": "must bet or fold on the river"}, status_code=400)
        st["phase"] = "flop" if phase == "preflop" else "turn"
        _ROUNDS[body.round_id] = st  # still live — restore
        reveal = _PHASE_REVEAL[st["phase"]]
        return {"round_id": body.round_id, "phase": st["phase"],
                "community": st["community"][:reveal]}

    if body.action == "fold":
        return await _settle(uid, st, folded=True)

    _ROUNDS[body.round_id] = st
    return JSONResponse({"error": "unknown action"}, status_code=400)


async def _settle(uid: str, st: dict, folded: bool = False) -> dict:
    ante, blind, trips, play = st["ante"], st["blind"], st["trips"], st["play"]
    dealer_score = _best_5_from_7(st["dealer"] + st["community"])
    dealer_qualifies = dealer_score[0] >= 1  # pair or better
    player_score = _best_5_from_7(st["hole"] + st["community"])

    credit = 0
    lines: list[str] = []
    wagered = ante + blind + trips + play

    if folded:
        lines.append("Folded — ante & blind lost")
    else:
        wins = player_score > dealer_score
        ties = player_score == dealer_score
        # Play bet
        if ties:
            credit += play; lines.append("Play: push")
        elif wins:
            credit += play * 2; lines.append(f"Play: win +{play}")
        else:
            lines.append("Play: loss")
        # Ante (pushes if dealer doesn't qualify)
        if ties:
            credit += ante; lines.append("Ante: push")
        elif wins:
            if dealer_qualifies:
                credit += ante * 2; lines.append(f"Ante: win +{ante}")
            else:
                credit += ante; lines.append("Ante: push (dealer didn't qualify)")
        else:
            lines.append("Ante: loss")
        # Blind (bonus per pay table on a win)
        if ties:
            credit += blind; lines.append("Blind: push")
        elif wins:
            bw, bl = _blind_payout(player_score, blind)
            credit += blind + bw
            lines.append(f"Blind: {bl} +{bw}" if bw > 0 else "Blind: push")
        else:
            lines.append("Blind: loss")

    # Trips side bet — paid on the player's hand regardless of fold/dealer.
    if trips > 0:
        tw, tl = _trips_payout(player_score, trips)
        if tw > 0:
            credit += trips + tw; lines.append(f"Trips: {tl} +{tw}")
        else:
            lines.append("Trips: loss")

    if credit > 0:
        await queries.update_casino_balance(uid, credit)
    await queries.log_casino_result(uid, "uth", wagered, credit)
    balance = await queries.get_casino_balance(uid) or 0
    return {"done": True, "payout": credit, "balance": balance, "lines": lines,
            "hole": st["hole"], "dealer": st["dealer"], "community": st["community"],
            "player_hand": _hand_name(player_score), "dealer_hand": _hand_name(dealer_score),
            "dealer_qualifies": dealer_qualifies, "folded": folded}
