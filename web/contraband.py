"""Contraband (Liar Game smuggling, 1v1) — web-native 2-player game engine,
WebSocket handler, API router.

Mirrors pd.py exactly (in-memory rooms + codes maps, WebPlayer dataclass,
_send/_broadcast helpers, the identify/next/end_match/reset protocol, disconnect
handling, and a stale-room cleanup coroutine). Rooms are created straight from
the browser — no Discord token, no coins. Guests join by a 4-letter code and
rejoin by name.

Unlike PD this is SEQUENTIAL within a round and the two roles ALTERNATE:
- one player is the Smuggler, the other the Inspector; roles swap every round.
  Round 1 → the host (slot A) smuggles. Smuggler slot = A on odd rounds, B on even.
- the Smuggler secretly seals a hidden amount `x` from 0..10 (0 = an empty decoy).
- the Inspector, WITHOUT seeing x, either Passes or Doubts with a guess N (1..10).
- coins banked by the SMUGGLER that round are `smug(x, doubted, N)` (below). The
  inspector banks 0 — but roles swap, so both accrue smuggler turns. Each PLAYER's
  cumulative banked total is tracked; higher total wins when the host ends it.

Payoff — coins banked by the smuggler `smug(x, doubted, N)`:
    Pass (not doubted)                → x        (money slips through untouched)
    Doubt & x == 0 (empty decoy)      → N/2      (inspector baited into a penalty)
    Doubt & x >= 1 & N >= x (caught)  → 0        (contraband confiscated)
    Doubt & x >= 1 & N <  x (under)   → x        (guessed too low — money slips)
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

MAX_AMOUNT = 10  # smuggler seals 0..10; 0 = empty decoy case


def smug(x: int, doubted: bool, guess: int) -> float:
    """Coins banked by the SMUGGLER this round — the sole payoff function.

    Pure and unit-tested. `guess` (N) is only meaningful when doubted.
    """
    if not doubted:
        return x                     # not doubted → the full amount slips through
    if x == 0:
        return guess / 2             # empty decoy baited a doubt → inspector's penalty
    if guess >= x:
        return 0                     # guessed high enough → caught, confiscated
    return x                         # under-guessed a real load → it slips through


def smuggler_slot(rnd: int) -> str:
    """Whose turn it is to smuggle on a given (1-based) round. Host (A) starts."""
    return "A" if rnd % 2 == 1 else "B"


# ── In-memory state ──────────────────────────────────────────────────────────


@dataclass
class WebPlayer:
    name: str
    slot: str  # "A" (host) or "B"
    ws: WebSocket | None = None
    connected: bool = False
    score: float = 0                 # cumulative coins banked across all rounds


@dataclass
class ContrabandRoom:
    room_id: str
    code: str
    players: list[WebPlayer] = field(default_factory=list)  # [0]=A (host), [1]=B
    round: int = 1
    sealed: bool = False             # smuggler has sealed the case this round
    sealed_amount: int | None = None  # the hidden x — private until the round reveals
    revealed: bool = False           # inspector has called & the round resolved
    last_round: dict | None = None   # resolved-round summary for the reveal screen
    history: list[dict] = field(default_factory=list)  # past resolved rounds
    match_over: bool = False         # host ended the match
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


rooms: dict[str, ContrabandRoom] = {}  # room_id → room
codes: dict[str, str] = {}              # CODE → room_id


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

router = APIRouter(prefix="/api/v1/contraband", tags=["contraband"])


@router.post("/rooms")
async def create_room(body: JoinBody):
    """Create a fresh room and register the caller as slot A (the host)."""
    name = (body.name or "Player").strip()[:32] or "Player"
    room_id = secrets.token_hex(4)
    code = _new_code()
    room = ContrabandRoom(room_id=room_id, code=code)
    room.players.append(WebPlayer(name=name, slot="A"))
    rooms[room_id] = room
    codes[code] = room_id
    logger.info("contraband room created code=%s id=%s host=%s", code, room_id, name)
    return {"room_id": room_id, "code": code}


@router.post("/rooms/{code}/join")
async def join_room(code: str, body: JoinBody):
    """Join an existing room by its 4-letter code as slot B (the second player)."""
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

    slot = "A" if not room.players else "B"
    room.players.append(WebPlayer(name=name, slot=slot))
    room.last_activity = time.time()
    await _broadcast_state(room)
    return {"room_id": room.room_id}


# ── WebSocket handler ────────────────────────────────────────────────────────


async def contraband_websocket(websocket: WebSocket, room_id: str):
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

            elif mtype == "seal":  # smuggler seals a hidden amount for this round
                if player is not None:
                    await _handle_seal(room, player, data)

            elif mtype == "call":  # inspector passes or doubts with a guess
                if player is not None:
                    await _handle_call(room, player, data)

            elif mtype == "next":  # advance to the next round after a reveal
                if player is not None:
                    await _handle_next(room, player)

            elif mtype == "end_match":  # host ends the match; higher cumulative wins
                if player is not None:
                    await _handle_end_match(room, player)

            elif mtype == "reset":  # host restarts scores/history from scratch
                if player is not None:
                    await _handle_reset(room, player)

            elif mtype == "ping":
                await _send(websocket, {"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("contraband_websocket error in room %s", room_id)
    finally:
        if player is not None:
            player.connected = False
            player.ws = None
            try:
                await _broadcast_state(room)
            except Exception:
                pass


def _attach_player(room: ContrabandRoom, name: str, ws: WebSocket) -> WebPlayer | None:
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


# ── Game actions ─────────────────────────────────────────────────────────────


def _both_connected(room: ContrabandRoom) -> bool:
    return len(room.players) >= 2 and sum(1 for p in room.players if p.connected) >= 2


async def _handle_seal(room: ContrabandRoom, player: WebPlayer, data: dict) -> None:
    if room.match_over:
        return
    if not _both_connected(room):
        await _send_error(player, "Waiting for your opponent.")
        return
    if room.revealed:
        return  # round already resolved — click Next round first
    if player.slot != smuggler_slot(room.round):
        await _send_error(player, "You're the inspector this round — wait for the seal.")
        return
    if room.sealed:
        return  # already sealed — ignore double-seals

    amount = data.get("amount")
    if not isinstance(amount, int) or amount < 0 or amount > MAX_AMOUNT:
        return  # bad input — ignore

    room.sealed_amount = amount
    room.sealed = True
    room.last_activity = time.time()
    await _broadcast_state(room)


async def _handle_call(room: ContrabandRoom, player: WebPlayer, data: dict) -> None:
    if room.match_over:
        return
    if not _both_connected(room):
        await _send_error(player, "Waiting for your opponent.")
        return
    if player.slot == smuggler_slot(room.round):
        await _send_error(player, "You're the smuggler this round — seal your case.")
        return
    if not room.sealed:
        await _send_error(player, "Wait for the smuggler to seal the case.")
        return
    if room.revealed:
        return  # already resolved

    action = data.get("action")
    if action not in ("pass", "doubt"):
        return

    doubted = action == "doubt"
    guess = 0
    if doubted:
        g = data.get("guess")
        if not isinstance(g, int) or g < 1 or g > MAX_AMOUNT:
            return  # a doubt needs a valid 1..10 guess
        guess = g

    _resolve_round(room, doubted, guess)
    room.last_activity = time.time()
    await _broadcast_state(room)


def _resolve_round(room: ContrabandRoom, doubted: bool, guess: int) -> None:
    """Inspector has called — apply smug() to the smuggler's cumulative bank."""
    sm_slot = smuggler_slot(room.round)
    smuggler = next(p for p in room.players if p.slot == sm_slot)
    x = room.sealed_amount if room.sealed_amount is not None else 0
    banked = smug(x, doubted, guess)
    smuggler.score += banked
    room.last_round = {
        "round": room.round,
        "smuggler_slot": sm_slot,
        "x": x,
        "doubted": doubted,
        "guess": guess if doubted else None,
        "banked": banked,
    }
    room.history.append(dict(room.last_round))
    room.revealed = True


async def _handle_next(room: ContrabandRoom, player: WebPlayer) -> None:
    """Either player can advance to the next round once the current one revealed."""
    if room.match_over or not room.revealed:
        return
    room.round += 1
    room.sealed = False
    room.sealed_amount = None
    room.revealed = False
    room.last_round = None
    room.last_activity = time.time()
    await _broadcast_state(room)


async def _handle_end_match(room: ContrabandRoom, player: WebPlayer) -> None:
    if player.slot != "A":
        await _send_error(player, "Only the host can end the match.")
        return
    room.match_over = True
    room.last_activity = time.time()
    await _broadcast_state(room)


async def _handle_reset(room: ContrabandRoom, player: WebPlayer) -> None:
    if player.slot != "A":
        await _send_error(player, "Only the host can reset the game.")
        return
    room.round = 1
    room.sealed = False
    room.sealed_amount = None
    room.revealed = False
    room.last_round = None
    room.history = []
    room.match_over = False
    for p in room.players:
        p.score = 0
    room.last_activity = time.time()
    await _broadcast_state(room)


# ── Broadcast helpers ────────────────────────────────────────────────────────


async def _send(ws: WebSocket | None, msg: dict) -> None:
    if ws and ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _send_error(player: WebPlayer, message: str) -> None:
    await _send(player.ws, {"type": "error", "message": message})


def _state_msg(room: ContrabandRoom, me: WebPlayer) -> dict:
    opp = next((p for p in room.players if p.slot != me.slot), None)
    sm_slot = smuggler_slot(room.round)
    my_role = "smuggler" if me.slot == sm_slot else "inspector"

    winner = None
    if room.match_over:
        if opp is None or me.score > opp.score:
            winner = "you"
        elif me.score < opp.score:
            winner = "opp"
        else:
            winner = "tie"

    # The sealed amount is private until the round resolves — but a smuggler may
    # always see their own sealed case.
    your_sealed = room.sealed_amount if (room.sealed and my_role == "smuggler") else None

    last = None
    if room.revealed and room.last_round is not None:
        lr = room.last_round
        last = {
            "round": lr["round"],
            "smuggler_slot": lr["smuggler_slot"],
            "smuggler_is_you": lr["smuggler_slot"] == me.slot,
            "x": lr["x"],
            "doubted": lr["doubted"],
            "guess": lr["guess"],
            "banked": lr["banked"],
        }

    return {
        "type": "state",
        "code": room.code,
        "round": room.round,
        "sealed": room.sealed,
        "revealed": room.revealed,
        "match_over": room.match_over,
        "you": me.slot,
        "is_host": me.slot == "A",
        "your_role": my_role,               # "smuggler" | "inspector" this round
        "smuggler_slot": sm_slot,
        "your_sealed_amount": your_sealed,  # only your own sealed case, pre-reveal
        "scores": {"me": me.score, "opp": (opp.score if opp else 0)},
        "last_round": last,
        "history": list(room.history),
        "winner": winner,
        "players": [
            {"name": p.name, "slot": p.slot, "score": p.score, "connected": p.connected}
            for p in room.players
        ],
    }


async def _broadcast_state(room: ContrabandRoom) -> None:
    for p in room.players:
        await _send(p.ws, _state_msg(room, me=p))


# ── Stale room cleanup ───────────────────────────────────────────────────────


async def cleanup_stale_contraband_rooms() -> None:
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
            logger.exception("cleanup_stale_contraband_rooms error — loop continues")
