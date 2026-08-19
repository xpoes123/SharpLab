"""Web arcade — solo Geography: name the country from its flag emoji.

Reuses the country data from the Discord /geography cog (capitals, ISO codes,
name aliases, and the accent-stripping normalizer) so the two stay in sync. The
flag is rendered as a regional-indicator emoji derived from the ISO alpha-2
code, so nothing external is fetched. Rounds are stateless: the country index
rides in a signed, time-limited token (no server-side room state), and coin
rewards are the shared per-day-capped activity reward, so it can't be farmed."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bot.cogs.geography import (
    CAPITALS,
    COUNTRY_ALIASES,
    COUNTRY_CODES,
    _normalize,
)
from db import queries
from web import auth, gameround

router = APIRouter(prefix="/api/v1/arcade/geo")


def _flag_emoji(iso2: str) -> str:
    """Turn an ISO 3166-1 alpha-2 code into its regional-indicator flag emoji."""
    return "".join(chr(0x1F1E6 + (ord(c) - ord("a"))) for c in iso2.lower())


def _build_countries() -> list[dict]:
    """Build the playable pool from the shared cog data.

    Only countries that have BOTH an ISO code (→ flag) and a known capital are
    included. `aliases` is the canonical name plus any accepted synonyms.
    """
    out: list[dict] = []
    for name, code in COUNTRY_CODES.items():
        cap = CAPITALS.get(name)
        if not cap:
            continue
        aliases = [name, *COUNTRY_ALIASES.get(name, [])]
        out.append({
            "name": name,
            "flag": _flag_emoji(code),
            "capital": cap[0],  # first entry is the canonical display capital
            "aliases": aliases,
        })
    return out


COUNTRIES: list[dict] = _build_countries()


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _country_from_token(token: str) -> dict | None:
    idx = gameround.peek(token)
    if not isinstance(idx, int):
        return None
    return COUNTRIES[idx] if 0 <= idx < len(COUNTRIES) else None


def _matches(guess: str, country: dict) -> bool:
    ng = _normalize(guess)
    if not ng:
        return False
    return any(_normalize(a) == ng for a in country["aliases"])


@router.post("/new")
async def geo_new(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    idx = secrets.randbelow(len(COUNTRIES))
    # Only the flag leaks; the name/capital stay server-side in the token.
    return {"token": gameround.stash(idx), "flag": COUNTRIES[idx]["flag"]}


class GuessBody(BaseModel):
    token: str
    guess: str


class TokenBody(BaseModel):
    token: str


@router.post("/guess")
async def geo_guess(request: Request, body: GuessBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    country = _country_from_token(body.token)
    if country is None:
        return JSONResponse({"error": "round expired — start a new one"}, status_code=400)
    if not _matches(body.guess, country):
        return {"correct": False}
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reward = await queries.grant_activity_reward(uid, "geo_guess", day)
    balance = await queries.get_casino_balance(uid) or 0
    return {
        "correct": True,
        "name": country["name"],
        "capital": country["capital"],
        "reward": reward,
        "balance": balance,
    }


@router.post("/hint")
async def geo_hint(request: Request, body: TokenBody):
    """Reveal the capital (or first letter as a fallback). No reward change."""
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    country = _country_from_token(body.token)
    if country is None:
        return JSONResponse({"error": "round expired"}, status_code=400)
    return {"capital": country["capital"], "first_letter": country["name"][0]}


@router.post("/reveal")
async def geo_reveal(request: Request, body: TokenBody):
    """Give up — reveal the answer, no reward."""
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    country = _country_from_token(body.token)
    if country is None:
        return JSONResponse({"error": "round expired"}, status_code=400)
    return {"correct": False, "gaveup": True, "name": country["name"], "capital": country["capital"]}
