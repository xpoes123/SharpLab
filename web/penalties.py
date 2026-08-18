"""Penalty Shootout — web-native 2-player game engine, WebSocket handler, API router.

Mirrors the RPS WS-game pattern (in-memory rooms + codes maps, WebPlayer
dataclass, _send/_broadcast helpers, identify/rematch, disconnect handling, and
a stale-room cleanup coroutine), but rooms are created straight from the browser
— no Discord token needed. This is a free game (no coins).

Like RPS, each kick is SIMULTANEOUS and secret, but the two players hold
DIFFERENT roles that alternate every kick. On kick k the shooter is slot A when
k is odd and slot B when k is even; the other player keeps. The shooter picks a
direction to shoot and the keeper picks a direction to dive, both hidden until
both are in. Same direction → SAVE, otherwise → GOAL.

Best-of-5: each player takes 5 kicks (10 kicks total). Whoever has more goals
after both have taken 5 wins; if tied, sudden death — one kick each until
someone leads after an equal number of kicks.
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
REGULATION_KICKS = 10  # 5 kicks each before sudden death

DIRS = ("left", "center", "right")


# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class WebPlayer:
    name: str
    slot: str  # "A" (host) or "B"
    ws: WebSocket | None = None
    connected: bool = False
    goals: int = 0


@dataclass
class PenaltiesRoom:
    room_id: str
    code: str
    players: list[WebPlayer] = field(default_factory=list)  # [0]=A (host), [1]=B
    kick: int = 1                       # current kick number (1-indexed)
    shot_dir: str | None = None         # shooter's pick for this kick — private
    dive_dir: str | None = None         # keeper's pick for this kick — private
    last: dict | None = None            # last resolved kick {shooter_dir,keeper_dir,goal}
    history: list[dict] = field(default_factory=list)  # every resolved kick
    match_winner: str | None = None     # None | "A" | "B"
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


rooms: dict[str, PenaltiesRoom] = {}   # room_id → room
codes: dict[str, str] = {}             # CODE → room_id


def _new_code() -> str:
    for _ in range(50):
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in codes:
            return code
    # Astronomically unlikely fallback.
    return "".join(random.choices(string.ascii_uppercase, k=4))


def _shooter_slot(room: PenaltiesRoom) -> str:
    """Slot taking the shot on the current kick — A on odd kicks, B on even."""
    return "A" if room.kick % 2 == 1 else "B"


# ── Request models ───────────────────────────────────────────────────────────


class JoinBody(BaseModel):
    name: str = "Player"


# ── API router ───────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/v1/penalties", tags=["penalties"])


@router.post("/rooms")
async def create_room(body: JoinBody):
    """Create a fresh room and register the caller as slot A (the host)."""
    name = (body.name or "Player").strip()[:32] or "Player"
    room_id = secrets.token_hex(4)
    code = _new_code()
    room = PenaltiesRoom(room_id=room_id, code=code)
    room.players.append(WebPlayer(name=name, slot="A"))
    rooms[room_id] = room
    codes[code] = room_id
    logger.info("penalties room created code=%s id=%s host=%s", code, room_id, name)
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


async def penalties_websocket(websocket: WebSocket, room_id: str):
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

            elif mtype == "shoot":
                if player is not None:
                    await _handle_action(room, player, data, role="shoot")

            elif mtype == "dive":
                if player is not None:
                    await _handle_action(room, player, data, role="keep")

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
        logger.exception("penalties_websocket error in room %s", room_id)
    finally:
        if player is not None:
            player.connected = False
            player.ws = None
            try:
                await _broadcast_state(room)
            except Exception:
                pass


def _attach_player(room: PenaltiesRoom, name: str, ws: WebSocket) -> WebPlayer | None:
    """Match an identify by name to a registered player and attach the socket.

    Returns None if the name isn't registered in this room (they must join via
    the REST endpoint first). Same-name identify reattaches — this is how rejoin
    after a disconnect/reload works.
    """
    if not name:
        return None
    for p in room.players:
        if p.name == name:
            p.ws = ws
            p.connected = True
            return p
    return None


async def _handle_action(room: PenaltiesRoom, player: WebPlayer, data: dict, role: str) -> None:
    """Handle a shoot or dive submission for the current kick.

    `role` is the role this message type implies ("shoot"/"keep"). We only accept
    it if the player actually holds that role on the current kick and hasn't
    already submitted. Bad directions and out-of-turn actions are ignored.
    """
    if room.match_winner is not None:
        return  # match's over — wait for a rematch
    if len(room.players) < 2:
        await _send_error(player, "Waiting for an opponent to join.")
        return

    dir_ = data.get("dir")
    if dir_ not in DIRS:
        return  # bad input — ignore

    shooter_slot = _shooter_slot(room)
    my_role = "shoot" if player.slot == shooter_slot else "keep"
    if my_role != role:
        return  # acting outside your role for this kick — ignore

    if role == "shoot":
        if room.shot_dir is not None:
            return  # already shot this kick
        room.shot_dir = dir_
    else:
        if room.dive_dir is not None:
            return  # already dove this kick
        room.dive_dir = dir_

    if room.shot_dir is not None and room.dive_dir is not None:
        _resolve_kick(room)

    room.last_activity = time.time()
    await _broadcast_state(room)


def _resolve_kick(room: PenaltiesRoom) -> None:
    shooter_slot = _shooter_slot(room)
    shooter = next(p for p in room.players if p.slot == shooter_slot)
    goal = room.dive_dir != room.shot_dir  # keeper guessed same dir → save
    if goal:
        shooter.goals += 1

    room.last = {"shooter_dir": room.shot_dir, "keeper_dir": room.dive_dir, "goal": goal}
    room.history.append({
        "kick": room.kick,
        "shooter": shooter_slot,
        "shot": room.shot_dir,
        "dive": room.dive_dir,
        "goal": goal,
    })

    # Advance to the next kick.
    room.kick += 1
    room.shot_dir = None
    room.dive_dir = None

    # Decide the match after both players have taken an equal (and ≥5) number of
    # kicks. Completed = kicks resolved so far = room.kick - 1.
    completed = room.kick - 1
    if completed >= REGULATION_KICKS and completed % 2 == 0:
        a, b = room.players[0], room.players[1]
        if a.goals != b.goals:
            room.match_winner = a.slot if a.goals > b.goals else b.slot


def _reset_match(room: PenaltiesRoom) -> None:
    room.kick = 1
    room.shot_dir = None
    room.dive_dir = None
    room.last = None
    room.history = []
    room.match_winner = None
    for p in room.players:
        p.goals = 0


# ── Broadcast helpers ────────────────────────────────────────────────────────


async def _send(ws: WebSocket | None, msg: dict) -> None:
    if ws and ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _send_error(player: WebPlayer, message: str) -> None:
    await _send(player.ws, {"type": "error", "message": message})


def _awaiting_for(room: PenaltiesRoom, me: WebPlayer) -> str:
    """Who still needs to act on the current kick, from `me`'s perspective."""
    shooter_slot = _shooter_slot(room)
    i_shoot = me.slot == shooter_slot
    my_in = room.shot_dir is not None if i_shoot else room.dive_dir is not None
    opp_in = room.dive_dir is not None if i_shoot else room.shot_dir is not None
    if not my_in and not opp_in:
        return "both"
    if not my_in:
        return "you"
    if not opp_in:
        return "opp"
    return "both"  # shouldn't happen — both in resolves instantly


def _history_for(room: PenaltiesRoom, me: WebPlayer) -> list[dict]:
    """History with shooter tagged relative to this viewer."""
    out = []
    for h in room.history:
        out.append({
            "kick": h["kick"],
            "shooter": "you" if h["shooter"] == me.slot else "opp",
            "shot": h["shot"],
            "dive": h["dive"],
            "goal": h["goal"],
        })
    return out


def _state_msg(room: PenaltiesRoom, me: WebPlayer) -> dict:
    opp = next((p for p in room.players if p.slot != me.slot), None)
    shooter_slot = _shooter_slot(room)
    match_winner = None
    if room.match_winner is not None:
        match_winner = "you" if room.match_winner == me.slot else "opp"
    return {
        "type": "state",
        "code": room.code,
        "kick": room.kick,
        "your_role": "shoot" if me.slot == shooter_slot else "keep",
        "scores": {"me": me.goals, "opp": (opp.goals if opp else 0)},
        "last": room.last,
        "awaiting": _awaiting_for(room, me),
        "history": _history_for(room, me),
        "match_winner": match_winner,
        "players": [
            {"name": p.name, "slot": p.slot, "goals": p.goals, "connected": p.connected}
            for p in room.players
        ],
        "you": me.slot,
    }


async def _broadcast_state(room: PenaltiesRoom) -> None:
    for p in room.players:
        await _send(p.ws, _state_msg(room, me=p))


# ── Stale room cleanup ───────────────────────────────────────────────────────


async def cleanup_stale_penalties_rooms() -> None:
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
            logger.exception("cleanup_stale_penalties_rooms error — loop continues")
