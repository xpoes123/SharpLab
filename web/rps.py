"""Rock-Paper-Scissors — web-native 2-player game engine, WebSocket handler, API router.

Mirrors the tic-tac-toe WS-game pattern (in-memory rooms, WebPlayer dataclass,
_send/_broadcast helpers, a WS handler, and a stale-room cleanup coroutine), but
rooms are created straight from the browser — no Discord token needed. This is a
free game (no coins).

Unlike tic-tac-toe, RPS is SIMULTANEOUS, not turn-based: each round both players
throw at the same time. A player's choice is kept private until *both* have thrown,
then the round resolves and the winner's score ticks up. Best-of-5 — first to 3
round wins takes the match.
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
WIN_TARGET = 3   # best-of-5 → first to 3 round wins

CHOICES = ("rock", "paper", "scissors")
# choice → what it beats
BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class WebPlayer:
    name: str
    slot: str  # "A" (host) or "B"
    ws: WebSocket | None = None
    connected: bool = False
    choice: str | None = None  # this round's throw — private until both are in
    score: int = 0


@dataclass
class RPSRoom:
    room_id: str
    code: str
    players: list[WebPlayer] = field(default_factory=list)  # [0]=A (host), [1]=B
    round: int = 1
    revealed: bool = False          # both thrown & round resolved (choices shown)
    last_winner: str | None = None  # slot of last round's winner, "tie", or None
    match_winner: str | None = None  # None | "A" | "B"
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


rooms: dict[str, RPSRoom] = {}   # room_id → room
codes: dict[str, str] = {}       # CODE → room_id


def _new_code() -> str:
    for _ in range(50):
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in codes:
            return code
    # Astronomically unlikely fallback.
    return "".join(random.choices(string.ascii_uppercase, k=4))


# ── Request models ───────────────────────────────────────────────────────────


class JoinBody(BaseModel):
    name: str = "Player"


# ── API router ───────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/v1/rps", tags=["rps"])


@router.post("/rooms")
async def create_room(body: JoinBody):
    """Create a fresh room and register the caller as slot A (the host)."""
    name = (body.name or "Player").strip()[:32] or "Player"
    room_id = secrets.token_hex(4)
    code = _new_code()
    room = RPSRoom(room_id=room_id, code=code)
    room.players.append(WebPlayer(name=name, slot="A"))
    rooms[room_id] = room
    codes[code] = room_id
    logger.info("rps room created code=%s id=%s host=%s", code, room_id, name)
    return {"room_id": room_id, "code": code}


@router.post("/rooms/{code}/join")
async def join_room(code: str, body: JoinBody):
    """Join an existing room by its 4-letter code as slot B (the second player)."""
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

    if len(room.players) >= 2:
        raise HTTPException(409, "Room full")

    slot = "A" if not room.players else "B"
    room.players.append(WebPlayer(name=name, slot=slot))
    room.last_activity = time.time()
    await _broadcast_state(room)
    return {"room_id": room.room_id}


# ── WebSocket handler ────────────────────────────────────────────────────────


async def rps_websocket(websocket: WebSocket, room_id: str):
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

            elif mtype == "throw":
                if player is not None:
                    await _handle_throw(room, player, data)

            elif mtype == "rematch":
                if player is not None:
                    _reset_match(room)
                    room.last_activity = time.time()
                    await _broadcast_state(room)

            elif mtype == "ping":
                await _send(websocket, {"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("rps_websocket error in room %s", room_id)
    finally:
        if player is not None:
            player.connected = False
            player.ws = None
            try:
                await _broadcast_state(room)
            except Exception:
                pass


def _attach_player(room: RPSRoom, name: str, ws: WebSocket) -> WebPlayer | None:
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


async def _handle_throw(room: RPSRoom, player: WebPlayer, data: dict) -> None:
    if room.match_winner is not None:
        return  # match's over — wait for a rematch
    if len(room.players) < 2:
        await _send_error(player, "Waiting for an opponent to join.")
        return

    choice = data.get("choice")
    if choice not in CHOICES:
        return  # bad input — ignore

    # A throw arriving while last round is still revealed starts a fresh round.
    if room.revealed:
        _start_round(room)

    if player.choice is not None:
        return  # already thrown this round — ignore double-throws

    player.choice = choice

    if all(p.choice is not None for p in room.players):
        _resolve_round(room)

    room.last_activity = time.time()
    await _broadcast_state(room)


def _start_round(room: RPSRoom) -> None:
    room.round += 1
    room.revealed = False
    room.last_winner = None
    for p in room.players:
        p.choice = None


def _resolve_round(room: RPSRoom) -> None:
    a, b = room.players[0], room.players[1]
    if a.choice == b.choice:
        room.last_winner = "tie"
    elif BEATS[a.choice] == b.choice:
        room.last_winner = a.slot
        a.score += 1
    else:
        room.last_winner = b.slot
        b.score += 1
    room.revealed = True
    if a.score >= WIN_TARGET:
        room.match_winner = a.slot
    elif b.score >= WIN_TARGET:
        room.match_winner = b.slot


def _reset_match(room: RPSRoom) -> None:
    room.round = 1
    room.revealed = False
    room.last_winner = None
    room.match_winner = None
    for p in room.players:
        p.choice = None
        p.score = 0


# ── Broadcast helpers ────────────────────────────────────────────────────────


async def _send(ws: WebSocket | None, msg: dict) -> None:
    if ws and ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _send_error(player: WebPlayer, message: str) -> None:
    await _send(player.ws, {"type": "error", "message": message})


def _result_for(room: RPSRoom, me: WebPlayer) -> str | None:
    """Translate the round outcome into this viewer's perspective."""
    if not room.revealed or room.last_winner is None:
        return None
    if room.last_winner == "tie":
        return "tie"
    return "win" if room.last_winner == me.slot else "lose"


def _state_msg(room: RPSRoom, me: WebPlayer) -> dict:
    opp = next((p for p in room.players if p.slot != me.slot), None)
    match_winner = None
    if room.match_winner is not None:
        match_winner = "you" if room.match_winner == me.slot else "opp"
    return {
        "type": "state",
        "code": room.code,
        "round": room.round,
        "revealed": room.revealed,
        "scores": {"me": me.score, "opp": (opp.score if opp else 0)},
        "your_choice": me.choice,
        # Opponent's choice stays hidden until both have thrown & resolved.
        "opp_choice": (opp.choice if (opp and room.revealed) else None),
        "opp_thrown": bool(opp and opp.choice is not None),
        "last_result": _result_for(room, me),
        "match_winner": match_winner,
        "players": [
            {"name": p.name, "slot": p.slot, "score": p.score, "connected": p.connected}
            for p in room.players
        ],
        "you": me.slot,
    }


async def _broadcast_state(room: RPSRoom) -> None:
    for p in room.players:
        await _send(p.ws, _state_msg(room, me=p))


# ── Stale room cleanup ───────────────────────────────────────────────────────


async def cleanup_stale_rps_rooms() -> None:
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
            logger.exception("cleanup_stale_rps_rooms error — loop continues")
