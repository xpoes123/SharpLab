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
import asyncio
import math
import os
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictInt

from db import queries
from shared import pokehoops
from web import auth, gameround

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/arcade/pokehoops")

DRAFT_TTL = 3600  # seconds a draft token stays good — drafting is unhurried
SHARE_CHANNEL = os.getenv("POKEHOOPS_SHARE_CHANNEL_ID", "1496179308954259698")  # SharpLab #bot
_shared: set[int] = set()  # ponytail: in-memory, a restart lets a season be posted once more; add a column if that matters


GUEST_COOKIE = "ph_guest"   # "<token>:<name>" — play without Discord (no coins, no #bot posts)
_GUEST_RE = re.compile(r"^[A-Za-z0-9_-]{16}:[\w .'-]{1,20}$")


def _clean_name(name: str | None) -> str:
    return re.sub(r"[^\w .'-]", "", (name or "")).strip()[:20]


def _guest(request: Request) -> str | None:
    """Guest uid "guest:<token>:<name>" from the cookie, if it's well-formed. The 16-char random token is the secret."""
    raw = request.cookies.get(GUEST_COOKIE) if hasattr(request, "cookies") else None
    return f"guest:{raw}" if raw and _GUEST_RE.match(raw) else None


def is_guest(uid: str | None) -> bool:
    return bool(uid) and uid.startswith("guest:")


def _uid(request: Request) -> str | None:
    sess = auth.read_session(request)
    return sess["id"] if sess else _guest(request)


def _name(uid: str, names: dict) -> str:
    if is_guest(uid):
        return uid.split(":", 2)[2] + " (guest)"
    return names.get(uid) or f"user-{uid[-4:]}"


class GuestBody(BaseModel):
    name: str


@router.post("/guest")
async def guest(body: GuestBody):
    """Start (or rename) a guest identity: sets the cookie the rest of the API reads. Renaming = a fresh guest."""
    name = _clean_name(body.name)
    if not name:
        return JSONResponse({"error": "pick a name"}, status_code=400)
    token = secrets.token_urlsafe(12)[:16]
    res = JSONResponse({"name": name})
    res.set_cookie(GUEST_COOKIE, f"{token}:{name}", max_age=365 * 86400, samesite="lax", secure=True, path="/")
    return res


@router.post("/start")
async def start(request: Request):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"error": "sign in to play"}, status_code=401)
    seed = pokehoops.new_seed()
    token = gameround.stash({"game": "pokehoops", "uid": uid, "seed": seed, "started": time.monotonic()})
    return {"token": token, "seed": seed}


class TouchBody(BaseModel):
    token: str


@router.post("/touch")
async def touch(request: Request, body: TouchBody):
    """Keep an open draft alive (the page calls this on reload and between bot games): resets its TTL clock."""
    state = gameround.peek(body.token)
    if not isinstance(state, dict) or state.get("game") != "pokehoops" or state.get("uid") != _uid(request) \
            or time.monotonic() - state["started"] > DRAFT_TTL:
        return JSONResponse({"error": "draft expired — start a new one"}, status_code=400)
    state["started"] = time.monotonic()
    return {"ok": True}


class TeamBody(BaseModel):
    five: list[int]
    bench: list[int]
    backs: list[int]


class SubmitBody(BaseModel):
    token: str
    picks: list[int]
    team: TeamBody
    vs: int | None = None  # a recorded season id: play its team best-of-seven instead of the 82-game season


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
        if body.vs is not None:
            return await pokehoops.finish_series(uid, state["seed"], body.picks, body.team.model_dump(), body.vs)
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
    return {"id": row["id"], "name": name or _name(row["discord_user"], {}),
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
async def season(request: Request, season_id: int):
    """A recorded season, for share links — the page replays it from the seeds."""
    row = await queries.get_pokehoops_season(season_id)
    if row is None:
        return JSONResponse({"error": "no such season"}, status_code=404)
    if queries.pokehoops_is_legacy(row):
        return JSONResponse({"error": "old-rules season — it can't be replayed under the current game"}, status_code=410)
    challenges = await queries.get_pokehoops_challenges(season_id)
    names = await queries.get_display_names([row["discord_user"]] + [c["challenger"] for c in challenges])
    nm = lambda u: _name(u, names)  # noqa: E731
    return {**_public(row, names.get(row["discord_user"])), "me": row["discord_user"] == _uid(request),
            "challenges": [{"id": c["id"], "name": nm(c["challenger"]), "wins": c["wins"], "losses": c["losses"]}
                           for c in challenges]}


@router.get("/series/{series_id}")
async def series(series_id: int):
    """A recorded head-to-head, for share links — the page replays it from the seeds and both teams."""
    row = await queries.get_pokehoops_series(series_id)
    if row is None:
        return JSONResponse({"error": "no such series"}, status_code=404)
    opp = await queries.get_pokehoops_season(row["vs_season"])
    names = await queries.get_display_names([row["challenger"], opp["discord_user"]])
    nm = lambda u: _name(u, names)  # noqa: E731
    return {"id": row["id"], "vs": row["vs_season"], "seed": row["seed"], "series_seed": row["series_seed"],
            "picks": row["picks"], "team": row["team"], "wins": row["wins"], "losses": row["losses"],
            "name": nm(row["challenger"]), "opp_name": nm(opp["discord_user"]), "opp_team": opp["lineup"]}


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
    if not uid or is_guest(uid):
        return JSONResponse({"error": "sign in with Discord to post to #bot"}, status_code=401)
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


# ── Live duel: two phones, one room. Both coaches draft at once (separate packs, lockstep rounds), set lineups,
# see the line (Monte Carlo of the exact matchup), optionally bet coins on it, then a best-of-seven simmed here.
# Players act with a per-seat key (no sign-in needed — guests can join by name; betting needs a SharpLab account).
# ponytail: rooms are in memory — a web restart ends live duels (finished ones are saved in pokehoops_duels)

DUEL_TTL = 6 * 3600
IDLE_TTL = 30 * 60    # a room nobody is drafting/betting in (lobby, or finished) closes after this long untouched
MAX_ROOMS = 500
CODE_LEN = 5          # 24^5 ≈ 8M codes vs ≤500 open rooms, and joins are rate-limited
VIG = 0.045           # the book's hold on series bets, split across both sides
MAX_STAKE = 5000
_duels: dict[str, dict] = {}
_CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_hits: dict[tuple[str, str], list[float]] = defaultdict(list)   # (bucket, ip) → recent timestamps
LIMITS = {"create": (10, 600), "join": (30, 60)}                  # bucket → (max calls, per seconds)


def _ip(request: Request) -> str:
    """Client IP behind Caddy (it sets X-Forwarded-For), same as the analytics endpoint."""
    fwd = getattr(request, "headers", {}).get("x-forwarded-for", "")
    client = getattr(request, "client", None)
    return fwd.split(",")[0].strip() if fwd else (client.host if client else "")


def _limited(request: Request, bucket: str) -> JSONResponse | None:
    """Sliding-window per-IP limit (in memory, like web/api.py's analytics limiter). Returns a 429 when over."""
    n, window = LIMITS[bucket]
    now, k = time.time(), (bucket, _ip(request))
    recent = [t for t in _hits[k] if now - t < window]
    if len(recent) >= n:
        _hits[k] = recent
        return JSONResponse({"error": "slow down — try again in a minute"}, status_code=429)
    _hits[k] = recent + [now]
    return None


class DuelBody(BaseModel):
    key: str | None = None      # legacy: the seat key now rides in the X-Duel-Key header (DuelKey)
    name: str | None = None
    n: StrictInt | None = None
    team: TeamBody | None = None
    side: StrictInt | None = None
    stake: StrictInt | None = None


DuelKey = Annotated[str | None, Header(alias="X-Duel-Key")]


def _seat_name(request: Request, name: str | None) -> tuple[str | None, str | None]:
    """(uid, display name): signed-in users keep their Discord name; guests must give one."""
    sess = auth.read_session(request)
    if sess:
        return sess["id"], sess.get("username") or sess.get("name") or "Coach"
    g = _guest(request)
    if g:
        return g, g.split(":", 2)[2]
    return None, _clean_name(name) or None


def _new_seat(uid: str | None, name: str) -> dict:
    return {"key": secrets.token_urlsafe(12), "uid": uid, "name": name}


def _phase(r: dict) -> str:
    if r["left"] is not None and r["result"] is None:
        return "left"
    if r["seats"][1] is None:
        return "lobby"
    if len(r["picks"][0]) < pokehoops.PICKS:
        return "draft"
    if None in r["teams"] or r["line"] is None:     # tip-off waits for the line (computed after both lock)
        return "lineup"
    if r["result"] is None:
        return "tip"
    return "done"


def _seat_of(r: dict, key: str | None) -> int | None:
    return next((i for i, st in enumerate(r["seats"]) if st and key and secrets.compare_digest(st["key"], key)), None)


def _price(p: float, half: float) -> float:
    """Decimal odds the book offers on an outcome the model gives probability p, holding `half` on this side. Always
    below fair (1/p), rounded down; a lock bottoms out at 1.0 (stake back) rather than ever paying +EV."""
    return max(1.0, int(1000 / (p + half)) / 1000)


def _prices(line: dict) -> list[float]:
    """Both sides' series prices. The line is a Monte Carlo estimate (and a client can re-run it for longer), so each
    side's hold is at least 2 standard errors of the series estimate: half = max(VIG/2, 2·se_series). se_series comes
    from the game-level se via dS/dp = (2k-1)!/((k-1)!)² · (pq)^(k-1) for a first-to-k series."""
    k = pokehoops.SERIES_WINS
    p = line["p"]
    se = line.get("se") or (p * (1 - p) / line["n"]) ** 0.5
    se_series = math.factorial(2 * k - 1) / math.factorial(k - 1) ** 2 * (p * (1 - p)) ** (k - 1) * se
    half = max(VIG / 2, 2 * se_series)
    return [_price(line["series"], half), _price(1 - line["series"], half)]


def _view(r: dict, seat: int) -> dict:
    ph, opp = _phase(r), 1 - seat
    line = r["line"]
    return {
        "code": r["code"], "seat": seat, "phase": ph, "seed": r["seed"],
        "names": [st["name"] if st else None for st in r["seats"]],
        "can_bet": bool(r["seats"][seat]["uid"]) and not is_guest(r["seats"][seat]["uid"]),
        "picks": r["picks"], "pending": r["pending"][seat], "opp_pending": r["pending"][opp] is not None,
        "team": r["teams"][seat], "opp_team": r["teams"][opp] if ph in ("tip", "done") else None,
        "opp_locked": r["teams"][opp] is not None,
        "line": line, "prices": _prices(line) if line else None,
        "ready": r["ready"], "bets": r["bets"],
        "result": r["result"], "left": r["left"],
    }


def _sweep():
    now = time.time()
    for c in [c for c, r in _duels.items() if now - r["created"] > DUEL_TTL
              or (now - r["touched"] > IDLE_TTL and _phase(r) in ("lobby", "done", "left"))]:
        _duels.pop(c, None)
    longest = max(w for _, w in LIMITS.values())
    for k in [k for k, ts in _hits.items() if not ts or now - ts[-1] > longest]:
        _hits.pop(k, None)


@router.post("/duel")
async def duel_create(request: Request, body: DuelBody):
    uid, name = _seat_name(request, body.name)
    if not name:
        return JSONResponse({"error": "pick a name"}, status_code=400)
    if err := _limited(request, "create"):
        return err
    _sweep()
    code = next((c for c in ("".join(secrets.choice(_CODE_CHARS) for _ in range(CODE_LEN)) for _ in range(20))
                 if c not in _duels), None)
    if code is None or len(_duels) >= MAX_ROOMS:
        return JSONResponse({"error": "too many rooms open right now — try again shortly"}, status_code=503)
    seat = _new_seat(uid, name)
    _duels[code] = {"code": code, "created": time.time(), "touched": time.time(), "lock": asyncio.Lock(), "seed": pokehoops.new_seed(),
                    "seats": [seat, None], "picks": [[], []], "pending": [None, None], "teams": [None, None],
                    "line": None, "ready": [False, False], "bets": [None, None], "result": None, "left": None}
    return {"code": code, "key": seat["key"]}


@router.post("/duel/{code}/join")
async def duel_join(request: Request, code: str, body: DuelBody):
    if err := _limited(request, "join"):
        return err
    r = _duels.get(code.upper())
    if r is None:
        return JSONResponse({"error": "no such room — check the code"}, status_code=404)
    uid, name = _seat_name(request, body.name)
    async with r["lock"]:
        if uid and any(st and st["uid"] == uid for st in r["seats"]):    # signed-in user re-opening their own room
            return {"code": r["code"], "key": next(st["key"] for st in r["seats"] if st and st["uid"] == uid)}
        if not name:
            return JSONResponse({"error": "pick a name"}, status_code=400)
        if r["seats"][1] is not None:
            return JSONResponse({"error": "that room is full"}, status_code=409)
        try:        # deal round one before taking the seat, so an engine hiccup leaves the room joinable
            r["packs"] = await pokehoops.duel_packs(r["seed"], r["picks"])
        except pokehoops.EngineUnavailable:
            return JSONResponse({"error": "the sim engine is down — try again shortly"}, status_code=503)
        r["seats"][1] = _new_seat(uid, name)
        r["touched"] = time.time()
        return {"code": r["code"], "key": r["seats"][1]["key"]}


@router.get("/duel/{code}/peek")
async def duel_peek(request: Request, code: str):
    """Before asking a visitor's name: is there a seat for them? (Signed-in seat-holders re-open via /join.)"""
    if err := _limited(request, "join"):
        return err
    r = _duels.get(code.upper())
    if r is None or _phase(r) == "left":
        return JSONResponse({"error": "that room has closed"}, status_code=404)
    return {"code": r["code"], "host": r["seats"][0]["name"], "open": r["seats"][1] is None}


def _room(code: str, key: str | None) -> tuple[dict | None, int | None, JSONResponse | None]:
    r = _duels.get(code.upper())
    if r is None:
        return None, None, JSONResponse({"error": "that room has closed"}, status_code=404)
    seat = _seat_of(r, key)
    if seat is None:
        return None, None, JSONResponse({"error": "you're not in this room"}, status_code=403)
    r["touched"] = time.time()
    return r, seat, None


@router.get("/duel/{code}")
async def duel_state(code: str, key: str = "", x_duel_key: DuelKey = None):
    r, seat, err = _room(code, x_duel_key or key)
    return err or _view(r, seat)


@router.post("/duel/{code}/pick")
async def duel_pick(code: str, body: DuelBody, x_duel_key: DuelKey = None):
    r, seat, err = _room(code, x_duel_key or body.key)
    if err:
        return err
    async with r["lock"]:
        if _phase(r) != "draft" or r["pending"][seat] is not None:
            return JSONResponse({"error": "not your pick right now"}, status_code=409)
        if body.n not in r["packs"][seat]:
            return JSONResponse({"error": "that one wasn't in your pack"}, status_code=400)
        pending = [body.n if s == seat else r["pending"][s] for s in (0, 1)]
        if None not in pending:                          # both in: reveal the round, deal the next packs
            picks = [r["picks"][s] + [pending[s]] for s in (0, 1)]
            try:                                         # deal first, so a failure leaves the round as it was
                r["packs"] = await pokehoops.duel_packs(r["seed"], picks)
            except pokehoops.EngineUnavailable:
                return JSONResponse({"error": "the sim engine is down — try again shortly"}, status_code=503)
            r["picks"], pending = picks, [None, None]
        r["pending"] = pending
        return _view(r, seat)


@router.post("/duel/{code}/team")
async def duel_team(code: str, body: DuelBody, x_duel_key: DuelKey = None):
    r, seat, err = _room(code, x_duel_key or body.key)
    if err:
        return err
    async with r["lock"]:
        if _phase(r) != "lineup" or r["teams"][seat] is not None:
            return JSONResponse({"error": "lineups are locked"}, status_code=409)
        team = body.team.model_dump() if body.team else {}
        if not pokehoops.legal_team(team, r["picks"][seat]):
            return JSONResponse({"error": "illegal lineup: each of your eight once, five starters + three bench"}, status_code=400)
        r["teams"][seat] = team
        if None not in r["teams"]:
            try:
                r["line"] = await pokehoops.duel_line(r["seed"], r["picks"], r["teams"])
            except pokehoops.EngineUnavailable:
                r["teams"][seat] = None
                return JSONResponse({"error": "the sim engine is down — try again shortly"}, status_code=503)
        return _view(r, seat)


@router.post("/duel/{code}/ready")
async def duel_ready(request: Request, code: str, body: DuelBody, x_duel_key: DuelKey = None):
    """Lock in for tip-off, optionally with a bet on either side of the series at the posted price. Coins move only
    at tip-off (see _tip_off), so a room that dies before then never holds anyone's stake."""
    r, seat, err = _room(code, x_duel_key or body.key)
    if err:
        return err
    async with r["lock"]:
        ph = _phase(r)
        if ph != "tip":
            msg = {"done": "this series is already over", "left": "this room has closed"}.get(ph, "not tip-off yet")
            return JSONResponse({"error": msg}, status_code=409)
        if r["ready"][seat]:
            return JSONResponse({"error": "already locked in"}, status_code=409)
        uid = r["seats"][seat]["uid"]
        if body.stake:
            if not uid or is_guest(uid) or _uid(request) != uid:
                return JSONResponse({"error": "sign in to bet coins"}, status_code=401)
            if body.side not in (0, 1) or not 1 <= body.stake <= MAX_STAKE:
                return JSONResponse({"error": f"bet 1–{MAX_STAKE:,} coins on one side"}, status_code=400)
            if await queries.get_or_create_casino_wallet(uid) < body.stake:
                return JSONResponse({"error": "not enough coins"}, status_code=400)
            r["bets"][seat] = {"side": body.side, "stake": body.stake, "price": _prices(r["line"])[body.side]}
        r["ready"][seat] = True
        if all(r["ready"]) and (err := await _tip_off(r)):
            return err
        return _view(r, seat)


async def _tip_off(r: dict) -> JSONResponse | None:
    """Both locked: debit both stakes, draw the series seed only now (after every bet), sim, settle, record.
    If a stake can't be debited or the sim fails, whatever was debited goes back and both coaches un-ready."""
    debited: list[tuple[str, int]] = []

    async def undo(msg: str, status: int) -> JSONResponse:
        for uid, stake in debited:
            await queries.update_casino_balance(uid, stake)
        r["ready"], r["bets"] = [False, False], [None, None]
        return JSONResponse({"error": msg}, status_code=status)

    for seat, bet in enumerate(r["bets"]):
        if bet:
            uid = r["seats"][seat]["uid"]
            try:
                await queries.update_casino_balance(uid, -bet["stake"])
            except ValueError:
                return await undo(f"{r['seats'][seat]['name']} no longer has {bet['stake']:,} coins — lock in again", 409)
            debited.append((uid, bet["stake"]))
    series_seed = pokehoops.new_seed()
    try:
        x = await pokehoops.duel_series(r["seed"], r["picks"], r["teams"], series_seed)
    except Exception:
        log.exception("pokehoops duel %s: series sim failed", r["code"])
        return await undo("the sim engine hiccuped — stakes refunded, lock in again", 503)
    winner = 0 if x["wins"][0] > x["wins"][1] else 1
    for seat, bet in enumerate(r["bets"]):
        if not bet:
            continue
        uid = r["seats"][seat]["uid"]
        bet["payout"] = int(bet["stake"] * bet["price"]) if bet["side"] == winner else 0
        bet["balance"] = await queries.update_casino_balance(uid, bet["payout"]) if bet["payout"] \
            else await queries.get_casino_balance(uid)
        await queries.log_casino_result(uid, "pokehoops", bet["stake"], bet["payout"])
    a, b = r["seats"]
    did = await queries.insert_pokehoops_duel({
        "a": a["uid"] or f"guest:-:{a['name']}", "b": b["uid"] or f"guest:-:{b['name']}", "a_name": a["name"],
        "b_name": b["name"], "seed": r["seed"], "series_seed": series_seed, "picks": r["picks"], "teams": r["teams"],
        "wins_a": x["wins"][0], "wins_b": x["wins"][1], "line": r["line"], "bets": r["bets"]})
    r["result"] = {"id": did, "series_seed": series_seed, "wins": x["wins"]}
    return None


@router.post("/duel/{code}/leave")
async def duel_leave(code: str, body: DuelBody, x_duel_key: DuelKey = None):
    """Walk out. A lobby just closes; mid-duel the other coach sees who left and heads home. No coins are ever
    held before tip-off, so there's nothing to refund; a finished series is unaffected."""
    r, seat, err = _room(code, x_duel_key or body.key)
    if err:
        return err
    async with r["lock"]:
        ph = _phase(r)
        if ph == "lobby":
            _duels.pop(r["code"], None)
        elif ph not in ("done", "left"):
            r["left"] = seat
        return {"ok": True}


@router.get("/duels/{duel_id}")
async def duel_record(duel_id: int):
    """A finished duel, for share links — the page replays it from the seeds."""
    d = await queries.get_pokehoops_duel(duel_id)
    if d is None:
        return JSONResponse({"error": "no such duel"}, status_code=404)
    return {"id": d["id"], "names": [d["a_name"], d["b_name"]], "seed": d["seed"], "series_seed": d["series_seed"],
            "picks": d["picks"], "teams": d["teams"], "wins": [d["wins_a"], d["wins_b"]], "line": d["line"],
            "bets": d["bets"]}


# ── Battle rooms: two phones, one 3v3 Battle. The server is only a relay. Each phone drafts its six from packs seeded
# off the room seed, posts its roster, then every turn both post {subs, pick} for that turn number; once both are in,
# the turn is revealed to both, and each phone replays the whole game from the seed (same engine, same rng order),
# so they agree without the server running the sim. Nothing is at stake, so rosters/picks aren't re-checked here:
# an illegal pick falls back to the first option on both phones alike.
# ponytail: trust the clients' rosters; re-check them with the node engine if Battle ever pays out coins.
COURTS = ("clear", "rain", "sun", "sand")
NO_BATTLE = {10, 11, 13, 14, 129, 132, 201}   # moveset-less (tv battle.js NO_BATTLE)
MAX_TURNS = 400
_battles: dict[str, dict] = {}


class BattleBody(BaseModel):
    name: str | None = None
    court: str | None = None
    picks: list[StrictInt] | None = None     # six dex numbers, starters first
    turn: StrictInt | None = None
    pick: str | None = None
    subs: list[list[StrictInt]] | None = None  # [[court slot, roster idx], ...] made at this dead ball


def _bphase(r: dict) -> str:
    if r["left"] is not None:
        return "left"
    if r["seats"][1] is None:
        return "lobby"
    return "draft" if None in r["rosters"] else "battle"


def _bview(r: dict, seat: int) -> dict:
    ph = _bphase(r)
    return {"code": r["code"], "seat": seat, "phase": ph, "seed": r["seed"], "game": r["game"], "court": r["court"],
            "names": [st["name"] if st else None for st in r["seats"]],
            "roster": r["rosters"][seat], "opp_locked": r["rosters"][1 - seat] is not None,
            "rosters": r["rosters"] if ph == "battle" else None,
            "turns": r["turns"], "pending": r["pending"][seat] is not None, "opp_pending": r["pending"][1 - seat] is not None,
            "left": r["left"]}


def _bsweep():
    now = time.time()
    for c in [c for c, r in _battles.items() if now - r["created"] > DUEL_TTL or now - r["touched"] > IDLE_TTL]:
        _battles.pop(c, None)


def _broom(code: str, key: str | None) -> tuple[dict | None, int | None, JSONResponse | None]:
    r = _battles.get(code.upper())
    if r is None:
        return None, None, JSONResponse({"error": "that room has closed"}, status_code=404)
    seat = _seat_of(r, key)
    if seat is None:
        return None, None, JSONResponse({"error": "you're not in this room"}, status_code=403)
    r["touched"] = time.time()
    return r, seat, None


@router.post("/battle")
async def battle_create(request: Request, body: BattleBody):
    uid, name = _seat_name(request, body.name)
    if not name:
        return JSONResponse({"error": "pick a name"}, status_code=400)
    if err := _limited(request, "create"):
        return err
    _bsweep()
    code = next((c for c in ("".join(secrets.choice(_CODE_CHARS) for _ in range(CODE_LEN)) for _ in range(20))
                 if c not in _battles and c not in _duels), None)
    if code is None or len(_battles) >= MAX_ROOMS:
        return JSONResponse({"error": "too many rooms open right now — try again shortly"}, status_code=503)
    seat = _new_seat(uid, name)
    _battles[code] = {"code": code, "created": time.time(), "touched": time.time(), "lock": asyncio.Lock(),
                      "seed": secrets.randbits(32), "game": 0, "court": body.court if body.court in COURTS else "clear",
                      "seats": [seat, None], "rosters": [None, None], "turns": [], "pending": [None, None], "left": None}
    return {"code": code, "key": seat["key"]}


@router.post("/battle/{code}/join")
async def battle_join(request: Request, code: str, body: BattleBody):
    if err := _limited(request, "join"):
        return err
    r = _battles.get(code.upper())
    if r is None or _bphase(r) == "left":
        return JSONResponse({"error": "that room has closed"}, status_code=404)
    uid, name = _seat_name(request, body.name)
    async with r["lock"]:
        if uid and any(st and st["uid"] == uid for st in r["seats"]):
            return {"code": r["code"], "key": next(st["key"] for st in r["seats"] if st and st["uid"] == uid)}
        if r["seats"][1] is not None:
            return JSONResponse({"error": "that room is full"}, status_code=409)
        if not name:
            return JSONResponse({"error": "pick a name"}, status_code=400)
        r["seats"][1] = _new_seat(uid, name)
        r["touched"] = time.time()
        return {"code": r["code"], "key": r["seats"][1]["key"]}


@router.get("/battle/{code}")
async def battle_state(code: str, x_duel_key: DuelKey = None):
    r, seat, err = _broom(code, x_duel_key)
    return err or _bview(r, seat)


@router.post("/battle/{code}/roster")
async def battle_roster(code: str, body: BattleBody, x_duel_key: DuelKey = None):
    r, seat, err = _broom(code, x_duel_key)
    if err:
        return err
    p = body.picks or []
    if len(p) != 6 or len(set(p)) != 6 or any(not 1 <= n <= 251 or n in NO_BATTLE for n in p):
        return JSONResponse({"error": "a roster is six different Pokémon"}, status_code=400)
    async with r["lock"]:
        if _bphase(r) != "draft" or r["rosters"][seat] is not None:
            return JSONResponse({"error": "your roster is already in"}, status_code=409)
        r["rosters"][seat] = p
        return _bview(r, seat)


@router.post("/battle/{code}/move")
async def battle_move(code: str, body: BattleBody, x_duel_key: DuelKey = None):
    r, seat, err = _broom(code, x_duel_key)
    if err:
        return err
    subs = body.subs or []
    if not body.pick or len(body.pick) > 16 or len(subs) > 3 or any(len(s) != 2 for s in subs):
        return JSONResponse({"error": "bad move"}, status_code=400)
    async with r["lock"]:
        if _bphase(r) != "battle" or body.turn != len(r["turns"]) or r["pending"][seat] is not None or len(r["turns"]) >= MAX_TURNS:
            return JSONResponse({"error": "not your call right now"}, status_code=409)
        r["pending"][seat] = {"pick": body.pick, "subs": subs}
        if None not in r["pending"]:      # both in: reveal the turn
            r["turns"].append(r["pending"])
            r["pending"] = [None, None]
        return _bview(r, seat)


@router.post("/battle/{code}/rematch")
async def battle_rematch(code: str, body: BattleBody, x_duel_key: DuelKey = None):
    """Same rosters, new tip-off: a fresh seed, the turn log cleared. `turn` is the caller's turn count, so two
    rematch taps racing each other only reset once."""
    r, seat, err = _broom(code, x_duel_key)
    if err:
        return err
    async with r["lock"]:
        if _bphase(r) == "battle" and body.turn == len(r["turns"]) and r["turns"]:
            r["seed"], r["game"], r["turns"], r["pending"] = secrets.randbits(32), r["game"] + 1, [], [None, None]
        return _bview(r, seat)


@router.post("/battle/{code}/leave")
async def battle_leave(code: str, x_duel_key: DuelKey = None):
    r, seat, err = _broom(code, x_duel_key)
    if err:
        return err
    async with r["lock"]:
        if _bphase(r) == "lobby":
            _battles.pop(r["code"], None)
        else:
            r["left"] = seat
        return {"ok": True}
