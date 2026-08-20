"""Web arcade — solo Mastermind (code-breaking) on the casino-coin economy.
Crack a secret 4-peg / 6-color code in 10 guesses. Rounds are stateless: the
secret code rides in a signed, time-limited token (no server-side room state),
and the win reward is the shared per-day-capped activity reward, so it can't be
farmed. Mirrors the arcade.py pattern."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from db import queries
from web import auth, gameround

router = APIRouter(prefix="/api/v1/arcade/mastermind")
_round_signer = URLSafeTimedSerializer(auth.SESSION_SECRET, salt="arcade-mastermind")
_ROUND_TTL = 1800  # seconds a round token stays valid

COLORS = ("red", "orange", "yellow", "green", "blue", "purple")
CODE_LEN = 4


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


def _code_from_token(token: str) -> list[str] | None:
    code = gameround.peek(token)
    if not isinstance(code, list) or len(code) != CODE_LEN:
        return None
    if not all(c in COLORS for c in code):
        return None
    return code


def _score(secret: list[str], guess: list[str]) -> tuple[int, int]:
    """Standard Mastermind feedback: (black, white).
    black = right color + right position. white = right color, wrong position,
    counted with the min-count rule over the remaining (non-black) pegs so
    colors are never double-counted."""
    black = sum(1 for s, g in zip(secret, guess) if s == g)
    white = 0
    for color in COLORS:
        in_secret = sum(1 for i, s in enumerate(secret) if s == color and guess[i] != color)
        in_guess = sum(1 for i, g in enumerate(guess) if g == color and secret[i] != color)
        white += min(in_secret, in_guess)
    return black, white


@router.post("/new")
async def new(request: Request):
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    code = [secrets.choice(COLORS) for _ in range(CODE_LEN)]
    return {"token": gameround.stash(code)}


class GuessBody(BaseModel):
    token: str
    guess: list[str]


@router.post("/guess")
async def guess(request: Request, body: GuessBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    secret = _code_from_token(body.token)
    if secret is None:
        return JSONResponse({"error": "round expired — start a new one"}, status_code=400)
    if len(body.guess) != CODE_LEN or not all(c in COLORS for c in body.guess):
        return JSONResponse({"error": "guess must be 4 valid colors"}, status_code=400)
    black, white = _score(secret, body.guess)
    solved = black == CODE_LEN
    out = {"black": black, "white": white, "solved": solved}
    if solved:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out["reward"] = await queries.grant_activity_reward(uid, "mastermind_win", day)
        out["balance"] = await queries.get_casino_balance(uid) or 0
    return out


class RevealBody(BaseModel):
    token: str


@router.post("/reveal")
async def reveal(request: Request, body: RevealBody):
    """Give up — reveal the code, no reward."""
    if not _uid(request):
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    secret = _code_from_token(body.token)
    if secret is None:
        return JSONResponse({"error": "round expired"}, status_code=400)
    return {"code": secret}


# ── Timed mode (gauntlet) — solve N codes as fast as you can → best-time leaderboard ──
GAUNTLET_N = 5      # codes per run
MAX_GUESSES = 10    # guesses allowed per code


@router.post("/run/start")
async def run_start(request: Request):
    """Begin a timed gauntlet. The clock is server-side (in the opaque run state), so it can't be
    faked. Returns a run token; play each code via /run/guess."""
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    codes = [[secrets.choice(COLORS) for _ in range(CODE_LEN)] for _ in range(GAUNTLET_N)]
    state = {"uid": uid, "codes": codes, "index": 0, "guesses": 0,
             "started_at": datetime.now(timezone.utc).isoformat()}
    return {"run_token": gameround.stash(state), "target": GAUNTLET_N,
            "index": 0, "max_guesses": MAX_GUESSES}


class RunGuessBody(BaseModel):
    run_token: str
    guess: list[str]


@router.post("/run/guess")
async def run_guess(request: Request, body: RunGuessBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    st = gameround.peek(body.run_token)   # mutable server-side state
    if not isinstance(st, dict) or st.get("uid") != uid:
        return JSONResponse({"error": "run expired — start a new one"}, status_code=400)
    if len(body.guess) != CODE_LEN or not all(c in COLORS for c in body.guess):
        return JSONResponse({"error": "guess must be 4 valid colors"}, status_code=400)

    black, white = _score(st["codes"][st["index"]], list(body.guess))
    st["guesses"] += 1
    out = {"black": black, "white": white, "index": st["index"], "target": GAUNTLET_N}

    if black == CODE_LEN:                       # solved this code
        st["index"] += 1
        out["code_solved"] = True
        if st["index"] >= GAUNTLET_N:           # whole gauntlet done → time it
            gameround.claim(body.run_token)
            elapsed = int((datetime.now(timezone.utc)
                           - datetime.fromisoformat(st["started_at"])).total_seconds() * 1000)
            best, is_new = await queries.record_skill_best("mastermind", uid, elapsed)
            rank = await queries.get_skill_rank("mastermind", uid)
            out.update(done=True, elapsed_ms=elapsed, best_ms=best, is_new_best=is_new, rank=rank)
            # timed runs also earn the capped daily coins on completion
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            out["reward"] = await queries.grant_activity_reward(uid, "mastermind_win", day)
            out["balance"] = await queries.get_casino_balance(uid) or 0
        else:
            st["guesses"] = 0                   # fresh guess budget for the next code
            out["index"] = st["index"]
        return out

    if st["guesses"] >= MAX_GUESSES:            # blew the budget on this code → run over (no time)
        failed = st["codes"][st["index"]]
        gameround.claim(body.run_token)
        return {**out, "done": True, "dnf": True, "code": failed}
    out["guesses_left"] = MAX_GUESSES - st["guesses"]
    return out


@router.get("/leaderboard")
async def leaderboard(request: Request):
    rows = await queries.get_skill_leaderboard("mastermind", 50)
    names = await queries.get_display_names([r["discord_user"] for r in rows])
    me = _uid(request)
    def fmt(ms):
        s = ms // 1000
        return f"{s // 60}:{s % 60:02d}"
    return {"game": "mastermind", "target": GAUNTLET_N,
            "top": [{"rank": i + 1, "name": names.get(r["discord_user"]) or f"user-{r['discord_user'][-4:]}",
                     "time": fmt(r["best_ms"]), "best_ms": r["best_ms"], "runs": r["runs"],
                     "me": r["discord_user"] == me} for i, r in enumerate(rows)]}
