"""Liar's Dice — web-native 2-player game engine, WebSocket handler, API router.

Mirrors the Tic-Tac-Toe web-native pattern (in-memory rooms + codes maps, a
WebPlayer dataclass, _send/_broadcast helpers, a WS handler, identify/rematch,
disconnect handling, and a stale-room cleanup coroutine). Rooms are created
straight from the browser — no Discord token needed. Free game, no coins.

Rules (2-player, standard with 1s wild): each player starts with 5 dice. Each
round both players roll secretly. Players alternate bids {quantity, face} —
"there are at least `quantity` dice showing `face` across ALL dice", 1s wild.
Each bid must strictly beat the last (higher quantity, or same quantity + higher
face). Instead of bidding a player may challenge ("call liar") the standing bid:
reveal everything and count dice showing the bid face (wild 1s included). If the
actual count >= the bid quantity the bid was GOOD → the challenger loses a die;
otherwise the bidder loses a die. The loser starts the next round. A player at 0
dice loses the match.
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

ROOM_TTL = 1800          # 30 minutes idle → drop
START_DICE = 5           # dice each player starts with
REVEAL_SECONDS = 4.5     # how long the reveal beat lingers before the next round


# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class WebPlayer:
    name: str
    seat: int                       # 0 (host) or 1
    dice: list = field(default_factory=list)   # this player's secret roll
    ws: WebSocket | None = None
    connected: bool = False


@dataclass
class LiarsDiceRoom:
    room_id: str
    code: str
    players: list = field(default_factory=list)  # [0]=host, [1]=joiner
    phase: str = "lobby"            # lobby | bidding | reveal | over
    started: bool = False
    current_bid: dict | None = None  # {"quantity": int, "face": int}
    turn_seat: int = 0              # whose turn it is to act
    winner_seat: int | None = None
    reveal: dict | None = None      # snapshot after a challenge (see _do_challenge)
    reveal_gen: int = 0             # token so a stale reveal timer can't fire late
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


rooms: dict[str, LiarsDiceRoom] = {}   # room_id → room
codes: dict[str, str] = {}             # CODE → room_id


def _new_code() -> str:
    for _ in range(50):
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in codes:
            return code
    return "".join(random.choices(string.ascii_uppercase, k=4))


def _roll(n: int) -> list:
    return sorted(random.randint(1, 6) for _ in range(n))


def _total_dice(room: LiarsDiceRoom) -> int:
    return sum(len(p.dice) for p in room.players)


# ── Request models ───────────────────────────────────────────────────────────


class JoinBody(BaseModel):
    name: str = "Player"


# ── API router ───────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/v1/liarsdice", tags=["liarsdice"])


@router.post("/rooms")
async def create_room(body: JoinBody):
    """Create a fresh room and register the caller as the host (seat 0)."""
    name = (body.name or "Player").strip()[:32] or "Player"
    room_id = secrets.token_hex(4)
    code = _new_code()
    room = LiarsDiceRoom(room_id=room_id, code=code)
    room.players.append(WebPlayer(name=name, seat=0))
    rooms[room_id] = room
    codes[code] = room_id
    logger.info("liarsdice room created code=%s id=%s host=%s", code, room_id, name)
    return {"room_id": room_id, "code": code}


@router.post("/rooms/{code}/join")
async def join_room(code: str, body: JoinBody):
    """Join an existing room by its 4-letter code as the second player (seat 1)."""
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

    room.players.append(WebPlayer(name=name, seat=len(room.players)))
    room.last_activity = time.time()
    _maybe_start(room)
    await _broadcast_state(room)
    return {"room_id": room.room_id}


# ── WebSocket handler ────────────────────────────────────────────────────────


async def liarsdice_websocket(websocket: WebSocket, room_id: str):
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
                _maybe_start(room)
                await _broadcast_state(room)

            elif mtype == "bid":
                if player is not None:
                    await _handle_bid(room, player, data)

            elif mtype == "challenge":
                if player is not None:
                    await _handle_challenge(room, player)

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
        logger.exception("liarsdice_websocket error in room %s", room_id)
    finally:
        if player is not None:
            player.connected = False
            player.ws = None
            try:
                await _broadcast_state(room)
            except Exception:
                pass


def _attach_player(room: LiarsDiceRoom, name: str, ws: WebSocket) -> WebPlayer | None:
    """Match an identify by name to a registered player and attach the socket.

    Returns None if the name isn't registered (they must join via REST first).
    Same-name identify reattaches — this is how rejoin after a reload works.
    """
    if not name:
        return None
    for p in room.players:
        if p.name == name:
            p.ws = ws
            p.connected = True
            return p
    return None


# ── Game logic ───────────────────────────────────────────────────────────────


def _maybe_start(room: LiarsDiceRoom) -> None:
    """Once both seats are filled, deal the first round (host bids first)."""
    if room.started or len(room.players) < 2:
        return
    room.started = True
    _start_round(room, first_seat=0)


def _start_round(room: LiarsDiceRoom, first_seat: int) -> None:
    for p in room.players:
        p.dice = _roll(len(p.dice) if p.dice else START_DICE)
    room.phase = "bidding"
    room.current_bid = None
    room.reveal = None
    room.turn_seat = first_seat if room.players[first_seat].dice else _other(first_seat)


def _other(seat: int) -> int:
    return 1 - seat


async def _handle_bid(room: LiarsDiceRoom, player: WebPlayer, data: dict) -> None:
    if room.phase != "bidding" or len(room.players) < 2:
        return
    if player.seat != room.turn_seat:
        return  # not your turn — ignore silently

    try:
        quantity = int(data.get("quantity"))
        face = int(data.get("face"))
    except (TypeError, ValueError):
        return
    if face < 1 or face > 6 or quantity < 1 or quantity > _total_dice(room):
        await _send_error(player, "That bid is out of range.")
        return

    cur = room.current_bid
    if cur is not None:
        higher = quantity > cur["quantity"] or (
            quantity == cur["quantity"] and face > cur["face"]
        )
        if not higher:
            await _send_error(player, "Your bid must beat the current bid.")
            return

    room.current_bid = {"quantity": quantity, "face": face}
    room.turn_seat = _other(player.seat)
    room.last_activity = time.time()
    await _broadcast_state(room)


async def _handle_challenge(room: LiarsDiceRoom, player: WebPlayer) -> None:
    if room.phase != "bidding" or len(room.players) < 2:
        return
    if player.seat != room.turn_seat:
        return
    if room.current_bid is None:
        await _send_error(player, "There's no bid to challenge yet.")
        return

    bid = room.current_bid
    challenger = player
    bidder = room.players[_other(player.seat)]

    face = bid["face"]
    count = 0
    for p in room.players:
        for d in p.dice:
            if d == face or (face != 1 and d == 1):
                count += 1

    good = count >= bid["quantity"]        # bid stood up → challenger loses a die
    loser = challenger if good else bidder

    room.reveal = {
        "dice_by_seat": {p.seat: list(p.dice) for p in room.players},
        "count": count,
        "bid": dict(bid),
        "loser_seat": loser.seat,
    }
    loser.dice = loser.dice[:-1]           # drop one die
    room.phase = "reveal"
    room.current_bid = None
    room.last_activity = time.time()

    if not loser.dice:                     # out of dice → match over
        room.phase = "over"
        room.winner_seat = _other(loser.seat)
        await _broadcast_state(room)
        return

    await _broadcast_state(room)
    room.reveal_gen += 1
    gen = room.reveal_gen
    asyncio.create_task(_advance_after_reveal(room, gen, loser.seat))


async def _advance_after_reveal(room: LiarsDiceRoom, gen: int, next_first_seat: int) -> None:
    """After the reveal beat, deal the next round (loser bids first)."""
    try:
        await asyncio.sleep(REVEAL_SECONDS)
        if rooms.get(room.room_id) is not room:
            return
        if room.reveal_gen != gen or room.phase != "reveal":
            return
        _start_round(room, first_seat=next_first_seat)
        room.last_activity = time.time()
        await _broadcast_state(room)
    except Exception:
        logger.exception("liarsdice _advance_after_reveal error — round not advanced")


def _reset_match(room: LiarsDiceRoom) -> None:
    room.reveal_gen += 1  # invalidate any pending reveal timer
    for p in room.players:
        p.dice = []  # empty → _start_round rolls a fresh START_DICE hand
    room.winner_seat = None
    room.started = True
    _start_round(room, first_seat=0)


# ── Broadcast helpers ────────────────────────────────────────────────────────


async def _send(ws: WebSocket | None, msg: dict) -> None:
    if ws and ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _send_error(player: WebPlayer, message: str) -> None:
    await _send(player.ws, {"type": "error", "message": message})


def _state_msg(room: LiarsDiceRoom, viewer: WebPlayer) -> dict:
    me = viewer
    opp = next((p for p in room.players if p.seat != viewer.seat), None)

    last_reveal = None
    if room.reveal is not None:
        by = room.reveal["dice_by_seat"]
        loser_seat = room.reveal["loser_seat"]
        last_reveal = {
            "all_dice": {
                "me": by.get(me.seat, []),
                "opp": by.get(opp.seat, []) if opp else [],
            },
            "count": room.reveal["count"],
            "bid": room.reveal["bid"],
            "loser": "me" if loser_seat == me.seat else "opp",
        }

    winner = None
    if room.winner_seat is not None:
        winner = "you" if room.winner_seat == me.seat else "opp"

    turn = None
    if room.phase == "bidding":
        turn = "me" if room.turn_seat == me.seat else "opp"

    return {
        "type": "state",
        "code": room.code,
        "phase": room.phase,
        "your_dice": list(me.dice),
        "dice_counts": {"me": len(me.dice), "opp": len(opp.dice) if opp else 0},
        "current_bid": dict(room.current_bid) if room.current_bid else None,
        "turn": turn,
        "last_reveal": last_reveal,
        "you": me.seat,
        "players": [
            {"name": p.name, "seat": p.seat, "connected": p.connected,
             "dice_count": len(p.dice), "you": p.seat == me.seat}
            for p in room.players
        ],
        "winner": winner,
    }


async def _broadcast_state(room: LiarsDiceRoom) -> None:
    for p in room.players:
        await _send(p.ws, _state_msg(room, p))


# ── Stale room cleanup ───────────────────────────────────────────────────────


async def cleanup_stale_liarsdice_rooms() -> None:
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
            logger.exception("cleanup_stale_liarsdice_rooms error — loop continues")
