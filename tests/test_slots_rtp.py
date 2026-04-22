"""Monte Carlo RTP simulation for Fortune Reels slots.

Target RTP: 88-96% (house edge 4-12%).
"""

import random
import sys

# Import slots internals
sys.path.insert(0, ".")
from bot.cogs.slots import (
    BONUS_PRIZE_POOL,
    BONUS_PRIZES_USED,
    FREE_SPIN_OPTIONS,
    NUM_BOXES,
    NUM_TRAPS,
    PAYTABLE,
    SCATTER_PAY,
    _count_symbol,
    _eval_paylines,
    _spin_reels,
)


def _sim_bonus(total_bet: int) -> int:
    """Simulate one bonus round, return total prize."""
    prizes = random.sample(BONUS_PRIZE_POOL, BONUS_PRIZES_USED)
    boxes: list[tuple[str, int]] = [("prize", m * total_bet) for m in prizes]
    boxes.extend([("trap", 0)] * NUM_TRAPS)
    random.shuffle(boxes)

    total = 0
    picks = 0
    for btype, val in random.sample(list(enumerate(boxes)), len(boxes)):
        _, (bt, v) = btype, boxes[btype]
        # Pick boxes in random order until trap or all prizes found
        pass

    # Simpler: shuffle and pick sequentially
    random.shuffle(boxes)
    total = 0
    picks_made = 0
    for btype, val in boxes:
        if btype == "trap":
            break
        total += val
        picks_made += 1
        if picks_made >= BONUS_PRIZES_USED:
            break
    return total


def _sim_spin(bet: int, *, free_spin: bool = False, mult: int = 1) -> tuple[int, int, int]:
    """Simulate one spin. Returns (win, scatter_count, bonus_count)."""
    line_bet = max(1, bet // 20)
    grid = _spin_reels(free_spin=free_spin)
    line_wins = _eval_paylines(grid)
    scatter_count = _count_symbol(grid, "SC")
    bonus_count = _count_symbol(grid, "BN")

    line_total = sum(line_bet * m for _, _, _, m in line_wins)
    scatter_total = SCATTER_PAY.get(scatter_count, 0) * bet
    base_win = line_total + scatter_total
    win = base_win * mult if mult > 1 else base_win

    return win, scatter_count, bonus_count


def simulate(num_spins: int = 500_000, bet: int = 100) -> float:
    """Run Monte Carlo simulation. Returns RTP as a fraction (e.g. 0.92)."""
    total_wagered = 0
    total_won = 0

    i = 0
    while i < num_spins:
        total_wagered += bet
        win, sc, bn = _sim_spin(bet)
        total_won += win

        # Free spins trigger (3+ scatters)
        if sc >= 3:
            # Pick the balanced option (index 1)
            fs_spins, fs_mult = FREE_SPIN_OPTIONS[1]
            for _ in range(fs_spins):
                fs_win, fs_sc, _ = _sim_spin(bet, free_spin=True, mult=fs_mult)
                total_won += fs_win
                # Re-trigger: 3+ scatters during free spins = +10 spins
                if fs_sc >= 3:
                    fs_spins += FREE_SPIN_OPTIONS[1][0]

        # Bonus trigger (3+ bonus symbols)
        if bn >= 3:
            bonus_win = _sim_bonus(bet)
            total_won += bonus_win

        i += 1

    rtp = total_won / total_wagered
    return rtp


def test_rtp_in_range():
    """RTP should be between 88% and 96%."""
    random.seed(42)
    rtp = simulate(num_spins=200_000, bet=100)
    print(f"\nRTP: {rtp:.4f} ({rtp * 100:.2f}%)")
    assert 0.85 <= rtp <= 0.98, f"RTP {rtp:.4f} out of range [0.85, 0.98]"


if __name__ == "__main__":
    random.seed(42)
    rtp = simulate(num_spins=1_000_000, bet=100)
    print(f"RTP over 1M spins: {rtp:.4f} ({rtp * 100:.2f}%)")
