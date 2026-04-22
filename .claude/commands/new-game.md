Scaffold a new casino game cog end-to-end.

Usage: /new-game <game-name>
Examples: /new-game poker, /new-game trivia, /new-game connect4

**Before writing any code, read `GAMES.md` at the project root.** It contains the full checklist for new games — registration, side pots, solo play, timeouts, and testing.

Workflow:
1. Read `GAMES.md` for the complete new-game checklist
2. Read `bot/cogs/casino.py` to understand the game registry (`CASINO_GAMES`, `GAME_LABELS`, `GAME_CATEGORIES`)
3. Read an existing game cog that's similar in mode:
   - Solo: `bot/cogs/roulette.py` or `bot/cogs/crash.py`
   - Duo: `bot/cogs/casino.py` (blackjack) or `bot/cogs/mastermind.py`
   - Party: `bot/cogs/bingo.py`, `bot/cogs/wordle.py`, or `bot/cogs/sudoku.py`
4. Create `bot/cogs/<game>.py` following the pattern of the reference cog:
   - Dataclasses for Player and Table state
   - Join modal with wager validation + balance deduction
   - View with buttons for each game phase
   - `on_timeout()` with full refund logic
   - Re-bet support via `table.last_bets`
   - `log_casino_result()` for every player in every round
5. For multiplayer games with variable bets: use `compute_side_pot_payouts` from `bot/cogs/_pool.py`
6. Register the game in all 4 places:
   - `bot/main.py` — add to `COGS` list
   - `bot/cogs/casino.py` — add to `CASINO_GAMES` (command, desc, category, mode)
   - `bot/cogs/casino.py` — add to `GAME_LABELS`
   - The cog file itself — `async def setup(bot)` at the bottom
7. Verify:
   - [ ] `/games` shows the new game in the correct category
   - [ ] `/random-game` can select it
   - [ ] Solo play works (if applicable)
   - [ ] Side pots are correct with unequal bets (if multiplayer)
   - [ ] Timeout during betting refunds all players
   - [ ] `/casino-stats` shows the correct label after a round
