# Port Zetamac + Math 24 into SharpLab — Design

**Date:** 2026-08-20
**Status:** Approved scope, pending implementation plan.

## 1. Summary

Bring **Zetamac** (arithmetic speed race) and **Math 24** (make-24 puzzle) from the separate
games service (`~/code/games`, games.djiang.xyz) into SharpLab's web app as **native games**,
each with a **solo** and a **multiplayer** mode, SharpLab login/coins, and **leaderboards**.
Remove the SharpLab `/games` tiles that externally link to **bridge / chess / ers** (those
stay running on games.djiang.xyz — SharpLab just stops linking to them).

Confirmed decisions:
- Remove SharpLab links to **bridge, chess, ers** (games keep running on games.djiang.xyz).
- **Port** zetamac + math24 natively into SharpLab (both are already FastAPI).
- Each gets **solo + multiplayer**.
- Add **leaderboards** for both.

## 2. Source & target

- Source: `~/code/games/src/zetamac/app.py` (~557 lines, FastAPI + WebSocket **room race** —
  "everyone gets the identical problem stream, most correct in DURATION seconds wins"),
  `~/code/games/src/math24/{app.py,game.py}` (~524 lines, FastAPI).
- Target patterns already in SharpLab web:
  - **Solo** → `web/g_mathsprint.py` + `web/static/mathsprint.{html,css,js}` (a solo arithmetic
    speed game — the closest existing analog; reuse its round/score/leaderboard shape).
  - **Multiplayer** → `web/minesweeper.py` / `web/blotto.py` (a `router` + `<game>_websocket` +
    `cleanup_stale_<game>_rooms`, all mounted in `web/api.py`; results feed
    `bot/cogs/_elo_helpers.update_elo_multiplayer`).
  - **Leaderboard** → `elo_ratings` + `/api/v1/elo/*` (multiplayer) and the solo high-score
    mechanism mathsprint already uses.

## 3. Scope of work

### 3.1 Remove external links (bridge/chess/ers)
- `web/static/games.html`: delete the three `<a class="gtile" href="/bridge/">`,
  `/chess/`, `/ers/` tiles.
- Replace the `/math24/` and `/zetamac/` proxied tiles with **native** SharpLab tiles
  (pointing at the new solo/multiplayer entry pages).
- `web/static/games.js` header comment updated (no longer proxying zetamac/math24).
- **Caddy** (`/etc/caddy/Caddyfile`, sharplab block `@gameapps`): drop `/zetamac*` and
  `/math24*` so SharpLab serves them natively; drop `/bridge* /chess* /ers*` too (now
  unlinked — they remain reachable at games.djiang.xyz, which has its own proxy block).
  Reload Caddy at deploy.

### 3.2 Zetamac (native)
- **Solo** (`/zetamac`): timed arithmetic drill (default 120s), classic mix (add/sub/mul/div).
  Port the problem generator from the source. Score = correct answers in the window.
  On finish, submit the score → coins (via the shared activity reward, capped) + a solo
  high-score leaderboard.
- **Multiplayer** (`/zetamac/race` or a mode toggle): WS room race — port the source's Room
  model onto SharpLab's multiplayer scaffold (`web/blotto.py` shape). Everyone in a room gets
  the identical problem stream; most-correct wins. On finish, `update_elo_multiplayer(finish
  order, "zetamac", …)` → elo leaderboard + winner coins (already wired via the coin feature).
- `web/zetamac.py` (router + `zetamac_websocket` + `cleanup_stale_zetamac_rooms`),
  `web/static/zetamac.{html,css,js}`, mounted in `web/api.py`.

### 3.3 Math 24 (native)
- **Solo** (`/math24`): deal 4 numbers; the player enters an expression making 24; port the
  validator/solver from `game.py`. Timed or per-puzzle; score → coins + solo leaderboard.
- **Multiplayer** (race): same 4-number deals streamed to a room; most puzzles solved wins;
  `update_elo_multiplayer(..., "math24", …)`.
- `web/math24.py` + `web/static/math24.{html,css,js}`, mounted in `web/api.py`.

### 3.4 Leaderboards
- **Multiplayer**: register `zetamac` + `math24` in `ELO_GAME_LABELS` (`web/api.py`);
  `update_elo_multiplayer` already writes `elo_ratings` + `elo_match_history`, surfaced by the
  existing `/api/v1/elo/*` endpoints and the leaderboard UI. (`math24` label already present.)
- **Solo**: a high-score board per game (best score / best time), following mathsprint's solo
  score persistence. Surface on each game page and/or the leaderboard hub.
- Add both to `GAME_LABELS` so casino/leaderboard views name them.

### 3.5 Games-hub tiles
- Add native tiles for **Zetamac** (with a solo/multiplayer entry) and **Math 24** (same) to
  `games.html`, category **Solo & Brain** + **Multiplayer** (both surfaces, since each has both
  modes). Use a mode picker on the game page rather than two separate tiles.

## 4. Build phasing (incremental — ship each)

1. **Link cleanup** (fast, standalone): remove bridge/chess/ers tiles; trim Caddy. Ship.
2. **Zetamac** end-to-end (solo + multiplayer + leaderboard + tile). Ship.
3. **Math 24** end-to-end (solo + multiplayer + leaderboard + tile). Ship.

Each phase is its own PR + deploy so nothing is half-wired.

## 5. Testing

- Unit: zetamac problem generator (answers correct across all four ops), math24 validator
  (accepts valid 24-expressions using each number once, rejects invalid), score→coins cap.
- Multiplayer: a room race resolves a finish order and calls `update_elo_multiplayer` with the
  right winner; stale-room cleanup.
- Reuse the `?mock=1` + headless-screenshot approach to verify each game page.

## 6. Non-goals

- Not touching bridge/chess/ers themselves (they keep running on games.djiang.xyz).
- Not migrating the games-service infrastructure — only zetamac + math24 game logic move.
- No cross-service score sync — the ported games write to SharpLab's DB from the start.
