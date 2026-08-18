"""Battleship — web-native 2-player game engine, WebSocket handler, API router.

Mirrors the tic-tac-toe web-native pattern (in-memory rooms + codes maps, a
WebPlayer dataclass, _send/_broadcast helpers, an identify/rematch WS handler,
disconnect handling, and a stale-room cleanup coroutine). Rooms are created
straight from the browser — no Discord token needed. Free game, no coins.

Two phases on a 10×10 grid:
  1. Placement — each player places 5 ships (Carrier 5, Battleship 4, Cruiser 3,
     Submarine 3, Destroyer 2). Client sends {type:"place", ships:[...]} or
     {type:"autoplace"}. Both placed → phase "battle".
  2. Battle — players alternate firing {type:"fire", row, col} at the opponent's
     grid. One shot per turn; hit or miss, the turn passes. Sinking all of the
     opponent's ships wins.

Never leak the opponent's un-hit ship positions.
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
BOARD = 10       # 10×10 grid

# name, length — the standard fleet.
SHIP_DEFS = [
    ("Carrier", 5),
    ("Battleship", 4),
    ("Cruiser", 3),
    ("Submarine", 3),
    ("Destroyer", 2),
]
REQUIRED_LENS = sorted(length for _, length in SHIP_DEFS)  # [2, 3, 3, 4, 5]


# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class Ship:
    name: str
    length: int
    cells: list  # [[row, col], ...]
    hits: set = field(default_factory=set)  # {(row, col), ...} taken by the enemy

    @property
    def sunk(self) -> bool:
        return len(self.hits) >= self.length


@dataclass
class WebPlayer:
    name: str
    symbol: str  # "P1" (host) or "P2"
    ws: WebSocket | None = None
    connected: bool = False
    placed: bool = False
    grid: list = field(default_factory=lambda: [[None] * BOARD for _ in range(BOARD)])  # ship idx | None
    ships: list = field(default_factory=list)   # list[Ship] — this player's own fleet
    shots: list = field(default_factory=lambda: [["" ] * BOARD for _ in range(BOARD)])  # our shots on enemy: ""|"hit"|"miss"


@dataclass
class BattleshipRoom:
    room_id: str
    code: str
    players: list = field(default_factory=list)  # [0]=P1 (host), [1]=P2
    phase: str = "lobby"  # "lobby" | "placement" | "battle" | "over"
    turn: str = "P1"
    winner: str | None = None  # None | "P1" | "P2"
    last_sunk: str | None = None  # ship name sunk on the most recent shot
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


rooms: dict[str, BattleshipRoom] = {}   # room_id → room
codes: dict[str, str] = {}              # CODE → room_id


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

router = APIRouter(prefix="/api/v1/battleship", tags=["battleship"])


@router.post("/rooms")
async def create_room(body: JoinBody):
    """Create a fresh room and register the caller as P1 (the host, fires first)."""
    name = (body.name or "Player").strip()[:32] or "Player"
    room_id = secrets.token_hex(4)
    code = _new_code()
    room = BattleshipRoom(room_id=room_id, code=code)
    room.players.append(WebPlayer(name=name, symbol="P1"))
    rooms[room_id] = room
    codes[code] = room_id
    logger.info("battleship room created code=%s id=%s host=%s", code, room_id, name)
    return {"room_id": room_id, "code": code}


@router.post("/rooms/{code}/join")
async def join_room(code: str, body: JoinBody):
    """Join an existing room by its 4-letter code as P2 (the second player)."""
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

    room.players.append(WebPlayer(name=name, symbol="P2"))
    if room.phase == "lobby":
        room.phase = "placement"
    room.last_activity = time.time()
    await _broadcast_state(room)
    return {"room_id": room.room_id}


# ── WebSocket handler ────────────────────────────────────────────────────────


async def battleship_websocket(websocket: WebSocket, room_id: str):
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

            elif mtype == "place":
                if player is not None:
                    await _handle_place(room, player, data.get("ships"))

            elif mtype == "autoplace":
                if player is not None:
                    await _handle_autoplace(room, player)

            elif mtype == "fire":
                if player is not None:
                    await _handle_fire(room, player, data)

            elif mtype == "rematch":
                if player is not None:
                    _reset_game(room)
                    room.last_activity = time.time()
                    await _broadcast_state(room)

            elif mtype == "ping":
                await _send(websocket, {"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("battleship_websocket error in room %s", room_id)
    finally:
        if player is not None:
            player.connected = False
            player.ws = None
            try:
                await _broadcast_state(room)
            except Exception:
                pass


def _attach_player(room: BattleshipRoom, name: str, ws: WebSocket) -> WebPlayer | None:
    """Match an identify by name to a registered player and attach the socket.

    Returns None if the name isn't registered (they must join via REST first).
    Same-name identify reattaches — this is how reload/rejoin works.
    """
    if not name:
        return None
    for p in room.players:
        if p.name == name:
            p.ws = ws
            p.connected = True
            return p
    return None


# ── Placement ────────────────────────────────────────────────────────────────


def _build_layout(ships_data) -> tuple | None:
    """Validate a placement payload → (grid, ships) or None if invalid.

    Requires exactly the fleet's lengths, all in-bounds, none overlapping.
    """
    if not isinstance(ships_data, list) or len(ships_data) != len(SHIP_DEFS):
        return None

    grid = [[None] * BOARD for _ in range(BOARD)]
    ships: list[Ship] = []
    used_lens: list[int] = []

    for idx, s in enumerate(ships_data):
        if not isinstance(s, dict):
            return None
        try:
            row = int(s.get("row"))
            col = int(s.get("col"))
            length = int(s.get("len"))
            direction = str(s.get("dir"))
        except (TypeError, ValueError):
            return None
        if direction not in ("h", "v"):
            return None
        if length not in REQUIRED_LENS:
            return None

        cells = []
        for i in range(length):
            r = row + (i if direction == "v" else 0)
            c = col + (i if direction == "h" else 0)
            if not (0 <= r < BOARD and 0 <= c < BOARD):
                return None
            if grid[r][c] is not None:
                return None  # overlap
            cells.append([r, c])

        for r, c in cells:
            grid[r][c] = idx
        # Name by matching this slot's length to the fleet (order-independent).
        name = SHIP_DEFS[idx][0] if SHIP_DEFS[idx][1] == length else f"Ship-{length}"
        ships.append(Ship(name=name, length=length, cells=cells))
        used_lens.append(length)

    if sorted(used_lens) != REQUIRED_LENS:
        return None
    return grid, ships


def _random_layout() -> tuple:
    """Generate a random valid fleet layout → (grid, ships). Always succeeds."""
    while True:
        grid = [[None] * BOARD for _ in range(BOARD)]
        ships: list[Ship] = []
        ok = True
        for idx, (name, length) in enumerate(SHIP_DEFS):
            placed = False
            for _ in range(200):
                direction = random.choice(("h", "v"))
                if direction == "h":
                    row = random.randint(0, BOARD - 1)
                    col = random.randint(0, BOARD - length)
                else:
                    row = random.randint(0, BOARD - length)
                    col = random.randint(0, BOARD - 1)
                cells = []
                clash = False
                for i in range(length):
                    r = row + (i if direction == "v" else 0)
                    c = col + (i if direction == "h" else 0)
                    if grid[r][c] is not None:
                        clash = True
                        break
                    cells.append([r, c])
                if clash:
                    continue
                for r, c in cells:
                    grid[r][c] = idx
                ships.append(Ship(name=name, length=length, cells=cells))
                placed = True
                break
            if not placed:
                ok = False
                break
        if ok:
            return grid, ships


async def _handle_place(room: BattleshipRoom, player: WebPlayer, ships_data) -> None:
    if room.phase not in ("lobby", "placement"):
        await _send_error(player, "You can't place ships right now.")
        return
    if player.placed:
        await _send_error(player, "You've already placed your fleet.")
        return
    built = _build_layout(ships_data)
    if built is None:
        await _send_error(player, "Invalid placement — check the fleet fits with no overlaps.")
        return
    player.grid, player.ships = built
    player.placed = True
    _maybe_start_battle(room)
    room.last_activity = time.time()
    await _broadcast_state(room)


async def _handle_autoplace(room: BattleshipRoom, player: WebPlayer) -> None:
    if room.phase not in ("lobby", "placement"):
        await _send_error(player, "You can't place ships right now.")
        return
    if player.placed:
        await _send_error(player, "You've already placed your fleet.")
        return
    player.grid, player.ships = _random_layout()
    player.placed = True
    _maybe_start_battle(room)
    room.last_activity = time.time()
    await _broadcast_state(room)


def _maybe_start_battle(room: BattleshipRoom) -> None:
    if len(room.players) >= 2 and all(p.placed for p in room.players):
        room.phase = "battle"
        room.turn = "P1"
        room.winner = None


# ── Battle ───────────────────────────────────────────────────────────────────


async def _handle_fire(room: BattleshipRoom, player: WebPlayer, data: dict) -> None:
    if room.phase != "battle" or room.winner is not None:
        return
    if player.symbol != room.turn:
        return  # not your turn — ignore silently

    opp = _opponent(room, player)
    if opp is None:
        return

    row, col = data.get("row"), data.get("col")
    if not isinstance(row, int) or not isinstance(col, int):
        return
    if not (0 <= row < BOARD and 0 <= col < BOARD):
        return
    if player.shots[row][col] != "":
        return  # already fired there

    room.last_sunk = None
    ship_idx = opp.grid[row][col]
    if ship_idx is not None:
        player.shots[row][col] = "hit"
        ship = opp.ships[ship_idx]
        ship.hits.add((row, col))
        if ship.sunk:
            room.last_sunk = ship.name
        if all(s.sunk for s in opp.ships):
            room.winner = player.symbol
            room.phase = "over"
    else:
        player.shots[row][col] = "miss"

    if room.winner is None:
        room.turn = "P2" if room.turn == "P1" else "P1"

    room.last_activity = time.time()
    await _broadcast_state(room)


def _opponent(room: BattleshipRoom, player: WebPlayer) -> WebPlayer | None:
    for p in room.players:
        if p.symbol != player.symbol:
            return p
    return None


def _reset_game(room: BattleshipRoom) -> None:
    for p in room.players:
        p.placed = False
        p.grid = [[None] * BOARD for _ in range(BOARD)]
        p.ships = []
        p.shots = [["" ] * BOARD for _ in range(BOARD)]
    room.phase = "placement" if len(room.players) >= 2 else "lobby"
    room.turn = "P1"
    room.winner = None
    room.last_sunk = None


# ── Broadcast helpers ────────────────────────────────────────────────────────


async def _send(ws: WebSocket | None, msg: dict) -> None:
    if ws and ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _send_error(player: WebPlayer, message: str) -> None:
    await _send(player.ws, {"type": "error", "message": message})


def _your_board(player: WebPlayer, opp: WebPlayer | None) -> tuple:
    """This player's own grid: ships plus the shots the enemy has landed on it.

    Cells: "" empty | "ship" un-hit ship | "hit" ship struck | "miss" enemy miss.
    Also returns the cells belonging to fully-sunk ships (for highlighting).
    """
    board = [["" ] * BOARD for _ in range(BOARD)]
    for r in range(BOARD):
        for c in range(BOARD):
            has_ship = player.grid[r][c] is not None
            enemy_shot = opp.shots[r][c] if opp else ""
            if enemy_shot == "hit":
                board[r][c] = "hit"
            elif enemy_shot == "miss":
                board[r][c] = "miss"
            elif has_ship:
                board[r][c] = "ship"
    sunk_cells = [cell for s in player.ships if s.sunk for cell in s.cells]
    return board, sunk_cells


def _enemy_board(player: WebPlayer, opp: WebPlayer | None) -> tuple:
    """Enemy waters as this player sees them: only their own shots.

    Cells: "" un-fired | "hit" | "miss". NEVER reveals un-hit enemy ships.
    Sunk-ship cells (all already hit) are returned so the client can highlight.
    """
    board = [row[:] for row in player.shots]
    sunk_cells = []
    if opp:
        sunk_cells = [cell for s in opp.ships if s.sunk for cell in s.cells]
    return board, sunk_cells


def _state_msg(room: BattleshipRoom, player: WebPlayer) -> dict:
    opp = _opponent(room, player)
    your_board, your_sunk = _your_board(player, opp)
    enemy_board, enemy_sunk = _enemy_board(player, opp)
    ships_left = sum(1 for s in opp.ships if not s.sunk) if opp else len(SHIP_DEFS)
    return {
        "type": "state",
        "code": room.code,
        "phase": room.phase,
        "turn": room.turn,
        "winner": room.winner,
        "you": player.symbol,
        "you_placed": player.placed,
        "sunk": room.last_sunk,
        "your_board": your_board,
        "your_sunk": your_sunk,
        "enemy_board": enemy_board,
        "enemy_sunk": enemy_sunk,
        "enemy_ships_left": ships_left,
        "players": [
            {
                "name": p.name,
                "symbol": p.symbol,
                "connected": p.connected,
                "placed": p.placed,
            }
            for p in room.players
        ],
    }


async def _broadcast_state(room: BattleshipRoom) -> None:
    for p in room.players:
        await _send(p.ws, _state_msg(room, p))


# ── Stale room cleanup ───────────────────────────────────────────────────────


async def cleanup_stale_battleship_rooms() -> None:
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
            logger.exception("cleanup_stale_battleship_rooms error — loop continues")
