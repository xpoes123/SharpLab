"""Daily Games engine — pure logic shared by the web app and the Discord bot.

Owns everything competitive that isn't game-specific: which puzzle-day it is (4am-ET rollover),
the rotation schedule (game + difficulty), deterministic seeding, placement points, the streak
multiplier, and the coin-reward tables. No I/O — DB access lives in db/queries.py, side effects in
web/bot. Individual puzzles are plugins in shared/daily_games/ (duck-typed: ID, NAME, ICON,
DIFFICULTIES, generate/validate/par/share_grid).
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from shared.daily_games import trappig

ET = ZoneInfo("America/New_York")
ROLLOVER_HOUR = 4                # a puzzle-day runs 4am ET → 4am ET
EPOCH = date(2026, 1, 1)         # day_index origin

# Registry + rotation order. Add plugins here; the daily rotates through DAILY_POOL.
DAILY_GAMES = {trappig.ID: trappig}
DAILY_POOL = [trappig.ID]


# ── puzzle-day & rotation ─────────────────────────────────────────────────────

def puzzle_day(now_utc: datetime | None = None) -> str:
    """The current puzzle-day as 'YYYY-MM-DD' (ET, rolling over at 04:00)."""
    now = (now_utc or datetime.now(timezone.utc)).astimezone(ET)
    return (now - timedelta(hours=ROLLOVER_HOUR)).date().isoformat()


def next_rollover(now_utc: datetime | None = None) -> datetime:
    """UTC datetime of the next 04:00-ET rollover (for scheduling the morning job)."""
    now = (now_utc or datetime.now(timezone.utc)).astimezone(ET)
    today_roll = now.replace(hour=ROLLOVER_HOUR, minute=0, second=0, microsecond=0)
    if now >= today_roll:
        today_roll = today_roll + timedelta(days=1)
    return today_roll.astimezone(timezone.utc)


def day_index(day: str) -> int:
    return (date.fromisoformat(day) - EPOCH).days


def schedule(day: str) -> tuple[str, str]:
    """(game_id, difficulty) for a puzzle-day. Game rotates through DAILY_POOL; difficulty
    cycles through that game's difficulties (easy→medium→hard→…)."""
    i = day_index(day)
    game_id = DAILY_POOL[i % len(DAILY_POOL)]
    diffs = DAILY_GAMES[game_id].DIFFICULTIES
    return game_id, diffs[i % len(diffs)]


def seed_for(game_id: str, day: str) -> int:
    """Stable 32-bit seed from (game, day). hashlib (not hash()) so it's identical across
    processes and restarts — everyone gets the same board."""
    h = hashlib.sha256(f"{game_id}:{day}".encode()).hexdigest()
    return int(h[:8], 16)


def build_puzzle(day: str) -> dict:
    """Generate today's puzzle payload + par for the scheduled game/difficulty.

    Prefers the plugin's `build_solvable` so the daily is GUARANTEED to have an answer (the
    generator only ships a board once it has computed a witness solution); falls back to plain
    `generate` for games that are solvable by construction."""
    game_id, difficulty = schedule(day)
    game = DAILY_GAMES[game_id]
    seed = seed_for(game_id, day)
    if hasattr(game, "build_solvable"):
        payload = game.build_solvable(seed, difficulty)
    else:
        payload = game.generate(seed, difficulty)
    par_v, approx = game.par(payload)
    return {"game_id": game_id, "difficulty": difficulty, "seed": seed,
            "payload": payload, "par": par_v, "par_approx": approx}


# ── competition scoring ───────────────────────────────────────────────────────

_PLACEMENT = {1: 100, 2: 80, 3: 65, 4: 55}


def placement_points(rank: int, solved: bool) -> int:
    """Season points for finishing at `rank` (1-based) on a day's board."""
    if not solved:
        return 3                       # played but didn't solve
    if rank in _PLACEMENT:
        return _PLACEMENT[rank]
    return max(10, 55 - (rank - 4) * 5)  # 5th=50, 6th=45 … floor 10


def streak_multiplier(overall_streak: int) -> float:
    """Season-points multiplier from the overall daily streak: +2%/day, capped +30%."""
    return 1.0 + min(0.30, 0.02 * max(0, overall_streak))


def rank_results(results: list[dict], game_id: str) -> list[dict]:
    """Order a day's results best→worst and attach rank + placement points.

    `results` rows: {discord_user, solved, primary_score, secondary_score}. Lower primary
    then lower secondary is better; solvers always rank above non-solvers."""
    ordered = sorted(
        results,
        key=lambda r: (0 if r["solved"] else 1, r["primary_score"], r["secondary_score"]),
    )
    out = []
    for i, r in enumerate(ordered, start=1):
        out.append({**r, "rank": i, "points": placement_points(i, bool(r["solved"]))})
    return out


# ── coin rewards ──────────────────────────────────────────────────────────────

DAILY_PLAY_COINS = 25              # participation, on first solve (capped once/day)


def placement_coins(rank: int, solved: bool) -> int:
    """Coins paid at day-close for a finishing position. Top spots pay, every solver gets a tip."""
    if not solved:
        return 0
    return {1: 500, 2: 300, 3: 200}.get(rank, 100 if rank <= 10 else 25)
