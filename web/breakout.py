"""Web casino — Breakout Blackjack. Bet Player or Dealer (both hands auto-play by fixed
house rules, hit-to-17 S17) plus an optional 'Breakout' side bet on the TOTAL number of
cards dealt across both hands. Coin safety is centralized in web.casino._play(): the total
stake (main + side) is debited atomically before the deal and the combined payout credited
after. This module only provides the pure resolver.

Even-money (1:1) main bet both sides — the sides are symmetric (H17, a double-bust
pushes), so the main bet is ~0% edge; the house edge lives in the Breakout side bet
(~1.1%), tuned by simulation (scripts/breakout_edge.py). Matches the Discord cog."""

from __future__ import annotations

import secrets  # CSPRNG — every card decides a payout, so no predictable random.*

from fastapi import APIRouter, Request
from pydantic import BaseModel

from web.casino import _play

router = APIRouter(prefix="/api/v1/casino")

_RANKS = [("A", 11), ("2", 2), ("3", 3), ("4", 4), ("5", 5), ("6", 6), ("7", 7),
          ("8", 8), ("9", 9), ("10", 10), ("J", 10), ("Q", 10), ("K", 10)]
_SUITS = ["♠", "♥", "♦", "♣"]

PLAYER_MULT = 2.0  # even money (gross, stake included)
DEALER_MULT = 2.0
_BREAKOUT = {7: 1, 8: 4, 9: 8, 10: 20, 11: 75}  # total cards -> win odds (X:1)
_BREAKOUT_12PLUS = 150


def _breakout_odds(total_cards: int) -> int:
    if total_cards >= 12:
        return _BREAKOUT_12PLUS
    return _BREAKOUT.get(total_cards, 0)


def _draw() -> tuple[str, int]:
    rank, value = secrets.choice(_RANKS)
    return rank + secrets.choice(_SUITS), value


def _hand_total(cards: list[tuple[str, int]]) -> int:
    """Best blackjack total (aces drop 11->1 while busting)."""
    total = sum(v for _, v in cards)
    aces = sum(1 for lbl, _ in cards if lbl[:-1] == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _is_soft(cards: list[tuple[str, int]]) -> bool:
    total = sum(v for _, v in cards)
    aces = sum(1 for lbl, _ in cards if lbl[:-1] == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return aces > 0


def _play_out() -> list[tuple[str, int]]:
    hand = [_draw(), _draw()]
    while True:  # hit below 17, and HIT soft 17 (H17)
        t = _hand_total(hand)
        if t < 17 or (t == 17 and _is_soft(hand)):
            hand.append(_draw())
            continue
        break
    return hand


def _winner(p_total: int, d_total: int) -> str:
    """player | dealer | push. Symmetric: a double-bust pushes (even-money main bet)."""
    p_bust, d_bust = p_total > 21, d_total > 21
    if p_bust and d_bust:
        return "push"
    if p_bust:
        return "dealer"
    if d_bust:
        return "player"
    if p_total > d_total:
        return "player"
    if d_total > p_total:
        return "dealer"
    return "push"


class BreakoutBody(BaseModel):
    bet: int
    side: str  # "player" | "dealer"
    breakout: int = 0  # optional side-bet stake


_MAIN = {"player": PLAYER_MULT, "dealer": DEALER_MULT}


@router.post("/breakout")
async def breakout(request: Request, body: BreakoutBody):
    side = body.side if body.side in _MAIN else "player"
    bo_bet = max(0, int(body.breakout))
    total_bet = int(body.bet) + bo_bet

    def resolve():
        player = _play_out()
        dealer = _play_out()
        p_total, d_total = _hand_total(player), _hand_total(dealer)
        outcome = _winner(p_total, d_total)
        total_cards = len(player) + len(dealer)
        odds = _breakout_odds(total_cards)

        if outcome == side:
            main_payout = round(body.bet * _MAIN[side])
        elif outcome == "push":
            main_payout = round(body.bet)  # stake returned
        else:
            main_payout = 0
        bo_payout = bo_bet * (odds + 1) if (bo_bet and odds) else 0

        detail = {
            "player": [c for c, _ in player],
            "dealer": [c for c, _ in dealer],
            "player_total": p_total,
            "dealer_total": d_total,
            "outcome": outcome,
            "side": side,
            "total_cards": total_cards,
            "breakout_odds": odds,
            "breakout_bet": bo_bet,
            "breakout_payout": bo_payout,
            "main_payout": main_payout,
        }
        return main_payout + bo_payout, detail

    return await _play(request, "breakout", total_bet, resolve)
