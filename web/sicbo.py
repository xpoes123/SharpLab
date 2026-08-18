"""Web Sic Bo — three-dice betting, instant resolve via the coin-safe _play. Bets: Small
(sum 4–10), Big (sum 11–17) both lose on a triple (the house edge), or a single number 1–6
that pays 2×/3×/4× for appearing on 1/2/3 dice."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from pydantic import BaseModel

from web.casino import _play

router = APIRouter(prefix="/api/v1/casino")


class SicBoBody(BaseModel):
    bet: int
    kind: str          # "small" | "big" | "num"
    value: int | None = None  # 1–6 for kind == "num"


@router.post("/sicbo")
async def sicbo(request: Request, body: SicBoBody):
    kind = body.kind
    num = body.value if body.value in (1, 2, 3, 4, 5, 6) else 1

    def resolve():
        dice = [secrets.randbelow(6) + 1 for _ in range(3)]
        total = sum(dice)
        triple = dice[0] == dice[1] == dice[2]
        mult = 0
        if kind == "small":
            mult = 2 if (4 <= total <= 10 and not triple) else 0
        elif kind == "big":
            mult = 2 if (11 <= total <= 17 and not triple) else 0
        elif kind == "num":
            c = dice.count(num)
            mult = (c + 1) if c else 0  # 1→2×, 2→3×, 3→4×
        return (body.bet * mult), {"dice": dice, "total": total, "triple": triple,
                                   "kind": kind, "num": num}

    return await _play(request, "sicbo", body.bet, resolve)
