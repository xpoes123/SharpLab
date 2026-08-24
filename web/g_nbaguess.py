"""Web arcade — NBA Player Guess (solo). Guess the mystery NBA player from
progressively-revealed clues (career team history + a headline stat). Reuses the
player dataset + answer-checker from the Discord cog so the two stay in sync.
Rounds are stateless: the answer index rides in a signed, time-limited token (no
server-side room state), and coin rewards are the shared per-day-capped activity
reward, so it can't be farmed.

Also offers a 120-second SPRINT mode (modeled on web/g_zetamac.py + web/g_sequence.py):
/sprint/start hands out a batch of players (clues only — the answer index stays
server-side under an opaque token) and /sprint/submit recounts correctness server-side
via the same check_nba_answer used by the practice mode, and rejects late submits."""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bot.cogs.nbaguess import NBA_PLAYERS_DATA, check_nba_answer
from db import queries
from web import auth, gameround

router = APIRouter(prefix="/api/v1/arcade/nbaguess")

SPRINT_DURATION = 120    # seconds in the sprint
SPRINT_GRACE = 15        # extra seconds allowed for the submit to land
SPRINT_N_PROBLEMS = 60   # batch size — repeats OK, secrets.randbelow per pick
SPRINT_GAME_ID = "nbaguess"


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
    idx = gameround.peek(token)
    if not isinstance(idx, int):
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
    return {"token": gameround.stash(idx), "clues": _clues(entry)}


class GuessBody(BaseModel):
    token: str
    guess: str = ""
    shown: int = 0  # clues the player had revealed when guessing (for the earliness bonus)


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
    # Earliness bonus: base 15 + 10 for every clue the player did NOT need. Guessing on the
    # first clue pays the most; using all clues pays the base. Client reports clues shown —
    # ponytail: the 200/day cap bounds any gaming of the self-reported count.
    total = len(_clues(entry))
    shown = max(1, min(body.shown or total, total))
    base = queries.ACTIVITY_REWARDS["nba_guess"][0]
    amount = base + 10 * (total - shown)
    reward = await queries.grant_activity_reward(
        uid, "nba_guess", day, amount_override=amount, reason="NBA player guess"
    )
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


# ── Sprint mode (120s, batch of players, highest-correct-wins leaderboard) ─────


@router.post("/sprint/start")
async def sprint_start(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    idxs = [secrets.randbelow(len(NBA_PLAYERS_DATA)) for _ in range(SPRINT_N_PROBLEMS)]
    problems = [{"clues": _clues(NBA_PLAYERS_DATA[i])} for i in idxs]
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
        1 for k, ans in enumerate(submitted)
        if check_nba_answer(ans, NBA_PLAYERS_DATA[idxs[k]])
    )

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    coins = 0
    for _ in range(correct):
        coins += await queries.grant_activity_reward(uid, "nba_guess", day)
    best, is_new = await queries.record_skill_best(SPRINT_GAME_ID, uid, correct)
    rank = await queries.get_skill_rank(SPRINT_GAME_ID, uid)
    balance = await queries.get_casino_balance(uid) or 0
    return {"correct": correct, "coins": coins, "balance": balance,
            "best": best, "is_new_best": is_new, "rank": rank}


@router.get("/sprint/leaderboard")
async def sprint_leaderboard(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    rows = await queries.get_skill_leaderboard(SPRINT_GAME_ID, 50)
    names = await queries.get_display_names([r["discord_user"] for r in rows])
    me = _uid(request)
    return {"game": SPRINT_GAME_ID, "duration": SPRINT_DURATION,
            "top": [{"rank": i + 1,
                     "name": names.get(r["discord_user"]) or f"user-{r['discord_user'][-4:]}",
                     "score": r["best_ms"], "runs": r["runs"],
                     "me": r["discord_user"] == me} for i, r in enumerate(rows)]}
