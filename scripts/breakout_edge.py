"""Monte-Carlo edge calc for Breakout Blackjack (bot/cogs/breakout.py).

Reproduces the ~1% house edge on Player (2.17x), Dealer (1.82x) and the
Breakout total-cards side bet. Run: python3 scripts/breakout_edge.py
"""
import random
DECK=[2,3,4,5,6,7,8,9,10,10,10,10,11]
def total(c):
    t=sum(c);a=c.count(11)
    while t>21 and a: t-=10;a-=1
    return t
def play(c):
    while total(c)<17: c.append(random.choice(DECK))
    return c
PLAYER_MULT=2.17; DEALER_MULT=1.82
BREAKOUT={7:1,8:4,9:12,10:25,11:75}  # 12+ ->200
def breakout_odds(n): return 200 if n>=12 else BREAKOUT.get(n,0)
N=5_000_000; random.seed(7); W=100
hp=hd=hb=0
for _ in range(N):
    p=play([random.choice(DECK),random.choice(DECK)])
    d=play([random.choice(DECK),random.choice(DECK)])
    pt,dt=total(p),total(d); pb,db=pt>21,dt>21
    if pb or (not db and dt>pt): win="dealer"
    elif db or pt>dt: win="player"
    else: win="push"
    # main payouts (floored), net for a 100-coin bet on each side
    if win=="player": hp += int(W*PLAYER_MULT)-W
    elif win=="push": hp += 0
    else: hp += -W
    if win=="dealer": hd += int(W*DEALER_MULT)-W
    elif win=="push": hd += 0
    else: hd += -W
    nc=len(p)+len(d); x=breakout_odds(nc)
    hb += (W*x) if x else -W   # X:1, net
print(f"Main PLAYER bet: house edge = {-hp/(N*W)*100:.3f}%")
print(f"Main DEALER bet: house edge = {-hd/(N*W)*100:.3f}%")
print(f"Breakout side:   house edge = {-hb/(N*W)*100:.3f}%")
