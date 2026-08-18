"""Web arcade — NBA Player Guess (solo). Guess the mystery NBA player from
progressively-revealed clues (career team history + a headline stat). Reuses the
player dataset + answer-checker from the Discord cog so the two stay in sync.
Rounds are stateless: the answer index rides in a signed, time-limited token (no
server-side room state), and coin rewards are the shared per-day-capped activity
reward, so it can't be farmed."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from bot.cogs.nbaguess import NBA_PLAYERS_DATA, check_nba_answer
from db import queries
from web import auth

router = APIRouter(prefix="/api/v1/arcade/nbaguess")
_round_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="arcade-nbaguess")
_ROUND_TTL = 900  # seconds a round token stays valid


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _clues(entry: tuple) -> list[str]:
    """Progressive hints derived from an entry. Ordered easiest-context-first:
    a career-span opener, then each team (abbrev + years) oldest → newest, then a
    headline stat line. The name is never included."""
    _id, _name, _alts, stints, stats = entry
    clues: list[str] = []

    # 1) Opener: how many teams + career span (across all stints).
    starts = [s for _t, s, _e in stints]
    ends = [e for _t, _s, e in stints]
    span_lo, span_hi = min(starts), max(ends)
    n = len(stints)
    clues.append(
        f"Played for {n} franchise{'s' if n != 1 else ''} "
        f"between {span_lo} and {span_hi}."
    )

    # 2) One clue per team stint (oldest first).
    for team, start, end in stints:
        clues.append(f"Suited up for the {team} ({start}–{end}).")

    # 3) Headline career stat line.
    if stats:
        clues.append(
            f"Career averages: {stats['ppg']} PPG, {stats['rpg']} RPG, "
            f"{stats['apg']} APG over {stats['gp']} games."
        )

    return clues


def _entry_from_token(token: str) -> tuple | None:
    try:
        idx = _round_signer.loads(token, max_age=_ROUND_TTL)
    except (BadSignature, SignatureExpired, ValueError):
        return None
    return NBA_PLAYERS_DATA[idx] if 0 <= idx < len(NBA_PLAYERS_DATA) else None


@router.post("/new")
async def nbaguess_new(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    idx = secrets.randbelow(len(NBA_PLAYERS_DATA))
    entry = NBA_PLAYERS_DATA[idx]
    # All clue strings are sent; the frontend reveals them one at a time. The
    # name never leaves the server until a correct guess or a reveal.
    return {"token": _round_signer.dumps(idx), "clues": _clues(entry)}


class GuessBody(BaseModel):
    token: str
    guess: str = ""


@router.post("/guess")
async def nbaguess_guess(request: Request, body: GuessBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    entry = _entry_from_token(body.token)
    if entry is None:
        return JSONResponse({"error": "round expired — start a new one"}, status_code=400)
    if not check_nba_answer(body.guess, entry):
        return {"correct": False}
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reward = await queries.grant_activity_reward(uid, "nba_guess", day)
    balance = await queries.get_casino_balance(uid) or 0
    return {"correct": True, "name": entry[1], "reward": reward, "balance": balance}


@router.post("/reveal")
async def nbaguess_reveal(request: Request, body: GuessBody):
    """Give up — reveal the answer, no reward."""
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    entry = _entry_from_token(body.token)
    if entry is None:
        return JSONResponse({"error": "round expired"}, status_code=400)
    return {"correct": False, "gaveup": True, "name": entry[1]}
