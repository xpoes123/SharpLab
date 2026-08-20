"""In-flight round persistence across restarts.

The multi-step casino games (blackjack, UTH, Pai Gow, craps, …) keep an open hand in an in-memory
dict keyed by round_id until it settles. A plain restart (every deploy) would wipe those dicts and
the player — mid-hand, ante already debited — would be stuck.

Instead of refunding, we PERSIST: each game registers its round store here; on shutdown we snapshot
every open round to the DB (SIGTERM → uvicorn graceful shutdown → the lifespan shutdown in
web/api.py), and on startup we load them back into the same in-memory dicts. Because the client
still holds its round_id, the very next action (hit / play / roll) finds the restored round and the
hand simply continues. Round state is plain JSON-serializable data (ints, strings, lists), so it
round-trips cleanly.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

# (game_name, rounds_dict). The dict is the game's live _ROUNDS store.
_REGISTRY: list[tuple[str, dict]] = []


def register(name: str, rounds: dict) -> None:
    _REGISTRY.append((name, rounds))


async def persist_all() -> int:
    """Snapshot every open round to the DB. Returns how many were saved."""
    from db import queries
    rows = []
    for name, rounds in _REGISTRY:
        for rid, st in rounds.items():
            try:
                rows.append((name, str(rid), st.get("uid"), json.dumps(st)))
            except (TypeError, ValueError):
                log.exception("inflight: round %s/%s not serializable — skipped", name, rid)
    await queries.save_inflight_rounds(rows)
    if rows:
        log.info("inflight: persisted %d open rounds", len(rows))
    return len(rows)


async def restore_all() -> int:
    """Load persisted rounds back into their in-memory stores, then clear the table."""
    from db import queries
    saved = await queries.load_inflight_rounds()
    by_game = {name: rounds for name, rounds in _REGISTRY}
    n = 0
    for game_id, round_id, state in saved:
        store = by_game.get(game_id)
        if store is None:
            continue
        try:
            store[round_id] = json.loads(state)
            n += 1
        except (TypeError, ValueError):
            log.exception("inflight: could not restore %s/%s", game_id, round_id)
    if n:
        await queries.clear_inflight_rounds()
        log.info("inflight: restored %d open rounds", n)
    return n
