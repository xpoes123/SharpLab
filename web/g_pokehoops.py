"""Web arcade — Poké Hoops. Draft five Gen 1 Pokémon (3 options a pick, a Legendary first), then
your auto-lineup plays an 82-game season against the TV cartridge's fixed league. 82-0 is the dream.

The page drafts locally from a server-issued seed (packs derive from it deterministically) and
submits only seed-token + picks; shared/pokehoops.py replays the draft, rejects illegal picks,
sims the season itself under a fresh season seed, records it, and returns it. The page then
replays that exact season (same engine, same seeds) for the reveal. Leaderboard = each player's
best season (pokehoops_seasons), shared with the Discord /play pokehoops draft."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import queries
from shared import pokehoops
from web import auth, gameround

router = APIRouter(prefix="/api/v1/arcade/pokehoops")

DRAFT_TTL = 3600  # seconds a draft token stays good — drafting is unhurried


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else None


@router.post("/start")
async def start(request: Request):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    seed = pokehoops.new_seed()
    token = gameround.stash({"game": "pokehoops", "uid": uid, "seed": seed, "started": time.monotonic()})
    return {"token": token, "seed": seed}


class SubmitBody(BaseModel):
    token: str
    picks: list[int]


@router.post("/submit")
async def submit(request: Request, body: SubmitBody):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    state = gameround.claim(body.token)  # single use: one season per draft
    if not isinstance(state, dict) or state.get("game") != "pokehoops" or state.get("uid") != uid:
        return JSONResponse({"error": "draft expired — start a new one"}, status_code=400)
    if time.monotonic() - state["started"] > DRAFT_TTL:
        return JSONResponse({"error": "draft expired — start a new one"}, status_code=400)
    try:
        res = await pokehoops.finish_season(uid, state["seed"], body.picks, "web")
    except pokehoops.IllegalDraft as e:
        return JSONResponse({"error": f"illegal draft: {e}"}, status_code=400)
    except pokehoops.EngineUnavailable:
        return JSONResponse({"error": "the sim engine is down — try again shortly"}, status_code=503)
    res.pop("lineup_cards", None)  # the page renders its own cards
    return res


def _public(row: dict, name: str | None) -> dict:
    s = row["summary"]
    return {"id": row["id"], "name": name or f"user-{row['discord_user'][-4:]}",
            "wins": row["wins"], "losses": row["losses"], "grade": row["grade"],
            "lineup": row["lineup"], "picks": row["picks"], "seed": row["seed"],
            "season_seed": row["season_seed"], "source": row["source"],
            "top_scorer": s.get("top_scorer"), "created_at": row["created_at"]}


@router.get("/leaderboard")
async def leaderboard(request: Request):
    rows = await queries.get_pokehoops_leaderboard(50)
    names = await queries.get_display_names([r["discord_user"] for r in rows])
    me = _uid(request)
    return {"game": "pokehoops",
            "top": [{**_public(r, names.get(r["discord_user"])), "rank": i + 1, "runs": r["runs"],
                     "me": r["discord_user"] == me} for i, r in enumerate(rows)]}


@router.get("/season/{season_id}")
async def season(season_id: int):
    """A recorded season, for share links — the page replays it from the seeds."""
    row = await queries.get_pokehoops_season(season_id)
    if row is None:
        return JSONResponse({"error": "no such season"}, status_code=404)
    names = await queries.get_display_names([row["discord_user"]])
    return _public(row, names.get(row["discord_user"]))
