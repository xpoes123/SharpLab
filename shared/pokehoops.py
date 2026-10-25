"""Poké Hoops — server-side verification + leaderboard bookkeeping, shared by the web arcade
(web/g_pokehoops.py) and the Discord cog (bot/cogs/pokehoops.py).

The sim is the TV cartridge's JS engine (vendored in web/static/pokehoops/), run under node via
shared/pokehoops_cli.mjs — never ported, so the browser, the bot and the server agree exactly.

Anti-cheat: the server issues the draft seed; every 3-option pack is a pure function of that seed
and the earlier picks, so finish_season() replays seed + picks and rejects any pick that wasn't
offered, and that the coach's team (five starters PG..C + three bench, each backing up a spot) uses
exactly the eight drafted players. The season itself is simmed HERE under a fresh season seed drawn only after the picks are
final, so a client can't brute-force the 243 pick paths offline for the luckiest season. The
season seed is returned (and stored) so the page can replay the exact same season for its reveal."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

from db import queries

CLI = Path(__file__).with_name("pokehoops_cli.mjs")
NODE_TIMEOUT = 15  # seconds; a season is ~60ms of sim + ~50ms node startup
LINE_TIMEOUT = 40  # a duel line sims thousands of games (~5 s on the VPS)
PICKS = 8
BENCH = 3
SERIES_WINS = 4    # best of seven (pokehoops-draft.js SERIES_WINS)
WEB_URL = "https://sharplab.djiang.xyz/pokehoops.html"
SPRITE_URL = "https://sharplab.djiang.xyz/pokehoops/sprites/{}.png"


class IllegalDraft(ValueError):
    """The submitted picks don't replay from the seed, or the team isn't a legal arrangement of them."""


class EngineUnavailable(RuntimeError):
    """node is missing / crashed / timed out — not the player's fault."""


def _node() -> str:
    return os.environ.get("POKEHOOPS_NODE") or shutil.which("node") or "node"


def new_seed() -> int:
    return secrets.randbits(32)


async def _call(req: dict, timeout: float = NODE_TIMEOUT) -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            _node(), str(CLI), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError as e:
        raise EngineUnavailable(f"can't start node: {e}") from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(req).encode()), timeout)
    except asyncio.TimeoutError as e:
        proc.kill()
        raise EngineUnavailable("engine timed out") from e
    try:
        res = json.loads(out or b"{}")
    except ValueError as e:
        raise EngineUnavailable(f"engine crashed: {err.decode(errors='replace')[-300:]}") from e
    if "error" in res:
        raise IllegalDraft(res["error"])
    return res


def _clean_picks(picks) -> list[int]:
    if not isinstance(picks, list) or not all(isinstance(n, int) and not isinstance(n, bool) for n in picks):
        raise IllegalDraft("picks must be a list of dex numbers")
    return picks


def _clean_team(team) -> dict:
    """Shape check only (lists of ints); legality vs the picks is the engine's validTeam."""
    if not isinstance(team, dict):
        raise IllegalDraft("team must be {five, bench, backs}")
    out = {}
    for k in ("five", "bench", "backs"):
        v = team.get(k)
        if not isinstance(v, list) or not all(isinstance(n, int) and not isinstance(n, bool) for n in v):
            raise IllegalDraft(f"team.{k} must be a list of integers")
        out[k] = v
    return out


def default_team(picks: list[int]) -> dict:
    """The arrangement before the coach touches it: draft order, bench backing up PG, SG, SF."""
    return {"five": picks[:5], "bench": picks[5:PICKS], "backs": [0, 1, 2][:BENCH]}


async def next_pack(seed: int, picks: list[int]) -> list[dict] | None:
    """The 3 option cards for the next pick (None once all 8 are in). Raises IllegalDraft."""
    res = await _call({"op": "pack", "seed": seed, "picks": _clean_picks(picks)})
    return None if res.get("done") else res["pack"]


async def sim_season(seed: int, picks: list[int], team: dict, season_seed: int) -> dict:
    return await _call({"op": "season", "seed": seed, "picks": _clean_picks(picks), "team": _clean_team(team),
                        "season_seed": season_seed})


async def finish_season(uid: str, seed: int, picks: list[int], team: dict, source: str) -> dict:
    """Verify a finished draft + the coach's team, sim its season server-side, record it, pay the capped reward.
    Returns the season summary plus id / season_seed / is_new_best / prev_best / rank / coins / balance."""
    picks = _clean_picks(picks)
    if len(picks) != PICKS:
        raise IllegalDraft("draft incomplete")
    team = _clean_team(team)
    season_seed = new_seed()
    summary = await sim_season(seed, picks, team, season_seed)
    prev = await queries.get_pokehoops_best(uid)
    store = {k: v for k, v in summary.items() if k not in ("lineup_cards", "bench_cards")}
    sid = await queries.insert_pokehoops_season(uid, source, seed, season_seed, picks, store)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    guest = uid.startswith("guest:")   # guests play for the board only: no coins
    coins = 0 if guest else await queries.grant_activity_reward(uid, "pokehoops_season", day)
    return {
        **summary, "id": sid, "seed": seed, "season_seed": season_seed, "picks": picks,
        "prev_best": prev["wins"] if prev else None,
        "is_new_best": prev is None or summary["wins"] > prev["wins"],
        "rank": await queries.get_pokehoops_rank(uid),
        "coins": coins, "balance": 0 if guest else await queries.get_casino_balance(uid) or 0,
    }


async def finish_series(uid: str, seed: int, picks: list[int], team: dict, vs: int) -> dict:
    """Head-to-head: your verified draft + team vs the team from recorded season `vs`, best of seven, simmed here
    under a fresh series seed. Recorded (no coins, so it can't be farmed). Raises IllegalDraft for a bad target."""
    picks = _clean_picks(picks)
    if len(picks) != PICKS:
        raise IllegalDraft("draft incomplete")
    team = _clean_team(team)
    opp = await queries.get_pokehoops_season(vs)
    if opp is None or queries.pokehoops_is_legacy(opp):
        raise IllegalDraft("that team can't be challenged")
    if opp["discord_user"] == uid:
        raise IllegalDraft("that's your own team — send the link to a friend")
    series_seed = new_seed()
    res = await _call({"op": "series", "seed": seed, "picks": picks, "team": team, "opp": opp["lineup"],
                       "series_seed": series_seed})
    sid = await queries.insert_pokehoops_series(uid, vs, seed, series_seed, picks, res["team"], res["wins"], res["losses"])
    return {**res, "id": sid, "vs": vs, "seed": seed, "series_seed": series_seed, "picks": picks}


# ── Live duels: the engine side (the room itself lives in web/g_pokehoops.py) ──

async def duel_packs(seed: int, picks: list[list[int]]) -> list[list[int]] | None:
    """This round's two packs ([side 0's three, side 1's three]), None once both have eight. Raises IllegalDraft."""
    res = await _call({"op": "duel_packs", "seed": seed, "picks": picks})
    return None if res.get("done") else res["packs"]


async def duel_line(seed: int, picks: list[list[int]], teams: list[dict]) -> dict:
    return await _call({"op": "duel_line", "seed": seed, "picks": picks, "teams": teams}, LINE_TIMEOUT)


async def duel_series(seed: int, picks: list[list[int]], teams: list[dict], series_seed: int) -> dict:
    return await _call({"op": "duel_series", "seed": seed, "picks": picks, "teams": teams, "series_seed": series_seed})


def legal_team(team: dict, picks: list[int]) -> bool:
    """Python mirror of the engine's validTeam, so a bad lineup bounces before both coaches are waiting on it."""
    five, bench, backs = team.get("five", []), team.get("bench", []), team.get("backs", [])
    roster = five + bench
    return (len(five) == 5 and len(bench) == PICKS - 5 and len(backs) == len(bench)
            and all(0 <= b < 5 for b in backs) and sorted(roster) == sorted(picks))
