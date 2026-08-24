"""Web arcade — solo Geography: name the country from its flag emoji.

Reuses the country data from the Discord /geography cog (capitals, ISO codes,
name aliases, and the accent-stripping normalizer) so the two stay in sync. The
flag is rendered as a regional-indicator emoji derived from the ISO alpha-2
code, so nothing external is fetched. Rounds are stateless: the country index
rides in a signed, time-limited token (no server-side room state), and coin
rewards are the shared per-day-capped activity reward, so it can't be farmed.

Also offers a 120-second SPRINT mode (modeled on web/g_zetamac.py /
web/g_sequence.py): /sprint/start hands out a batch of flags (country indices
stay server-side under an opaque token) and /sprint/submit recounts
correctness with the same `_matches` alias-matching used by /guess, and
rejects late submits — same batch-model tradeoffs as the other sprints."""

from __future__ import annotations

import secrets
import time
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

SPRINT_DURATION = 120     # seconds in the sprint
SPRINT_GRACE = 15         # extra seconds allowed for the submit to land
SPRINT_N_PROBLEMS = 80    # repeats OK — plenty of flags for 120s of typing
SPRINT_GAME_ID = "geo"


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


# ── Sprint mode (120s, batch of flags, highest-correct-wins leaderboard) ──────


@router.post("/sprint/start")
async def sprint_start(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    idxs = [secrets.randbelow(len(COUNTRIES)) for _ in range(SPRINT_N_PROBLEMS)]
    problems = [{"flag": COUNTRIES[i]["flag"]} for i in idxs]
    token = gameround.stash({"idxs": idxs, "started": time.monotonic()})
    return {"token": token, "problems": problems, "duration": SPRINT_DURATION}


class SprintSubmitBody(BaseModel):
    token: str
    answers: list[str]


@router.post("/sprint/submit")
async def sprint_submit(request: Request, body: SprintSubmitBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    state = gameround.claim(body.token)  # single-use: can't resubmit to farm
    if not isinstance(state, dict):
        return JSONResponse({"error": "run expired — start a new one"}, status_code=400)
    if time.monotonic() - state["started"] > SPRINT_DURATION + SPRINT_GRACE:
        return JSONResponse({"error": "too slow — that run timed out"}, status_code=400)

    idxs = state["idxs"]
    submitted = body.answers[:len(idxs)]  # ignore anything past the problem count
    correct = sum(
        1 for i, guess in enumerate(submitted) if _matches(guess, COUNTRIES[idxs[i]])
    )

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    coins = 0
    for _ in range(correct):
        coins += await queries.grant_activity_reward(uid, "geo_guess", day)
    best, is_new = await queries.record_skill_best(SPRINT_GAME_ID, uid, correct)
    rank = await queries.get_skill_rank(SPRINT_GAME_ID, uid)
    balance = await queries.get_casino_balance(uid) or 0
    return {"correct": correct, "coins": coins, "balance": balance,
            "best": best, "is_new_best": is_new, "rank": rank}


@router.get("/sprint/leaderboard")
async def sprint_leaderboard(request: Request):
    rows = await queries.get_skill_leaderboard(SPRINT_GAME_ID, 50)
    names = await queries.get_display_names([r["discord_user"] for r in rows])
    me = _uid(request)
    return {"game": SPRINT_GAME_ID, "duration": SPRINT_DURATION,
            "top": [{"rank": i + 1,
                     "name": names.get(r["discord_user"]) or f"user-{r['discord_user'][-4:]}",
                     "score": r["best_ms"], "runs": r["runs"],
                     "me": r["discord_user"] == me} for i, r in enumerate(rows)]}
