"""Poké Hoops — server-side verification + leaderboard bookkeeping, shared by the web arcade
(web/g_pokehoops.py) and the Discord cog (bot/cogs/pokehoops.py).

The sim is the TV cartridge's JS engine (vendored in web/static/pokehoops/), run under node via
shared/pokehoops_cli.mjs — never ported, so the browser, the bot and the server agree exactly.

Anti-cheat: the server issues the draft seed; every 3-option pack is a pure function of that seed
and the earlier picks, so finish_season() replays seed + picks and rejects any pick that wasn't
offered. The season itself is simmed HERE under a fresh season seed drawn only after the picks are
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
PICKS = 5
WEB_URL = "https://sharplab.djiang.xyz/pokehoops.html"
SPRITE_URL = "https://sharplab.djiang.xyz/pokehoops/sprites/{}.png"


class IllegalDraft(ValueError):
    """The submitted picks don't replay from the seed (or are malformed)."""


class EngineUnavailable(RuntimeError):
    """node is missing / crashed / timed out — not the player's fault."""


def _node() -> str:
    return os.environ.get("POKEHOOPS_NODE") or shutil.which("node") or "node"


def new_seed() -> int:
    return secrets.randbits(32)


async def _call(req: dict) -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            _node(), str(CLI), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError as e:
        raise EngineUnavailable(f"can't start node: {e}") from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(req).encode()), NODE_TIMEOUT)
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


async def next_pack(seed: int, picks: list[int]) -> list[dict] | None:
    """The 3 option cards for the next pick (None once all 5 are in). Raises IllegalDraft."""
    res = await _call({"op": "pack", "seed": seed, "picks": _clean_picks(picks)})
    return None if res.get("done") else res["pack"]


async def sim_season(seed: int, picks: list[int], season_seed: int) -> dict:
    return await _call({"op": "season", "seed": seed, "picks": _clean_picks(picks), "season_seed": season_seed})


async def finish_season(uid: str, seed: int, picks: list[int], source: str) -> dict:
    """Verify a finished draft, sim its season server-side, record it, pay the capped reward.
    Returns the season summary plus id / season_seed / is_new_best / prev_best / rank / coins / balance."""
    picks = _clean_picks(picks)
    if len(picks) != PICKS:
        raise IllegalDraft("draft incomplete")
    season_seed = new_seed()
    summary = await sim_season(seed, picks, season_seed)
    prev = await queries.get_pokehoops_best(uid)
    store = {k: v for k, v in summary.items() if k != "lineup_cards"}
    sid = await queries.insert_pokehoops_season(uid, source, seed, season_seed, picks, store)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    coins = await queries.grant_activity_reward(uid, "pokehoops_season", day)
    return {
        **summary, "id": sid, "seed": seed, "season_seed": season_seed, "picks": picks,
        "prev_best": prev["wins"] if prev else None,
        "is_new_best": prev is None or summary["wins"] > prev["wins"],
        "rank": await queries.get_pokehoops_rank(uid),
        "coins": coins, "balance": await queries.get_casino_balance(uid) or 0,
    }
