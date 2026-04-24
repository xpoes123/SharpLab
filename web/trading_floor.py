"""Trading Floor — web-based market simulation game engine."""

import asyncio
import json
import math
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

WEB_API_SECRET = os.environ.get("WEB_API_SECRET", "dev-secret")
ROOM_TTL = 1800
ROUND_DELAY = 8

# ── Constants ──────────────────────────────────────────────────────────────

MIN_PLAYERS = 1
MAX_PLAYERS = 8
NUM_ROUNDS = 8
ROUND_SECONDS = 45
STARTING_CASH = 10_000.0
MAX_SHORT_PER_STOCK = 25
MARKET_IMPACT_K = 0.4

STOCKS_DEF = [
    ("CHIP", "tech", "\U0001f4bb", "ChipWorks"),
    ("SOFT", "tech", "\U0001f4f1", "SoftLayer"),
    ("OIL", "energy", "\U0001f6e2\ufe0f", "OilCorp"),
    ("SOLAR", "energy", "\u2600\ufe0f", "SolarGrid"),
]
TICKERS = [t for t, *_ in STOCKS_DEF]

# ── Event cards ────────────────────────────────────────────────────────────

EVENT_CARDS = [
    {"name": "Tech Boom", "emoji": "\U0001f4bb", "desc": "AI breakthrough drives tech stocks higher.",
     "effects": {"CHIP": 0.20, "SOFT": 0.15, "OIL": -0.05, "SOLAR": -0.05}},
    {"name": "Energy Crisis", "emoji": "\u26a1", "desc": "Supply disruption sends energy soaring.",
     "effects": {"OIL": 0.25, "SOLAR": 0.15, "CHIP": -0.10, "SOFT": -0.10}},
    {"name": "Market Crash", "emoji": "\U0001f4c9", "desc": "Panic selling across all sectors.",
     "effects": {"CHIP": -0.20, "SOFT": -0.20, "OIL": -0.15, "SOLAR": -0.15}},
    {"name": "Bull Run", "emoji": "\U0001f4c8", "desc": "Investor confidence is sky-high.",
     "effects": {"CHIP": 0.12, "SOFT": 0.12, "OIL": 0.10, "SOLAR": 0.10}},
    {"name": "AI Revolution", "emoji": "\U0001f916", "desc": "Artificial intelligence changes everything.",
     "effects": {"CHIP": 0.25, "SOFT": 0.20, "OIL": -0.05, "SOLAR": 0.05}},
    {"name": "Oil Spill", "emoji": "\U0001f30a", "desc": "A tanker spill tanks OIL but boosts solar.",
     "effects": {"OIL": -0.25, "SOLAR": 0.20, "CHIP": 0.05, "SOFT": 0.0}},
    {"name": "Green Energy Boom", "emoji": "\u2600\ufe0f", "desc": "Renewable energy subsidies soar.",
     "effects": {"SOLAR": 0.25, "OIL": -0.10, "CHIP": 0.05, "SOFT": 0.05}},
    {"name": "Stimulus Package", "emoji": "\U0001f4b0", "desc": "Government pumps money into the economy.",
     "effects": {"CHIP": 0.10, "SOFT": 0.10, "OIL": 0.10, "SOLAR": 0.10}},
    {"name": "Chip Shortage", "emoji": "\U0001f3ed", "desc": "Global chip shortage hits production.",
     "effects": {"CHIP": -0.20, "SOFT": -0.10, "OIL": 0.05, "SOLAR": 0.05}},
    {"name": "Cloud Boom", "emoji": "\u2601\ufe0f", "desc": "Enterprise cloud spending surges.",
     "effects": {"SOFT": 0.25, "CHIP": 0.10, "OIL": -0.05, "SOLAR": 0.0}},
    {"name": "Interest Rate Hike", "emoji": "\U0001f3e6", "desc": "The Fed raises rates — growth stocks hit.",
     "effects": {"CHIP": -0.15, "SOFT": -0.15, "OIL": 0.10, "SOLAR": -0.05}},
    {"name": "OPEC Cuts", "emoji": "\U0001f6e2\ufe0f", "desc": "OPEC slashes production. Oil rallies.",
     "effects": {"OIL": 0.30, "SOLAR": -0.05, "CHIP": -0.05, "SOFT": -0.05}},
    {"name": "Cybersecurity Breach", "emoji": "\U0001f525", "desc": "Major tech companies hacked.",
     "effects": {"SOFT": -0.20, "CHIP": -0.10, "OIL": 0.05, "SOLAR": 0.05}},
    {"name": "Solar Mandate", "emoji": "\U0001f3e0", "desc": "New law requires solar on all new buildings.",
     "effects": {"SOLAR": 0.30, "OIL": -0.15, "CHIP": 0.05, "SOFT": 0.0}},
    {"name": "Tech Regulation", "emoji": "\u26d4", "desc": "Antitrust crackdown on big tech.",
     "effects": {"CHIP": -0.15, "SOFT": -0.20, "OIL": 0.05, "SOLAR": 0.10}},
    {"name": "Earnings Beat", "emoji": "\U0001f4b5", "desc": "Strong earnings across sectors.",
     "effects": {"CHIP": 0.15, "SOFT": 0.10, "OIL": 0.05, "SOLAR": 0.10}},
]

TIP_TEMPLATES = [
    "Insider tip: {sector} sector expected to move {direction}.",
    "Rumor: {name} — {desc}",
    "Sources say {ticker} will {direction_verb} next round.",
]

# ── Dataclasses ────────────────────────────────────────────────────────────


@dataclass
class TFStock:
    ticker: str
    sector: str
    emoji: str
    name: str
    price: float = 100.0
    fair_value: float = 100.0
    history: list[float] = field(default_factory=lambda: [100.0])
    halted: bool = False


@dataclass
class LimitOrder:
    order_id: int
    player_id: str
    player_name: str
    ticker: str
    side: str  # "buy" | "sell"
    price: float
    qty: int


@dataclass
class TFPlayer:
    discord_user: str
    display_name: str
    wager: int
    is_host: bool = False
    cash: float = STARTING_CASH
    positions: dict[str, int] = field(default_factory=lambda: {t: 0 for t in TICKERS})
    cost_basis: dict[str, float] = field(default_factory=lambda: {t: 0.0 for t in TICKERS})
    trade_count: int = 0
    ws: WebSocket | None = None

    def portfolio_value(self, stocks: dict[str, TFStock]) -> float:
        val = self.cash
        for ticker, qty in self.positions.items():
            if ticker in stocks:
                val += qty * stocks[ticker].price
        return val


@dataclass
class TradingFloor:
    room_id: str
    host_id: str
    channel_id: str
    phase: str = "waiting"
    players: dict[str, TFPlayer] = field(default_factory=dict)
    stocks: dict[str, TFStock] = field(default_factory=dict)
    orders: list[LimitOrder] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    event_deck: list[dict] = field(default_factory=list)
    current_event: dict | None = None
    round_num: int = 0
    trade_open: bool = False
    round_end: float = 0.0
    news_feed: list[str] = field(default_factory=list)
    private_tips: dict[str, str] = field(default_factory=dict)
    game_task: asyncio.Task | None = field(default=None, repr=False)
    result_data: dict | None = None
    created_at: float = field(default_factory=time.time)
    next_order_id: int = 0


rooms: dict[str, TradingFloor] = {}

# ── Request models ─────────────────────────────────────────────────────────


class CreateRoomRequest(BaseModel):
    host_discord_id: str
    channel_id: str
    host_display_name: str


class CreateTokenRequest(BaseModel):
    discord_user: str
    display_name: str
    wager: int


# ── API router ─────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/v1/tradingfloor", tags=["tradingfloor"])


def _check_api_key(api_key: str) -> None:
    if api_key != WEB_API_SECRET:
        raise HTTPException(401, "Invalid API key")


@router.post("/rooms")
async def create_room(body: CreateRoomRequest, x_api_key: str = Header()):
    _check_api_key(x_api_key)
    room_id = secrets.token_hex(4)
    room = TradingFloor(room_id=room_id, host_id=body.host_discord_id, channel_id=body.channel_id)
    rooms[room_id] = room
    await queries.create_game_session(room_id, "tradingfloor", body.host_discord_id, body.channel_id)
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
    room.players[body.discord_user] = TFPlayer(
        discord_user=body.discord_user,
        display_name=body.display_name,
        wager=body.wager,
        is_host=is_host,
    )
    await queries.create_game_token(token, room_id, body.discord_user, body.display_name, body.wager)
    await _broadcast_room_state(room)
    base = os.environ.get("WEB_BASE_URL", "https://djiang.xyz")
    return {"token": token, "url": f"{base}/tradingfloor/{room_id}?t={token}"}


@router.get("/rooms/{room_id}/result")
async def get_result(room_id: str):
    room = rooms.get(room_id)
    if room and room.result_data:
        return room.result_data
    session = await queries.get_game_session(room_id)
    if session and session["status"] == "finished" and session["result_json"]:
        return json.loads(session["result_json"])
    raise HTTPException(404, "Game not finished yet")


# ── WebSocket handler ──────────────────────────────────────────────────────


async def tf_websocket(websocket: WebSocket, room_id: str):
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

    if room.phase == "playing":
        await _send(websocket, _portfolio_msg(player, room))
        await _send(websocket, _market_state_msg(room))

    try:
        while True:
            data = await websocket.receive_json()
            await _handle_message(room, player, data)
    except WebSocketDisconnect:
        player.ws = None
    except Exception:
        player.ws = None


async def _handle_message(room: TradingFloor, player: TFPlayer, data: dict) -> None:
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

    elif msg_type == "buy":
        qty = max(1, int(data.get("qty", 1)))
        await _handle_market_order(room, player, data.get("ticker", ""), qty, "buy")

    elif msg_type == "sell":
        qty = max(1, int(data.get("qty", 1)))
        await _handle_market_order(room, player, data.get("ticker", ""), qty, "sell")

    elif msg_type == "short":
        qty = max(1, int(data.get("qty", 1)))
        await _handle_market_order(room, player, data.get("ticker", ""), qty, "short")

    elif msg_type == "sell_all":
        ticker = data.get("ticker", "").upper()
        if ticker not in room.stocks:
            await _send_error(player, "Invalid ticker")
            return
        held = player.positions.get(ticker, 0)
        if held <= 0:
            await _send_error(player, "No shares to sell")
            return
        await _handle_market_order(room, player, ticker, held, "sell")

    elif msg_type == "cover":
        ticker = data.get("ticker", "").upper()
        if ticker not in room.stocks:
            await _send_error(player, "Invalid ticker")
            return
        qty = player.positions.get(ticker, 0)
        if qty >= 0:
            await _send_error(player, "No short position to cover")
            return
        await _handle_market_order(room, player, ticker, abs(qty), "buy")

    elif msg_type == "limit_order":
        await _handle_limit_order(room, player, data)

    elif msg_type == "cancel_orders":
        room.orders = [o for o in room.orders if o.player_id != player.discord_user]
        await _broadcast(room, _market_state_msg(room))

    elif msg_type == "portfolio":
        await _send(player.ws, _portfolio_msg(player, room))


async def _handle_market_order(
    room: TradingFloor, player: TFPlayer, ticker: str, qty: int, action: str,
) -> None:
    if not room.trade_open:
        await _send_error(player, "Trading is closed")
        return

    ticker = ticker.upper()
    stock = room.stocks.get(ticker)
    if not stock:
        await _send_error(player, "Invalid ticker")
        return
    if stock.halted:
        await _send_error(player, f"{ticker} is halted (circuit breaker)")
        return

    price = stock.price

    if action == "buy":
        cost = price * qty
        if player.cash < cost:
            # Buy max affordable instead of rejecting
            qty = int(player.cash // price)
            if qty <= 0:
                await _send_error(player, f"Not enough cash (have {player.cash:.0f})")
                return
            cost = price * qty
        player.cash -= cost
        old_qty = player.positions.get(ticker, 0)
        old_cost = player.cost_basis.get(ticker, 0.0)
        player.positions[ticker] = old_qty + qty
        # Update cost basis (weighted average for longs)
        if old_qty >= 0:
            player.cost_basis[ticker] = old_cost + price * qty
        else:
            # Covering a short — reduce cost basis
            player.cost_basis[ticker] = old_cost + price * qty

    elif action == "sell":
        held = player.positions.get(ticker, 0)
        if held < qty:
            await _send_error(player, f"Not enough shares (have {held})")
            return
        player.cash += price * qty
        player.positions[ticker] = held - qty
        # Reduce cost basis proportionally
        if held > 0:
            player.cost_basis[ticker] -= (player.cost_basis.get(ticker, 0.0) / held) * qty

    elif action == "short":
        current_pos = player.positions.get(ticker, 0)
        if current_pos - qty < -MAX_SHORT_PER_STOCK:
            await _send_error(player, f"Max short is {MAX_SHORT_PER_STOCK} shares")
            return
        player.cash += price * qty
        player.positions[ticker] = current_pos - qty
        # Track short entry cost (negative cost basis = short proceeds)
        player.cost_basis[ticker] = player.cost_basis.get(ticker, 0.0) - price * qty

    player.trade_count += 1

    # Real-time market impact: price moves immediately on each trade
    direction = 1 if action == "buy" else -1
    impact = MARKET_IMPACT_K * math.sqrt(qty) * direction
    stock.price = round(max(1.0, stock.price + impact), 1)

    trade = {
        "player_id": player.discord_user,
        "player_name": player.display_name,
        "action": action,
        "ticker": ticker,
        "price": round(price, 1),
        "qty": qty,
    }
    room.trades.append(trade)
    await _broadcast(room, {"type": "trade_executed", **trade})
    await _send(player.ws, _portfolio_msg(player, room))
    await _broadcast(room, _market_state_msg(room))


async def _handle_limit_order(room: TradingFloor, player: TFPlayer, data: dict) -> None:
    if not room.trade_open:
        await _send_error(player, "Trading is closed")
        return

    ticker = data.get("ticker", "").upper()
    side = data.get("side", "")
    price = data.get("price")
    qty = data.get("qty", 1)

    if ticker not in room.stocks:
        await _send_error(player, "Invalid ticker")
        return
    if side not in ("buy", "sell"):
        await _send_error(player, "Side must be buy or sell")
        return
    if not isinstance(price, (int, float)) or price <= 0:
        await _send_error(player, "Invalid price")
        return
    if not isinstance(qty, int) or qty < 1:
        await _send_error(player, "Invalid quantity")
        return

    room.next_order_id += 1
    order = LimitOrder(
        order_id=room.next_order_id,
        player_id=player.discord_user,
        player_name=player.display_name,
        ticker=ticker,
        side=side,
        price=float(price),
        qty=qty,
    )
    room.orders.append(order)
    await _broadcast(room, {"type": "order_posted", "ticker": ticker, "side": side,
                             "price": price, "qty": qty, "player_name": player.display_name})
    await _broadcast(room, _market_state_msg(room))


# ── Game loop ──────────────────────────────────────────────────────────────


def _init_stocks() -> dict[str, TFStock]:
    return {
        ticker: TFStock(ticker=ticker, sector=sector, emoji=emoji, name=name)
        for ticker, sector, emoji, name in STOCKS_DEF
    }


def _distribute_partial_intel(
    event: dict, player_ids: list[str],
) -> dict[str, list[tuple[str, float]]]:
    """Give each player exactly 2 of the 4 stock effects — symmetric info, different pieces.

    Every player sees the same amount of information (2 effects), but different
    tickers. The skill is trading on what you know and inferring the rest from
    other players' behavior.

    For solo play (1 player): show 2 random effects.
    For 2 players: each sees 2, with 1 overlapping ticker.
    For 3+ players: random 2-ticker subsets, shuffled so no two players are guaranteed the same pair.
    """
    tickers_with_effects = [(t, pct) for t, pct in event["effects"].items() if t in TICKERS]
    # Pad if event doesn't affect all 4 tickers (some events have 0 effects on a ticker)
    for t in TICKERS:
        if t not in event["effects"]:
            tickers_with_effects.append((t, 0.0))

    intel: dict[str, list[tuple[str, float]]] = {}

    for uid in player_ids:
        # Each player gets 2 random effects
        chosen = random.sample(tickers_with_effects, 2)
        intel[uid] = chosen

    return intel


# ── NPC Analysts ───────────────────────────────────────────────────────────

NPC_ANALYSTS = [
    {"name": "Cramer", "style": "cramer"},
    {"name": "Pelosi", "style": "pelosi"},
]


async def _run_npc_picks(room: TradingFloor) -> None:
    """2 NPC analysts each give a BUY and SELL pick once per round.

    Cramer (high vol): 50% chance his pick is correct, 50% it's inverted.
    When right, he picks the biggest movers. When wrong, he's dead wrong.
    High risk, high reward — following him is a coin flip but the payoff is big.

    Pelosi (low vol): 80% chance her pick is correct, but she picks safer
    stocks (closest to fair value / smallest moves). Low risk, consistent edge.

    Both have the same expected value over many rounds, but different risk profiles.
    """
    # Stagger: Cramer at ~8s, Pelosi at ~22s
    for idx, analyst in enumerate(NPC_ANALYSTS):
        delay = 8 + idx * 14 + random.uniform(-2, 2)
        await asyncio.sleep(delay)
        if not room.trade_open:
            break

        npc_name = analyst["name"]
        style = analyst["style"]

        # Sort stocks by price change since last round
        moves = {}
        for t, s in room.stocks.items():
            if s.history and not s.halted:
                moves[t] = s.price - s.history[-1]
        if len(moves) < 2:
            continue

        sorted_by_move = sorted(moves.items(), key=lambda x: x[1])
        # sorted_by_move[0] = worst performer, sorted_by_move[-1] = best performer

        if style == "cramer":
            # Cramer: goes for the biggest moves. 50% chance he's right.
            is_right = random.random() < 0.50
            if is_right:
                # Correct: buy the winner, sell the loser
                buy_ticker = sorted_by_move[-1][0]
                sell_ticker = sorted_by_move[0][0]
                buy_reason = f"this is going to the MOON"
                sell_reason = f"SELL SELL SELL"
            else:
                # Wrong: inverted — buy the loser, sell the winner
                buy_ticker = sorted_by_move[0][0]
                sell_ticker = sorted_by_move[-1][0]
                buy_reason = f"buying the dip, trust me"
                sell_reason = f"overextended, take profits"

        else:
            # Pelosi: safer picks, closer to fundamentals. 80% correct.
            is_right = random.random() < 0.80
            # She picks the 2nd best/worst (not the extremes — safer)
            mid_high = sorted_by_move[-2] if len(sorted_by_move) >= 3 else sorted_by_move[-1]
            mid_low = sorted_by_move[1] if len(sorted_by_move) >= 3 else sorted_by_move[0]
            if is_right:
                buy_ticker = mid_high[0]
                sell_ticker = mid_low[0]
                buy_reason = f"steady growth, insider info"
                sell_reason = f"quietly rotating out"
            else:
                buy_ticker = mid_low[0]
                sell_ticker = mid_high[0]
                buy_reason = f"undervalued opportunity"
                sell_reason = f"taking profits early"

        await _broadcast(room, {
            "type": "analyst_pick",
            "analyst": npc_name,
            "buy_ticker": buy_ticker,
            "buy_reason": buy_reason,
            "sell_ticker": sell_ticker,
            "sell_reason": sell_reason,
        })


async def _run_bot_trades(room: TradingFloor) -> None:
    """Bot players trade during each round (only active in solo mode)."""
    bot_players = [p for p in room.players.values() if p.discord_user.startswith("__bot_")]
    if not bot_players:
        return

    num_actions = random.randint(4, 8)
    interval = (ROUND_SECONDS - 6) / max(num_actions, 1)

    for i in range(num_actions):
        await asyncio.sleep(interval + random.uniform(-1, 1))
        if not room.trade_open:
            break

        bot = random.choice(bot_players)
        ticker = random.choice(TICKERS)
        stock = room.stocks.get(ticker)
        if not stock or stock.halted:
            continue

        # Bot strategy: mix of momentum + noise
        if bot.discord_user == "__bot_1__":
            # Trading Bot: momentum — buy winners, sell losers
            if len(stock.history) >= 1:
                move = stock.price - stock.history[-1]
                if move > 1:
                    action = "buy"
                elif move < -1:
                    action = "sell" if bot.positions.get(ticker, 0) > 0 else "short"
                else:
                    action = random.choice(["buy", "sell"])
            else:
                action = random.choice(["buy", "sell"])
        else:
            # Market Maker: mean reversion — buy cheap, sell expensive
            if stock.price < 95:
                action = "buy"
            elif stock.price > 105 and bot.positions.get(ticker, 0) > 0:
                action = "sell"
            else:
                action = random.choice(["buy", "sell", "buy"])  # slight buy bias

        qty = random.randint(1, 4)
        price = stock.price

        # Execute the bot trade
        if action == "buy":
            cost = price * qty
            if bot.cash < cost:
                continue
            bot.cash -= cost
            bot.positions[ticker] = bot.positions.get(ticker, 0) + qty
        elif action == "sell":
            held = bot.positions.get(ticker, 0)
            if held < qty:
                qty = held
            if qty <= 0:
                continue
            bot.cash += price * qty
            bot.positions[ticker] = held - qty
        elif action == "short":
            if bot.positions.get(ticker, 0) - qty < -MAX_SHORT_PER_STOCK:
                continue
            bot.cash += price * qty
            bot.positions[ticker] = bot.positions.get(ticker, 0) - qty

        bot.trade_count += 1
        direction = 1 if action == "buy" else -1
        impact = MARKET_IMPACT_K * math.sqrt(qty) * direction
        stock.price = round(max(1.0, stock.price + impact), 1)

        trade = {
            "player_id": bot.discord_user,
            "player_name": bot.display_name,
            "action": action,
            "ticker": ticker,
            "price": round(price, 1),
            "qty": qty,
        }
        room.trades.append(trade)
        await _broadcast(room, {"type": "trade_executed", **trade})
        await _broadcast(room, _market_state_msg(room))


def _apply_noise(room: TradingFloor) -> None:
    """Random walk noise ±1-3c per stock."""
    for stock in room.stocks.values():
        if stock.halted:
            continue
        noise = random.gauss(0, 2.0)
        stock.price = max(1.0, stock.price + noise)


def _apply_event(room: TradingFloor, event: dict) -> None:
    """Apply event effects to stock prices."""
    for ticker, pct in event["effects"].items():
        stock = room.stocks.get(ticker)
        if not stock or stock.halted:
            continue
        old_price = stock.price
        stock.price = max(1.0, stock.price * (1 + pct))
        pct_actual = ((stock.price - old_price) / old_price * 100) if old_price > 0 else 0
        if abs(pct_actual) > 1:
            arrow = "\U0001f4c8" if pct_actual > 0 else "\U0001f4c9"
            room.news_feed.insert(0,
                f"[R{room.round_num}] {arrow} {stock.emoji} {ticker} {pct_actual:+.1f}%"
            )


def _check_circuit_breakers(room: TradingFloor) -> None:
    """Halt stocks that moved >25% in one round."""
    for stock in room.stocks.values():
        if len(stock.history) < 1:
            continue
        prev = stock.history[-1]
        if prev <= 0:
            continue
        pct_change = abs((stock.price - prev) / prev)
        if pct_change > 0.25:
            stock.halted = True
            room.news_feed.insert(0,
                f"[R{room.round_num}] \U0001f534 CIRCUIT BREAKER: {stock.emoji} {stock.ticker} halted after {pct_change*100:.0f}% move"
            )


def _record_prices(room: TradingFloor) -> None:
    """Snapshot current prices into history, reset halts."""
    for stock in room.stocks.values():
        stock.price = round(stock.price, 1)
        stock.history.append(stock.price)
        stock.halted = False


async def _game_loop(room: TradingFloor) -> None:
    try:
        room.phase = "playing"
        room.stocks = _init_stocks()
        room.trades.clear()
        room.orders.clear()
        room.news_feed.clear()

        # Shuffle and draw event deck
        deck = list(EVENT_CARDS)
        random.shuffle(deck)
        room.event_deck = deck[:NUM_ROUNDS]

        # Add bot player if solo mode
        if len(room.players) == 1:
            for bot_id, bot_name in [("__bot_1__", "Trading Bot"), ("__bot_2__", "Market Maker")]:
                room.players[bot_id] = TFPlayer(
                    discord_user=bot_id,
                    display_name=f"\U0001f916 {bot_name}",
                    wager=0,  # bots don't wager
                )

        # Reset players
        for p in room.players.values():
            p.cash = STARTING_CASH
            p.positions = {t: 0 for t in TICKERS}
            p.cost_basis = {t: 0.0 for t in TICKERS}
            p.trade_count = 0

        # Broadcast game start
        await _broadcast(room, {
            "type": "game_start",
            "num_rounds": NUM_ROUNDS,
            "round_seconds": ROUND_SECONDS,
            "stocks": {t: {"price": 100.0, "emoji": s.emoji, "name": s.name, "sector": s.sector}
                       for t, s in room.stocks.items()},
        })

        # Send initial portfolio to each player
        for p in room.players.values():
            await _send(p.ws, _portfolio_msg(p, room))

        # Trading rounds
        for rnd in range(1, NUM_ROUNDS + 1):
            room.round_num = rnd
            room.orders.clear()
            room.private_tips.clear()

            # Draw event (hidden for now)
            event = room.event_deck[rnd - 1]
            room.current_event = event

            # Send partial intel to each human player (2 of 4 effects)
            human_ids = [uid for uid in room.players if not uid.startswith("__bot_")]
            intel = _distribute_partial_intel(event, human_ids)
            for uid, effects in intel.items():
                room.private_tips[uid] = str(effects)
                p = room.players[uid]
                await _send(p.ws, {
                    "type": "intel",
                    "round": rnd,
                    "effects": [{"ticker": t, "pct": pct} for t, pct in effects],
                })

            # Open trading
            room.trade_open = True
            room.round_end = time.monotonic() + ROUND_SECONDS

            await _broadcast(room, {
                "type": "round_start",
                "round_num": rnd,
                "time_limit": ROUND_SECONDS,
            })
            await _broadcast(room, _market_state_msg(room))

            # Run NPC analyst picks + bot trades in background during the trading window
            npc_task = asyncio.create_task(_run_npc_picks(room))
            bot_task = asyncio.create_task(_run_bot_trades(room))

            # Timer loop
            elapsed = 0
            while elapsed < ROUND_SECONDS:
                await asyncio.sleep(min(10, ROUND_SECONDS - elapsed))
                elapsed = time.monotonic() - (room.round_end - ROUND_SECONDS)
                remaining = max(0, int(room.round_end - time.monotonic()))
                await _broadcast(room, {"type": "timer", "remaining": remaining})

            room.trade_open = False
            npc_task.cancel()
            bot_task.cancel()
            await _broadcast(room, {"type": "round_end", "round_num": rnd})

            # ── Settlement sequence (paced for clarity) ──

            # Step 1: Show event card (3s to read it)
            await asyncio.sleep(1)
            await _broadcast(room, {
                "type": "event_reveal",
                "round_num": rnd,
                "event": {"name": event["name"], "emoji": event["emoji"],
                          "desc": event["desc"], "effects": event["effects"]},
            })
            await asyncio.sleep(3)

            # Step 2: Apply effects + noise, then show price changes with animation
            _apply_noise(room)
            _apply_event(room, event)
            _check_circuit_breakers(room)
            _record_prices(room)

            # Send "prices_updated" so client can animate the changes
            await _broadcast(room, {
                "type": "prices_updated",
                "stocks": {
                    t: {
                        "price": round(s.price, 1),
                        "prev": s.history[-2] if len(s.history) >= 2 else 100.0,
                        "change": round(s.price - (s.history[-2] if len(s.history) >= 2 else 100.0), 1),
                    }
                    for t, s in room.stocks.items()
                },
            })
            await _broadcast(room, _market_state_msg(room))

            # Step 3: Update portfolios
            await asyncio.sleep(2)
            for p in room.players.values():
                await _send(p.ws, _portfolio_msg(p, room))

            if rnd < NUM_ROUNDS:
                await asyncio.sleep(ROUND_DELAY)

        await _end_game(room)
    except asyncio.CancelledError:
        pass
    except Exception:
        room.phase = "finished"


async def _end_game(room: TradingFloor) -> None:
    room.phase = "finished"

    # Liquidate all positions at final price
    for uid, player in room.players.items():
        for ticker, qty in player.positions.items():
            if qty != 0:
                price = room.stocks[ticker].price
                player.cash += qty * price  # negative qty = short (costs money if price up)
        player.positions = {t: 0 for t in TICKERS}

    prize_pool = sum(p.wager for p in room.players.values())

    # Rank by final cash (which is now portfolio value since positions are liquidated)
    scores = {uid: round(p.cash) for uid, p in room.players.items()}
    max_score = max(scores.values()) if scores else 0
    winner_ids = [uid for uid, s in scores.items() if s == max_score]

    bets = {uid: p.wager for uid, p in room.players.items()}
    payouts = compute_side_pot_payouts(bets, winner_ids)

    results = []
    for uid, player in room.players.items():
        is_bot = uid.startswith("__bot_")
        payout = payouts.get(uid, 0)
        if payout > 0 and not is_bot:
            await queries.update_casino_balance(uid, payout)
        bal = 0
        if not is_bot:
            bal = await queries.get_casino_balance(uid) or 0
            await queries.log_casino_result(uid, "tradingfloor", player.wager, payout)
        pnl = round(player.cash - STARTING_CASH)
        results.append({
            "discord_user": uid,
            "display_name": player.display_name,
            "final_cash": round(player.cash),
            "pnl": pnl,
            "trades": player.trade_count,
            "wager": player.wager,
            "payout": payout,
            "net": payout - player.wager,
            "balance": bal,
            "is_winner": uid in winner_ids,
        })

    results.sort(key=lambda r: r["final_cash"], reverse=True)

    # Stock summary
    stock_summary = {}
    for ticker, stock in room.stocks.items():
        stock_summary[ticker] = {
            "emoji": stock.emoji,
            "name": stock.name,
            "final_price": stock.price,
            "history": stock.history,
            "return": round((stock.price - 100) / 100 * 100, 1),
        }

    room.result_data = {
        "status": "finished",
        "results": results,
        "stocks": stock_summary,
        "total_trades": len(room.trades),
        "channel_id": room.channel_id,
    }

    await queries.finish_game_session(room.room_id, json.dumps(room.result_data), prize_pool)

    await _broadcast(room, {
        "type": "game_over",
        "results": results,
        "stocks": stock_summary,
    })

    await asyncio.sleep(120)
    rooms.pop(room.room_id, None)


# ── Broadcast helpers ──────────────────────────────────────────────────────


async def _send(ws: WebSocket | None, msg: dict) -> None:
    if ws and ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _send_error(player: TFPlayer, message: str) -> None:
    await _send(player.ws, {"type": "error", "message": message})


async def _broadcast(room: TradingFloor, msg: dict) -> None:
    for p in room.players.values():
        await _send(p.ws, msg)


async def _broadcast_room_state(room: TradingFloor) -> None:
    for uid, p in room.players.items():
        await _send(p.ws, _room_state_msg(room, viewer=uid))


def _room_state_msg(room: TradingFloor, viewer: str | None = None) -> dict:
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
            }
            for p in room.players.values()
        ],
        "prize_pool": sum(p.wager for p in room.players.values()),
        "you": viewer,
    }


def _portfolio_msg(player: TFPlayer, room: TradingFloor) -> dict:
    positions = []
    for ticker in TICKERS:
        qty = player.positions.get(ticker, 0)
        if qty != 0:
            stock = room.stocks[ticker]
            market_value = qty * stock.price
            cost = player.cost_basis.get(ticker, 0.0)
            # For longs: pnl = market_value - cost_basis
            # For shorts: pnl = -cost_basis - market_value (cost_basis is negative for shorts)
            unrealized_pnl = market_value - cost
            avg_entry = abs(cost / qty) if qty != 0 else 0
            positions.append({
                "ticker": ticker,
                "emoji": stock.emoji,
                "qty": qty,
                "price": round(stock.price, 1),
                "avg_entry": round(avg_entry, 1),
                "value": round(market_value, 1),
                "pnl": round(unrealized_pnl, 1),
            })
    return {
        "type": "portfolio",
        "cash": round(player.cash, 1),
        "positions": positions,
        "portfolio_value": round(player.portfolio_value(room.stocks), 1),
        "pnl": round(player.portfolio_value(room.stocks) - STARTING_CASH, 1),
        "trade_count": player.trade_count,
    }


def _sparkline(history: list[float]) -> str:
    if len(history) < 2:
        return ""
    chars = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    lo, hi = min(history), max(history)
    rng = hi - lo
    if rng == 0:
        return chars[3] * len(history)
    return "".join(chars[min(7, int((v - lo) / rng * 7))] for v in history)


def _market_state_msg(room: TradingFloor) -> dict:
    stocks = {}
    for ticker, stock in room.stocks.items():
        prev = stock.history[-1] if stock.history else 100.0
        pct = ((stock.price - prev) / prev * 100) if prev > 0 else 0
        # Limit order book summary
        bids = [o for o in room.orders if o.ticker == ticker and o.side == "buy"]
        asks = [o for o in room.orders if o.ticker == ticker and o.side == "sell"]
        best_bid = max((o.price for o in bids), default=None)
        best_ask = min((o.price for o in asks), default=None)
        stocks[ticker] = {
            "emoji": stock.emoji,
            "name": stock.name,
            "sector": stock.sector,
            "price": round(stock.price, 1),
            "change_pct": round(pct, 1),
            "history": [round(p, 1) for p in stock.history],
            "sparkline": _sparkline(stock.history + [stock.price]),
            "halted": stock.halted,
            "best_bid": round(best_bid, 1) if best_bid else None,
            "best_ask": round(best_ask, 1) if best_ask else None,
        }

    standings = []
    for uid, p in room.players.items():
        pv = p.portfolio_value(room.stocks)
        standings.append({
            "id": uid,
            "name": p.display_name,
            "portfolio_value": round(pv, 1),
            "pnl": round(pv - STARTING_CASH, 1),
            "trades": p.trade_count,
        })
    standings.sort(key=lambda s: s["portfolio_value"], reverse=True)

    return {
        "type": "market_state",
        "round_num": room.round_num,
        "trade_open": room.trade_open,
        "stocks": stocks,
        "standings": standings,
        "news": room.news_feed[:10],
        "recent_trades": [
            {"player": t["player_name"], "action": t["action"],
             "ticker": t["ticker"], "price": t["price"], "qty": t["qty"]}
            for t in room.trades[-10:]
        ],
    }


# ── Stale room cleanup ────────────────────────────────────────────────────


async def cleanup_stale_tf_rooms() -> None:
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
