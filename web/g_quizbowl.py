"""Web arcade — solo Quiz Bowl. Answer qbreader.org 3-part bonuses for coins.

Mirrors the stateless pattern in ``web/arcade.py`` (Who's That Pokémon?): there is
no server-side room state. Each bonus's answerlines + part question texts ride in a
signed, time-limited token so the client can walk through all 3 parts while the
answers never leave the server. Coin rewards use the shared per-day-capped activity
reward (source ``quizbowl_win``), so the game can't be farmed.

Reuses the Discord cog's qbreader helpers (``_fetch_bonuses`` / ``_check_answer`` /
``_strip_html``) so the web + Discord games stay in sync.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from bot.cogs.quizbowl import QB_API, _check_answer, _fetch_bonuses, _strip_html
from db import queries
from web import auth, gameround

router = APIRouter(prefix="/api/v1/arcade")
_bonus_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="arcade-quizbowl")
_BONUS_TTL = 600  # seconds a bonus token stays valid


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


@router.post("/quizbowl/new")
async def quizbowl_new(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)

    try:
        async with httpx.AsyncClient() as client:
            bonuses = await _fetch_bonuses(client, "all", "all", count=1)
    except Exception:
        return JSONResponse({"error": "couldn't reach qbreader — try again"}, status_code=502)

    bonus = next((b for b in bonuses if len(b.get("parts", [])) == 3), None)
    if bonus is None:
        return JSONResponse({"error": "no bonus available — try again"}, status_code=502)

    parts = [_strip_html(p) for p in bonus["parts"][:3]]
    # Answerlines stay server-side, folded into the signed token. answers = raw
    # answerlines (with [accept X] brackets) for the checker; answers_sanitized =
    # clean strings we reveal to the player.
    answers = bonus.get("answers") or bonus.get("answers_sanitized") or []
    answers_clean = [_strip_html(a) for a in (bonus.get("answers_sanitized") or answers)[:3]]
    cat = bonus.get("category", "")
    subcat = bonus.get("subcategory", "")
    category = cat + (f" / {subcat}" if subcat else "")

    # Answers stay SERVER-SIDE (a signed token's payload is client-readable → would leak them).
    token = gameround.stash({
        "parts": parts,
        "answers": list(answers[:3]),
        "reveal": answers_clean,
    })
    return {
        "token": token,
        "category": category or "All Categories",
        "leadin": _strip_html(bonus.get("leadin", "")),
        "parts": parts,  # all 3 questions; the client reveals one part at a time
    }


def _payload_from_token(token: str) -> dict | None:
    data = gameround.peek(token)
    return data if isinstance(data, dict) else None


class AnswerBody(BaseModel):
    token: str
    part: int
    given: str


@router.post("/quizbowl/answer")
async def quizbowl_answer(request: Request, body: AnswerBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    data = _payload_from_token(body.token)
    if data is None:
        return JSONResponse({"error": "bonus expired — start a new one"}, status_code=400)
    part = body.part
    answers = data.get("answers", [])
    reveal = data.get("reveal", [])
    if not (0 <= part < len(answers)):
        return JSONResponse({"error": "bad part index"}, status_code=400)

    answerline = answers[part]
    try:
        async with httpx.AsyncClient() as client:
            directive = await _check_answer(client, answerline, body.given.strip())
    except Exception:
        directive = "reject"
    correct = directive in ("accept", "prompt")

    answer_text = reveal[part] if part < len(reveal) else _strip_html(str(answerline))
    result: dict = {"correct": correct, "answer": answer_text, "directive": directive}
    if correct:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        result["reward"] = await queries.grant_activity_reward(uid, "quizbowl_win", day)
        result["balance"] = await queries.get_casino_balance(uid) or 0
    return result


class NextPartBody(BaseModel):
    token: str
    part: int


@router.post("/quizbowl/next-part")
async def quizbowl_next_part(request: Request, body: NextPartBody):
    """Return the question text for a given part (0-2), read from the signed token."""
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    data = _payload_from_token(body.token)
    if data is None:
        return JSONResponse({"error": "bonus expired — start a new one"}, status_code=400)
    parts = data.get("parts", [])
    if not (0 <= body.part < len(parts)):
        return JSONResponse({"error": "no such part"}, status_code=400)
    return {"part": body.part, "question": parts[body.part]}
