"""Web arcade — Poké Hoops. Draft eight Gen 1–2 Pokémon (3 options a pick, a Legendary first), coach
them into five starters (PG..C) + a three-man bench, then play an 82-game season against the TV
cartridge's fixed league. 82-0 is the dream.

The page drafts locally from a server-issued seed (packs derive from it deterministically) and
submits only seed-token + picks + team; shared/pokehoops.py replays the draft, rejects illegal picks
or an illegal team,
sims the season itself under a fresh season seed, records it, and returns it. The page then
replays that exact season (same engine, same seeds) for the reveal. Leaderboard = each player's
best season (pokehoops_seasons), shared with the Discord /play pokehoops draft."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import queries
from shared import pokehoops
from web import auth, gameround

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/arcade/pokehoops")

DRAFT_TTL = 3600  # seconds a draft token stays good — drafting is unhurried
SHARE_CHANNEL = os.getenv("POKEHOOPS_SHARE_CHANNEL_ID", "1496179308954259698")  # SharpLab #bot
_shared: set[int] = set()  # ponytail: in-memory, a restart lets a season be posted once more; add a column if that matters


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


class TeamBody(BaseModel):
    five: list[int]
    bench: list[int]
    backs: list[int]


class SubmitBody(BaseModel):
    token: str
    picks: list[int]
    team: TeamBody


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
        res = await pokehoops.finish_season(uid, state["seed"], body.picks, body.team.model_dump(), "web")
    except pokehoops.IllegalDraft as e:
        return JSONResponse({"error": f"illegal draft: {e}"}, status_code=400)
    except pokehoops.EngineUnavailable:
        return JSONResponse({"error": "the sim engine is down — try again shortly"}, status_code=503)
    res.pop("lineup_cards", None)  # the page renders its own cards
    res.pop("bench_cards", None)
    return res


def _public(row: dict, name: str | None) -> dict:
    s = row["summary"]
    team = row["lineup"] if isinstance(row["lineup"], dict) else {"five": row["lineup"], "bench": [], "backs": []}
    return {"id": row["id"], "name": name or f"user-{row['discord_user'][-4:]}",
            "wins": row["wins"], "losses": row["losses"], "grade": row["grade"],
            "lineup": team["five"], "team": team, "picks": row["picks"], "seed": row["seed"],
            "season_seed": row["season_seed"], "source": row["source"],
            "top_scorer": s.get("top_scorer"), "created_at": row["created_at"]}


@router.get("/leaderboard")
async def leaderboard(request: Request, period: str = "all"):
    since = None
    if period == "day":
        since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    rows = await queries.get_pokehoops_leaderboard(50, since)
    names = await queries.get_display_names([r["discord_user"] for r in rows])
    me = _uid(request)
    return {"game": "pokehoops", "period": "day" if since else "all",
            "top": [{**_public(r, names.get(r["discord_user"])), "rank": i + 1, "runs": r["runs"],
                     "me": r["discord_user"] == me} for i, r in enumerate(rows)]}


@router.get("/season/{season_id}")
async def season(season_id: int):
    """A recorded season, for share links — the page replays it from the seeds."""
    row = await queries.get_pokehoops_season(season_id)
    if row is None:
        return JSONResponse({"error": "no such season"}, status_code=404)
    if queries.pokehoops_is_legacy(row):
        return JSONResponse({"error": "old-rules season — it can't be replayed under the current game"}, status_code=410)
    names = await queries.get_display_names([row["discord_user"]])
    return _public(row, names.get(row["discord_user"]))


POS = ("PG", "SG", "SF", "PF", "C")


def _share_embed(row: dict, cards: list[dict], bench: list[dict], backs: list[int], name: str, rank: int | None) -> dict:
    ts = row["summary"].get("top_scorer") or {}
    five = "\n".join(f"`{pos:<2}` **{c['name']}** — {c['move']}" for pos, c in zip(POS, cards))
    if bench:
        five += "\nBench: " + " · ".join(f"{c['name']} (backs {POS[s]})" for c, s in zip(bench, backs))
    embed = {
        "title": f"🏀 {name} went {row['wins']}–{row['losses']} ({row['grade']})",
        "url": f"{pokehoops.WEB_URL}?s={row['id']}",
        "description": five,
        "color": 0xF7D02C,
        "thumbnail": {"url": pokehoops.SPRITE_URL.format(ts["n"])} if ts.get("n") else None,
        "fields": [f for f in (
            {"name": "Top scorer", "value": f"{ts['name']} — season-high {ts.get('high', '?')}", "inline": True} if ts.get("name") else None,
            {"name": "Leaderboard", "value": f"#{rank}", "inline": True} if rank else None,
        ) if f],
        "footer": {"text": "Can your five go 82–0? Draft on SharpLab or /play pokehoops"},
    }
    return {k: v for k, v in embed.items() if v is not None}


@router.post("/share/{season_id}")
async def share(request: Request, season_id: int):
    """Post one of your seasons to SharpLab's #bot channel (once per season)."""
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to share"}, status_code=401)
    row = await queries.get_pokehoops_season(season_id)
    if row is None or row["discord_user"] != uid:
        return JSONResponse({"error": "not your season"}, status_code=404)
    if queries.pokehoops_is_legacy(row):
        return JSONResponse({"error": "old-rules season"}, status_code=410)
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        return JSONResponse({"error": "sharing is off"}, status_code=503)
    if season_id in _shared:
        return JSONResponse({"error": "already posted"}, status_code=409)
    _shared.add(season_id)  # claim before awaiting so a double-click posts once
    try:
        await _post(token, row, uid)
    except Exception:
        log.exception("pokehoops share %s failed", season_id)
        _shared.discard(season_id)
        return JSONResponse({"error": "couldn't post to Discord — try again"}, status_code=502)
    return {"ok": True}


async def _post(token: str, row: dict, uid: str) -> None:
    season_id = row["id"]
    # the stored summary has dex numbers only; a replay (~0.1s) gives the full cards
    team = row["lineup"]
    res = await pokehoops.sim_season(row["seed"], row["picks"], team, row["season_seed"])
    names = await queries.get_display_names([uid])
    best = await queries.get_pokehoops_best(uid)
    rank = await queries.get_pokehoops_rank(uid) if best and best["id"] == season_id else None
    body = {"content": f"<@{uid}> shared a Poké Hoops season", "allowed_mentions": {"parse": []},
            "embeds": [_share_embed(row, res["lineup_cards"], res["bench_cards"], team["backs"], names.get(uid) or f"user-{uid[-4:]}", rank)]}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"https://discord.com/api/v10/channels/{SHARE_CHANNEL}/messages",
                              headers={"Authorization": f"Bot {token}"}, json=body)
    r.raise_for_status()
