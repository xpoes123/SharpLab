Compute Closing Line Value (CLV) for recent bets. CLV = did you beat the closing line?

CLV > 0 means you got a better number than where the market settled. Consistently positive CLV = skill.
CLV < 0 means the line moved against you. Even if you won, you got worse of it than the market.

Workflow:
1. Read the `bets` table for bets with status 'open' or 'won'/'lost' but no CLV filled in yet
2. For each bet, find the corresponding `close` snapshot in `odds_snapshots`:
   - Match on `game_id` and `source` (book where you placed the bet)
   - Filter where `kind = 'close'`
3. Extract the closing line from `payload` JSON for the relevant market (spread, moneyline, total)
4. Compute CLV:
   - For spread bets: CLV = (close_spread - bet_line) × direction (positive if line moved in your favor)
   - For moneyline/total: convert American odds to implied probability, compute difference
5. Update the `clv` column in the `bets` table for each bet that now has a close snapshot
6. Print a summary:
   - Table: game | market | bet line | close line | CLV | result
   - Average CLV across all computed bets
   - Win rate
   - Flag any bets where CLV and result disagree badly (bad beat or lucky win)
7. If no close snapshots exist yet (pipeline not running), say so clearly — don't compute fake CLV

Notes:
- CLV is undefined if the close capture didn't run for that game. Check `kind = 'close'` rows exist.
- If Kalshi is the book, the "close" is the final market price before resolution.
- Positive CLV at sufficient sample size (50+ bets) is the primary signal that the process is working.
