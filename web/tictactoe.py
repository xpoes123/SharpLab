"""Tic-Tac-Toe — web-native 2-player game engine, WebSocket handler, API router.

Mirrors the minesweeper WS-game pattern (in-memory rooms, WebPlayer dataclass,
_send/_broadcast helpers, a WS handler, and a stale-room cleanup coroutine), but
rooms are created straight from the browser — no Discord token needed. This is a
free game (no coins) that proves the web multiplayer pattern.
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

# 8 winning triples on a 3×3 board.
WIN_LINES = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8),  # rows
    (0, 3, 6), (1, 4, 7), (2, 5, 8),  # cols
    (0, 4, 8), (2, 4, 6),             # diagonals
)


# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class WebPlayer:
    name: str
    symbol: str  # "X" or "O"
    ws: WebSocket | None = None
    connected: bool = False


@dataclass
class TicTacToeRoom:
    room_id: str
    code: str
    players: list[WebPlayer] = field(default_factory=list)  # [0]=X (host), [1]=O
    board: list = field(default_factory=lambda: [None] * 9)
    turn: str = "X"
    winner: str | None = None  # None | "X" | "O" | "draw"
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


rooms: dict[str, TicTacToeRoom] = {}         # room_id → room
codes: dict[str, str] = {}                   # CODE → room_id


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

router = APIRouter(prefix="/api/v1/tictactoe", tags=["tictactoe"])


@router.post("/rooms")
async def create_room(body: JoinBody):
    """Create a fresh room and register the caller as X (the host, goes first)."""
    name = (body.name or "Player").strip()[:32] or "Player"
    room_id = secrets.token_hex(4)
    code = _new_code()
    room = TicTacToeRoom(room_id=room_id, code=code)
    room.players.append(WebPlayer(name=name, symbol="X"))
    rooms[room_id] = room
    codes[code] = room_id
    logger.info("tictactoe room created code=%s id=%s host=%s", code, room_id, name)
    return {"room_id": room_id, "code": code}


@router.post("/rooms/{code}/join")
async def join_room(code: str, body: JoinBody):
    """Join an existing room by its 4-letter code as O (the second player)."""
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

    symbol = "X" if not room.players else "O"
    room.players.append(WebPlayer(name=name, symbol=symbol))
    room.last_activity = time.time()
    await _broadcast_state(room)
    return {"room_id": room.room_id}


# ── WebSocket handler ────────────────────────────────────────────────────────


async def tictactoe_websocket(websocket: WebSocket, room_id: str):
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

            elif mtype == "move":
                if player is not None:
                    await _handle_move(room, player, data)

            elif mtype == "rematch":
                if player is not None:
                    _reset_board(room)
                    room.last_activity = time.time()
                    await _broadcast_state(room)

            elif mtype == "ping":
                await _send(websocket, {"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("tictactoe_websocket error in room %s", room_id)
    finally:
        if player is not None:
            player.connected = False
            player.ws = None
            try:
                await _broadcast_state(room)
            except Exception:
                pass


def _attach_player(room: TicTacToeRoom, name: str, ws: WebSocket) -> WebPlayer | None:
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


async def _handle_move(room: TicTacToeRoom, player: WebPlayer, data: dict) -> None:
    if room.winner is not None:
        return
    if len(room.players) < 2:
        await _send_error(player, "Waiting for an opponent to join.")
        return
    if player.symbol != room.turn:
        return  # not your turn — ignore silently

    cell = data.get("cell")
    if not isinstance(cell, int) or cell < 0 or cell > 8:
        return
    if room.board[cell] is not None:
        return  # occupied

    room.board[cell] = player.symbol

    win = _check_winner(room.board)
    if win:
        room.winner = win
    elif all(c is not None for c in room.board):
        room.winner = "draw"
    else:
        room.turn = "O" if room.turn == "X" else "X"

    room.last_activity = time.time()
    await _broadcast_state(room)


def _check_winner(board: list) -> str | None:
    for a, b, c in WIN_LINES:
        if board[a] is not None and board[a] == board[b] == board[c]:
            return board[a]
    return None


def _reset_board(room: TicTacToeRoom) -> None:
    room.board = [None] * 9
    room.turn = "X"
    room.winner = None


# ── Broadcast helpers ────────────────────────────────────────────────────────


async def _send(ws: WebSocket | None, msg: dict) -> None:
    if ws and ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _send_error(player: WebPlayer, message: str) -> None:
    await _send(player.ws, {"type": "error", "message": message})


def _state_msg(room: TicTacToeRoom, you: str | None) -> dict:
    return {
        "type": "state",
        "code": room.code,
        "board": room.board,
        "turn": room.turn,
        "winner": room.winner,
        "players": [
            {"name": p.name, "symbol": p.symbol, "connected": p.connected}
            for p in room.players
        ],
        "you": you,
    }


async def _broadcast_state(room: TicTacToeRoom) -> None:
    for p in room.players:
        await _send(p.ws, _state_msg(room, you=p.symbol))


# ── Stale room cleanup ───────────────────────────────────────────────────────


async def cleanup_stale_tictactoe_rooms() -> None:
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
            logger.exception("cleanup_stale_tictactoe_rooms error — loop continues")
