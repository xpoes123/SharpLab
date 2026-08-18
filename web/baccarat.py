"""Web casino — Baccarat (Punto Banco). Standard third-card rules, single-hand resolve
on the shared casino-coin economy. Coin safety is centralized in web.casino._play(): the
bet is debited atomically before the hand is dealt and the payout credited after, exactly
like the other web games. This module only provides a pure resolver returning (payout, detail)."""

from __future__ import annotations

import secrets  # CSPRNG — every card decides a payout, so no predictable random.*

from fastapi import APIRouter, Request
from pydantic import BaseModel

from web.casino import _play

router = APIRouter(prefix="/api/v1/casino")

# Rank -> baccarat point value (A=1, 2-9 face, 10/J/Q/K=0). Suits are cosmetic.
_RANKS = [("A", 1), ("2", 2), ("3", 3), ("4", 4), ("5", 5), ("6", 6),
          ("7", 7), ("8", 8), ("9", 9), ("10", 0), ("J", 0), ("Q", 0), ("K", 0)]
_SUITS = ["♠", "♥", "♦", "♣"]


def _draw() -> tuple[str, int]:
    """Draw one card from an effectively infinite (uniform) shoe. Returns (label, value)."""
    rank, value = secrets.choice(_RANKS)
    return rank + secrets.choice(_SUITS), value


def _total(cards: list[tuple[str, int]]) -> int:
    return sum(v for _, v in cards) % 10


def _banker_draws(banker_total: int, player_third: int | None) -> bool:
    """Standard Punto Banco banker third-card rule.
    player_third is None when the player stood on the first two cards."""
    if player_third is None:  # player stood: banker acts like a player (draw on 0-5)
        return banker_total <= 5
    if banker_total <= 2:
        return True
    if banker_total == 3:
        return player_third != 8
    if banker_total == 4:
        return 2 <= player_third <= 7
    if banker_total == 5:
        return 4 <= player_third <= 7
    if banker_total == 6:
        return 6 <= player_third <= 7
    return False  # 7 (or higher, impossible) -> stand


def _deal() -> tuple[list, list, str]:
    """Play out one Punto Banco hand. Returns (player_cards, banker_cards, outcome)."""
    player = [_draw(), _draw()]
    banker = [_draw(), _draw()]
    p, b = _total(player), _total(banker)

    # Naturals: an 8 or 9 on the first two cards ends the hand immediately.
    if p < 8 and b < 8:
        player_third = None
        if p <= 5:  # player draws a third card (stands on 6-7)
            card = _draw()
            player.append(card)
            player_third = card[1]
        if _banker_draws(_total(banker), player_third):
            banker.append(_draw())

    p, b = _total(player), _total(banker)
    outcome = "player" if p > b else "banker" if b > p else "tie"
    return player, banker, outcome


class BaccaratBody(BaseModel):
    bet: int
    side: str  # "player" | "banker" | "tie"


# Payout multipliers (× bet, stake included). Tie pushes player/banker bets.
_PAYOUT = {"player": 2.0, "banker": 1.95, "tie": 9.0}


@router.post("/baccarat")
async def baccarat(request: Request, body: BaccaratBody):
    side = body.side if body.side in _PAYOUT else "player"

    def resolve():
        player, banker, outcome = _deal()
        if outcome == "tie" and side in ("player", "banker"):
            payout = round(body.bet)  # push — stake returned
        elif outcome == side:
            payout = round(body.bet * _PAYOUT[side])
        else:
            payout = 0
        detail = {
            "player": [c for c, _ in player],
            "banker": [c for c, _ in banker],
            "player_total": _total(player),
            "banker_total": _total(banker),
            "outcome": outcome,
            "side": side,
        }
        return payout, detail

    return await _play(request, "baccarat", body.bet, resolve)
