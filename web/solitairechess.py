"""Web Solitaire Chess — game engine, WebSocket handler, API router."""

import asyncio
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from db import queries
from shared.solitairechess_logic import (
    Board, copy_board, count_pieces, generate_puzzle,
    get_captures, get_hint, make_move, undo_move,
    PIECE_NAME, PIECE_EMOJI,
)

from web._apisec import check_api_key
ROOM_TTL = 1800  # 30 minutes

# Game constants
WINS_TO_WIN = 3
MAX_ROUNDS = 5
ROUND_TIME = 300   # 5 minutes per puzzle
ROUND_DELAY = 5    # seconds between rounds
SOLO_PAYOUT = 2    # solo win multiplier

DIFFICULTY_PIECES = {"easy": 4, "medium": 5, "hard": 6, "expert": 7}

# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class WebPlayer:
    discord_user: str
    display_name: str
    wager: int
    is_host: bool = False
    rounds_won: int = 0
    # Per-round state
    board: Board = field(default_factory=list)
    history: list[tuple[list[int], list[int], str]] = field(default_factory=list)
    move_count: int = 0
    solved: bool = False
    gave_up: bool = False
    ws: WebSocket | None = None

    @property
    def pieces_left(self) -> int:
        if not self.board:
            return 0
        return count_pieces(self.board)

    @property
    def done(self) -> bool:
        return self.solved or self.gave_up


@dataclass
class SolChessRoom:
    room_id: str
    host_id: str
    channel_id: str
    difficulty: str = "medium"
    phase: str = "waiting"
    players: dict[str, WebPlayer] = field(default_factory=dict)
    round_num: int = 0
    starting_board: Board = field(default_factory=list)
    round_start_time: float = 0.0
    round_solved: asyncio.Event = field(default_factory=asyncio.Event)
    race_task: asyncio.Task | None = field(default=None, repr=False)
    total_rounds_played: int = 0
    created_at: float = field(default_factory=time.time)
    result_data: dict | None = None


rooms: dict[str, SolChessRoom] = {}

# ── Request models ───────────────────────────────────────────────────────────


class CreateRoomRequest(BaseModel):
    host_discord_id: str
    channel_id: str
    host_display_name: str
    difficulty: str = "medium"


class CreateTokenRequest(BaseModel):
    discord_user: str
    display_name: str
    wager: int


# ── API router ───────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/v1/solitairechess", tags=["solitairechess"])


def _check_api_key(api_key: str) -> None:
    if not check_api_key(api_key):
        raise HTTPException(401, "Invalid API key")


@router.post("/rooms")
async def create_room(body: CreateRoomRequest, x_api_key: str = Header()):
    _check_api_key(x_api_key)
    room_id = secrets.token_hex(4)
    difficulty = body.difficulty if body.difficulty in DIFFICULTY_PIECES else "medium"
    room = SolChessRoom(
        room_id=room_id,
        host_id=body.host_discord_id,
        channel_id=body.channel_id,
        difficulty=difficulty,
    )
    rooms[room_id] = room
    await queries.create_game_session(
        room_id, "solitairechess", body.host_discord_id, body.channel_id,
    )
    return {"room_id": room_id}


@router.post("/rooms/{room_id}/tokens")
async def create_token(
    room_id: str, body: CreateTokenRequest, x_api_key: str = Header(),
):
    _check_api_key(x_api_key)
    room = rooms.get(room_id)
    if not room or room.phase != "waiting":
        raise HTTPException(404, "Room not found or not accepting players")
    if body.discord_user in room.players:
        raise HTTPException(409, "Already in this room")

    token = secrets.token_hex(16)
    is_host = body.discord_user == room.host_id
    room.players[body.discord_user] = WebPlayer(
        discord_user=body.discord_user,
        display_name=body.display_name,
        wager=body.wager,
        is_host=is_host,
    )
    await queries.create_game_token(
        token, room_id, body.discord_user, body.display_name, body.wager,
    )
    await _broadcast_room_state(room)
    base = os.environ.get("WEB_BASE_URL", "https://sharplab.djiang.xyz")
    url = f"{base}/solitairechess/{room_id}?t={token}"
    return {"token": token, "url": url}


@router.get("/rooms/{room_id}/result")
async def get_result(room_id: str):
    room = rooms.get(room_id)
    if room and room.result_data:
        return room.result_data
    session = await queries.get_game_session(room_id)
    if session and session["status"] == "finished" and session["result_json"]:
        return json.loads(session["result_json"])
    raise HTTPException(404, "Game not finished yet")


class PublicJoinRequest(BaseModel):
    display_name: str


@router.post("/public/create")
async def public_create_room(body: PublicJoinRequest):
    user_id = f"web_{secrets.token_hex(6)}"
    room_id = secrets.token_hex(4)
    room = SolChessRoom(room_id=room_id, host_id=user_id, channel_id="web", difficulty="medium")
    rooms[room_id] = room
    await queries.create_game_session(room_id, "solitairechess", user_id, "web")
    token = secrets.token_hex(16)
    room.players[user_id] = WebPlayer(
        discord_user=user_id, display_name=body.display_name, wager=0, is_host=True,
    )
    await queries.create_game_token(token, room_id, user_id, body.display_name, 0)
    base = os.environ.get("WEB_BASE_URL", "https://sharplab.djiang.xyz")
    return {"room_id": room_id, "token": token, "url": f"{base}/solitairechess/{room_id}?t={token}"}


@router.post("/public/join/{room_id}")
async def public_join_room(room_id: str, body: PublicJoinRequest):
    room = rooms.get(room_id)
    if not room or room.phase != "waiting":
        raise HTTPException(404, "Room not found or not accepting players")
    if len(room.players) >= 8:
        raise HTTPException(400, "Room is full")
    user_id = f"web_{secrets.token_hex(6)}"
    token = secrets.token_hex(16)
    room.players[user_id] = WebPlayer(
        discord_user=user_id, display_name=body.display_name, wager=0,
    )
    await queries.create_game_token(token, room_id, user_id, body.display_name, 0)
    await _broadcast_room_state(room)
    base = os.environ.get("WEB_BASE_URL", "https://sharplab.djiang.xyz")
    return {"token": token, "url": f"{base}/solitairechess/{room_id}?t={token}"}



# ── WebSocket handler ────────────────────────────────────────────────────────


async def solchess_websocket(websocket: WebSocket, room_id: str):
    token = websocket.query_params.get("t")
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    token_data = await queries.get_game_token(token)
    if not token_data or token_data["room_id"] != room_id:
        await websocket.close(code=4001, reason="Invalid token")
        return

    room = rooms.get(room_id)
    if not room or room.phase == "finished":
        await websocket.close(code=4002, reason="Room closed")
        return

    discord_user = token_data["discord_user"]
    player = room.players.get(discord_user)
    if not player:
        await websocket.close(code=4001, reason="Not a player in this room")
        return

    await websocket.accept()
    player.ws = websocket

    await _broadcast_room_state(room)

    try:
        while True:
            data = await websocket.receive_json()
            await _handle_message(room, player, data)
    except WebSocketDisconnect:
        player.ws = None
    except Exception:
        player.ws = None


async def _handle_message(room: SolChessRoom, player: WebPlayer, data: dict) -> None:
    msg_type = data.get("type")

    if msg_type == "start":
        if player.discord_user != room.host_id:
            await _send_error(player, "Only the host can start")
            return
        if room.phase != "waiting":
            await _send_error(player, "Game already started")
            return
        if len(room.players) < 1:
            await _send_error(player, "Need at least 1 player")
            return
        room.race_task = asyncio.create_task(_race_loop(room))

    elif msg_type == "move":
        if room.phase != "playing":
            return
        if player.done:
            return

        from_pos = data.get("from")
        to_pos = data.get("to")
        if not _valid_pos(from_pos) or not _valid_pos(to_pos):
            await _send_error(player, "Invalid position")
            return

        fr, fc = from_pos
        tr, tc = to_pos

        piece = player.board[fr][fc]
        if piece is None:
            await _send_error(player, "No piece at that position")
            return

        target = player.board[tr][tc]
        if target is None:
            await _send_error(player, "Must capture a piece")
            return

        valid = get_captures(player.board, (fr, fc))
        if (tr, tc) not in valid:
            await _send_error(player, f"{PIECE_NAME[piece]} can't reach there")
            return

        # Execute move
        captured = make_move(player.board, (fr, fc), (tr, tc))
        player.history.append((from_pos, to_pos, captured))
        player.move_count += 1

        if count_pieces(player.board) == 1:
            player.solved = True

        # Send board update to this player
        await _send(player.ws, {
            "type": "board_update",
            "board": player.board,
            "pieces_left": player.pieces_left,
            "move_count": player.move_count,
            "solved": player.solved,
            "last_move": {"from": from_pos, "to": to_pos, "piece": piece, "captured": captured},
        })

        # Broadcast progress to all
        await _broadcast_progress(room, player)

        if player.solved:
            room.round_solved.set()

    elif msg_type == "undo":
        if room.phase != "playing" or player.done:
            return
        if not player.history:
            await _send_error(player, "No moves to undo")
            return

        from_pos, to_pos, captured = player.history.pop()
        undo_move(player.board, (from_pos[0], from_pos[1]), (to_pos[0], to_pos[1]), captured)
        player.move_count -= 1

        await _send(player.ws, {
            "type": "board_update",
            "board": player.board,
            "pieces_left": player.pieces_left,
            "move_count": player.move_count,
            "solved": False,
        })
        await _broadcast_progress(room, player)

    elif msg_type == "hint":
        if room.phase != "playing" or player.done:
            return
        hint = get_hint(player.board)
        if hint is None:
            await _send(player.ws, {
                "type": "hint_result",
                "stuck": True,
                "message": "No solution from here! Use Undo.",
            })
        else:
            fr, to = hint
            piece = player.board[fr[0]][fr[1]]
            target = player.board[to[0]][to[1]]
            await _send(player.ws, {
                "type": "hint_result",
                "stuck": False,
                "from": list(fr),
                "to": list(to),
                "piece": piece,
                "target": target,
            })

    elif msg_type == "giveup":
        if room.phase != "playing" or player.done:
            return
        player.gave_up = True
        await _broadcast_progress(room, player)
        # Check if all done
        if all(p.done for p in room.players.values()):
            room.round_solved.set()

    elif msg_type == "ping":
        await _send(player.ws, {"type": "pong"})


def _valid_pos(pos: object) -> bool:
    return (
        isinstance(pos, list)
        and len(pos) == 2
        and all(isinstance(v, int) and 0 <= v < 4 for v in pos)
    )


# ── Race loop ────────────────────────────────────────────────────────────────


async def _race_loop(room: SolChessRoom) -> None:
    try:
        for rnd in range(1, MAX_ROUNDS + 1):
            num_pieces = DIFFICULTY_PIECES.get(room.difficulty, 5)
            board = generate_puzzle(num_pieces)
            if board is None:
                await _broadcast(room, {
                    "type": "error",
                    "message": "Failed to generate puzzle. Retrying...",
                })
                board = generate_puzzle(num_pieces, max_attempts=3000)
                if board is None:
                    break

            room.starting_board = board
            room.round_num = rnd
            room.round_solved.clear()
            room.phase = "playing"
            room.round_start_time = time.monotonic()

            # Give each player their own board copy
            for p in room.players.values():
                p.board = copy_board(board)
                p.history.clear()
                p.move_count = 0
                p.solved = False
                p.gave_up = False

            await _broadcast(room, {
                "type": "round_start",
                "round_num": rnd,
                "board": board,
                "difficulty": room.difficulty,
                "time_limit": ROUND_TIME,
            })

            # Wait for solve or timeout
            await _wait_for_round(room)

            # Determine round winner
            solvers = [p for p in room.players.values() if p.solved]
            winner = None
            if solvers:
                winner = min(solvers, key=lambda p: p.move_count)
                winner.rounds_won += 1

            room.total_rounds_played += 1
            scoreboard = _scoreboard(room)

            await _broadcast(room, {
                "type": "round_result",
                "round_num": rnd,
                "winner_id": winner.discord_user if winner else None,
                "winner_name": winner.display_name if winner else None,
                "moves": winner.move_count if winner else None,
                "scoreboard": scoreboard,
            })

            if any(p.rounds_won >= WINS_TO_WIN for p in room.players.values()):
                break
            if rnd >= MAX_ROUNDS:
                break

            room.phase = "between_rounds"
            await asyncio.sleep(ROUND_DELAY)

        await _end_game(room)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("_race_loop crashed in room %s", room.room_id)
        room.phase = "finished"


async def _wait_for_round(room: SolChessRoom) -> None:
    elapsed = 0
    while elapsed < ROUND_TIME:
        try:
            await asyncio.wait_for(room.round_solved.wait(), timeout=15)
            return
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - room.round_start_time
            remaining = max(0, ROUND_TIME - elapsed)
            await _broadcast(room, {"type": "timer", "remaining": int(remaining)})
            if remaining <= 0:
                return


async def _end_game(room: SolChessRoom) -> None:
    room.phase = "finished"
    n_players = len(room.players)
    prize_pool = sum(p.wager for p in room.players.values())

    max_wins = max((p.rounds_won for p in room.players.values()), default=0)

    if max_wins == 0:
        # Nobody won any round — refund all
        payouts = {uid: p.wager for uid, p in room.players.items()}
    elif n_players == 1:
        # Solo: house payout
        uid = list(room.players.keys())[0]
        p = room.players[uid]
        if p.rounds_won >= WINS_TO_WIN:
            payouts = {uid: p.wager * SOLO_PAYOUT}
        else:
            payouts = {uid: 0}
    else:
        # Multiplayer: winner takes pot (paytable)
        sorted_players = sorted(
            room.players.values(), key=lambda p: p.rounds_won, reverse=True,
        )
        winner = sorted_players[0]
        payouts = {uid: 0 for uid in room.players}
        payouts[winner.discord_user] = prize_pool

    results = []
    for uid, player in room.players.items():
        payout = payouts.get(uid, 0)
        if payout > 0:
            await queries.update_casino_balance(uid, payout)
        bal = await queries.get_casino_balance(uid) or 0
        await queries.log_casino_result(uid, "solitaire-chess", player.wager, payout)
        results.append({
            "discord_user": uid,
            "display_name": player.display_name,
            "rounds_won": player.rounds_won,
            "wager": player.wager,
            "payout": payout,
            "net": payout - player.wager,
            "balance": bal,
        })

    results.sort(key=lambda r: r["rounds_won"], reverse=True)
    room.result_data = {
        "status": "finished",
        "results": results,
        "total_rounds": room.total_rounds_played,
        "channel_id": room.channel_id,
    }

    await queries.finish_game_session(
        room.room_id, json.dumps(room.result_data), prize_pool,
    )
    await _broadcast(room, {"type": "game_over", "results": results})

    await asyncio.sleep(120)
    rooms.pop(room.room_id, None)


# ── Broadcast helpers ────────────────────────────────────────────────────────


async def _send(ws: WebSocket | None, msg: dict) -> None:
    if ws and ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _send_error(player: WebPlayer, message: str) -> None:
    await _send(player.ws, {"type": "error", "message": message})


async def _broadcast(room: SolChessRoom, msg: dict) -> None:
    for p in room.players.values():
        await _send(p.ws, msg)


async def _broadcast_room_state(room: SolChessRoom) -> None:
    for uid, p in room.players.items():
        await _send(p.ws, _room_state_msg(room, viewer=uid))


def _room_state_msg(room: SolChessRoom, viewer: str | None = None) -> dict:
    return {
        "type": "room_state",
        "room_id": room.room_id,
        "phase": room.phase,
        "difficulty": room.difficulty,
        "players": [
            {
                "id": p.discord_user,
                "name": p.display_name,
                "wager": p.wager,
                "rounds_won": p.rounds_won,
                "is_host": p.is_host,
                "connected": p.ws is not None,
            }
            for p in room.players.values()
        ],
        "prize_pool": sum(p.wager for p in room.players.values()),
        "round_num": room.round_num,
        "you": viewer,
    }


async def _broadcast_progress(room: SolChessRoom, player: WebPlayer) -> None:
    await _broadcast(room, {
        "type": "player_progress",
        "player_id": player.discord_user,
        "name": player.display_name,
        "pieces_left": player.pieces_left,
        "move_count": player.move_count,
        "solved": player.solved,
        "gave_up": player.gave_up,
    })


def _scoreboard(room: SolChessRoom) -> list[dict]:
    return sorted(
        [
            {"id": p.discord_user, "name": p.display_name, "rounds_won": p.rounds_won}
            for p in room.players.values()
        ],
        key=lambda x: x["rounds_won"],
        reverse=True,
    )


# ── Stale room cleanup ──────────────────────────────────────────────────────


async def cleanup_stale_solchess_rooms() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            now = time.time()
            stale = [
                rid for rid, room in rooms.items()
                if room.phase == "waiting" and (now - room.created_at) > ROOM_TTL
            ]
            for rid in stale:
                room = rooms.pop(rid, None)
                if room:
                    for p in room.players.values():
                        await queries.update_casino_balance(p.discord_user, p.wager)
                        await _send(p.ws, {
                            "type": "error",
                            "message": "Room expired. Your bet has been refunded.",
                        })
        except Exception:
            logger.exception("cleanup_stale_solchess_rooms error — loop continues")
