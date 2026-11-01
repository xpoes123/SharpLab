"""Wall Street Poker — 2–6 player auction poker on casino coins, web-native.

Room plumbing mirrors contraband.py (in-memory rooms + 4-letter codes, _send/_broadcast,
stale-room cleanup), but seats are Discord-session users because real coins are at stake:
each player buys in for the room's fixed stack, plays one hand, and cashes out.

Rules (one hand):
- Everyone is dealt 2 hidden hole cards. Cards then come out 3 at a time ("streets").
- Each card is auctioned left to right with a SEALED bid: every player still needing cards
  bids at once, bids reveal together, the high bid wins the card and pays into the pot.
  Won cards are face-up. Ties go to the bidder closest left of the button.
- No card is ever skipped: if every bid is 0 — or only one player still needs cards — the
  first eligible seat left of the button eats it at the forced price (why small blind hurts).
- Players can buy anyone's face-up cards at any time (buyer pays seller directly, not the pot);
  a seller who drops to 4 cards bids again from the next card.
- Fold any time: you keep your stack, forfeit what you put in the pot.
- When every live player holds 5 cards, best 5-card poker hand takes the pot (ties split).
"""

import asyncio
import logging
import random
import secrets
import string
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from bot.cogs.uth import RANKS, SUITS, _evaluate_5, _hand_name
from db import queries
from web import auth

logger = logging.getLogger(__name__)

GAME_KEY = "wallstreet"
ROOM_TTL = 1800          # 30 minutes idle → refund + drop
MIN_PLAYERS, MAX_PLAYERS = 2, 6
HAND_SIZE = 5
STREET_SIZE = 3
BID_SECONDS = 20         # sealed-bid window; missing bids count as 0
MIN_BUY_IN, MAX_BUY_IN = 10, 10_000
DEFAULT_BUY_IN = 100


def forced_price(buy_in: int) -> int:
    """What the forced taker pays for an unwanted card — 5% of the stack, like a blind."""
    return max(1, buy_in // 20)


def new_deck() -> list[str]:
    deck = [r + s for s in SUITS for r in RANKS]
    secrets.SystemRandom().shuffle(deck)  # CSPRNG — the deal decides real payouts
    return deck


# ── Hand engine (pure: no I/O, no clock — the web layer drives bid timeouts) ──


class Hand:
    def __init__(self, seats: list[str], stacks: dict[str, int], button: int, forced: int,
                 deck: list[str] | None = None):
        self.seats = list(seats)
        self.stack = dict(stacks)
        self.button = button % len(self.seats)
        self.forced = forced
        self.deck = deck if deck is not None else new_deck()
        self.hole = {u: [self.deck.pop(0), self.deck.pop(0)] for u in self.seats}
        self.board: dict[str, list[str]] = {u: [] for u in self.seats}
        self.paid = {u: 0 for u in self.seats}  # each seat's pot contribution
        self.pot = 0
        self.folded: set[str] = set()
        self.street: list[str] = []             # face-up cards still waiting for auction
        self.card: str | None = None            # card currently on the block
        self.card_seq = 0                       # bumps whenever a fresh auction opens (bid timer key)
        self.bidders: list[str] = []            # left-of-button order — tiebreak + forced priority
        self.bids: dict[str, int] = {}
        self.offers: dict[int, dict] = {}
        self._offer_seq = 0
        self.log: list[dict] = []
        self.result: dict | None = None
        self._step()

    def order(self) -> list[str]:
        """Seats starting left of the button."""
        n = len(self.seats)
        return [self.seats[(self.button + 1 + i) % n] for i in range(n)]

    def active(self) -> list[str]:
        return [u for u in self.order() if u not in self.folded]

    def need(self, u: str) -> int:
        return HAND_SIZE - len(self.hole[u]) - len(self.board[u])

    def available(self, u: str) -> int:
        """Chips not locked behind a sealed bid on the current card."""
        return self.stack[u] - self.bids.get(u, 0)

    # ── Driver ──

    def _step(self) -> None:
        """Advance until the hand needs player input (a live auction) or ends."""
        while self.result is None:
            live = self.active()
            if len(live) <= 1 or all(self.need(u) == 0 for u in live):
                self._showdown()
                return
            if self.card is None:
                if not self.street:
                    n = min(STREET_SIZE, sum(self.need(u) for u in live))
                    self.street = [self.deck.pop(0) for _ in range(n)]
                self.card = self.street.pop(0)
            # Folds and trades can knock bidders out mid-auction; if nobody's left, reopen
            # the same card to whoever needs cards now (e.g. a player who just sold one).
            self.bidders = [u for u in self.bidders if u in live and self.need(u) > 0]
            if not self.bidders:
                self.bidders = [u for u in live if self.need(u) > 0]
                self.bids = {}
                self.card_seq += 1
            self.bids = {u: b for u, b in self.bids.items() if u in self.bidders}
            if len(self.bidders) == 1:
                self._award(self.bidders[0], self.forced, "forced")
            elif all(u in self.bids for u in self.bidders):
                self._resolve()
            else:
                return

    def _resolve(self) -> None:
        top = max(self.bids[u] for u in self.bidders)
        if top > 0:
            self._award(next(u for u in self.bidders if self.bids[u] == top), top, "bid")
        else:
            self._award(self.bidders[0], self.forced, "forced")

    def _award(self, u: str, price: int, how: str) -> None:
        price = min(price, self.stack[u])
        self.stack[u] -= price
        self.paid[u] += price
        self.pot += price
        self.board[u].append(self.card)
        self.log.append({"kind": "card", "card": self.card, "winner": u, "price": price,
                         "how": how, "bids": dict(self.bids)})
        self.card, self.bidders, self.bids = None, [], {}

    def _showdown(self) -> None:
        live = self.active()
        self.card, self.bidders, self.bids, self.offers = None, [], {}, {}
        hands = {}
        if len(live) == 1:
            winners = live
        else:
            scores = {u: _evaluate_5(self.hole[u] + self.board[u]) for u in live}
            best = max(scores.values())
            winners = [u for u in live if scores[u] == best]
            hands = {u: _hand_name(s) for u, s in scores.items()}
        share, extra = divmod(self.pot, len(winners))
        final = dict(self.stack)
        for i, u in enumerate(winners):
            final[u] += share + (extra if i == 0 else 0)  # odd chip to the first winner left of button
        self.result = {"winners": winners, "final": final, "hands": hands, "pot": self.pot}

    # ── Player actions — each returns an error string, or None on success ──

    def bid(self, u: str, amount) -> str | None:
        if self.result or u not in self.bidders:
            return "You're not bidding on this card."
        if type(amount) is not int or not 0 <= amount <= self.stack[u]:
            return f"Bid 0–{self.stack[u]}."
        self.bids[u] = amount
        self._step()
        return None

    def timeout(self, seq: int) -> None:
        """Bid window expired — anyone who hasn't bid is in at 0."""
        if self.result or seq != self.card_seq:
            return
        for u in self.bidders:
            self.bids.setdefault(u, 0)
        self._step()

    def fold(self, u: str) -> str | None:
        if self.result or u not in self.seats or u in self.folded:
            return "You can't fold now."
        self.folded.add(u)
        self.offers = {k: o for k, o in self.offers.items() if u not in (o["buyer"], o["seller"])}
        self.log.append({"kind": "fold", "who": u})
        self._step()
        return None

    def _offer_error(self, o: dict) -> str | None:
        buyer, seller = o["buyer"], o["seller"]
        if self.result:
            return "The hand is over."
        if buyer == seller or buyer in self.folded or seller in self.folded:
            return "That trade isn't possible."
        if o["card"] not in self.board.get(seller, []):
            return "That card isn't for sale anymore."
        if self.need(buyer) <= 0:
            return "Buyer already has 5 cards."
        if o["price"] > self.available(buyer):
            return "Buyer can't cover that price."
        return None

    def offer(self, buyer: str, seller: str, card: str, price) -> str | None:
        if buyer not in self.seats or seller not in self.seats:
            return "Unknown player."
        if type(price) is not int or price < 1:
            return "Offer at least 1 chip."
        o = {"buyer": buyer, "seller": seller, "card": card, "price": price}
        err = self._offer_error(o)
        if err:
            return err
        self._offer_seq += 1
        self.offers[self._offer_seq] = o
        return None

    def accept(self, seller: str, oid) -> str | None:
        o = self.offers.get(oid)
        if not o or o["seller"] != seller:
            return "That offer is gone."
        err = self._offer_error(o)
        if err:
            del self.offers[oid]
            return err
        buyer, card, price = o["buyer"], o["card"], o["price"]
        self.stack[buyer] -= price
        self.stack[seller] += price
        self.board[seller].remove(card)
        self.board[buyer].append(card)
        self.offers = {k: x for k, x in self.offers.items() if x["card"] != card}
        self.log.append({"kind": "trade", "card": card, "buyer": buyer, "seller": seller, "price": price})
        self._step()
        return None

    def withdraw(self, u: str, oid) -> str | None:
        """Buyer cancels or seller declines."""
        o = self.offers.get(oid)
        if not o or u not in (o["buyer"], o["seller"]):
            return "That offer is gone."
        del self.offers[oid]
        return None


# ── Rooms ────────────────────────────────────────────────────────────────────


@dataclass
class Seat:
    uid: str
    name: str
    ws: WebSocket | None = None
    connected: bool = False
    bought_in: bool = False


@dataclass
class Room:
    room_id: str
    code: str
    host: str
    buy_in: int
    seats: list[Seat] = field(default_factory=list)
    hand: Hand | None = None
    button: int = 0
    last_result: dict | None = None
    deadline: float | None = None
    timer_seq: int = 0
    timer_task: asyncio.Task | None = None
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


rooms: dict[str, Room] = {}   # room_id → room
codes: dict[str, str] = {}    # CODE → room_id


def _new_code() -> str:
    for _ in range(50):
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in codes:
            return code
    return "".join(random.choices(string.ascii_uppercase, k=4))


def _seat(room: Room, uid: str) -> Seat | None:
    return next((s for s in room.seats if s.uid == uid), None)


# ── API router ───────────────────────────────────────────────────────────────


class CreateBody(BaseModel):
    buy_in: int = DEFAULT_BUY_IN


router = APIRouter(prefix="/api/v1/wallstreet", tags=["wallstreet"])


def _session(request: Request) -> dict:
    sess = auth.read_session(request)
    if not sess:
        raise HTTPException(401, "Sign in to play")
    return sess


@router.post("/rooms")
async def create_room(request: Request, body: CreateBody):
    sess = _session(request)
    if not MIN_BUY_IN <= body.buy_in <= MAX_BUY_IN:
        raise HTTPException(400, f"Buy-in must be {MIN_BUY_IN}–{MAX_BUY_IN:,}")
    room_id, code = secrets.token_hex(4), _new_code()
    room = Room(room_id=room_id, code=code, host=sess["id"], buy_in=body.buy_in)
    room.seats.append(Seat(uid=sess["id"], name=(sess.get("username") or "Player")[:32]))
    rooms[room_id] = room
    codes[code] = room_id
    logger.info("wallstreet room created code=%s id=%s buy_in=%d", code, room_id, body.buy_in)
    return {"room_id": room_id, "code": code}


@router.post("/rooms/{code}/join")
async def join_room(request: Request, code: str):
    sess = _session(request)
    room_id = codes.get((code or "").strip().upper())
    room = rooms.get(room_id) if room_id else None
    if not room:
        raise HTTPException(404, "Room not found")
    if _seat(room, sess["id"]):
        return {"room_id": room.room_id}
    if len(room.seats) >= MAX_PLAYERS:
        raise HTTPException(409, "Room full")
    room.seats.append(Seat(uid=sess["id"], name=(sess.get("username") or "Player")[:32]))
    room.last_activity = time.time()
    await _broadcast_state(room)
    return {"room_id": room.room_id}


# ── WebSocket handler ────────────────────────────────────────────────────────


async def wallstreet_websocket(websocket: WebSocket, room_id: str):
    room = rooms.get(room_id)
    sess = auth.read_session(websocket)
    seat = _seat(room, sess["id"]) if (room and sess) else None
    if seat is None:
        await websocket.close(code=4004, reason="Not seated in this room")
        return

    await websocket.accept()
    old = seat.ws
    seat.ws, seat.connected = websocket, True  # newest tab owns the seat (same Discord user)
    if old is not None:
        try:
            await old.close(code=4000, reason="Opened in another tab")
        except Exception:
            pass
    await _broadcast_state(room)

    try:
        while True:
            data = await websocket.receive_json()
            if isinstance(data, dict):
                if data.get("type") == "ping":
                    await _send(websocket, {"type": "pong"})
                    continue
                err = await _handle(room, seat, data)
                if err:
                    await _send(websocket, {"type": "error", "message": err})
                room.last_activity = time.time()
                await _after(room)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("wallstreet_websocket error in room %s", room_id)
    finally:
        if seat.ws is websocket:
            seat.ws, seat.connected = None, False
            try:
                await _broadcast_state(room)
            except Exception:
                pass


async def _handle(room: Room, seat: Seat, data: dict) -> str | None:
    t, uid, hand = data.get("type"), seat.uid, room.hand

    if t == "buyin":
        if hand or seat.bought_in:
            return None
        try:
            await queries.update_casino_balance(uid, -room.buy_in)  # atomic; raises if short
        except ValueError:
            return "Not enough coins for this buy-in."
        seat.bought_in = True
    elif t == "cashout":
        if hand or not seat.bought_in:
            return None
        seat.bought_in = False
        await queries.update_casino_balance(uid, room.buy_in)
    elif t == "leave":
        if hand and uid in hand.seats:
            return "Fold or finish the hand first."
        if seat.bought_in:
            seat.bought_in = False
            await queries.update_casino_balance(uid, room.buy_in)
        room.seats.remove(seat)
        if not room.seats:
            _drop(room)
        elif room.host == uid:
            room.host = room.seats[0].uid
        await _send(seat.ws, {"type": "left"})
    elif t == "start":
        if uid != room.host:
            return "Only the host can deal."
        if hand:
            return None
        players = [s.uid for s in room.seats if s.bought_in]
        if len(players) < MIN_PLAYERS:
            return f"Need at least {MIN_PLAYERS} players bought in."
        room.hand = Hand(players, {u: room.buy_in for u in players}, room.button,
                         forced_price(room.buy_in))
        room.button += 1
        room.timer_seq = 0
        room.last_result = None
    elif hand is None:
        return "No hand in progress."
    elif t == "bid":
        return hand.bid(uid, data.get("amount"))
    elif t == "fold":
        return hand.fold(uid)
    elif t == "offer":
        return hand.offer(uid, str(data.get("seller")), str(data.get("card")), data.get("price"))
    elif t == "accept":
        return hand.accept(uid, data.get("id"))
    elif t == "withdraw":
        return hand.withdraw(uid, data.get("id"))
    return None


async def _after(room: Room) -> None:
    """Settle a finished hand, (re)arm the bid timer, push state."""
    hand = room.hand
    if hand and hand.result:
        await _settle(room)
    elif hand and hand.card is not None and room.timer_seq != hand.card_seq:
        room.timer_seq = hand.card_seq
        room.deadline = time.time() + BID_SECONDS
        room.timer_task = asyncio.create_task(_bid_timer(room, hand, hand.card_seq))
    await _broadcast_state(room)


async def _bid_timer(room: Room, hand: Hand, seq: int) -> None:
    await asyncio.sleep(BID_SECONDS)
    if room.hand is hand and hand.card_seq == seq and rooms.get(room.room_id) is room:
        hand.timeout(seq)
        await _after(room)


async def _settle(room: Room) -> None:
    hand = room.hand
    room.hand = None  # claim before any await so a racing action can't settle twice
    room.deadline = None
    names = {s.uid: s.name for s in room.seats}
    res = hand.result
    for u in hand.seats:
        payout = res["final"][u]
        if payout:
            await queries.update_casino_balance(u, payout)
        await queries.log_casino_result(u, GAME_KEY, room.buy_in, payout)
    for s in room.seats:
        s.bought_in = False
    room.last_result = {
        "winners": [names.get(u, "?") for u in res["winners"]],
        "pot": res["pot"],
        "players": [
            {"name": names.get(u, "?"), "hole": hand.hole[u], "board": hand.board[u],
             "folded": u in hand.folded, "hand": res["hands"].get(u),
             "won": u in res["winners"], "net": res["final"][u] - room.buy_in}
            for u in hand.order()
        ],
    }


async def _refund(room: Room) -> None:
    """Return everyone's money: in-hand chips (stack + pot contribution) or lobby buy-ins."""
    hand, room.hand = room.hand, None
    for s in room.seats:
        if hand and s.uid in hand.seats:
            amount = hand.stack[s.uid] + hand.paid[s.uid]
        else:
            amount = room.buy_in if s.bought_in else 0
        s.bought_in = False
        if amount:
            await queries.update_casino_balance(s.uid, amount)


def _drop(room: Room) -> None:
    rooms.pop(room.room_id, None)
    codes.pop(room.code, None)


# ── Broadcast helpers ────────────────────────────────────────────────────────


async def _send(ws: WebSocket | None, msg: dict) -> None:
    if ws and ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


def _state_msg(room: Room, me: str) -> dict:
    hand = room.hand
    names = {s.uid: s.name for s in room.seats}
    players = []
    for s in room.seats:
        p = {"uid": s.uid, "name": s.name, "connected": s.connected, "bought_in": s.bought_in,
             "is_host": s.uid == room.host}
        if hand and s.uid in hand.seats:
            p.update({
                "in_hand": True,
                "stack": hand.stack[s.uid],
                "paid": hand.paid[s.uid],
                "board": hand.board[s.uid],
                "folded": s.uid in hand.folded,
                "need": hand.need(s.uid),
                "bidding": s.uid in hand.bidders,
                "has_bid": s.uid in hand.bids,
                "button": s.uid == hand.seats[hand.button],
            })
        players.append(p)

    msg = {"type": "state", "code": room.code, "me": me, "host": room.host, "buy_in": room.buy_in,
           "forced": forced_price(room.buy_in), "players": players, "last_result": room.last_result,
           "hand": None}
    if hand:
        msg["hand"] = {
            "order": hand.order(),
            "pot": hand.pot,
            "card": hand.card,
            "street": hand.street,
            "deadline": room.deadline,
            "my_hole": hand.hole.get(me),
            "my_bid": hand.bids.get(me),
            "my_available": hand.available(me) if me in hand.seats else 0,
            "offers": [
                {"id": k, **o, "buyer_name": names.get(o["buyer"], "?"),
                 "seller_name": names.get(o["seller"], "?")}
                for k, o in hand.offers.items() if me in (o["buyer"], o["seller"])
            ],
            "log": [
                {**e, "bids": {names.get(u, "?"): b for u, b in e.get("bids", {}).items()},
                 **{k: names.get(e[k], "?") for k in ("winner", "who", "buyer", "seller") if k in e}}
                for e in hand.log[-12:]
            ],
        }
    return msg


async def _broadcast_state(room: Room) -> None:
    for s in room.seats:
        await _send(s.ws, _state_msg(room, s.uid))


# ── Stale room cleanup ───────────────────────────────────────────────────────


async def refund_all_wallstreet_rooms() -> None:
    """Shutdown hook — rooms are in-memory, so a restart would otherwise eat buy-ins."""
    for room in list(rooms.values()):
        try:
            await _refund(room)
        except Exception:
            logger.exception("wallstreet refund failed for room %s", room.room_id)
        _drop(room)


async def cleanup_stale_wallstreet_rooms() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            now = time.time()
            for room in [r for r in rooms.values() if now - r.last_activity > ROOM_TTL]:
                await _refund(room)
                _drop(room)
                for s in room.seats:
                    await _send(s.ws, {"type": "error", "message": "Room expired — buy-ins refunded."})
        except Exception:
            logger.exception("cleanup_stale_wallstreet_rooms error — loop continues")
