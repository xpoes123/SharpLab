"""Web Bingo — game engine, WebSocket handler, API router."""

import asyncio
import json
import os
import random
import secrets
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from db import queries
from bot.cogs._pool import compute_side_pot_payouts
from shared.bingo_logic import (
    BINGO_PATTERNS, BINGO_LETTERS, BINGO_RANGES,
    MAX_PLAYERS, MAX_CARDS, CARD_PRICE, CALL_INTERVAL,
    generate_card, mark_card, number_to_bingo, pick_pattern,
)

WEB_API_SECRET = os.environ.get("WEB_API_SECRET", "dev-secret")
ROOM_TTL = 1800

# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class WebBingoCard:
    grid: list[list[int]]
    marked: list[list[bool]]


@dataclass
class WebBingoPlayer:
    discord_user: str
    display_name: str
    num_cards: int
    wager: int  # = num_cards * CARD_PRICE
    is_host: bool = False
    cards: list[WebBingoCard] = field(default_factory=list)
    won: bool = False
    winning_card: int = -1
    ws: WebSocket | None = None


@dataclass
class BingoRoom:
    room_id: str
    host_id: str
    channel_id: str
    phase: str = "waiting"
    players: dict[str, WebBingoPlayer] = field(default_factory=dict)
    pattern_idx: int = -1
    pattern_name: str = ""
    pattern_target: list[list[bool]] = field(default_factory=list)
    called_numbers: list[int] = field(default_factory=list)
    called_set: set[int] = field(default_factory=set)
    pool: list[int] = field(default_factory=list)
    winners: list[str] = field(default_factory=list)
    call_task: asyncio.Task | None = field(default=None, repr=False)
    result_data: dict | None = None
    created_at: float = field(default_factory=time.time)


rooms: dict[str, BingoRoom] = {}

# ── Request models ───────────────────────────────────────────────────────────


class CreateRoomRequest(BaseModel):
    host_discord_id: str
    channel_id: str
    host_display_name: str


class CreateTokenRequest(BaseModel):
    discord_user: str
    display_name: str
    num_cards: int


# ── API router ───────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/v1/bingo", tags=["bingo"])


def _check_api_key(api_key: str) -> None:
    if api_key != WEB_API_SECRET:
        raise HTTPException(401, "Invalid API key")


@router.post("/rooms")
async def create_room(body: CreateRoomRequest, x_api_key: str = Header()):
    _check_api_key(x_api_key)
    room_id = secrets.token_hex(4)
    pattern, idx = pick_pattern()
    room = BingoRoom(
        room_id=room_id, host_id=body.host_discord_id, channel_id=body.channel_id,
        pattern_idx=idx, pattern_name=pattern.name, pattern_target=pattern.target,
    )
    rooms[room_id] = room
    await queries.create_game_session(room_id, "bingo", body.host_discord_id, body.channel_id)
    return {"room_id": room_id, "pattern": pattern.name}


@router.post("/rooms/{room_id}/tokens")
async def create_token(room_id: str, body: CreateTokenRequest, x_api_key: str = Header()):
    _check_api_key(x_api_key)
    room = rooms.get(room_id)
    if not room or room.phase != "waiting":
        raise HTTPException(404, "Room not found or not accepting players")
    if body.discord_user in room.players:
        raise HTTPException(409, "Already in this room")
    if len(room.players) >= MAX_PLAYERS:
        raise HTTPException(400, "Room is full")
    if body.num_cards < 1 or body.num_cards > MAX_CARDS:
        raise HTTPException(400, f"Cards must be 1-{MAX_CARDS}")

    token = secrets.token_hex(16)
    wager = body.num_cards * CARD_PRICE
    is_host = body.discord_user == room.host_id

    # Generate cards
    cards = []
    for _ in range(body.num_cards):
        grid, marked = generate_card()
        cards.append(WebBingoCard(grid=grid, marked=marked))

    room.players[body.discord_user] = WebBingoPlayer(
        discord_user=body.discord_user,
        display_name=body.display_name,
        num_cards=body.num_cards,
        wager=wager,
        is_host=is_host,
        cards=cards,
    )
    await queries.create_game_token(token, room_id, body.discord_user, body.display_name, wager)
    await _broadcast_room_state(room)
    base = os.environ.get("WEB_BASE_URL", "https://sharplab.djiang.xyz")
    return {"token": token, "url": f"{base}/bingo/{room_id}?t={token}"}


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


async def bingo_websocket(websocket: WebSocket, room_id: str):
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

    # If game is in progress, send current cards
    if room.phase == "calling":
        await _send(websocket, _cards_msg(player, room))

    try:
        while True:
            data = await websocket.receive_json()
            await _handle_message(room, player, data)
    except WebSocketDisconnect:
        player.ws = None
    except Exception:
        player.ws = None


async def _handle_message(room: BingoRoom, player: WebBingoPlayer, data: dict) -> None:
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
        room.call_task = asyncio.create_task(_call_loop(room))

    elif msg_type == "ping":
        await _send(player.ws, {"type": "pong"})


# ── Call loop ────────────────────────────────────────────────────────────────


async def _call_loop(room: BingoRoom) -> None:
    try:
        room.phase = "calling"
        room.pool = list(range(1, 76))
        random.shuffle(room.pool)
        room.called_numbers.clear()
        room.called_set.clear()

        # Find the pattern object
        pattern = BINGO_PATTERNS[room.pattern_idx]

        # Send cards to each player
        for uid, player in room.players.items():
            await _send(player.ws, _cards_msg(player, room))

        await _broadcast(room, {
            "type": "game_start",
            "pattern": room.pattern_name,
            "pattern_target": room.pattern_target,
        })

        while room.pool:
            number = room.pool.pop()
            room.called_numbers.append(number)
            room.called_set.add(number)

            # Auto-mark all player cards
            for player in room.players.values():
                for card in player.cards:
                    mark_card(card.grid, card.marked, number)

            letter = number_to_bingo(number)

            # Check for winners
            round_winners = []
            for uid, player in room.players.items():
                if player.won:
                    continue
                for i, card in enumerate(player.cards):
                    if pattern.check_card(card.marked):
                        player.won = True
                        player.winning_card = i
                        round_winners.append(uid)
                        break

            # Broadcast the called number + progress
            progress = {}
            for uid, player in room.players.items():
                best = (0, 1)
                for card in player.cards:
                    m, t = pattern.progress(card.marked)
                    if m > best[0]:
                        best = (m, t)
                progress[uid] = {"marked": best[0], "total": best[1], "name": player.display_name}

            await _broadcast(room, {
                "type": "number_called",
                "number": number,
                "letter": letter,
                "called_count": len(room.called_numbers),
                "progress": progress,
            })

            # Send updated cards to each player
            for uid, player in room.players.items():
                await _send(player.ws, _cards_msg(player, room))

            if round_winners:
                room.winners = round_winners
                break

            await asyncio.sleep(CALL_INTERVAL)

        await _end_game(room)
    except asyncio.CancelledError:
        pass
    except Exception:
        room.phase = "finished"


async def _end_game(room: BingoRoom) -> None:
    room.phase = "finished"
    prize_pool = sum(p.wager for p in room.players.values())

    if not room.winners:
        # No winner — refund all
        payouts = {uid: p.wager for uid, p in room.players.items()}
    else:
        bets = {uid: p.wager for uid, p in room.players.items()}
        payouts = compute_side_pot_payouts(bets, room.winners)

    results = []
    for uid, player in room.players.items():
        payout = payouts.get(uid, 0)
        if payout > 0:
            await queries.update_casino_balance(uid, payout)
        bal = await queries.get_casino_balance(uid) or 0
        await queries.log_casino_result(uid, "bingo", player.wager, payout)
        results.append({
            "discord_user": uid,
            "display_name": player.display_name,
            "num_cards": player.num_cards,
            "wager": player.wager,
            "payout": payout,
            "net": payout - player.wager,
            "balance": bal,
            "is_winner": uid in room.winners,
        })

    results.sort(key=lambda r: (not r["is_winner"], -r["payout"]))
    room.result_data = {
        "status": "finished",
        "results": results,
        "pattern": room.pattern_name,
        "numbers_called": len(room.called_numbers),
        "channel_id": room.channel_id,
        "winners": [room.players[w].display_name for w in room.winners],
    }

    await queries.finish_game_session(room.room_id, json.dumps(room.result_data), prize_pool)
    await _broadcast(room, {"type": "game_over", "results": results, "pattern": room.pattern_name,
                            "numbers_called": len(room.called_numbers)})

    await asyncio.sleep(120)
    rooms.pop(room.room_id, None)


# ── Broadcast helpers ────────────────────────────────────────────────────────


async def _send(ws: WebSocket | None, msg: dict) -> None:
    if ws and ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _send_error(player: WebBingoPlayer, message: str) -> None:
    await _send(player.ws, {"type": "error", "message": message})


async def _broadcast(room: BingoRoom, msg: dict) -> None:
    for p in room.players.values():
        await _send(p.ws, msg)


async def _broadcast_room_state(room: BingoRoom) -> None:
    """Send personalized room_state to each player (with correct 'you' field)."""
    for uid, p in room.players.items():
        await _send(p.ws, _room_state_msg(room, viewer=uid))


def _room_state_msg(room: BingoRoom, viewer: str | None = None) -> dict:
    return {
        "type": "room_state",
        "room_id": room.room_id,
        "phase": room.phase,
        "pattern": room.pattern_name,
        "pattern_target": room.pattern_target,
        "players": [
            {
                "id": p.discord_user,
                "name": p.display_name,
                "num_cards": p.num_cards,
                "wager": p.wager,
                "is_host": p.is_host,
                "connected": p.ws is not None,
            }
            for p in room.players.values()
        ],
        "prize_pool": sum(p.wager for p in room.players.values()),
        "you": viewer,
    }


def _cards_msg(player: WebBingoPlayer, room: BingoRoom) -> dict:
    return {
        "type": "cards",
        "cards": [
            {"grid": c.grid, "marked": c.marked}
            for c in player.cards
        ],
        "pattern_target": room.pattern_target,
        "called_set": list(room.called_set),
    }


async def cleanup_stale_bingo_rooms() -> None:
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
                    await _send(p.ws, {"type": "error", "message": "Room expired. Bet refunded."})
