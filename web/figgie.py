"""Web Figgie — trading card game engine, WebSocket handler, API router."""

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
from bot.cogs._pool import compute_side_pot_payouts
from shared.figgie_logic import (
    SUITS, SUIT_ALIASES, SUIT_NAMES, SUIT_COLORS,
    MIN_PLAYERS, MAX_PLAYERS, NUM_ROUNDS, ROUND_SECONDS, STARTING_CHIPS, GOAL_VALUE,
    setup_deck, deal_cards, try_match, clean_stale_orders, score_player,
)

WEB_API_SECRET = os.environ.get("WEB_API_SECRET", "dev-secret")
ROOM_TTL = 1800
ROUND_DELAY = 3

# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class WebFiggiePlayer:
    discord_user: str
    display_name: str
    wager: int
    is_host: bool = False
    hand: dict[str, int] = field(default_factory=lambda: {s: 0 for s in SUITS})
    chips: int = STARTING_CHIPS
    ws: WebSocket | None = None


@dataclass
class FiggieRoom:
    room_id: str
    host_id: str
    channel_id: str
    phase: str = "waiting"
    players: dict[str, WebFiggiePlayer] = field(default_factory=dict)
    goal_suit: str = ""
    common_suit: str = ""
    suit_counts: dict[str, int] = field(default_factory=dict)
    round_num: int = 0
    orders: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    trade_open: bool = False
    round_end: float = 0.0
    hidden_cards: int = 0
    game_task: asyncio.Task | None = field(default=None, repr=False)
    result_data: dict | None = None
    created_at: float = field(default_factory=time.time)


rooms: dict[str, FiggieRoom] = {}

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

router = APIRouter(prefix="/api/v1/figgie", tags=["figgie"])


def _check_api_key(api_key: str) -> None:
    if api_key != WEB_API_SECRET:
        raise HTTPException(401, "Invalid API key")


@router.post("/rooms")
async def create_room(body: CreateRoomRequest, x_api_key: str = Header()):
    _check_api_key(x_api_key)
    room_id = secrets.token_hex(4)
    room = FiggieRoom(room_id=room_id, host_id=body.host_discord_id, channel_id=body.channel_id)
    rooms[room_id] = room
    await queries.create_game_session(room_id, "figgie", body.host_discord_id, body.channel_id)
    return {"room_id": room_id}


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

    token = secrets.token_hex(16)
    is_host = body.discord_user == room.host_id
    room.players[body.discord_user] = WebFiggiePlayer(
        discord_user=body.discord_user,
        display_name=body.display_name,
        wager=body.wager,
        is_host=is_host,
    )
    await queries.create_game_token(token, room_id, body.discord_user, body.display_name, body.wager)
    await _broadcast(room, _room_state_msg(room))
    base = os.environ.get("WEB_BASE_URL", "https://djiang.xyz")
    return {"token": token, "url": f"{base}/figgie/{room_id}?t={token}"}


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


async def figgie_websocket(websocket: WebSocket, room_id: str):
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

    await _send(websocket, _room_state_msg(room, viewer=discord_user))
    await _broadcast(room, _room_state_msg(room))

    # If game is in progress, send current hand to reconnecting player
    if room.phase == "playing":
        await _send(websocket, _hand_msg(player, room))

    try:
        while True:
            data = await websocket.receive_json()
            await _handle_message(room, player, data)
    except WebSocketDisconnect:
        player.ws = None
    except Exception:
        player.ws = None


async def _handle_message(room: FiggieRoom, player: WebFiggiePlayer, data: dict) -> None:
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

    elif msg_type == "order":
        if not room.trade_open:
            await _send_error(player, "Trading is closed")
            return
        action = data.get("action")
        suit_raw = data.get("suit", "").lower()
        suit = SUIT_ALIASES.get(suit_raw)
        price = data.get("price")

        if action not in ("buy", "sell"):
            await _send_error(player, "Action must be buy or sell")
            return
        if not suit:
            await _send_error(player, "Invalid suit")
            return
        if not isinstance(price, int) or price < 1 or price > 200:
            await _send_error(player, "Price must be 1-200")
            return
        if action == "buy" and player.chips < price:
            await _send_error(player, "Not enough chips")
            return
        if action == "sell" and player.hand.get(suit, 0) < 1:
            await _send_error(player, "You don't have that card")
            return

        # Try to match
        trade = try_match(
            action, suit, price, player.discord_user, player.display_name,
            room.orders, room.players,
        )
        if trade:
            room.trades.append(trade)
            room.orders = clean_stale_orders(room.orders, room.players)
            await _broadcast(room, {"type": "trade_executed", **trade})
            # Send updated hands to buyer and seller
            buyer = room.players.get(trade["buyer_id"])
            seller = room.players.get(trade["seller_id"])
            if buyer:
                await _send(buyer.ws, _hand_msg(buyer, room))
            if seller:
                await _send(seller.ws, _hand_msg(seller, room))
            await _broadcast(room, _market_state_msg(room))
        else:
            order = {
                "player_id": player.discord_user,
                "player_name": player.display_name,
                "action": action,
                "suit": suit,
                "price": price,
            }
            room.orders.append(order)
            await _broadcast(room, {"type": "order_posted", **order})
            await _broadcast(room, _market_state_msg(room))

    elif msg_type == "cancel_orders":
        room.orders = [o for o in room.orders if o["player_id"] != player.discord_user]
        await _broadcast(room, _market_state_msg(room))
        await _send_error(player, "Orders cancelled")  # feedback

    elif msg_type == "hand":
        if room.phase == "playing":
            await _send(player.ws, _hand_msg(player, room))


# ── Game loop ────────────────────────────────────────────────────────────────


async def _game_loop(room: FiggieRoom) -> None:
    try:
        # Setup
        room.phase = "playing"
        goal, common, counts = setup_deck()
        room.goal_suit = goal
        room.common_suit = common
        room.suit_counts = counts

        player_ids = list(room.players.keys())
        hands, hidden = deal_cards(counts, player_ids)
        room.hidden_cards = hidden

        for uid, hand in hands.items():
            room.players[uid].hand = hand
            room.players[uid].chips = STARTING_CHIPS

        room.orders.clear()
        room.trades.clear()

        # Send private hands to each player
        for uid, player in room.players.items():
            await _send(player.ws, _hand_msg(player, room))

        await _broadcast(room, {
            "type": "game_start",
            "num_rounds": NUM_ROUNDS,
            "round_seconds": ROUND_SECONDS,
            "hidden_cards": hidden,
            "player_chips": {uid: STARTING_CHIPS for uid in room.players},
        })

        # Trading rounds
        for rnd in range(1, NUM_ROUNDS + 1):
            room.round_num = rnd
            room.orders.clear()
            room.trade_open = True
            room.round_end = time.monotonic() + ROUND_SECONDS

            await _broadcast(room, {
                "type": "round_start",
                "round_num": rnd,
                "time_limit": ROUND_SECONDS,
            })

            # Timer loop
            elapsed = 0
            while elapsed < ROUND_SECONDS:
                await asyncio.sleep(min(10, ROUND_SECONDS - elapsed))
                elapsed = time.monotonic() - (room.round_end - ROUND_SECONDS)
                remaining = max(0, int(room.round_end - time.monotonic()))
                await _broadcast(room, {"type": "timer", "remaining": remaining})

            room.trade_open = False
            await _broadcast(room, {"type": "round_end", "round_num": rnd})

            if rnd < NUM_ROUNDS:
                await asyncio.sleep(ROUND_DELAY)

        await _end_game(room)
    except asyncio.CancelledError:
        pass
    except Exception:
        room.phase = "finished"


async def _end_game(room: FiggieRoom) -> None:
    room.phase = "finished"
    prize_pool = sum(p.wager for p in room.players.values())

    # Score each player
    scores = {}
    for uid, p in room.players.items():
        scores[uid] = score_player(p, room.goal_suit)

    max_score = max(scores.values()) if scores else 0
    winner_ids = [uid for uid, s in scores.items() if s == max_score]

    # Compute payouts using side-pot logic
    bets = {uid: p.wager for uid, p in room.players.items()}
    payouts = compute_side_pot_payouts(bets, winner_ids)

    results = []
    for uid, player in room.players.items():
        payout = payouts.get(uid, 0)
        if payout > 0:
            await queries.update_casino_balance(uid, payout)
        bal = await queries.get_casino_balance(uid) or 0
        await queries.log_casino_result(uid, "figgie", player.wager, payout)
        results.append({
            "discord_user": uid,
            "display_name": player.display_name,
            "hand": player.hand,
            "chips": player.chips,
            "goal_cards": player.hand.get(room.goal_suit, 0),
            "score": scores[uid],
            "wager": player.wager,
            "payout": payout,
            "net": payout - player.wager,
            "balance": bal,
            "is_winner": uid in winner_ids,
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    room.result_data = {
        "status": "finished",
        "results": results,
        "goal_suit": room.goal_suit,
        "goal_suit_name": SUIT_NAMES[room.goal_suit],
        "common_suit": room.common_suit,
        "common_suit_name": SUIT_NAMES[room.common_suit],
        "suit_counts": room.suit_counts,
        "total_trades": len(room.trades),
        "channel_id": room.channel_id,
    }

    await queries.finish_game_session(room.room_id, json.dumps(room.result_data), prize_pool)

    await _broadcast(room, {
        "type": "game_over",
        "goal_suit": room.goal_suit,
        "goal_suit_name": SUIT_NAMES[room.goal_suit],
        "common_suit": room.common_suit,
        "suit_counts": room.suit_counts,
        "results": results,
    })

    await asyncio.sleep(120)
    rooms.pop(room.room_id, None)


# ── Broadcast helpers ────────────────────────────────────────────────────────


async def _send(ws: WebSocket | None, msg: dict) -> None:
    if ws and ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _send_error(player: WebFiggiePlayer, message: str) -> None:
    await _send(player.ws, {"type": "error", "message": message})


async def _broadcast(room: FiggieRoom, msg: dict) -> None:
    for p in room.players.values():
        await _send(p.ws, msg)


def _room_state_msg(room: FiggieRoom, viewer: str | None = None) -> dict:
    return {
        "type": "room_state",
        "room_id": room.room_id,
        "phase": room.phase,
        "players": [
            {
                "id": p.discord_user,
                "name": p.display_name,
                "wager": p.wager,
                "is_host": p.is_host,
                "connected": p.ws is not None,
                "chips": p.chips if room.phase == "playing" else None,
                "card_count": sum(p.hand.values()) if room.phase == "playing" else None,
            }
            for p in room.players.values()
        ],
        "prize_pool": sum(p.wager for p in room.players.values()),
        "round_num": room.round_num,
        "trade_open": room.trade_open,
        "you": viewer,
    }


def _hand_msg(player: WebFiggiePlayer, room: FiggieRoom) -> dict:
    return {
        "type": "hand",
        "hand": player.hand,
        "chips": player.chips,
        "total_cards": sum(player.hand.values()),
    }


def _market_state_msg(room: FiggieRoom) -> dict:
    # Best bid/ask per suit
    market: dict[str, dict] = {}
    for suit in SUITS:
        bids = [o["price"] for o in room.orders if o["suit"] == suit and o["action"] == "buy"]
        asks = [o["price"] for o in room.orders if o["suit"] == suit and o["action"] == "sell"]
        market[suit] = {
            "best_bid": max(bids) if bids else None,
            "best_ask": min(asks) if asks else None,
            "bid_count": len(bids),
            "ask_count": len(asks),
        }
    return {
        "type": "market_state",
        "market": market,
        "orders": room.orders[-20:],  # last 20 orders
        "trades": room.trades[-10:],  # last 10 trades
        "players": {
            uid: {"chips": p.chips, "card_count": sum(p.hand.values()), "name": p.display_name}
            for uid, p in room.players.items()
        },
    }


# ── Stale room cleanup ──────────────────────────────────────────────────────

async def cleanup_stale_figgie_rooms() -> None:
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
