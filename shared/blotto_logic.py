"""Colonel Blotto — pure game logic.

No external dependencies.  Shared between the web engine and (optionally)
any Discord-side simulation or tests.
"""

import random
from itertools import groupby

# ── Constants ──────────────────────────────────────────────────────────────

SOLDIERS = 100
BATTLEFIELDS = 5
WINS_TO_WIN = 3        # first to 3 round wins
MAX_ROUNDS = 9          # hard cap on rounds
ALLOCATION_TIME = 60    # seconds per allocation phase
REVEAL_DELAY = 1.5      # seconds between battlefield reveals
ROUND_DELAY = 5         # seconds between rounds
MIN_PLAYERS = 2
MAX_PLAYERS = 8

BATTLEFIELD_NAMES = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
BATTLEFIELD_WEIGHTS = [1, 1, 2, 2, 3]  # shuffled each round; need majority of points (5/9)

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

DEFAULT_ALLOCATION = [0] * BATTLEFIELDS  # auto-clear: start empty


def generate_weights() -> list[int]:
    """Return a shuffled copy of BATTLEFIELD_WEIGHTS for a new round."""
    w = list(BATTLEFIELD_WEIGHTS)
    random.shuffle(w)
    return w


# ── Validation ─────────────────────────────────────────────────────────────


def validate_allocation(soldiers: list[int]) -> str | None:
    """Return None if valid, else an error message."""
    if not isinstance(soldiers, list) or len(soldiers) != BATTLEFIELDS:
        return f"Must allocate across exactly {BATTLEFIELDS} battlefields"
    if any(not isinstance(s, int) or s < 0 for s in soldiers):
        return "All allocations must be non-negative integers"
    total = sum(soldiers)
    if total != SOLDIERS:
        return f"Must deploy exactly {SOLDIERS} soldiers (got {total})"
    return None


# ── Resolution ─────────────────────────────────────────────────────────────


def resolve_battlefield(allocations: dict[str, int]) -> str | None:
    """Given {player_id: soldiers_count}, return the winner or None for tie.

    The player with the most soldiers wins.  If two or more players tie
    for the maximum, nobody wins (returns None).
    """
    if not allocations:
        return None
    max_val = max(allocations.values())
    leaders = [uid for uid, v in allocations.items() if v == max_val]
    if len(leaders) == 1:
        return leaders[0]
    return None  # tie


def resolve_round(
    all_allocations: dict[str, list[int]],
    weights: list[int] | None = None,
) -> dict:
    """Resolve a full round.

    Parameters
    ----------
    all_allocations : {player_id: [soldiers_per_battlefield]}
    weights : point value per battlefield (default: all 1s)

    Returns
    -------
    dict with keys:
      - battlefield_winners: list of (winner_id | None) per battlefield
      - points_won: {player_id: total_points_this_round}
      - battlefields_won: {player_id: count}
      - round_winner: player_id | None
    """
    if weights is None:
        weights = [1] * BATTLEFIELDS

    player_ids = list(all_allocations.keys())
    battlefield_winners: list[str | None] = []
    battlefields_won: dict[str, int] = {uid: 0 for uid in player_ids}
    points_won: dict[str, int] = {uid: 0 for uid in player_ids}
    total_points = sum(weights)

    for bf_idx in range(BATTLEFIELDS):
        bf_allocs = {
            uid: alloc[bf_idx] for uid, alloc in all_allocations.items()
        }
        winner = resolve_battlefield(bf_allocs)
        battlefield_winners.append(winner)
        if winner is not None:
            battlefields_won[winner] += 1
            points_won[winner] += weights[bf_idx]

    # Round winner: whoever has majority of POINTS (e.g. 5 of 9)
    points_needed = total_points // 2 + 1
    max_pts = max(points_won.values()) if points_won else 0
    if max_pts >= points_needed:
        candidates = [uid for uid, p in points_won.items() if p == max_pts]
        round_winner = candidates[0] if len(candidates) == 1 else None
    else:
        round_winner = None

    return {
        "battlefield_winners": battlefield_winners,
        "points_won": points_won,
        "battlefields_won": battlefields_won,
        "round_winner": round_winner,
    }


# ── Ranking & Payouts ─────────────────────────────────────────────────────


def rank_players(
    players: list[dict],
) -> list[dict]:
    """Sort player dicts by rounds_won desc, then battlefields_won_total desc.

    Each dict must have 'rounds_won' and 'battlefields_won_total' keys.
    Returns a new sorted list.
    """
    return sorted(
        players,
        key=lambda p: (p["rounds_won"], p["battlefields_won_total"]),
        reverse=True,
    )


def compute_payouts(
    player_data: list[dict],
    prize_pool: int,
    n_players: int,
) -> dict[str, int]:
    """Distribute prize_pool using the PAYTABLE.

    *player_data* is a list of dicts with 'user_id' and 'rounds_won' keys,
    already sorted by rank (best first).

    Returns {user_id: payout_amount}.
    """
    pct_table = PAYTABLE.get(n_players, PAYTABLE[8])
    payouts: dict[str, int] = {p["user_id"]: 0 for p in player_data}

    in_money = [p for p in player_data if p["rounds_won"] > 0]
    if not in_money:
        # Nobody won any rounds → refund everyone
        for p in player_data:
            payouts[p["user_id"]] = p.get("wager", 0)
        return payouts

    paid_positions = len(pct_table)
    pos = 0
    for _wins, group_iter in groupby(in_money, key=lambda p: p["rounds_won"]):
        group = list(group_iter)
        if pos >= paid_positions:
            break
        end = min(pos + len(group), paid_positions)
        combined_share = sum(pct_table[pos:end])
        per_player = int(prize_pool * combined_share / len(group))
        for p in group:
            payouts[p["user_id"]] = per_player
        pos += len(group)

    # Distribute rounding remainder to top finisher(s)
    total_paid = sum(payouts.values())
    leftover = prize_pool - total_paid
    if leftover > 0 and in_money:
        top_wins = in_money[0]["rounds_won"]
        top_group = [p for p in in_money if p["rounds_won"] == top_wins]
        extra = leftover // len(top_group)
        for p in top_group:
            payouts[p["user_id"]] += extra

    return payouts
