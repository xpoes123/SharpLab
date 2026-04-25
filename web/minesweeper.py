"""Minesweeper Race — web game engine, WebSocket handler, API router."""

import asyncio
import json
import os
import secrets
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from db import queries
from shared.minesweeper_logic import (
    ROWS, COLS, MINES, SAFE_CELLS,
    WINS_TO_WIN, MAX_ROUNDS, ROUND_TIME, ROUND_DELAY,
    generate_board, flood_fill, compute_payouts,
)

WEB_API_SECRET = os.environ.get("WEB_API_SECRET", "dev-secret")
ROOM_TTL = 1800  # 30 minutes

# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class WebPlayer:
    discord_user: str
    display_name: str
    wager: int
    is_host: bool = False
    rounds_won: int = 0
    revealed: set[tuple[int, int]] = field(default_factory=set)
    flags: set[tuple[int, int]] = field(default_factory=set)
    alive: bool = True
    solve_time: float = 0.0
    ws: WebSocket | None = None


@dataclass
class MinesweeperRoom:
    room_id: str
    host_id: str
    channel_id: str
    phase: str = "waiting"
    players: dict[str, WebPlayer] = field(default_factory=dict)
    round_num: int = 0
    grid: list[list[int]] = field(default_factory=list)
    mine_set: set[tuple[int, int]] = field(default_factory=set)
    round_start_time: float = 0.0
    round_won: asyncio.Event = field(default_factory=asyncio.Event)
    race_task: asyncio.Task | None = field(default=None, repr=False)
    total_rounds_played: int = 0
    created_at: float = field(default_factory=time.time)
    result_data: dict | None = None


rooms: dict[str, MinesweeperRoom] = {}

# ── Request models ───────────────────────────────────────────────────────────


class CreateRoomRequest(BaseModel):
    host_discord_id: str
    channel_id: str
    host_display_name: str


class CreateTokenRequest(BaseModel):
    discord_user: str
    display_name: str
    wager: int


# ── API router ───────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/v1/minesweeper", tags=["minesweeper"])


def _check_api_key(api_key: str) -> None:
    if api_key != WEB_API_SECRET:
        raise HTTPException(401, "Invalid API key")


@router.post("/rooms")
async def create_room(
    body: CreateRoomRequest, x_api_key: str = Header(),
):
    _check_api_key(x_api_key)
    room_id = secrets.token_hex(4)
    room = MinesweeperRoom(
        room_id=room_id,
        host_id=body.host_discord_id,
        channel_id=body.channel_id,
    )
    rooms[room_id] = room
    await queries.create_game_session(
        room_id, "minesweeper", body.host_discord_id, body.channel_id,
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
    base = os.environ.get("WEB_BASE_URL", "https://djiang.xyz")
    url = f"{base}/minesweeper/{room_id}?t={token}"
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


# ── WebSocket handler ────────────────────────────────────────────────────────


async def minesweeper_websocket(websocket: WebSocket, room_id: str):
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


async def _handle_message(room: MinesweeperRoom, player: WebPlayer, data: dict) -> None:
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

    elif msg_type == "reveal":
        if room.phase != "playing":
            return
        if not player.alive:
            return
        r = data.get("r")
        c = data.get("c")
        if not isinstance(r, int) or not isinstance(c, int):
            return
        if r < 0 or r >= ROWS or c < 0 or c >= COLS:
            return
        if (r, c) in player.revealed:
            return

        # Check for mine
        if (r, c) in room.mine_set:
            player.alive = False
            await _send(player.ws, {
                "type": "mine_hit",
                "r": r,
                "c": c,
                "mines": [[mr, mc] for mr, mc in room.mine_set],
            })
            await _broadcast_progress(room)
            # Check if all players eliminated
            if not any(p.alive for p in room.players.values()):
                room.round_won.set()
            return

        # Flood fill reveal
        newly_revealed = flood_fill(room.grid, player.revealed, r, c)
        if newly_revealed:
            await _send(player.ws, {
                "type": "reveal_result",
                "cells": [{"r": cr, "c": cc, "value": v} for cr, cc, v in newly_revealed],
            })
            await _broadcast_progress(room)

            # Check win
            if len(player.revealed) >= SAFE_CELLS:
                player.solve_time = time.monotonic() - room.round_start_time
                room.round_won.set()

    elif msg_type == "flag":
        if room.phase != "playing":
            return
        if not player.alive:
            return
        r = data.get("r")
        c = data.get("c")
        if not isinstance(r, int) or not isinstance(c, int):
            return
        if r < 0 or r >= ROWS or c < 0 or c >= COLS:
            return
        if (r, c) in player.revealed:
            return
        # Toggle flag
        if (r, c) in player.flags:
            player.flags.discard((r, c))
        else:
            player.flags.add((r, c))
        await _send(player.ws, {
            "type": "flag_result",
            "r": r,
            "c": c,
            "flagged": (r, c) in player.flags,
        })

    elif msg_type == "ping":
        await _send(player.ws, {"type": "pong"})


# ── Race loop ────────────────────────────────────────────────────────────────


async def _race_loop(room: MinesweeperRoom) -> None:
    try:
        for rnd in range(1, MAX_ROUNDS + 1):
            grid, mine_set = generate_board()
            room.grid = grid
            room.mine_set = mine_set
            room.round_num = rnd
            room.round_won.clear()
            room.phase = "playing"
            room.round_start_time = time.monotonic()

            for p in room.players.values():
                p.revealed = set()
                p.flags = set()
                p.alive = True
                p.solve_time = 0.0

            await _broadcast(room, {
                "type": "round_start",
                "round_num": rnd,
                "rows": ROWS,
                "cols": COLS,
                "mines": MINES,
                "time_limit": ROUND_TIME,
            })

            # Wait for someone to clear or timeout
            await _wait_for_round(room)

            # Determine winner
            clearers = [
                p for p in room.players.values()
                if p.alive and len(p.revealed) >= SAFE_CELLS
            ]
            winner = None
            if clearers:
                winner = min(clearers, key=lambda p: p.solve_time)
                winner.rounds_won += 1

            room.total_rounds_played += 1

            scoreboard = _scoreboard(room)
            await _broadcast(room, {
                "type": "round_result",
                "round_num": rnd,
                "winner_id": winner.discord_user if winner else None,
                "winner_name": winner.display_name if winner else None,
                "solve_time": round(winner.solve_time, 1) if winner else None,
                "mines": [[mr, mc] for mr, mc in room.mine_set],
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
        room.phase = "finished"


async def _wait_for_round(room: MinesweeperRoom) -> None:
    elapsed = 0
    while elapsed < ROUND_TIME:
        try:
            await asyncio.wait_for(
                room.round_won.wait(), timeout=15,
            )
            return
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - room.round_start_time
            remaining = max(0, ROUND_TIME - elapsed)
            await _broadcast(room, {
                "type": "timer",
                "remaining": int(remaining),
            })
            if remaining <= 0:
                return


async def _end_game(room: MinesweeperRoom) -> None:
    room.phase = "finished"
    n_players = len(room.players)
    prize_pool = sum(p.wager for p in room.players.values())

    max_wins = max((p.rounds_won for p in room.players.values()), default=0)
    if max_wins == 0:
        payouts = {uid: p.wager for uid, p in room.players.items()}
    else:
        player_data = {
            uid: {"rounds_won": p.rounds_won, "wager": p.wager}
            for uid, p in room.players.items()
        }
        payouts = compute_payouts(player_data, n_players)

    results = []
    for uid, player in room.players.items():
        payout = payouts.get(uid, 0)
        if payout > 0:
            await queries.update_casino_balance(uid, payout)
        bal = await queries.get_casino_balance(uid) or 0
        await queries.log_casino_result(uid, "minesweeper", player.wager, payout)
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


async def _broadcast(room: MinesweeperRoom, msg: dict) -> None:
    for p in room.players.values():
        await _send(p.ws, msg)


async def _broadcast_room_state(room: MinesweeperRoom) -> None:
    for uid, p in room.players.items():
        await _send(p.ws, _room_state_msg(room, viewer=uid))


async def _broadcast_progress(room: MinesweeperRoom) -> None:
    msg = {
        "type": "player_progress",
        "players": [
            {
                "id": p.discord_user,
                "name": p.display_name,
                "revealed": len(p.revealed),
                "total": SAFE_CELLS,
                "alive": p.alive,
            }
            for p in room.players.values()
        ],
    }
    await _broadcast(room, msg)


def _room_state_msg(room: MinesweeperRoom, viewer: str | None = None) -> dict:
    return {
        "type": "room_state",
        "room_id": room.room_id,
        "phase": room.phase,
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


def _scoreboard(room: MinesweeperRoom) -> list[dict]:
    return sorted(
        [
            {"id": p.discord_user, "name": p.display_name, "rounds_won": p.rounds_won}
            for p in room.players.values()
        ],
        key=lambda x: x["rounds_won"],
        reverse=True,
    )


# ── Stale room cleanup ──────────────────────────────────────────────────────


async def cleanup_stale_minesweeper_rooms() -> None:
    """Background task: refund and remove rooms that sat idle too long."""
    while True:
        await asyncio.sleep(60)
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
