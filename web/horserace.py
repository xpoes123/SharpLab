"""Web Horse Race — pick a horse, one instant race. Fixed odds with a built-in ~10% edge:
each horse i wins with probability p_i and pays odds_i ≈ 0.9 / p_i, so E[payout] ≈ 0.9×bet for
any pick. Reuses the coin-safe _play (atomic bet debit → resolve → payout → log)."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from pydantic import BaseModel

from web.casino import _play

router = APIRouter(prefix="/api/v1/casino")

# (name, win probability, payout multiplier). Probabilities sum to 1; odds ≈ 0.9/p (10% edge).
HORSES = [
    {"name": "Thunderbolt", "p": 0.40, "odds": 2.2},
    {"name": "Sea Biscuit", "p": 0.25, "odds": 3.5},
    {"name": "Night Fury", "p": 0.16, "odds": 5.5},
    {"name": "Lucky Star", "p": 0.10, "odds": 9.0},
    {"name": "Dark Horse", "p": 0.06, "odds": 15.0},
    {"name": "Moonshot", "p": 0.03, "odds": 30.0},
]
_CUM: list[float] = []
_acc = 0.0
for _h in HORSES:
    _acc += _h["p"]
    _CUM.append(_acc)


def _run_race() -> int:
    """Return the winning horse index, weighted by win probability (CSPRNG)."""
    r = secrets.randbelow(10**9) / 10**9  # [0, 1)
    for i, cum in enumerate(_CUM):
        if r < cum:
            return i
    return len(HORSES) - 1


class RaceBody(BaseModel):
    bet: int
    horse: int  # index into HORSES


@router.post("/horserace")
async def horserace(request: Request, body: RaceBody):
    pick = body.horse if 0 <= body.horse < len(HORSES) else 0

    def resolve():
        winner = _run_race()
        won = winner == pick
        payout = round(body.bet * HORSES[pick]["odds"]) if won else 0
        return payout, {
            "winner": winner, "picked": pick,
            "horses": [{"name": h["name"], "odds": h["odds"]} for h in HORSES],
        }

    return await _play(request, "horserace", body.bet, resolve)
