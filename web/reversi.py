"""Reversi (Othello) — web-native 2-player game engine, WebSocket handler, API router.

Mirrors the tic-tac-toe web-native pattern (in-memory rooms, WebPlayer dataclass,
_send/_broadcast helpers, a WS handler, and a stale-room cleanup coroutine): rooms
are created straight from the browser — no Discord token needed. Free game, no coins.

Board is 8×8. Black ('B', first joiner) moves first, White ('W') second. A legal
move places your disc on an empty square that outflanks ≥1 opponent disc in a
straight line (8 directions), flipping all outflanked discs. If a player has no
legal move their turn is skipped; if neither can move (or the board is full) the
game ends and the higher disc count wins (or draw).
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

BOARD_N = 8
# 8 directions: N, NE, E, SE, S, SW, W, NW
DIRECTIONS = (
    (-1, 0), (-1, 1), (0, 1), (1, 1),
    (1, 0), (1, -1), (0, -1), (-1, -1),
)


# ── Board helpers ────────────────────────────────────────────────────────────


def _empty_board() -> list:
    """8×8 board, "" = empty, standard Othello starting position at the center."""
    board = [["" for _ in range(BOARD_N)] for _ in range(BOARD_N)]
    board[3][3] = "W"
    board[3][4] = "B"
    board[4][3] = "B"
    board[4][4] = "W"
    return board


def _opponent(disc: str) -> str:
    return "W" if disc == "B" else "B"


def _flips_for_move(board: list, row: int, col: int, disc: str) -> list:
    """Return the list of [r, c] discs that would flip if `disc` plays (row, col).

    Empty list means the move is illegal (outflanks nothing).
    """
    if not (0 <= row < BOARD_N and 0 <= col < BOARD_N):
        return []
    if board[row][col] != "":
        return []
    opp = _opponent(disc)
    flips: list = []
    for dr, dc in DIRECTIONS:
        line: list = []
        r, c = row + dr, col + dc
        while 0 <= r < BOARD_N and 0 <= c < BOARD_N and board[r][c] == opp:
            line.append([r, c])
            r += dr
            c += dc
        # Only a run of opponent discs terminated by our own disc counts.
        if line and 0 <= r < BOARD_N and 0 <= c < BOARD_N and board[r][c] == disc:
            flips.extend(line)
    return flips


def _legal_moves(board: list, disc: str) -> list:
    moves: list = []
    for r in range(BOARD_N):
        for c in range(BOARD_N):
            if board[r][c] == "" and _flips_for_move(board, r, c, disc):
                moves.append([r, c])
    return moves


def _scores(board: list) -> dict:
    b = sum(cell == "B" for row in board for cell in row)
    w = sum(cell == "W" for row in board for cell in row)
    return {"B": b, "W": w}


# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class WebPlayer:
    name: str
    disc: str  # "B" or "W"
    ws: WebSocket | None = None
    connected: bool = False


@dataclass
class ReversiRoom:
    room_id: str
    code: str
    players: list[WebPlayer] = field(default_factory=list)  # [0]=B (host), [1]=W
    board: list = field(default_factory=_empty_board)
    turn: str = "B"
    winner: str | None = None  # None | "B" | "W" | "draw"
    skipped: str | None = None  # disc whose turn was just auto-skipped (transient)
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


rooms: dict[str, ReversiRoom] = {}   # room_id → room
codes: dict[str, str] = {}           # CODE → room_id


def _new_code() -> str:
    for _ in range(50):
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in codes:
            return code
    return "".join(random.choices(string.ascii_uppercase, k=4))


# ── Request models ───────────────────────────────────────────────────────────


class JoinBody(BaseModel):
    name: str = "Player"


# ── API router ───────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/v1/reversi", tags=["reversi"])


@router.post("/rooms")
async def create_room(body: JoinBody):
    """Create a fresh room and register the caller as Black (the host, goes first)."""
    name = (body.name or "Player").strip()[:32] or "Player"
    room_id = secrets.token_hex(4)
    code = _new_code()
    room = ReversiRoom(room_id=room_id, code=code)
    room.players.append(WebPlayer(name=name, disc="B"))
    rooms[room_id] = room
    codes[code] = room_id
    logger.info("reversi room created code=%s id=%s host=%s", code, room_id, name)
    return {"room_id": room_id, "code": code}


@router.post("/rooms/{code}/join")
async def join_room(code: str, body: JoinBody):
    """Join an existing room by its 4-letter code as White (the second player)."""
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

    disc = "B" if not room.players else "W"
    room.players.append(WebPlayer(name=name, disc=disc))
    room.last_activity = time.time()
    await _broadcast_state(room)
    return {"room_id": room.room_id}


# ── WebSocket handler ────────────────────────────────────────────────────────


async def reversi_websocket(websocket: WebSocket, room_id: str):
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
        logger.exception("reversi_websocket error in room %s", room_id)
    finally:
        if player is not None:
            player.connected = False
            player.ws = None
            try:
                await _broadcast_state(room)
            except Exception:
                pass


def _attach_player(room: ReversiRoom, name: str, ws: WebSocket) -> WebPlayer | None:
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


async def _handle_move(room: ReversiRoom, player: WebPlayer, data: dict) -> None:
    if room.winner is not None:
        return
    if len(room.players) < 2:
        await _send_error(player, "Waiting for an opponent to join.")
        return
    if player.disc != room.turn:
        return  # not your turn — ignore silently

    row = data.get("row")
    col = data.get("col")
    if not isinstance(row, int) or not isinstance(col, int):
        return
    if not (0 <= row < BOARD_N and 0 <= col < BOARD_N):
        return

    flips = _flips_for_move(room.board, row, col, player.disc)
    if not flips:
        return  # illegal move — ignore silently

    # Apply the move + all flips.
    room.board[row][col] = player.disc
    for fr, fc in flips:
        room.board[fr][fc] = player.disc

    # Advance the turn, skipping a player with no legal move; end if neither can.
    _advance_turn(room, player.disc)

    room.last_activity = time.time()
    await _broadcast_state(room)


def _advance_turn(room: ReversiRoom, just_moved: str) -> None:
    """Set room.turn to the next player who can move; end the game if none can."""
    room.skipped = None
    opp = _opponent(just_moved)
    if _legal_moves(room.board, opp):
        room.turn = opp
        return
    # Opponent can't move — either skip back to the mover or end the game.
    if _legal_moves(room.board, just_moved):
        room.turn = just_moved
        room.skipped = opp  # opponent's turn was auto-skipped
        return
    # Neither side can move → game over.
    _finish(room)


def _finish(room: ReversiRoom) -> None:
    s = _scores(room.board)
    if s["B"] > s["W"]:
        room.winner = "B"
    elif s["W"] > s["B"]:
        room.winner = "W"
    else:
        room.winner = "draw"


def _reset_board(room: ReversiRoom) -> None:
    room.board = _empty_board()
    room.turn = "B"
    room.winner = None
    room.skipped = None


# ── Broadcast helpers ────────────────────────────────────────────────────────


async def _send(ws: WebSocket | None, msg: dict) -> None:
    if ws and ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _send_error(player: WebPlayer, message: str) -> None:
    await _send(player.ws, {"type": "error", "message": message})


def _state_msg(room: ReversiRoom, you: str | None) -> dict:
    # legal_moves are only meaningful for the viewer whose turn it is.
    legal = _legal_moves(room.board, room.turn) if (
        room.winner is None and you == room.turn and len(room.players) >= 2
    ) else []
    return {
        "type": "state",
        "code": room.code,
        "board": room.board,
        "turn": room.turn,
        "winner": room.winner,
        "skipped": room.skipped,
        "scores": _scores(room.board),
        "legal_moves": legal,
        "players": [
            {"name": p.name, "disc": p.disc, "connected": p.connected}
            for p in room.players
        ],
        "you": you,
    }


async def _broadcast_state(room: ReversiRoom) -> None:
    for p in room.players:
        await _send(p.ws, _state_msg(room, you=p.disc))


# ── Stale room cleanup ───────────────────────────────────────────────────────


async def cleanup_stale_reversi_rooms() -> None:
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
            logger.exception("cleanup_stale_reversi_rooms error — loop continues")
