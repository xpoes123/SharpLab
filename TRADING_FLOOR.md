# Trading Floor — Game Spec

A deep market simulation game for `/tradingfloor`. Replaces the shallow `/stockmarket` with a game where player trades move prices, information is asymmetric, NPCs create noise, and second-order thinking is rewarded.

**Target audience**: quants, SWEs, poker players — anyone who wants to reason about markets.

**Game key**: `"tradingfloor"` in `GAME_LABELS`. Separate cog from `stockmarket.py` (that game stays as the casual version).

---

## Why the Current Stock Market Game Doesn't Work

| Problem | Detail |
|---|---|
| Trades don't affect prices | Everyone sees the same prices regardless of what anyone buys/sells |
| Events before trading | Prices move from the event *before* the trade window opens — no edge |
| No hidden info | Every player sees the same board. No private signals, no inference |
| No reason to sell | Holding costs nothing, no short selling, no rotation incentive |
| MEME lottery | One stock has 2x the variance of everything else — just yolo it |
| No NPCs | No market noise, no liquidity, no ambient activity |
| Modals too slow | Typing a ticker into a modal takes 10-20s. 30s window = 1 trade max |

---

## Core Design

### Stocks: 4 tickers, 2 correlated sectors

| Ticker | Sector | Name | Emoji |
|--------|--------|------|-------|
| CHIP | Tech | ChipWorks | `💻` |
| SOFT | Tech | SoftLayer | `📱` |
| OIL | Energy | OilCorp | `🛢️` |
| SOLAR | Energy | SolarGrid | `☀️` |

All start at **100c**. Float prices (one decimal).

### Correlation matrix

```
       CHIP  SOFT   OIL  SOLAR
CHIP   1.0   0.6  -0.2  -0.2
SOFT   0.6   1.0  -0.2  -0.2
OIL   -0.2  -0.2   1.0   0.6
SOLAR -0.2  -0.2   0.6   1.0
```

Within-sector stocks move together (~0.6). Cross-sector stocks move slightly opposite (~-0.2). Implementation: Cholesky decomposition of the correlation matrix to generate correlated random shocks each round.

### Game parameters

| Param | Value |
|-------|-------|
| Players | 2-8 |
| Rounds | 8 |
| Trading window | 45 seconds |
| Starting in-game cash | 10,000c |
| Buy-in | Casino coins (variable, goes to pot) |
| Payout | Side-pot system via `_pool.py` |

---

## Round Structure

Each of the 8 rounds follows this sequence:

### 1. Pre-round: Private tips (5s)
- 1-2 random players receive an **ephemeral DM or followup** with a tip about the upcoming event
- Tip examples: "Insider: CHIP earnings beat — expect strong tech performance" or "Rumor: energy regulation incoming"
- Tips have a **reliability rating** (shown to the player):
  - `★★★` High confidence (90% accurate — the event will match)
  - `★★☆` Medium (70% — directionally correct but magnitude may vary)
  - `★☆☆` Low (50% — could be noise)
- Players who get tips can start trading immediately when the window opens. Others see the event *after* trading closes.

### 2. Trading window (45s)
- All players can buy and sell via **buttons** (no modals for basic trades)
- NPC traders also execute during this window
- A countdown timer updates every 10s in the embed
- Order book (limit orders) is active

### 3. Settlement
- Player market orders and NPC trades are batched → net volume per stock computed
- **Market impact** applied: `Δprice = k × √|net_vol| × sign(net_vol)` where `k ≈ 0.3-0.5`
- **Correlated random shock** applied (from Cholesky decomposition)
- **Event card effect** applied to "fair values" (NPC mean-reversion targets update)
- Circuit breakers checked

### 4. Post-round reveal (3s)
- Event card shown publicly (now everyone sees what tipped players already knew)
- Price changes displayed with arrows and percentages
- Sparkline price history per stock
- Player P&L leaderboard updated
- News feed updated with public events + anonymous volume info

---

## Trading Mechanics

### Button layout (5 rows × 5 buttons max)

```
Row 0: [Buy CHIP]  [Sell CHIP]  [Buy SOFT]  [Sell SOFT]  [📊 Portfolio]
Row 1: [Buy OIL]   [Sell OIL]   [Buy SOLAR] [Sell SOLAR] [📋 Limit Order]
Row 2: [Short CHIP] [Short SOFT] [Short OIL] [Short SOLAR] [🔄 Cover All]
Row 3: [⚡ Leverage] [📰 News]   [📖 Book]  [📈 Metrics] [      ]
Row 4: [🔄 New Game] [🚪 Close]
```

**Buy/Sell buttons**: Each click = **1 share** at current market price. Instant execution. Fast clicks = multiple shares.

**Limit Order button**: Opens a modal with fields: `Ticker`, `Side (buy/sell)`, `Price`, `Quantity`. Order sits on the book until filled or round ends.

**Short buttons**: Sell shares you don't own. Creates a negative position. Shorting 1 share of CHIP at 105c means you owe 1 share — if CHIP drops to 90c, you profit 15c when you cover.

**Cover All**: Close all short positions at current market prices.

**Leverage toggle**: Switches between 1x (default) and 2x. At 2x, your buying power doubles but so does your risk. If portfolio value drops below 20% of starting cash (2,000c), **margin call** — all positions force-liquidated at market price.

**Portfolio button**: Ephemeral message showing your cash, positions (long + short), unrealized P&L, total portfolio value.

**Book button**: Ephemeral message showing order book depth for all 4 stocks (top 3 bids + top 3 asks per stock).

**Metrics button**: Ephemeral message showing your round-by-round P&L, position exposure per sector, and current leverage ratio.

**News button**: Ephemeral message showing the last 10 news feed entries.

### Market orders vs limit orders

- **Market order** (buy/sell buttons): Executes immediately. Checks limit order book first — if there's a matching limit order at a better price, fills against it. Otherwise fills at current market price.
- **Limit order** (modal): Sits on the book. Filled when another player's market order crosses it, or when NPC trades cross it. Unfilled orders at round end remain for the next round (persistent across rounds). Can be cancelled via the Book view.

### Short selling rules

- You can short up to 10 shares per stock (prevents infinite shorting)
- Short positions have a **borrow cost**: 1% of position value per round (deducted from cash at settlement)
- At game end, all shorts are force-closed at final market price
- Short P&L: `shares × (entry_price - current_price)` (negative shares, so price drop = profit)

---

## Price Model

Each round's price update has 4 components:

### 1. Correlated random shocks
Using the Cholesky decomposition `L` of the correlation matrix:

```python
z = [random.gauss(0, 1) for _ in range(4)]  # independent normals
correlated = L @ z                            # correlated normals
for i, stock in enumerate(stocks):
    stock.fair_value += correlated[i] * volatility  # volatility ≈ 3-5c per round
```

This ensures CHIP and SOFT move together, OIL and SOLAR move together, and tech/energy are slightly inverse.

### 2. Event effects
Each event card modifies fair values:
```python
for ticker, pct in event.effects.items():
    stocks[ticker].fair_value *= (1 + pct)
```

Fair values are **hidden** — only the mean-reversion NPC trades toward them. Players see price, not fair value.

### 3. Market impact (from player + NPC trades)
```python
net_vol = sum(buy_quantities) - sum(sell_quantities)  # per stock
price_delta = k * math.sqrt(abs(net_vol)) * (1 if net_vol > 0 else -1)
stock.price += price_delta
```

`k ≈ 0.4`. Square root dampens large orders so one whale can't 10x a stock, but buying pressure still moves the price meaningfully.

### 4. Circuit breaker
If a stock's price moves > 25% in a single round (from all sources combined), it is **halted** for the remainder of that round — no more trades in that stock. The halt is announced in the news feed.

---

## NPC Traders

Three NPC types trade each round to create realistic market noise:

### Noise Trader ("Retail Investor")
- Randomly buys or sells 1-3 shares of random stocks each round
- No strategy, pure noise
- Adds baseline volume so prices move even when players are idle

### Momentum Chaser ("Algo Fund")
- Looks at each stock's price change over the last 2 rounds
- Buys stocks trending up (positive momentum), sells stocks trending down
- Volume proportional to trend strength: `abs(pct_change) * 2` shares
- Creates trend reinforcement — a stock going up will attract more buying

### Mean Reversion Bot ("Smart Money")
- Knows each stock's hidden fair value
- Buys stocks trading below fair value, sells stocks trading above
- Trade size proportional to the gap: `(fair_value - price) / 10` shares (rounded)
- Acts as a stabilizing force — prevents prices from going to infinity or zero
- Also creates a soft floor/ceiling around fair value that observant players can infer

### NPC order execution
NPC orders are placed as market orders during the trading window. They're batched with player orders at settlement. NPC volume is visible in the news feed ("Heavy algo buying detected in OIL — 5 shares") but not attributed to specific NPCs.

---

## Private Information System

### Tip distribution
- Rounds 1-2: 1 player gets a tip
- Rounds 3-5: 1-2 players get tips
- Rounds 6-8: 2 players get tips (more information in later rounds creates faster-moving markets)

### Tip format
Delivered as ephemeral followup (only the recipient sees it):

```
🔒 INSIDER TIP (★★★ High confidence)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHIP is expected to surge after earnings beat.
Tech sector outlook: strongly positive.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ This information is private. Trade wisely.
```

### Tip reliability mechanics
- `★★★` (90%): The event's sector direction is guaranteed correct. Magnitude is accurate ±5%.
- `★★☆` (70%): Sector direction is correct, but could be a minor move (the tip might say "surge" but the actual effect is +10% not +30%).
- `★☆☆` (50%): Essentially coin flip. Could be correct or completely wrong. Exists to create uncertainty about tip quality.

Distribution of reliability: 40% high, 40% medium, 20% low.

### Information inference
Observant players can infer that *someone* got a tip by watching for unusual early-round buying. If CHIP volume spikes before the event is revealed, someone probably knows something. This creates an inference game on top of the trading game.

---

## Event System

### Event cards (24 total, 8 drawn per game)
Reuse the flavor from the existing `stockmarket.py` EVENT_CARDS but adapted for 4 stocks:

```python
EventCard("Tech Boom", "💻", "AI breakthrough drives tech stocks higher.",
          {"CHIP": 0.25, "SOFT": 0.20, "OIL": -0.10, "SOLAR": -0.05}),
EventCard("Energy Crisis", "⚡", "Supply disruption sends energy soaring.",
          {"OIL": 0.30, "SOLAR": 0.20, "CHIP": -0.10, "SOFT": -0.15}),
EventCard("Market Crash", "📉", "Panic selling across all sectors.",
          {"CHIP": -0.20, "SOFT": -0.25, "OIL": -0.15, "SOLAR": -0.20}),
# ... etc.
```

### Event chains (advanced)
Some events increase the probability of follow-up events:
- "Energy Crisis" in round 2 → "Green Energy Boom" 3x more likely in rounds 4-6
- "Market Crash" → "Stimulus Package" 2x more likely in next 2 rounds
- "Tech Boom" → "Regulatory Crackdown" 2x more likely in next 3 rounds

Implementation: an `event_modifiers: dict[str, float]` on the table state. When drawing the next event, weight probabilities by these modifiers. Players who recognize the pattern can position ahead of likely follow-ups.

---

## Leverage & Margin

### How leverage works
- Default: 1x (normal). Toggle to 2x via button.
- At 2x: buying power = 2 × cash. You can buy twice as many shares with the same cash.
- Internally: cash can go negative (borrowed money). Portfolio value = cash + longs - shorts.
- Leverage applies to both longs and shorts.

### Margin call
- Threshold: portfolio value drops below **20% of starting cash** (2,000c with default 10,000c start)
- When triggered:
  1. All positions force-liquidated at current market price
  2. Player's cash set to whatever remains after liquidation
  3. `⚠️ MARGIN CALL: [Player] has been liquidated!` posted to news feed
  4. Player can still trade (re-enter positions) but is way behind

### Why this matters
Leverage amplifies both gains and losses. A leveraged long on a stock that drops 25% could wipe out most of your cash. A leveraged short on a stock that surges 30% is even worse. It creates genuine risk management decisions:
- "I'm 70% sure CHIP goes up, but should I lever? What if I'm wrong?"
- "My portfolio is at 3,000c with 2x leverage. One bad round and I'm margin called."

---

## News Feed

A scrolling list of strings shown in the embed footer or a dedicated field. Updated each round:

```
📰 NEWS FEED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[R5] 🔴 CIRCUIT BREAKER: OIL halted after 28% surge
[R5] 📊 Heavy buying in CHIP detected — 12 shares traded
[R5] 📈 SOLAR +15.2% this round
[R4] 💰 Stimulus Package announced — broad market rally
[R4] 📊 Low volume round — only 6 total shares traded
[R3] ⚠️ MARGIN CALL: Alice has been liquidated!
```

News entries include:
- Public event reveals (after trading window closes)
- Anonymous aggregate volume per stock ("heavy buying/selling detected")
- Circuit breaker alerts
- Margin call announcements
- Round-over-round price changes for each stock
- Limit order fills ("Limit buy in CHIP filled at 98.5")

---

## End of Game

After round 8:

1. **Force-close all shorts** at final market price
2. **Cancel all unfilled limit orders**
3. **Compute final portfolio values**: `cash + Σ(long_shares × price) - Σ(|short_shares| × price)`
4. **Rank players** by portfolio value
5. **Payout** via `compute_side_pot_payouts(buy_ins, [winner_uids])`
6. **Log** to `casino_history` as game `"tradingfloor"` for each player
7. **Display final standings** embed:
   - Each player: portfolio value, net P&L, # trades, best trade, worst trade
   - Stock final prices vs starting (100c) with full sparkline history
   - MVP stat: "Most profitable single trade: Bob bought CHIP at 85 → sold at 127 (+42c)"

---

## Embed Layout

### Main game embed (during trading)

```
📈 Trading Floor — Round 4/8 [35s remaining]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💻 CHIP   112.3c  📈+5.2%  ▁▂▃▅▆▆█▇
📱 SOFT   108.7c  📈+3.1%  ▁▂▃▄▅▅▆▇
🛢️ OIL     87.4c  📉-8.6%  ▅▆▅▃▂▁▁▂
☀️ SOLAR   91.2c  📉-4.3%  ▅▅▄▃▃▂▂▃

📊 STANDINGS
1. Alice    11,240c  📈+12.4%
2. Bob      10,510c  📈+5.1%
3. Charlie   9,830c  📉-1.7%
4. Dave      8,420c  📉-15.8%  ⚡2x

📰 Tech Boom announced last round — CHIP & SOFT surged
📊 Heavy selling in OIL detected (8 shares)
```

The embed updates every 10s during the trading window.

### Portfolio (ephemeral, on button click)

```
📋 Your Portfolio — Round 4
━━━━━━━━━━━━━━━━━━━━━━━━━━
Cash:    4,230c
Leverage: 1x

POSITIONS
💻 CHIP    +5 shares  @ avg 102.1  → 112.3  (unrealized +51.0c)
📱 SOFT    +3 shares  @ avg 105.0  → 108.7  (unrealized +11.1c)
🛢️ OIL    -2 shares  @ avg 95.0   → 87.4   (unrealized +15.2c) [SHORT]

Total value: 10,510c  (net +5.1%)
```

### Order Book (ephemeral, on button click)

```
📖 Order Book
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💻 CHIP (market: 112.3)
  BID               ASK
  3 @ 111.0         2 @ 113.5
  1 @ 110.5         1 @ 114.0

📱 SOFT (market: 108.7)
  BID               ASK
  (empty)           1 @ 110.0

🛢️ OIL (market: 87.4)
  BID               ASK
  2 @ 86.0          (empty)

☀️ SOLAR (market: 91.2)
  (no orders)
```

---

## Dataclass Architecture

```python
@dataclass
class TFStock:
    ticker: str
    sector: str         # "tech" | "energy"
    emoji: str
    name: str
    price: float = 100.0
    fair_value: float = 100.0  # hidden, NPC mean-reversion target
    history: list[float] = field(default_factory=lambda: [100.0])
    halted: bool = False       # circuit breaker

@dataclass
class LimitOrder:
    order_id: int
    player_uid: int
    ticker: str
    side: str           # "buy" | "sell"
    price: float
    quantity: int
    round_placed: int

@dataclass
class OrderBook:
    bids: list[LimitOrder]  # sorted price descending (best bid first)
    asks: list[LimitOrder]  # sorted price ascending (best ask first)

@dataclass
class TFPlayer:
    user_id: int
    display_name: str
    bet: int                 # buy-in (casino coins, goes to pot)
    cash: float = 10_000.0   # in-game cash
    positions: dict[str, int] = field(default_factory=dict)    # ticker -> shares (neg = short)
    entry_prices: dict[str, list[float]] = field(default_factory=dict)  # for avg cost tracking
    leverage: float = 1.0    # 1.0 or 2.0
    margin_called: bool = False
    trade_count: int = 0

    def portfolio_value(self, stocks: dict[str, TFStock]) -> float:
        """cash + longs - shorts at current prices."""
        val = self.cash
        for ticker, qty in self.positions.items():
            if ticker in stocks:
                val += qty * stocks[ticker].price  # negative qty for shorts works naturally
        return val

    def buying_power(self, stocks: dict[str, TFStock]) -> float:
        """Available cash considering leverage."""
        return max(0, self.cash * self.leverage)

@dataclass
class NPC:
    name: str
    style: str  # "noise" | "momentum" | "mean_reversion"

@dataclass
class TradingFloor:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "lobby"     # lobby | pre_round | trading | settling | finished
    players: dict[int, TFPlayer] = field(default_factory=dict)
    stocks: dict[str, TFStock] = field(default_factory=dict)
    order_books: dict[str, OrderBook] = field(default_factory=dict)
    npcs: list[NPC] = field(default_factory=list)
    event_deck: list[EventCard] = field(default_factory=list)
    current_event: EventCard | None = None
    round_num: int = 0
    round_timer: int = 0
    news_feed: list[str] = field(default_factory=list)
    private_tips: dict[int, str] = field(default_factory=dict)
    event_modifiers: dict[str, float] = field(default_factory=dict)  # event chain weights
    message: discord.Message | None = None
    game_task: asyncio.Task | None = field(default=None, repr=False)
    trade_locked: bool = False
    game_num: int = 1
    last_bets: dict[int, tuple[str, int]] = field(default_factory=dict)
    winners: list[int] = field(default_factory=list)
    next_order_id: int = 0
```

---

## Implementation Phases

### Phase 1 — MVP (playable core)
- 4 stocks, 2 sectors (no correlation yet — just independent random walks)
- Basic event cards (12-16 cards)
- Buy/Sell buttons (market orders only, 1 share per click)
- Noise trader NPC only
- 8-round game loop, 45s trading windows
- Portfolio button (ephemeral)
- Side-pot payout at end
- `log_casino_result` + `on_casino_result` wiring

**Key files**: `bot/cogs/tradingfloor.py` (new), add to `bot/main.py` COGS + `casino.py` GAME_LABELS

### Phase 2 — Depth (the "aha" mechanics)
- Correlated price model (Cholesky decomposition)
- Market impact function (`k × √|net_vol|`)
- Short selling + Cover All button
- Momentum + mean-reversion NPCs
- Private tips (ephemeral followups, reliability tiers)
- News feed in embed
- Player P&L leaderboard in embed

### Phase 3 — Advanced (full market sim)
- Limit orders + order book (modal for placement, ephemeral book view)
- Leverage toggle (1x/2x) + margin calls
- Circuit breakers (>25% halt)
- Event chain system (conditional probabilities)
- Risk metrics button (exposure, leverage ratio, per-round P&L)
- Short borrow cost (1%/round)
- End-of-game detailed stats (best trade, worst trade, MVP)

---

## Balance & Tuning

### Price volatility
Target: prices should move 5-15% per round on average (event + noise + player impact combined). After 8 rounds, a stock should plausibly range from 50c-200c (enough range to reward skill, not so much that it's random).

### NPC volume calibration
- Noise: 2-4 shares/round across all stocks
- Momentum: 0-6 shares/round (proportional to trend)
- Mean reversion: 0-4 shares/round (proportional to mispricing)
- Total NPC volume: ~5-15 shares/round. Player volume should be roughly comparable.

### Information edge value
A high-confidence tip should be worth ~500-1500c if acted on optimally (buy 5-10 shares before a 15% move). This is enough to matter but not enough to guarantee winning the game (other players can still outperform through better overall trading).

### Market impact calibration
With `k=0.4` and `√` dampening:
- 1 share: 0.4c price move (negligible)
- 4 shares: 0.8c move (noticeable)
- 9 shares: 1.2c move (significant — roughly 1% at 100c)
- 25 shares: 2.0c move (large — getting expensive to buy more)

This means the first few shares are cheap but loading up gets progressively more expensive. Encourages diversification over concentration.

---

## No New DB Tables Needed

Trading Floor uses existing infrastructure only:
- `casino_wallets` — buy-in/payout
- `casino_history` — game logging (key = `"tradingfloor"`)
- `_pool.py` — side-pot payout
- `_progression.py` — XP/achievements/daily challenges (via `on_casino_result`)
