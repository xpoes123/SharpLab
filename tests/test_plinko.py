"""Tests for Plinko: risk-tier multiplier tables and multi-ball resolution."""

import math
import random
from itertools import combinations  # noqa: F401  (kept for parity w/ other tests)

from bot.cogs.plinko import (
    MAX_BALLS, MULTIPLIERS, RISK_CYCLE, PlinkoPlayer, PlinkoTable, _resolve_drops,
)


def _bucket_probs() -> list[float]:
    """Binomial probability of each of the 9 buckets over 8 rows."""
    return [math.comb(8, k) / 256 for k in range(9)]


def test_all_risk_tiers_are_roughly_fair() -> None:
    probs = _bucket_probs()
    for risk, mults in MULTIPLIERS.items():
        assert len(mults) == 9, risk
        ev = sum(p * m for p, m in zip(probs, mults))
        # Match the existing convention: ~fair, never a big house/player edge.
        assert 0.98 <= ev <= 1.02, f"{risk} EV={ev:.4f}"


def test_higher_tiers_have_bigger_top_multipliers() -> None:
    tops = [max(MULTIPLIERS[r]) for r in ("low", "medium", "high", "extreme", "insane")]
    assert tops == sorted(tops)  # strictly increasing edge payouts
    assert max(MULTIPLIERS["insane"]) > max(MULTIPLIERS["high"])


def test_risk_cycle_visits_every_tier_and_loops() -> None:
    seen = ["low"]
    r = "low"
    for _ in range(len(MULTIPLIERS) + 1):
        r = RISK_CYCLE[r]
        if r == "low":
            break
        seen.append(r)
    assert set(seen) == set(MULTIPLIERS)


def test_total_stake_is_bet_times_balls() -> None:
    p = PlinkoPlayer(user_id=1, display_name="A", bet=100, balls=3)
    assert p.total_stake == 300
    assert PlinkoPlayer(user_id=2, display_name="B", bet=50).total_stake == 50


def test_resolve_drops_pays_sum_of_balls() -> None:
    random.seed(7)
    table = PlinkoTable(channel_id=1, host_id=1, host_name="h", risk="high")
    table.players[1] = PlinkoPlayer(user_id=1, display_name="A", bet=100, balls=4)
    table.players[2] = PlinkoPlayer(user_id=2, display_name="B", bet=20, balls=1)

    _path, _bucket, showcase_mult = _resolve_drops(table)

    for p in table.players.values():
        assert len(p.ball_results) == p.balls
        assert p.payout == sum(math.floor(p.bet * m) for _b, m in p.ball_results)

    # Showcase multiplier is the single best drop of the whole round.
    all_mults = [m for p in table.players.values() for _b, m in p.ball_results]
    assert showcase_mult == max(all_mults)


def test_resolve_drops_handles_empty_table() -> None:
    table = PlinkoTable(channel_id=1, host_id=1, host_name="h", risk="low")
    path, bucket, mult = _resolve_drops(table)
    assert len(path) == 8
    assert 0 <= bucket <= 8
    assert mult == MULTIPLIERS["low"][bucket]


def test_max_balls_is_reasonable() -> None:
    assert 1 < MAX_BALLS <= 10
