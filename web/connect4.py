"""Connect Four — web-native 2-player game engine, WebSocket handler, API router.

Mirrors tictactoe.py exactly (in-memory rooms + codes maps, WebPlayer dataclass,
_send/_broadcast helpers, identify/move/rematch protocol, disconnect handling,
stale-room cleanup), but the game is Connect Four on a 7-column × 6-row board.
Rooms are created straight from the browser — no Discord token. Free, no coins.
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

COLS = 7
ROWS = 6


# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class WebPlayer:
    name: str
    symbol: str  # "R" or "Y"
    ws: WebSocket | None = None
    connected: bool = False


def _empty_board() -> list:
    return [["" for _ in range(COLS)] for _ in range(ROWS)]


@dataclass
class ConnectFourRoom:
    room_id: str
    code: str
    players: list[WebPlayer] = field(default_factory=list)  # [0]=R (host), [1]=Y
    board: list = field(default_factory=_empty_board)  # [ROWS][COLS] of ""|"R"|"Y"
    turn: str = "R"
    winner: str | None = None  # None | "R" | "Y" | "draw"
    winning_cells: list | None = None  # [[r,c], ...] or None
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


rooms: dict[str, ConnectFourRoom] = {}      # room_id → room
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

router = APIRouter(prefix="/api/v1/connect4", tags=["connect4"])


@router.post("/rooms")
async def create_room(body: JoinBody):
    """Create a fresh room and register the caller as R (the host, goes first)."""
    name = (body.name or "Player").strip()[:32] or "Player"
    room_id = secrets.token_hex(4)
    code = _new_code()
    room = ConnectFourRoom(room_id=room_id, code=code)
    room.players.append(WebPlayer(name=name, symbol="R"))
    rooms[room_id] = room
    codes[code] = room_id
    logger.info("connect4 room created code=%s id=%s host=%s", code, room_id, name)
    return {"room_id": room_id, "code": code}


@router.post("/rooms/{code}/join")
async def join_room(code: str, body: JoinBody):
    """Join an existing room by its 4-letter code as Y (the second player)."""
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

    symbol = "R" if not room.players else "Y"
    room.players.append(WebPlayer(name=name, symbol=symbol))
    room.last_activity = time.time()
    await _broadcast_state(room)
    return {"room_id": room.room_id}


# ── WebSocket handler ────────────────────────────────────────────────────────


async def connect4_websocket(websocket: WebSocket, room_id: str):
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
        logger.exception("connect4_websocket error in room %s", room_id)
    finally:
        if player is not None:
            player.connected = False
            player.ws = None
            try:
                await _broadcast_state(room)
            except Exception:
                pass


def _attach_player(room: ConnectFourRoom, name: str, ws: WebSocket) -> WebPlayer | None:
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


async def _handle_move(room: ConnectFourRoom, player: WebPlayer, data: dict) -> None:
    if room.winner is not None:
        return
    if len(room.players) < 2:
        await _send_error(player, "Waiting for an opponent to join.")
        return
    if player.symbol != room.turn:
        return  # not your turn — ignore silently

    col = data.get("col")
    if not isinstance(col, int) or col < 0 or col >= COLS:
        return

    # Drop to the lowest empty row in the column.
    row = _drop_row(room.board, col)
    if row is None:
        return  # column full

    room.board[row][col] = player.symbol

    cells = _winning_cells(room.board, row, col, player.symbol)
    if cells:
        room.winner = player.symbol
        room.winning_cells = cells
    elif _board_full(room.board):
        room.winner = "draw"
    else:
        room.turn = "Y" if room.turn == "R" else "R"

    room.last_activity = time.time()
    await _broadcast_state(room)


def _drop_row(board: list, col: int) -> int | None:
    """Return the lowest empty row index in `col`, or None if the column is full."""
    for r in range(ROWS - 1, -1, -1):
        if board[r][col] == "":
            return r
    return None


def _board_full(board: list) -> bool:
    return all(board[0][c] != "" for c in range(COLS))


def _winning_cells(board: list, row: int, col: int, sym: str) -> list | None:
    """Return the list of 4 cells that complete a line through (row, col), or None."""
    directions = ((0, 1), (1, 0), (1, 1), (1, -1))  # horiz, vert, both diagonals
    for dr, dc in directions:
        line = [[row, col]]
        # Extend forward.
        r, c = row + dr, col + dc
        while 0 <= r < ROWS and 0 <= c < COLS and board[r][c] == sym:
            line.append([r, c])
            r, c = r + dr, c + dc
        # Extend backward.
        r, c = row - dr, col - dc
        while 0 <= r < ROWS and 0 <= c < COLS and board[r][c] == sym:
            line.insert(0, [r, c])
            r, c = r - dr, c - dc
        if len(line) >= 4:
            # Return exactly the 4 cells around the placed one for a clean highlight.
            idx = line.index([row, col])
            start = max(0, min(idx - 3, len(line) - 4))
            return line[start:start + 4]
    return None


def _reset_board(room: ConnectFourRoom) -> None:
    room.board = _empty_board()
    room.turn = "R"
    room.winner = None
    room.winning_cells = None


# ── Broadcast helpers ────────────────────────────────────────────────────────


async def _send(ws: WebSocket | None, msg: dict) -> None:
    if ws and ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _send_error(player: WebPlayer, message: str) -> None:
    await _send(player.ws, {"type": "error", "message": message})


def _state_msg(room: ConnectFourRoom, you: str | None) -> dict:
    msg = {
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
    if room.winning_cells:
        msg["winning_cells"] = room.winning_cells
    return msg


async def _broadcast_state(room: ConnectFourRoom) -> None:
    for p in room.players:
        await _send(p.ws, _state_msg(room, you=p.symbol))


# ── Stale room cleanup ───────────────────────────────────────────────────────


async def cleanup_stale_connect4_rooms() -> None:
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
            logger.exception("cleanup_stale_connect4_rooms error — loop continues")
