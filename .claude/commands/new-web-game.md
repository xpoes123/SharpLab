Scaffold a new web-based casino game (browser gameplay via WebSocket).

Usage: /new-web-game <game-name>
Examples: /new-web-game minesweeper, /new-web-game hex, /new-web-game codenames

Use this for games that need interactive browser UI (clickable grids, drag-and-drop, real-time animation, simultaneous hidden input). For Discord-only games (buttons, modals, chat-based), use `/new-game` instead.

**Before writing any code, read `GAMES.md` at the project root.**

---

## Architecture Overview

Web games have 4 layers:

```
Discord cog (bot/cogs/<game>.py)          — creates rooms, handles join/betting, polls results
Web backend (web/<game>.py)               — FastAPI router + WebSocket handler + game engine
Shared logic (shared/<game>_logic.py)     — pure game logic, constants, validation (no deps)
Static frontend (web/static/<game>.html)  — single HTML file with inline CSS/JS
```

Pattern: Discord for betting/coordination, browser for gameplay.

---

## Step 1 — Write shared game logic

Create `shared/<game>_logic.py` with:

- Game constants: `MAX_PLAYERS`, `MIN_PLAYERS`, `WINS_TO_WIN`, `ROUND_TIME`, `MAX_ROUNDS`, `PAYTABLE`
- Pure game logic functions (board generation, move validation, win detection)
- `compute_payouts(players, ...)` helper
- NO imports from `discord`, `db`, `web`, or `bot` — this file must be dependency-free

Reference: `shared/sudoku_logic.py`, `shared/figgie_logic.py`, `shared/bingo_logic.py`

Use the standard PAYTABLE:
```python
PAYTABLE: dict[int, list[float]] = {
    1: [1.0],
    2: [1.0],
    3: [0.70, 0.30],
    4: [0.55, 0.30, 0.15],
    5: [0.45, 0.25, 0.18, 0.12],
    6: [0.40, 0.24, 0.16, 0.12, 0.08],
    7: [0.36, 0.22, 0.16, 0.12, 0.08, 0.06],
    8: [0.33, 0.21, 0.16, 0.12, 0.08, 0.06, 0.04],
}
```

---

## Step 2 — Write the web backend

Create `web/<game>.py` following `web/sudoku.py` exactly:

### Required exports (imported by `web/api.py`):
```python
router = APIRouter(prefix="/api/v1/<game>", tags=["<game>"])
async def <game>_websocket(websocket: WebSocket, room_id: str): ...
async def cleanup_stale_<game>_rooms() -> int: ...
```

### Required structure:
```python
# Dataclasses: WebPlayer (discord_user, display_name, wager, ws, game state)
# Room class (room_id, host_id, phase, players dict, game state, result_data)
# In-memory dict: rooms: dict[str, Room] = {}
# Pydantic models: CreateRoomRequest, CreateTokenRequest
# API router with these endpoints:
#   POST /rooms                     — create room (auth: X-Api-Key)
#   POST /rooms/{id}/tokens         — create player token (auth: X-Api-Key)
#   GET  /rooms/{id}/result         — poll for finished game result
# WebSocket handler:
#   Authenticate via token in first message
#   Broadcast game state changes to all connected players
#   Handle player actions (moves, guesses, etc.)
#   On game end: compute payouts, log results, store result_data
```

### Auth pattern:
- Room creation + token creation use `X-Api-Key` header (matches `WEB_API_SECRET` env var)
- Players authenticate to WebSocket by sending `{"type": "auth", "token": "..."}` as first message
- Each player gets a unique token embedded in their game URL

### Coin flow for web games:
1. **Discord cog**: deduct coins on Join (via modal) — before creating token
2. **Web backend**: plays game, tracks wagers in `WebPlayer.wager`
3. **Web backend**: on game end, compute payouts via `compute_payouts()` and call `queries.update_casino_balance()` + `queries.log_casino_result()` directly
4. **Discord cog**: polls `/result` endpoint, posts result embed to channel

### Room TTL:
```python
ROOM_TTL = 1800  # 30 minutes — auto-expire unused rooms
```

---

## Step 3 — Write the static frontend

Create `web/static/<game>.html` — single self-contained file:

- Dark theme (background `#1a1a2e`, text `#e0e0e0`, accent `#f1c40f`)
- No build step, no external dependencies (vanilla HTML/CSS/JS)
- WebSocket connection to `wss://djiang.xyz/ws/<game>/{room_id}?token={token}`
- Parse `room_id` and `token` from URL path/query params
- Responsive layout that works on desktop and mobile
- Show live game state, opponent progress, timer if applicable
- Handle disconnection gracefully (auto-reconnect or "disconnected" overlay)

Reference: `web/static/sudoku.html`, `web/static/figgie.html`

---

## Step 4 — Write the Discord cog

Create `bot/cogs/<game>.py` following `bot/cogs/sudoku.py` exactly:

```python
class GameCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._pending_web_rooms: dict[str, int] = {}  # room_id -> channel_id

    async def cog_load(self):
        self._poll_web_results.start()

    async def cog_unload(self):
        self._poll_web_results.cancel()

    @app_commands.command(name="<game>", description="...")
    async def game_cmd(self, interaction):
        # POST to web API to create room
        # Send embed with Join button (WebLobbyView)

    @tasks.loop(seconds=10)
    async def _poll_web_results(self):
        # GET /api/v1/<game>/rooms/{id}/result
        # Post result embed to Discord channel

    @_poll_web_results.before_loop
    async def _before_poll(self):
        await self.bot.wait_until_ready()
```

Join modal: deducts coins, creates token via web API, sends ephemeral game link.

---

## Step 5 — Wire into web/api.py

Add 3 things to `web/api.py`:

```python
# 1. Import
from web.<game> import router as <game>_router, <game>_websocket, cleanup_stale_<game>_rooms

# 2. Include router (after existing routers)
app.include_router(<game>_router)

# 3. WebSocket endpoint
@app.websocket("/ws/<game>/{room_id}")
async def ws_<game>(websocket: WebSocket, room_id: str):
    await <game>_websocket(websocket, room_id)

# 4. Add to startup cleanup
sessions += await cleanup_stale_<game>_rooms()
```

---

## Step 6 — Caddy route (deployment)

Add to `/etc/caddy/Caddyfile` on the VPS:

```
handle_path /<game>/* {
    rewrite * /<game>.html
    root * /path/to/web/static
    file_server
}
```

---

## Step 7 — Register in casino system

Same 4 places as any game (see `/new-game`):

1. `bot/main.py` — add to `COGS` list
2. `bot/cogs/casino.py` — add to `CASINO_GAMES` and `GAME_LABELS`
3. Cog file — `async def setup(bot)` at bottom

---

## Step 8 — Verify

- [ ] `POST /api/v1/<game>/rooms` creates a room
- [ ] `POST /api/v1/<game>/rooms/{id}/tokens` creates a token and returns URL
- [ ] WebSocket connects and authenticates with token
- [ ] Game plays correctly in browser
- [ ] Results appear in Discord channel after game ends
- [ ] Coin payouts are correct (check with `/casino-stats`)
- [ ] Stale room cleanup works on restart
- [ ] `/games` shows the game in the correct category
