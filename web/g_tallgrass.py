"""Tall Grass duels: two players, equal money, armies hidden until both lock in.

Rooms use the same friend-code + seat-key pattern as Poké Hoops duels. The server is the only thing that holds both
armies before the reveal, and it checks each one with the same JS the browser runs (shared/tallgrass_cli.mjs). Once both
are locked it picks the seed, so neither player can choose a favourable one, and both browsers replay the identical war.
No coins or achievements: wars here are just for fun."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from web.g_pokehoops import DuelKey, _limited, _new_seat, _seat_name, _seat_of

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/arcade/tallgrass")

CLI = Path(__file__).resolve().parent.parent / "shared" / "tallgrass_cli.mjs"
NODE_TIMEOUT = 20.0
BUDGETS = {20000, 50000, 150000}     # keep in step with BUDGETS in web/static/tallgrass/price.js
BIOMES = {"meadow", "forest", "cave", "shore", "snow", "ember", "dusk"}
MAX_SQUADS = 40
MAX_ROOMS = 200
ROOM_TTL = 6 * 3600
IDLE_TTL = 3600
_CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LEN = 5
_rooms: dict[str, dict] = {}         # ponytail: in memory, a web restart ends open duels; persist them if that ever hurts


class EngineDown(RuntimeError):
    pass


async def _engine(req: dict) -> dict:
    node = shutil.which("node") or "node"
    try:
        proc = await asyncio.create_subprocess_exec(
            node, str(CLI), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError as e:
        raise EngineDown(str(e)) from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(req).encode()), NODE_TIMEOUT)
    except asyncio.TimeoutError as e:
        proc.kill()
        raise EngineDown("timed out") from e
    try:
        return json.loads(out)
    except ValueError as e:
        raise EngineDown(err.decode(errors="replace")[-300:]) from e


class DuelBody(BaseModel):
    name: str | None = None
    budget: int | None = None
    biome: str | None = None
    squads: list[dict] | None = None


def _phase(r: dict) -> str:
    if r["left"] is not None:
        return "left"
    if r["seats"][1] is None:
        return "lobby"
    return "war" if r["result"] is not None else "build"


def _view(r: dict, seat: int) -> dict:
    ph = _phase(r)
    revealed = ph == "war"
    return {
        "code": r["code"], "seat": seat, "phase": ph, "budget": r["budget"], "biome": r["biome"], "round": r["round"],
        "names": [st["name"] if st else None for st in r["seats"]], "wins": r["wins"],
        "army": r["armies"][seat], "opp_locked": r["armies"][1 - seat] is not None,
        # the opponent's army and the seed never leave the server until both are locked
        "armies": r["armies"] if revealed else None, "seed": r["seed"] if revealed else None, "result": r["result"],
        "last": r["last"], "rematch": r["rematch"], "left": r["left"],
    }


def _sweep() -> None:
    now = time.time()
    for code in [c for c, r in _rooms.items() if now - r["created"] > ROOM_TTL or now - r["touched"] > IDLE_TTL]:
        _rooms.pop(code, None)


def _room(code: str, key: str | None) -> tuple[dict | None, int | None, JSONResponse | None]:
    r = _rooms.get(code.upper())
    if r is None:
        return None, None, JSONResponse({"error": "that room has closed"}, status_code=404)
    seat = _seat_of(r, key)
    if seat is None:
        return None, None, JSONResponse({"error": "you're not in this room"}, status_code=403)
    r["touched"] = time.time()
    return r, seat, None


@router.post("/duel")
async def duel_create(request: Request, body: DuelBody):
    uid, name = _seat_name(request, body.name)
    if not name:
        return JSONResponse({"error": "pick a name"}, status_code=400)
    if body.budget not in BUDGETS or body.biome not in BIOMES:
        return JSONResponse({"error": "pick a budget and a battlefield"}, status_code=400)
    if err := _limited(request, "create"):
        return err
    _sweep()
    code = next((c for c in ("".join(secrets.choice(_CODE_CHARS) for _ in range(CODE_LEN)) for _ in range(20))
                 if c not in _rooms), None)
    if code is None or len(_rooms) >= MAX_ROOMS:
        return JSONResponse({"error": "too many rooms open right now, try again shortly"}, status_code=503)
    seat = _new_seat(uid, name)
    _rooms[code] = {"code": code, "created": time.time(), "touched": time.time(), "lock": asyncio.Lock(), "budget": body.budget,
                    "biome": body.biome, "seats": [seat, None], "armies": [None, None], "seed": None, "result": None,
                    "round": 1, "wins": [0, 0], "last": None, "rematch": [False, False], "left": None}
    return {"code": code, "key": seat["key"]}


@router.get("/duel/{code}/peek")
async def duel_peek(request: Request, code: str):
    if err := _limited(request, "join"):
        return err
    r = _rooms.get(code.upper())
    if r is None or _phase(r) == "left":
        return JSONResponse({"error": "that room has closed"}, status_code=404)
    return {"code": r["code"], "host": r["seats"][0]["name"], "open": r["seats"][1] is None, "budget": r["budget"], "biome": r["biome"]}


@router.post("/duel/{code}/join")
async def duel_join(request: Request, code: str, body: DuelBody):
    if err := _limited(request, "join"):
        return err
    r = _rooms.get(code.upper())
    if r is None:
        return JSONResponse({"error": "no such room, check the code"}, status_code=404)
    uid, name = _seat_name(request, body.name)
    async with r["lock"]:
        if uid and any(st and st["uid"] == uid for st in r["seats"]):
            return {"code": r["code"], "key": next(st["key"] for st in r["seats"] if st and st["uid"] == uid)}
        if not name:
            return JSONResponse({"error": "pick a name"}, status_code=400)
        if r["seats"][1] is not None:
            return JSONResponse({"error": "that room is full"}, status_code=409)
        r["seats"][1] = _new_seat(uid, name)
        r["touched"] = time.time()
        return {"code": r["code"], "key": r["seats"][1]["key"]}


@router.get("/duel/{code}")
async def duel_state(code: str, x_duel_key: DuelKey = None):
    r, seat, err = _room(code, x_duel_key)
    return err or _view(r, seat)


@router.post("/duel/{code}/lock")
async def duel_lock(code: str, body: DuelBody, x_duel_key: DuelKey = None):
    r, seat, err = _room(code, x_duel_key)
    if err:
        return err
    if not body.squads or len(body.squads) > MAX_SQUADS:
        return JSONResponse({"error": f"an army is 1 to {MAX_SQUADS} squads"}, status_code=400)
    async with r["lock"]:
        if _phase(r) != "build":
            return JSONResponse({"error": "armies can't be changed right now"}, status_code=409)
        try:
            checked = await _engine({"op": "validate", "squads": body.squads, "side": seat, "budget": r["budget"]})
        except EngineDown:
            log.exception("tallgrass duel %s: validate failed", r["code"])
            return JSONResponse({"error": "the sim engine is down, try again shortly"}, status_code=503)
        if "error" in checked:
            return JSONResponse({"error": checked["error"]}, status_code=400)
        r["armies"][seat] = checked["squads"]
        if None not in r["armies"]:
            seed = secrets.randbits(32)     # drawn only now, after both armies are fixed
            try:
                r["result"] = await _engine({"op": "war", "squads": r["armies"][0] + r["armies"][1], "seed": seed})
            except EngineDown:
                log.exception("tallgrass duel %s: war failed", r["code"])
                r["armies"][seat] = None
                return JSONResponse({"error": "the sim engine is down, lock in again shortly"}, status_code=503)
            r["seed"] = seed
            if r["result"]["winner"] is not None:
                r["wins"][r["result"]["winner"]] += 1
        return _view(r, seat)


@router.post("/duel/{code}/unlock")
async def duel_unlock(code: str, x_duel_key: DuelKey = None):
    r, seat, err = _room(code, x_duel_key)
    if err:
        return err
    async with r["lock"]:
        if _phase(r) != "build":
            return JSONResponse({"error": "too late, the war has started"}, status_code=409)
        r["armies"][seat] = None
        return _view(r, seat)


@router.post("/duel/{code}/rematch")
async def duel_rematch(code: str, x_duel_key: DuelKey = None):
    r, seat, err = _room(code, x_duel_key)
    if err:
        return err
    async with r["lock"]:
        if _phase(r) != "war":
            return JSONResponse({"error": "the war isn't over"}, status_code=409)
        r["rematch"][seat] = True
        if all(r["rematch"]):
            r.update(last=r["armies"], armies=[None, None], seed=None, result=None, rematch=[False, False], round=r["round"] + 1)
        return _view(r, seat)


@router.post("/duel/{code}/leave")
async def duel_leave(code: str, x_duel_key: DuelKey = None):
    r, seat, err = _room(code, x_duel_key)
    if err:
        return err
    async with r["lock"]:
        r["left"] = seat
        return _view(r, seat)
