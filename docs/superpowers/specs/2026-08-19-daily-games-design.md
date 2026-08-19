# Daily Games Platform — Design

**Date:** 2026-08-19
**Status:** Draft for review
**Goal:** Drive daily retention and pull lurkers into the SharpLab server by giving it a
competitive daily-puzzle habit — NYT/LinkedIn-style, but ranked. Competition is the primary
driver; streaks and shareable results feed it.

## 1. Problem & success criteria

Many members sit in the server and never speak. A daily, competitive, shared puzzle gives them a
low-effort reason to check in every day and a natural first thing to post (a result grid, a
leaderboard brag). Success = measurable lift in daily active users and messages
(`user_engagement` already tracks messages/voice), and a leaderboard that people race on.

Non-goals: multiplayer live games (handled elsewhere), monetary stakes, anti-cheat beyond
light-touch server validation.

## 2. Shape: a platform, not a game

The deliverable is a **daily-games platform** that any puzzle plugs into. Individual games stay
tiny; the engine owns everything competitive and social. Games are added over time (David authors
them); **Trap the Pig** is the launch plugin.

### 2.1 `DailyGame` plugin interface

```python
class DailyGame:
    id: str            # "trappig"
    name: str          # "Trap the Pig"
    icon: str          # "🐷"
    surface: str       # "web" (Discord-native surface allowed later)
    difficulties: list[str]   # ["easy","medium","hard"]

    def generate(self, seed: int, difficulty: str) -> dict:
        """Deterministic puzzle for the day. Same (seed, difficulty) → identical puzzle."""

    def par(self, puzzle: dict) -> int:
        """Optimal/target score for the board (bounded search); cached per daily board."""

    def validate(self, puzzle: dict, solution: dict) -> Result | None:
        """Server-authoritative replay. None if the solution is invalid/doesn't solve.
        Result = {solved: bool, primary: int, secondary: int} (lower is better)."""

    def share_grid(self, result: Result, meta: dict) -> str:
        """Spoiler-free emoji grid for pasting into Discord."""
```

Registered in a `DAILY_GAMES: dict[str, DailyGame]` registry (mirrors the casino registry). A
`/new-daily-game` scaffold skill follows later so authoring one = filling in these methods.

### 2.2 Components

- `shared/daily.py` — pure engine: puzzle-day computation, the rotation schedule, seeding,
  placement-points, streak math. Unit-testable, no I/O.
- `web/daily.py` — FastAPI router: `/daily` hub, per-game play page, `POST submit`, leaderboard
  endpoints. Serves `web/static/daily*.{html,js,css}` + the Trap the Pig board.
- `bot/cogs/daily.py` — the Discord surface: morning post, `/daily`, evening leaderboard, streak
  flex, "share to channel".
- Trap the Pig plugin lives in `web/daily_games/trappig.py` (logic) + `web/static/trappig.*`
  (board UI, reused for free-play too).

## 3. The daily rotation

- **Puzzle-day** = the date in America/New_York shifted so the day rolls over at **04:00 ET**
  (`now_ET - 4h` → date). One puzzle runs 4am→4am. (Matches pick'em's ET convention.)
- **Schedule** is deterministic from `day_index` (days since a fixed epoch, e.g. 2026-01-01):
  - `game = DAILY_POOL[day_index % len(DAILY_POOL)]`
  - `difficulty = game.difficulties[day_index % len(game.difficulties)]`
  - With one game, only difficulty cycles (easy→medium→hard→…); the game axis rotates as the pool
    grows.
- **Seed** = a stable hash of `(game.id, puzzle_date)`. `generate(seed, difficulty)` yields the
  identical board for everyone.
- Puzzle + computed par are cached in `daily_puzzles(game_id, puzzle_date)` on first request (or
  precomputed by the morning job) so par isn't recomputed per user.

## 4. Competition & scoring

- **One submission per user per day per game** — enforced by `UNIQUE(game_id, puzzle_date,
  discord_user)` on `daily_results`. First valid submission counts; no retry-farming.
- **Per-day leaderboard**: rank by `(solved desc, primary asc, secondary asc)`. For Trap the Pig,
  primary = fences used, secondary = server-timed elapsed ms. Board shows par.
- **Placement points**: rank → points on a curve (1st = 100, 2nd = 80, 3rd = 65, 4th = 55, then
  −5 each to a floor of 10 for any solve; a played-but-failed attempt = 3). Computed at read-time
  from the current field so standings stay live and heterogeneous games compare.
- **Season** = calendar month (ET). Season standings = `SUM(points)` over the month; resets each
  month so there's always a fresh race and nobody runs away.
- **Streak multiplier**: `points *= 1 + min(0.30, 0.02 * current_overall_streak)` — showing up
  daily compounds your standing, tying retention and competition together.

### 4.1 Coin rewards

Daily play pays into the same `casino_wallets` balance used across the arcade
(`queries.update_casino_balance`), so the daily habit feeds the whole economy.

- **Participation** (immediate, on first valid solve): a flat grant, capped once per day via the
  existing `ACTIVITY_REWARDS`/`daily_coin_earn` mechanism (source `"daily_play"`). Starting value
  **25🪙**. You get it just for solving today's puzzle — the reliable check-in incentive.
- **Placement** (paid at day close, i.e. the 4am-ET rollover job for the *previous* day, once
  standings are final): **1st 500🪙 · 2nd 300 · 3rd 200 · 4th–10th 100 · any other solver 25**
  (tunable). Paid on top of participation.
- **Idempotency**: `daily_puzzles.awarded` flag is set when a day's placement payouts complete, so
  the rollover job never double-pays (safe across restarts).
- **Streak milestone** coins (optional, small): a one-time bonus at 7/30/100-day overall streaks;
  can land in phase 1 or 2. The streak's main effect stays the season-points multiplier (§4).

### 4.2 Integrity (light-touch)

- Server is authoritative: it regenerates the board from the seed and **replays the submitted
  solution** via `validate()`; only a real solve is scored.
- Time is server-bounded: opening the daily issues a start token with a server timestamp; elapsed
  = submit_time − start_time. Good enough for a friendly server; no heavy anti-cheat.

## 5. Trap the Pig (launch plugin)

- **Board**: pointy-top hex grid (odd-r offset). Pig starts center. `generate` seeds N pre-placed
  fences (not on the pig or its neighbours).
- **Turn**: player places one fence on an empty hex; then the pig steps to the open neighbour on
  the shortest path to any border (multi-source BFS from open border cells; deterministic
  direction tie-break). Pig on a border cell = escaped (loss). Pig with no path out = trapped
  (win).
- **Score**: fences used (primary), server elapsed (secondary).
- **Difficulty** (tunable):
  - easy: 9×9, ~9 starting fences
  - medium: 11×11, ~7 starting fences
  - hard: 13×13, ~5 starting fences
  (bigger board + fewer starting fences = harder)
- **Par**: minimum fences to guarantee a trap, via bounded IDA*/minimax over the player's fence
  choices against the deterministic pig (move-ordering: prioritize hexes on the pig's current
  shortest escape path). Computed once per daily board and cached; if the search hits its bound,
  fall back to a strong greedy solver's result as par (flagged approximate).
- `validate` replays the move sequence from the seed, checks each placement was legal and the end
  state is trapped, returns `{solved, primary=fences, secondary=elapsed}`.
- `share_grid`: e.g. `🐷 Trap the Pig · medium · 5 fences (par 4) · 0:48` + a small 🟩 block.

Trap the Pig is also exposed as a **free-play** arcade game (random board, no leaderboard) via the
same board UI, added to the `/games` grid.

## 6. Data model (new)

```sql
CREATE TABLE daily_puzzles (
  game_id TEXT, puzzle_date TEXT, difficulty TEXT, seed INTEGER,
  payload TEXT, par INTEGER, par_approx INTEGER DEFAULT 0,
  awarded INTEGER DEFAULT 0,     -- placement payouts done for this day (idempotent rollover)
  PRIMARY KEY (game_id, puzzle_date)
);
CREATE TABLE daily_results (
  game_id TEXT, puzzle_date TEXT, discord_user TEXT,
  solved INTEGER, primary_score INTEGER, secondary_score INTEGER,
  submitted_at TEXT,
  PRIMARY KEY (game_id, puzzle_date, discord_user)   -- one submit
);
CREATE TABLE daily_streaks (
  discord_user TEXT, game_id TEXT,   -- game_id '__overall__' = all-games streak
  current INTEGER, longest INTEGER, last_date TEXT,
  PRIMARY KEY (discord_user, game_id)
);
```

Season standings derive from `daily_results` (no extra table). Placement points are computed at
read-time.

## 7. Discord surface (`bot/cogs/daily.py`)

- **Morning post** (`@tasks.loop` at ~09:00 ET, board already rolled at 04:00): "🎯 Today's Daily —
  🐷 Trap the Pig (medium)" + Play link + pings an opt-in `@Daily` role. Configurable channel
  (`daily_channel` bot setting), mirroring pick'em.
- **`/daily`**: today's game/difficulty, your status (played? your score vs par), your streak, a
  Play button, and `/daily leaderboard` (today + season).
- **Evening results post**: @-mentions the day's top finishers + season top 3.
- **Streak flex**: milestone announcements ("🔥 David hit a 30-day streak") and an optional
  about-to-break nudge.
- **Share to channel**: the web result screen offers "Copy grid" (paste anywhere) and, when
  signed in, a "Share to Discord" that posts the grid via the bot.

## 8. Web surface

- `/games` gains a pinned **Daily strip** at top: today's game, difficulty, your streak, Play +
  Leaderboard.
- `/daily` — hub: today's puzzle (auto-routes to the current game's board), your streak, the live
  leaderboard, and (after submit) your share grid.
- Discord-login gated (existing OAuth) so results tie to the Discord user — which also links a
  lurker's web activity to their identity.

## 9. Testing

- `shared/daily.py`: puzzle-day rollover at 4am ET across timezones/DST; rotation schedule
  (game+difficulty cycle); placement-points curve; streak increment/break; season windowing.
- Trap the Pig: `generate` determinism (same seed→same board); pig AI (escapes on open board,
  trapped when enclosed); `validate` accepts a real solve and rejects illegal/incomplete ones; par
  sanity (par ≥ 1, a par-length solution validates).
- One-submit enforcement (second submit rejected); leaderboard ordering; overdraw N/A (no coins).

## 10. Phasing

1. **Engine + Trap the Pig + web + rewards**: `shared/daily.py`, tables, Trap the Pig plugin +
   board UI (free-play + daily), `/daily` page, submit, per-day + season leaderboard, participation
   coins on solve, and the `/games` daily strip.
2. **Discord surface + placement payouts**: morning post + `@Daily`, `/daily` command, evening
   results, streak flex, share-to-channel, and the 4am rollover job that finalizes standings and
   pays placement bonuses (idempotent via `daily_puzzles.awarded`).
3. **Scale**: `/new-daily-game` scaffold skill + a second game; streak-milestone coins.

## 11. Open questions

- Reward amounts (participation 25🪙; placement 500/300/200/100/25) and the placement-points curve
  / streak cap are first guesses; tune after launch once real fields exist.
- Par search bound for hard boards — measure; approximate-par fallback is acceptable.
- Placement rewards create a coin *faucet* — watch total daily issuance vs. the rest of the
  economy and adjust amounts if it inflates.
