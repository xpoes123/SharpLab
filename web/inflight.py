"""In-flight bet protection for graceful shutdown.

The multi-step casino games (blackjack, UTH, Pai Gow, craps, …) debit the bet up front and keep the
round in an in-memory dict until it settles. A plain process restart (every deploy) would wipe those
dicts and the player's already-debited coins would just vanish — they'd be "kicked out mid-hand and
lose their ante."

Each such game registers its round store here with a function that reports how much a round has at
risk. On shutdown (SIGTERM from `systemctl restart` → uvicorn graceful shutdown → the lifespan
shutdown in web/api.py) we REFUND every open round before the process exits. Players lose the
in-progress hand but get their coins back — which is the honest outcome for a server-side restart.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# (game_name, rounds_dict, debited_fn). debited_fn(round_state) -> coins currently at risk.
_REGISTRY: list[tuple[str, dict, object]] = []


def register(name: str, rounds: dict, debited) -> None:
    _REGISTRY.append((name, rounds, debited))


async def refund_all() -> int:
    """Refund every open round's debited coins. Returns the total refunded. Safe to call once at
    shutdown; clears each store so a refund can't be double-applied."""
    from db import queries
    total = 0
    for name, rounds, debited in _REGISTRY:
        if not rounds:
            continue
        for rid, st in list(rounds.items()):
            uid = st.get("uid")
            try:
                amt = int(debited(st))
            except Exception:
                amt = 0
            if uid and amt > 0:
                try:
                    await queries.update_casino_balance(uid, amt)
                    total += amt
                except Exception:
                    log.exception("inflight: failed to refund %s round for %s", name, uid)
        rounds.clear()
    if total:
        log.info("inflight: refunded %d coins across open rounds on shutdown", total)
    return total
