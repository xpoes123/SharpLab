"""Monte-Carlo edge calc for Breakout Blackjack (bot/cogs/breakout.py + web/breakout.py).

Design: the main Player/Dealer bet is EVEN MONEY (1:1). Both hands auto-play by fixed
house rules (hit below 17, HIT soft 17 = H17). The sides are symmetric — a double-bust
pushes — so the main bet is a ~0%-edge coin flip; the house edge lives entirely in the
Breakout side bet (total cards dealt across both hands), tuned here to ~1.1%.

Run: python3 scripts/breakout_edge.py
"""
import random

DECK = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]  # 11 = ace


def val(cards):
    t = sum(cards)
    a = cards.count(11)
    while t > 21 and a:
        t -= 10
        a -= 1
    return t, (a > 0 and t <= 21)  # (best total, is_soft)


def play(cards):
    while True:
        t, soft = val(cards)
        if t < 17 or (t == 17 and soft):  # H17
            cards.append(random.choice(DECK))
            continue
        break
    return cards


# Breakout side bet: total cards -> win odds (X:1); <7 loses. 12+ uses key 12.
BREAKOUT = {7: 1, 8: 4, 9: 8, 10: 20, 11: 75, 12: 150}


def breakout_odds(n):
    return BREAKOUT.get(12, 0) if n >= 12 else BREAKOUT.get(n, 0)


def main():
    N = 10_000_000
    random.seed(99)
    W = 100
    main_net = bo_net = 0
    for _ in range(N):
        p = play([random.choice(DECK), random.choice(DECK)])
        d = play([random.choice(DECK), random.choice(DECK)])
        pt, dt = val(p)[0], val(d)[0]
        pb, db = pt > 21, dt > 21
        # even-money Player bet, true push, symmetric (double-bust pushes)
        if (pb and db) or pt == dt:
            main_net += 0
        elif pb or (not db and dt > pt):
            main_net += -W
        else:
            main_net += W
        x = breakout_odds(len(p) + len(d))
        bo_net += (W * x) if x else -W
    print(f"Main (even money): house edge = {-main_net / (N * W) * 100:.3f}%  (expect ~0)")
    print(f"Breakout side bet: house edge = {-bo_net / (N * W) * 100:.3f}%")


if __name__ == "__main__":
    main()
