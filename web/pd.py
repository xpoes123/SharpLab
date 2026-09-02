"""Iterated Prisoner's Dilemma — web-native 2-player game engine, WebSocket handler, API router.

Mirrors rps.py exactly (in-memory rooms + codes maps, WebPlayer dataclass,
_send/_broadcast helpers, the identify/submit protocol, disconnect handling, and
a stale-room cleanup coroutine). Rooms are created straight from the browser — no
Discord token, no coins. Guests join by a 4-letter code and rejoin by name.

Like RPS this is SIMULTANEOUS, not turn-based: each round both players secretly
choose Cooperate or Defect; the choice stays hidden until *both* are in, then the
round reveals and the payoff matrix is applied to cumulative scores. Unlike RPS it
is *iterated* and open-ended — a full move history for both players is kept
visible so players can read patterns, and the host ends the match when they like
(higher cumulative score wins).

Payoff matrix (my_move, their_move) → (my_points, their_points):
    C,C → 3/3   ·   D,C → 5/0   ·   C,D → 0/5   ·   D,D → 1/1
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

CHOICES = ("C", "D")  # Cooperate / Defect

# (my_move, their_move) → (my_points, their_points)
PAYOFF: dict[tuple[str, str], tuple[int, int]] = {
    ("C", "C"): (3, 3),
    ("C", "D"): (0, 5),
    ("D", "C"): (5, 0),
    ("D", "D"): (1, 1),
}


# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class WebPlayer:
    name: str
    slot: str  # "A" (host) or "B"
    ws: WebSocket | None = None
    connected: bool = False
    choice: str | None = None       # this round's move — private until both are in
    score: int = 0                  # cumulative points across rounds
    history: list[str] = field(default_factory=list)  # past moves, "C"/"D"


@dataclass
class PDRoom:
    room_id: str
    code: str
    players: list[WebPlayer] = field(default_factory=list)  # [0]=A (host), [1]=B
    round: int = 1
    revealed: bool = False           # both chosen & round resolved (moves shown)
    last_payoff: dict | None = None  # {"A": pts, "B": pts} for the revealed round
    match_over: bool = False         # host ended the match
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


rooms: dict[str, PDRoom] = {}    # room_id → room
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

router = APIRouter(prefix="/api/v1/pd", tags=["pd"])


@router.post("/rooms")
async def create_room(body: JoinBody):
    """Create a fresh room and register the caller as slot A (the host)."""
    name = (body.name or "Player").strip()[:32] or "Player"
    room_id = secrets.token_hex(4)
    code = _new_code()
    room = PDRoom(room_id=room_id, code=code)
    room.players.append(WebPlayer(name=name, slot="A"))
    rooms[room_id] = room
    codes[code] = room_id
    logger.info("pd room created code=%s id=%s host=%s", code, room_id, name)
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


async def pd_websocket(websocket: WebSocket, room_id: str):
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

            elif mtype == "submit":
                if player is not None:
                    await _handle_submit(room, player, data)

            elif mtype == "next":  # advance to the next round after a reveal
                if player is not None:
                    await _handle_next(room, player)

            elif mtype == "end_match":  # host ends the match; higher cumulative wins
                if player is not None:
                    await _handle_end_match(room, player)

            elif mtype == "reset":  # host restarts scores/history from scratch
                if player is not None:
                    await _handle_reset(room, player)

            elif mtype == "ping":
                await _send(websocket, {"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("pd_websocket error in room %s", room_id)
    finally:
        if player is not None:
            player.connected = False
            player.ws = None
            try:
                await _broadcast_state(room)
            except Exception:
                pass


def _attach_player(room: PDRoom, name: str, ws: WebSocket) -> WebPlayer | None:
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


async def _handle_submit(room: PDRoom, player: WebPlayer, data: dict) -> None:
    if room.match_over:
        return  # match's over — wait for a reset
    if len(room.players) < 2:
        await _send_error(player, "Waiting for an opponent to join.")
        return
    if sum(1 for p in room.players if p.connected) < 2:
        await _send_error(player, "Waiting for your opponent to reconnect.")
        return
    if room.revealed:
        return  # this round is already resolved — click Next round first

    choice = data.get("choice")
    if choice not in CHOICES:
        return  # bad input — ignore

    if player.choice is not None:
        return  # already chosen this round — ignore double-submits

    player.choice = choice

    if all(p.choice is not None for p in room.players):
        _resolve_round(room)

    room.last_activity = time.time()
    await _broadcast_state(room)


def _resolve_round(room: PDRoom) -> None:
    """Both players have chosen — apply the payoff matrix and record history."""
    a, b = room.players[0], room.players[1]
    a_pts, b_pts = PAYOFF[(a.choice, b.choice)]
    a.score += a_pts
    b.score += b_pts
    a.history.append(a.choice)
    b.history.append(b.choice)
    room.last_payoff = {"A": a_pts, "B": b_pts}
    room.revealed = True


async def _handle_next(room: PDRoom, player: WebPlayer) -> None:
    """Either player can advance to the next round once the current one revealed."""
    if room.match_over or not room.revealed:
        return
    room.round += 1
    room.revealed = False
    room.last_payoff = None
    for p in room.players:
        p.choice = None
    room.last_activity = time.time()
    await _broadcast_state(room)


async def _handle_end_match(room: PDRoom, player: WebPlayer) -> None:
    if player.slot != "A":
        await _send_error(player, "Only the host can end the match.")
        return
    room.match_over = True
    room.last_activity = time.time()
    await _broadcast_state(room)


async def _handle_reset(room: PDRoom, player: WebPlayer) -> None:
    if player.slot != "A":
        await _send_error(player, "Only the host can reset the game.")
        return
    room.round = 1
    room.revealed = False
    room.last_payoff = None
    room.match_over = False
    for p in room.players:
        p.choice = None
        p.score = 0
        p.history = []
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


def _state_msg(room: PDRoom, me: WebPlayer) -> dict:
    opp = next((p for p in room.players if p.slot != me.slot), None)
    winner = None
    if room.match_over:
        if opp is None or me.score > opp.score:
            winner = "you"
        elif me.score < opp.score:
            winner = "opp"
        else:
            winner = "tie"
    payoff = None
    if room.revealed and room.last_payoff is not None:
        payoff = {"me": room.last_payoff[me.slot],
                  "opp": (room.last_payoff[opp.slot] if opp else 0)}
    return {
        "type": "state",
        "code": room.code,
        "round": room.round,
        "revealed": room.revealed,
        "match_over": room.match_over,
        "you": me.slot,
        "is_host": me.slot == "A",
        "your_choice": me.choice,
        # Opponent's move stays hidden until both are in & the round resolved.
        "opp_choice": (opp.choice if (opp and room.revealed) else None),
        "you_submitted": me.choice is not None,
        "opp_submitted": bool(opp and opp.choice is not None),
        "scores": {"me": me.score, "opp": (opp.score if opp else 0)},
        "last_payoff": payoff,
        "history": {"me": list(me.history), "opp": (list(opp.history) if opp else [])},
        "winner": winner,
        "players": [
            {"name": p.name, "slot": p.slot, "score": p.score, "connected": p.connected}
            for p in room.players
        ],
    }


async def _broadcast_state(room: PDRoom) -> None:
    for p in room.players:
        await _send(p.ws, _state_msg(room, me=p))


# ── Stale room cleanup ───────────────────────────────────────────────────────


async def cleanup_stale_pd_rooms() -> None:
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
            logger.exception("cleanup_stale_pd_rooms error — loop continues")
