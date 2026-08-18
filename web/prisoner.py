"""Iterated Prisoner's Dilemma — web-native 2-player game engine, WebSocket handler, API router.

Mirrors the RPS/tic-tac-toe WS-game pattern (in-memory rooms, WebPlayer dataclass,
_send/_broadcast helpers, a WS handler, and a stale-room cleanup coroutine), but
rooms are created straight from the browser — no Discord token needed. This is a
free game (no coins).

Like RPS this is SIMULTANEOUS, not turn-based: each round both players secretly
pick Cooperate or Defect at the same time. A player's move is kept private until
*both* are in, then the round scores with the classic payoff matrix and each
player's running total ticks up. It's *iterated* — 10 rounds — and after round 10
the higher total wins (ties are possible).

Payoff (row = you, col = opponent):
  both Cooperate → +3 / +3
  both Defect    → +1 / +1
  you Cooperate, they Defect → +0 (you) / +5 (them)
  you Defect, they Cooperate → +5 (you) / +0 (them)
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
ROUNDS = 10      # iterated over 10 rounds; highest total after round 10 wins

MOVES = ("cooperate", "defect")

# (my move, opponent move) → (my points, opponent points)
PAYOFF = {
    ("cooperate", "cooperate"): (3, 3),
    ("defect", "defect"): (1, 1),
    ("cooperate", "defect"): (0, 5),
    ("defect", "cooperate"): (5, 0),
}


# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class WebPlayer:
    name: str
    slot: str  # "A" (host) or "B"
    ws: WebSocket | None = None
    connected: bool = False
    move: str | None = None  # this round's pick — private until both are in
    total: int = 0


@dataclass
class PrisonerRoom:
    room_id: str
    code: str
    players: list[WebPlayer] = field(default_factory=list)  # [0]=A (host), [1]=B
    round: int = 1
    revealed: bool = False           # both picked & round resolved (moves shown)
    # history[i] = {"A": move, "B": move, "gain_A": int, "gain_B": int}
    history: list = field(default_factory=list)
    match_winner: str | None = None  # None | "A" | "B" | "tie"
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


rooms: dict[str, PrisonerRoom] = {}   # room_id → room
codes: dict[str, str] = {}            # CODE → room_id


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

router = APIRouter(prefix="/api/v1/prisoner", tags=["prisoner"])


@router.post("/rooms")
async def create_room(body: JoinBody):
    """Create a fresh room and register the caller as slot A (the host)."""
    name = (body.name or "Player").strip()[:32] or "Player"
    room_id = secrets.token_hex(4)
    code = _new_code()
    room = PrisonerRoom(room_id=room_id, code=code)
    room.players.append(WebPlayer(name=name, slot="A"))
    rooms[room_id] = room
    codes[code] = room_id
    logger.info("prisoner room created code=%s id=%s host=%s", code, room_id, name)
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


async def prisoner_websocket(websocket: WebSocket, room_id: str):
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

            elif mtype == "choice":
                if player is not None:
                    await _handle_choice(room, player, data)

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
        logger.exception("prisoner_websocket error in room %s", room_id)
    finally:
        if player is not None:
            player.connected = False
            player.ws = None
            try:
                await _broadcast_state(room)
            except Exception:
                pass


def _attach_player(room: PrisonerRoom, name: str, ws: WebSocket) -> WebPlayer | None:
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


async def _handle_choice(room: PrisonerRoom, player: WebPlayer, data: dict) -> None:
    if room.match_winner is not None:
        return  # match's over — wait for a rematch
    if len(room.players) < 2:
        await _send_error(player, "Waiting for an opponent to join.")
        return

    move = data.get("move")
    if move not in MOVES:
        return  # bad input — ignore

    # A choice arriving while last round is still revealed starts a fresh round.
    if room.revealed:
        _start_round(room)

    if player.move is not None:
        return  # already picked this round — ignore double-choices

    player.move = move

    if all(p.move is not None for p in room.players):
        _resolve_round(room)

    room.last_activity = time.time()
    await _broadcast_state(room)


def _start_round(room: PrisonerRoom) -> None:
    room.round += 1
    room.revealed = False
    for p in room.players:
        p.move = None


def _resolve_round(room: PrisonerRoom) -> None:
    a, b = room.players[0], room.players[1]
    gain_a, gain_b = PAYOFF[(a.move, b.move)]
    a.total += gain_a
    b.total += gain_b
    room.history.append({
        "A": a.move, "B": b.move, "gain_A": gain_a, "gain_B": gain_b,
    })
    room.revealed = True
    if room.round >= ROUNDS:
        if a.total > b.total:
            room.match_winner = a.slot
        elif b.total > a.total:
            room.match_winner = b.slot
        else:
            room.match_winner = "tie"


def _reset_match(room: PrisonerRoom) -> None:
    room.round = 1
    room.revealed = False
    room.match_winner = None
    room.history = []
    for p in room.players:
        p.move = None
        p.total = 0


# ── Broadcast helpers ────────────────────────────────────────────────────────


async def _send(ws: WebSocket | None, msg: dict) -> None:
    if ws and ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _send_error(player: WebPlayer, message: str) -> None:
    await _send(player.ws, {"type": "error", "message": message})


def _history_for(room: PrisonerRoom, me: WebPlayer, opp: WebPlayer | None) -> list:
    """Translate slot-keyed history into this viewer's me/opp perspective."""
    ms, os_ = me.slot, (opp.slot if opp else ("B" if me.slot == "A" else "A"))
    out = []
    for h in room.history:
        out.append({
            "me": h[ms],
            "opp": h[os_],
            "gain_me": h["gain_" + ms],
            "gain_opp": h["gain_" + os_],
        })
    return out


def _state_msg(room: PrisonerRoom, me: WebPlayer) -> dict:
    opp = next((p for p in room.players if p.slot != me.slot), None)
    match_winner = None
    if room.match_winner is not None:
        match_winner = (
            "tie" if room.match_winner == "tie"
            else ("you" if room.match_winner == me.slot else "opp")
        )
    hist = _history_for(room, me, opp)
    last = hist[-1] if hist else {"me": None, "opp": None}
    return {
        "type": "state",
        "code": room.code,
        "round": room.round,
        "rounds": ROUNDS,
        "revealed": room.revealed,
        "totals": {"me": me.total, "opp": (opp.total if opp else 0)},
        "your_move": me.move,
        # Opponent's move stays hidden until both have picked & the round resolves.
        "opp_move": (opp.move if (opp and room.revealed) else None),
        "opp_chosen": bool(opp and opp.move is not None),
        "last": {"me": last["me"], "opp": last["opp"]},
        "history": hist,
        "match_winner": match_winner,
        "players": [
            {"name": p.name, "slot": p.slot, "total": p.total, "connected": p.connected}
            for p in room.players
        ],
        "you": me.slot,
    }


async def _broadcast_state(room: PrisonerRoom) -> None:
    for p in room.players:
        await _send(p.ws, _state_msg(room, me=p))


# ── Stale room cleanup ───────────────────────────────────────────────────────


async def cleanup_stale_prisoner_rooms() -> None:
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
            logger.exception("cleanup_stale_prisoner_rooms error — loop continues")
