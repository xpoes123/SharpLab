"""Keynesian Beauty Contest — web-native N-player game engine, WebSocket handler, API router.

The classic "guess 2/3 of the average" game. Every round each connected player
secretly submits a number in [0, 100]; the server computes the average and the
target = multiplier × average (default 2/3). The player(s) closest to the target
win the round (+1 point each). Scores accumulate across rounds.

Follows connect4.py's guest model exactly — rooms are created straight from the
browser (no Discord token, no coins), players share a 4-letter code, and rejoin by
name via the identify handshake (``_attach_player``). Unlike connect4 it is
N-player with a host who starts rounds, mirroring the bingo/minesweeper lobby.
"""

import asyncio
import logging
import random
import secrets
import string
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)

ROOM_TTL = 1800  # 30 minutes idle → drop
MAX_PLAYERS = 20
DEFAULT_MULTIPLIER = 2 / 3


# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class WebPlayer:
    name: str
    is_host: bool = False
    ws: WebSocket | None = None
    connected: bool = False
    score: int = 0
    guess: float | None = None  # this round's secret submission (None = not yet)


@dataclass
class BeautyRoom:
    room_id: str
    code: str
    multiplier: float = DEFAULT_MULTIPLIER
    players: list[WebPlayer] = field(default_factory=list)  # [0] = host
    phase: str = "lobby"  # "lobby" | "submitting" | "reveal"
    round_num: int = 0
    result: dict | None = None  # last round's reveal payload
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


rooms: dict[str, BeautyRoom] = {}      # room_id → room
codes: dict[str, str] = {}             # CODE → room_id


def _new_code() -> str:
    for _ in range(50):
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in codes:
            return code
    return "".join(random.choices(string.ascii_uppercase, k=4))


# ── Request models ───────────────────────────────────────────────────────────


class CreateBody(BaseModel):
    name: str = "Player"
    multiplier: float | None = None


class JoinBody(BaseModel):
    name: str = "Player"


# ── API router ───────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/v1/beauty", tags=["beauty"])


@router.post("/rooms")
async def create_room(body: CreateBody):
    """Create a fresh room and register the caller as the host."""
    name = (body.name or "Player").strip()[:32] or "Player"
    mult = body.multiplier if body.multiplier is not None else DEFAULT_MULTIPLIER
    # Keep the multiplier sane; a beauty contest only makes sense in (0, 1].
    if not isinstance(mult, (int, float)) or not (0 < mult <= 1):
        mult = DEFAULT_MULTIPLIER
    room_id = secrets.token_hex(4)
    code = _new_code()
    room = BeautyRoom(room_id=room_id, code=code, multiplier=float(mult))
    room.players.append(WebPlayer(name=name, is_host=True))
    rooms[room_id] = room
    codes[code] = room_id
    logger.info("beauty room created code=%s id=%s host=%s mult=%.4f", code, room_id, name, mult)
    return {"room_id": room_id, "code": code}


@router.post("/rooms/{code}/join")
async def join_room(code: str, body: JoinBody):
    """Join an existing room by its 4-letter code with just a name (guest)."""
    code = (code or "").strip().upper()
    room_id = codes.get(code)
    room = rooms.get(room_id) if room_id else None
    if not room:
        raise HTTPException(404, "Room not found")
    name = (body.name or "Player").strip()[:32] or "Player"

    # Allow rejoin by same name (someone reloading the tab).
    for p in room.players:
        if p.name == name:
            return {"room_id": room.room_id}

    if len(room.players) >= MAX_PLAYERS:
        raise HTTPException(409, "Room full")

    room.players.append(WebPlayer(name=name))
    room.last_activity = time.time()
    await _broadcast_state(room)
    return {"room_id": room.room_id}


# ── WebSocket handler ────────────────────────────────────────────────────────


async def beauty_websocket(websocket: WebSocket, room_id: str):
    room = rooms.get(room_id)
    if not room:
        await websocket.close(code=4004, reason="Room not found")
        return

    await websocket.accept()
    player: WebPlayer | None = None

    try:
        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                continue
            mtype = data.get("type")

            if mtype == "identify":
                name = str(data.get("name") or "").strip()[:32]
                player = _attach_player(room, name, websocket)
                if player is None:
                    await _send(websocket, {
                        "type": "error",
                        "message": "You're not in this room — join with the code first.",
                    })
                    continue
                room.last_activity = time.time()
                await _broadcast_state(room)

            elif mtype == "start_round":
                if player is not None:
                    await _handle_start_round(room, player)

            elif mtype == "submit":
                if player is not None:
                    await _handle_submit(room, player, data)

            elif mtype == "reveal":  # host force-reveal early
                if player is not None:
                    await _handle_force_reveal(room, player)

            elif mtype == "reset":  # host resets scores back to zero
                if player is not None:
                    await _handle_reset(room, player)

            elif mtype == "ping":
                await _send(websocket, {"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("beauty_websocket error in room %s", room_id)
    finally:
        if player is not None:
            player.connected = False
            player.ws = None
            try:
                await _broadcast_state(room)
                # A mid-round disconnect shouldn't stall the round: if everyone
                # still connected has already submitted, reveal now.
                await _maybe_reveal(room)
            except Exception:
                pass


def _attach_player(room: BeautyRoom, name: str, ws: WebSocket) -> WebPlayer | None:
    """Match an identify by name to a registered player and attach the socket.

    Returns None if the name isn't registered in this room (they must join via
    the REST endpoint first). Same-name identify reattaches — this is how rejoin
    after a disconnect/reload works.
    """
    if not name:
        return None
    for p in room.players:
        if p.name == name:
            if p.connected and p.ws is not None:
                return None  # seat is live — don't let a same-name socket hijack it
            p.ws = ws
            p.connected = True
            return p
    return None


# ── Game actions ─────────────────────────────────────────────────────────────


async def _handle_start_round(room: BeautyRoom, player: WebPlayer) -> None:
    if not player.is_host:
        await _send_error(player, "Only the host can start a round.")
        return
    if room.phase == "submitting":
        await _send_error(player, "A round is already in progress.")
        return
    connected = [p for p in room.players if p.connected]
    if len(connected) < 2:
        await _send_error(player, "Need at least 2 connected players.")
        return
    room.round_num += 1
    room.phase = "submitting"
    room.result = None
    for p in room.players:
        p.guess = None
    room.last_activity = time.time()
    await _broadcast_state(room)


async def _handle_submit(room: BeautyRoom, player: WebPlayer, data: dict) -> None:
    if room.phase != "submitting":
        return
    raw = data.get("guess")
    try:
        val = float(raw)
    except (TypeError, ValueError):
        await _send_error(player, "Enter a number between 0 and 100.")
        return
    if val != val or val in (float("inf"), float("-inf")):  # NaN / inf guard
        await _send_error(player, "Enter a number between 0 and 100.")
        return
    if val < 0 or val > 100:
        await _send_error(player, "Guess must be between 0 and 100.")
        return
    # One decimal place, per the rules.
    player.guess = round(val, 1)
    room.last_activity = time.time()
    await _broadcast_state(room)
    await _maybe_reveal(room)


async def _handle_force_reveal(room: BeautyRoom, player: WebPlayer) -> None:
    if not player.is_host:
        await _send_error(player, "Only the host can reveal.")
        return
    if room.phase != "submitting":
        return
    if not any(p.guess is not None for p in room.players):
        await _send_error(player, "No submissions yet.")
        return
    await _reveal(room)


async def _handle_reset(room: BeautyRoom, player: WebPlayer) -> None:
    if not player.is_host:
        await _send_error(player, "Only the host can reset the game.")
        return
    room.phase = "lobby"
    room.round_num = 0
    room.result = None
    for p in room.players:
        p.guess = None
        p.score = 0
    room.last_activity = time.time()
    await _broadcast_state(room)


async def _maybe_reveal(room: BeautyRoom) -> None:
    """Reveal once every *connected* player has submitted (disconnected players
    who never submitted don't block the round)."""
    if room.phase != "submitting":
        return
    connected = [p for p in room.players if p.connected]
    if not connected:
        return
    if all(p.guess is not None for p in connected):
        await _reveal(room)


async def _reveal(room: BeautyRoom) -> None:
    """Compute the average, target and winner(s), award points, broadcast."""
    submitted = [p for p in room.players if p.guess is not None]
    if not submitted:
        return
    guesses = [p.guess for p in submitted]
    average = sum(guesses) / len(guesses)
    target = room.multiplier * average
    closest = min(abs(p.guess - target) for p in submitted)
    winners = [p for p in submitted if abs(p.guess - target) == closest]
    for p in winners:
        p.score += 1

    room.result = {
        "round_num": room.round_num,
        "multiplier": round(room.multiplier, 4),
        "average": round(average, 2),
        "target": round(target, 2),
        "guesses": [
            {"name": p.name, "guess": p.guess, "won": p in winners}
            for p in sorted(submitted, key=lambda x: abs(x.guess - target))
        ],
        "winners": [p.name for p in winners],
    }
    room.phase = "reveal"
    room.last_activity = time.time()
    await _broadcast_state(room)


# ── Broadcast helpers ────────────────────────────────────────────────────────


async def _send(ws: WebSocket | None, msg: dict) -> None:
    if ws and ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _send_error(player: WebPlayer, message: str) -> None:
    await _send(player.ws, {"type": "error", "message": message})


def _state_msg(room: BeautyRoom, you: str | None) -> dict:
    return {
        "type": "state",
        "code": room.code,
        "phase": room.phase,
        "round_num": room.round_num,
        "multiplier": round(room.multiplier, 4),
        "you": you,
        "players": [
            {
                "name": p.name,
                "is_host": p.is_host,
                "connected": p.connected,
                "score": p.score,
                "submitted": p.guess is not None,
            }
            for p in room.players
        ],
        "result": room.result,
    }


async def _broadcast_state(room: BeautyRoom) -> None:
    for p in room.players:
        await _send(p.ws, _state_msg(room, you=p.name))


# ── Stale room cleanup ───────────────────────────────────────────────────────


async def cleanup_stale_beauty_rooms() -> None:
    """Background task: drop rooms that have sat idle past the TTL."""
    while True:
        await asyncio.sleep(60)
        try:
            now = time.time()
            stale = [
                rid for rid, room in rooms.items()
                if (now - room.last_activity) > ROOM_TTL
            ]
            for rid in stale:
                room = rooms.pop(rid, None)
                if room:
                    codes.pop(room.code, None)
                    for p in room.players:
                        await _send(p.ws, {
                            "type": "error",
                            "message": "Room expired after inactivity.",
                        })
        except Exception:
            logger.exception("cleanup_stale_beauty_rooms error — loop continues")
