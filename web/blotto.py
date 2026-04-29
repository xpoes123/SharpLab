"""Colonel Blotto — web game engine, WebSocket handler, API router."""

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
from shared.blotto_logic import (
    SOLDIERS,
    BATTLEFIELDS,
    BATTLEFIELD_NAMES,
    WINS_TO_WIN,
    MAX_ROUNDS,
    ALLOCATION_TIME,
    REVEAL_DELAY,
    ROUND_DELAY,
    MIN_PLAYERS,
    MAX_PLAYERS,
    DEFAULT_ALLOCATION,
    validate_allocation,
    resolve_round,
    rank_players,
    compute_payouts,
    generate_weights,
)

WEB_API_SECRET = os.environ.get("WEB_API_SECRET", "dev-secret")
ROOM_TTL = 1800  # 30 minutes

# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class BlottoPlayer:
    discord_user: str
    display_name: str
    wager: int
    is_host: bool = False
    rounds_won: int = 0
    battlefields_won_total: int = 0
    current_allocation: list[int] | None = None
    locked_in: bool = False
    ws: WebSocket | None = None

    @property
    def user_id(self) -> str:
        return self.discord_user


@dataclass
class BlottoRoom:
    room_id: str
    host_id: str
    channel_id: str
    phase: str = "waiting"  # waiting | allocating | revealing | between_rounds | finished
    players: dict[str, BlottoPlayer] = field(default_factory=dict)
    round_num: int = 0
    total_rounds_played: int = 0
    all_locked: asyncio.Event = field(default_factory=asyncio.Event)
    game_task: asyncio.Task | None = field(default=None, repr=False)
    created_at: float = field(default_factory=time.time)
    result_data: dict | None = None
    current_weights: list[int] = field(default_factory=list)
    # {player_id: [soldiers]} from last round — sent at round_start so opponents can adapt
    last_round_allocations: dict[str, list[int]] = field(default_factory=dict)


rooms: dict[str, BlottoRoom] = {}

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

router = APIRouter(prefix="/api/v1/blotto", tags=["blotto"])


def _check_api_key(api_key: str) -> None:
    if api_key != WEB_API_SECRET:
        raise HTTPException(401, "Invalid API key")


@router.post("/rooms")
async def create_room(
    body: CreateRoomRequest, x_api_key: str = Header(),
):
    _check_api_key(x_api_key)
    room_id = secrets.token_hex(4)
    room = BlottoRoom(
        room_id=room_id,
        host_id=body.host_discord_id,
        channel_id=body.channel_id,
    )
    rooms[room_id] = room
    await queries.create_game_session(
        room_id, "blotto", body.host_discord_id, body.channel_id,
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
    room.players[body.discord_user] = BlottoPlayer(
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
    url = f"{base}/blotto/{room_id}?t={token}"
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
    room = BlottoRoom(room_id=room_id, host_id=user_id, channel_id="web")
    rooms[room_id] = room
    await queries.create_game_session(room_id, "blotto", user_id, "web")
    token = secrets.token_hex(16)
    room.players[user_id] = BlottoPlayer(
        discord_user=user_id, display_name=body.display_name, wager=0, is_host=True,
    )
    await queries.create_game_token(token, room_id, user_id, body.display_name, 0)
    base = os.environ.get("WEB_BASE_URL", "https://sharplab.djiang.xyz")
    return {"room_id": room_id, "token": token, "url": f"{base}/blotto/{room_id}?t={token}"}


@router.post("/public/join/{room_id}")
async def public_join_room(room_id: str, body: PublicJoinRequest):
    room = rooms.get(room_id)
    if not room or room.phase != "waiting":
        raise HTTPException(404, "Room not found or not accepting players")
    if len(room.players) >= MAX_PLAYERS:
        raise HTTPException(400, "Room is full")
    user_id = f"web_{secrets.token_hex(6)}"
    token = secrets.token_hex(16)
    room.players[user_id] = BlottoPlayer(
        discord_user=user_id, display_name=body.display_name, wager=0,
    )
    await queries.create_game_token(token, room_id, user_id, body.display_name, 0)
    await _broadcast_room_state(room)
    base = os.environ.get("WEB_BASE_URL", "https://sharplab.djiang.xyz")
    return {"token": token, "url": f"{base}/blotto/{room_id}?t={token}"}


# ── WebSocket handler ────────────────────────────────────────────────────────


async def blotto_websocket(websocket: WebSocket, room_id: str):
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


async def _handle_message(room: BlottoRoom, player: BlottoPlayer, data: dict) -> None:
    msg_type = data.get("type")

    if msg_type == "start":
        if player.discord_user != room.host_id:
            await _send_error(player, "Only the host can start")
            return
        if room.phase != "waiting":
            await _send_error(player, "Game already started")
            return
        if len(room.players) < MIN_PLAYERS:
            await _send_error(player, f"Need at least {MIN_PLAYERS} players")
            return
        room.game_task = asyncio.create_task(_game_loop(room))

    elif msg_type == "allocate":
        if room.phase != "allocating":
            return
        if player.locked_in:
            await _send_error(player, "Already locked in")
            return
        soldiers = data.get("soldiers")
        if not isinstance(soldiers, list):
            await _send_error(player, "Invalid allocation format")
            return
        # Coerce to int
        try:
            soldiers = [int(s) for s in soldiers]
        except (ValueError, TypeError):
            await _send_error(player, "Allocations must be integers")
            return
        err = validate_allocation(soldiers)
        if err:
            await _send_error(player, err)
            return
        player.current_allocation = soldiers
        await _send(player.ws, {
            "type": "allocation_accepted",
            "soldiers": soldiers,
            "total": sum(soldiers),
        })

    elif msg_type == "lock_in":
        if room.phase != "allocating":
            return
        if player.locked_in:
            return
        if player.current_allocation is None:
            await _send_error(player, "Submit an allocation first")
            return
        player.locked_in = True
        await _broadcast(room, {
            "type": "player_locked",
            "player_id": player.discord_user,
            "player_name": player.display_name,
        })
        # Check if all players locked in
        if all(p.locked_in for p in room.players.values()):
            room.all_locked.set()

    elif msg_type == "ping":
        await _send(player.ws, {"type": "pong"})


# ── Game loop ────────────────────────────────────────────────────────────────


async def _game_loop(room: BlottoRoom) -> None:
    try:
        for rnd in range(1, MAX_ROUNDS + 1):
            room.round_num = rnd
            room.all_locked.clear()
            room.phase = "allocating"

            # Generate random battlefield weights for this round
            room.current_weights = generate_weights()

            # Reset per-round state
            for p in room.players.values():
                p.current_allocation = None
                p.locked_in = False

            scoreboard = _scoreboard(room)

            # Build opponent history: what everyone played last round
            history = {
                uid: {
                    "name": room.players[uid].display_name,
                    "soldiers": alloc,
                }
                for uid, alloc in room.last_round_allocations.items()
                if uid in room.players
            } if room.last_round_allocations else {}

            await _broadcast(room, {
                "type": "round_start",
                "round_num": rnd,
                "soldiers": SOLDIERS,
                "battlefields": BATTLEFIELDS,
                "battlefield_names": BATTLEFIELD_NAMES,
                "weights": room.current_weights,
                "time_limit": ALLOCATION_TIME,
                "scoreboard": scoreboard,
                "last_round": history,
            })

            # Wait for all players to lock in or timeout
            await _wait_for_allocations(room)

            # Auto-submit default allocation for anyone who didn't lock in
            for p in room.players.values():
                if p.current_allocation is None:
                    p.current_allocation = list(DEFAULT_ALLOCATION)
                p.locked_in = True

            # Resolve the round with weights
            all_allocations = {
                uid: p.current_allocation
                for uid, p in room.players.items()
            }
            result = resolve_round(all_allocations, room.current_weights)

            # Save allocations for next round's history
            room.last_round_allocations = {
                uid: list(alloc) for uid, alloc in all_allocations.items()
            }

            # Reveal phase — one battlefield at a time
            room.phase = "revealing"
            for bf_idx in range(BATTLEFIELDS):
                bf_allocations = {
                    uid: alloc[bf_idx]
                    for uid, alloc in all_allocations.items()
                }
                winner_id = result["battlefield_winners"][bf_idx]
                winner_name = (
                    room.players[winner_id].display_name
                    if winner_id else None
                )
                await _broadcast(room, {
                    "type": "reveal_battlefield",
                    "battlefield": bf_idx,
                    "battlefield_name": BATTLEFIELD_NAMES[bf_idx],
                    "weight": room.current_weights[bf_idx],
                    "allocations": {
                        uid: {
                            "soldiers": count,
                            "name": room.players[uid].display_name,
                        }
                        for uid, count in bf_allocations.items()
                    },
                    "winner_id": winner_id,
                    "winner_name": winner_name,
                })
                await asyncio.sleep(REVEAL_DELAY)

            # Update scores
            round_winner = result["round_winner"]
            if round_winner:
                room.players[round_winner].rounds_won += 1
            for uid, count in result["battlefields_won"].items():
                room.players[uid].battlefields_won_total += count

            room.total_rounds_played += 1
            scoreboard = _scoreboard(room)

            await _broadcast(room, {
                "type": "round_result",
                "round_num": rnd,
                "battlefields_won": result["battlefields_won"],
                "points_won": result["points_won"],
                "round_winner_id": round_winner,
                "round_winner_name": (
                    room.players[round_winner].display_name
                    if round_winner else None
                ),
                "scoreboard": scoreboard,
            })

            # Check for match winner
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


async def _wait_for_allocations(room: BlottoRoom) -> None:
    """Wait for all players to lock in or timeout, with periodic timer broadcasts."""
    elapsed = 0
    start = time.monotonic()
    while elapsed < ALLOCATION_TIME:
        try:
            await asyncio.wait_for(
                room.all_locked.wait(), timeout=15,
            )
            return  # All locked in
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            remaining = max(0, ALLOCATION_TIME - elapsed)
            await _broadcast(room, {
                "type": "timer",
                "remaining": int(remaining),
            })
            if remaining <= 0:
                return


async def _end_game(room: BlottoRoom) -> None:
    room.phase = "finished"
    n_players = len(room.players)
    prize_pool = sum(p.wager for p in room.players.values())

    # Build ranked player data
    player_data = [
        {
            "user_id": uid,
            "display_name": p.display_name,
            "rounds_won": p.rounds_won,
            "battlefields_won_total": p.battlefields_won_total,
            "wager": p.wager,
        }
        for uid, p in room.players.items()
    ]
    player_data = rank_players(player_data)

    # Compute payouts
    max_wins = max((p["rounds_won"] for p in player_data), default=0)
    if max_wins == 0:
        payouts = {p["user_id"]: p["wager"] for p in player_data}
    else:
        payouts = compute_payouts(player_data, prize_pool, n_players)

    results = []
    for p in player_data:
        uid = p["user_id"]
        payout = payouts.get(uid, 0)
        if payout > 0:
            await queries.update_casino_balance(uid, payout)
        bal = await queries.get_casino_balance(uid) or 0
        await queries.log_casino_result(uid, "blotto", p["wager"], payout)
        results.append({
            "discord_user": uid,
            "display_name": p["display_name"],
            "rounds_won": p["rounds_won"],
            "battlefields_won_total": p["battlefields_won_total"],
            "wager": p["wager"],
            "payout": payout,
            "net": payout - p["wager"],
            "balance": bal,
        })

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


async def _send_error(player: BlottoPlayer, message: str) -> None:
    await _send(player.ws, {"type": "error", "message": message})


async def _broadcast(room: BlottoRoom, msg: dict) -> None:
    for p in room.players.values():
        await _send(p.ws, msg)


async def _broadcast_room_state(room: BlottoRoom) -> None:
    for uid, p in room.players.items():
        await _send(p.ws, _room_state_msg(room, viewer=uid))


def _room_state_msg(room: BlottoRoom, viewer: str | None = None) -> dict:
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
                "battlefields_won_total": p.battlefields_won_total,
                "is_host": p.is_host,
                "connected": p.ws is not None,
                "locked_in": p.locked_in,
            }
            for p in room.players.values()
        ],
        "prize_pool": sum(p.wager for p in room.players.values()),
        "round_num": room.round_num,
        "you": viewer,
    }


def _scoreboard(room: BlottoRoom) -> list[dict]:
    return sorted(
        [
            {
                "id": p.discord_user,
                "name": p.display_name,
                "rounds_won": p.rounds_won,
                "battlefields_won_total": p.battlefields_won_total,
            }
            for p in room.players.values()
        ],
        key=lambda x: (x["rounds_won"], x["battlefields_won_total"]),
        reverse=True,
    )


# ── Stale room cleanup ──────────────────────────────────────────────────────


async def cleanup_stale_blotto_rooms() -> None:
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
