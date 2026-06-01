Scaffold a new casino game cog end-to-end.

Usage: /new-game <game-name>
Examples: /new-game poker, /new-game connect4, /new-game sequence

**Before writing any code, read `GAMES.md` at the project root.** It is the single source of truth for every pattern, convention, and checklist item.

If the game involves a browser UI (interactive grid, drag-and-drop, real-time animation), use `/new-web-game` instead.

---

## Step 0 — Classify the game

Determine the game's **mode** and **archetype** before reading any reference cog:

| Mode | Description | Reference cogs |
|------|-------------|----------------|
| `solo` | Player vs house / single-player | `roulette.py`, `crash.py`, `slots.py` |
| `duo` | 1v1 with challenge/accept flow | `tictactoe.py`, `mastermind.py`, `liarsdice.py` |
| `party` | 2-8 players, lobby + game loop | `wordle.py`, `valorant.py`, `pokemon.py` |
| `solo` (sports sim) | Bet on simulated game outcome | `nbasim.py`, `nflsim.py` |

Also determine the **archetype**:

| Archetype | Pattern | Examples |
|-----------|---------|----------|
| House game | Fixed odds, no competition | Roulette, Slots, Crash, Plinko |
| Racing/trivia | First to answer or best score wins | Wordle, Pokemon, Valorant, Math 24, Geography |
| Board/strategy | Turn-based with game state | Tic Tac Toe, Mastermind, Liar's Dice |
| Simulation | Generate teams/ratings, simulate outcome | NBA Sim, NFL Sim, Soccer Sim |
| Data guessing | Show real-world data, guess the value | Stock Guess, Quiz Bowl |

---

## Step 1 — Read references

1. Read `GAMES.md` (full checklist — registration, side pots, solo play, timeouts, ELO, data)
2. Read **one** reference cog matching your mode (from the table above)
3. Read `bot/cogs/casino.py` registries: `GAME_LABELS` (~line 38), `GAME_CATEGORIES` (~line 93), `CASINO_GAMES` (~line 2363). Also read `bot/cogs/game_menu.py` `GAME_DISPATCH` (~line 17) and `PARAMETERIZED_SHORTCUTS` (~line 68) — the `/play` launcher routes games through these.
4. If the game has a curated data bank (trivia, guessing, etc.), read how `valorant.py` or `pokemon.py` structures its data

---

## Step 2 — Build the cog

Create `bot/cogs/<game>.py` following the reference cog exactly. Required structure:

```
# Constants section (MAX_PLAYERS, MIN_PLAYERS, WINS_TO_WIN, PAYTABLE, etc.)
# Data section (if applicable — questions, teams, items)
# Dataclasses (Player, Table/Game)
# Embed builders (_betting_embed, _round_embed, etc.)
# Modals (JoinModal with wager validation)
# Views (GameView with buttons for each phase)
# Cog class (active_tables dict, slash command, on_message if chat-based)
# setup() function
```

### Coin flow (MUST follow exactly):
```python
# 1. Check balance
bal = await queries.get_casino_balance(uid)
if bal < wager:
    # reject

# 2. Deduct on JOIN (not on game start)
await queries.update_casino_balance(uid, -wager)

# 3. Credit winnings after resolution
await queries.update_casino_balance(uid, payout)

# 4. Log for EVERY player (including losers with payout=0)
await queries.log_casino_result(uid, "game_name", wager, payout)
```

### Key invariants:
- `active_tables: dict[int, Table]` keyed by `channel_id` — one game per channel
- Every `View` implements `on_timeout()` with refund logic
- `table.last_bets` stores previous wagers for re-bet button
- `log_casino_result` game name MUST match the key in `GAME_LABELS`
- Party games: use `PAYTABLE` dict and `compute_side_pot_payouts` from `bot/cogs/_pool.py`

### For racing/trivia games (chat-based guessing):
- Add `@commands.Cog.listener("on_message")` to the cog class
- Normalize guesses: `unicodedata.normalize("NFKD", ...).lower().strip()`
- Accept alternate names/spellings (store as list of aliases per answer)
- Progressive hints: hint1 at 0s, hint2 at 10s, hint3 at 20s (30s round)
- Use `asyncio.Event` for answer detection within the round loop

### For 1v1 duo games:
- Challenger coins deducted on command invocation
- Opponent coins deducted on accept (via ChallengeView)
- Decline = refund challenger immediately
- Use `compute_side_pot_payouts` if bets can differ

### For sports sim games:
- Team ratings dict with real calibrated data
- Simulation function with period-by-period scoring
- Odds generation from ratings (spread, ML, total)
- Live embed updates showing score progression
- Bet resolution comparing final score to bet params

---

## Step 3 — Add ELO (if skill-based)

If the game involves skill (not pure luck):

1. Add an `elo_key` string constant (lowercase, underscore-separated, e.g. `"connect4"`)
2. Import from `bot/cogs/_elo_helpers.py`:
   - `update_elo_multiplayer(finish_order, game_key, context)` — for party games
   - `update_elo_1v1(winner_id, loser_id, game_key, context)` — for duo games
3. Call the ELO update after the game resolves (before or after coin payouts, doesn't matter)
4. Add the game to `ELO_GAME_LABELS` dict in `bot/cogs/_elo_helpers.py`
5. Display ELO changes in the results embed using `fmt_elo_change(old, new)`

---

## Step 4 — Curate game data (if applicable)

For trivia/guessing games, the data lives directly in the cog file as Python constants (not external files). Follow the `valorant.py` pattern:

- Each item is a tuple: `(id, name, [alt_names], category, origin/metadata, hint_text)`
- Organize with section comments (e.g. `# -- Launch agents (10)`)
- Be comprehensive — aim for 20+ items minimum
- Alt names list enables flexible answer matching
- Hint text should be detailed enough for progressive reveal (1-3 sentences)

---

## Step 5 — Register (5 places)

1. `bot/main.py` — add `"bot.cogs.<game>"` to `COGS` list
2. `bot/cogs/casino.py` — add tuple to `CASINO_GAMES` list (~line 2363): `("<command>", "<desc>", "<category>", "<mode>")`
3. `bot/cogs/casino.py` — add entry to `GAME_LABELS` dict (~line 38): `"<command>": "<Display Name>"`
4. **`bot/cogs/game_menu.py` — add an entry to `GAME_DISPATCH` (~line 17): `"<command>": ("<CogClassName>", "<callback_method_name>")`.** This is the `/play` launcher map — **if you skip this, `/play` won't list or launch the game** even though the cog loads. If the launch needs args (e.g. an opponent), add the key to `PARAMETERIZED_SHORTCUTS` (~line 68) and handle it there instead.
5. Bottom of cog file — `async def setup(bot): await bot.add_cog(GameCog(bot))`

> The category in `CASINO_GAMES` must be one of the `GAME_CATEGORIES` names (~line 93):
> "Card Games", "Table & Arcade", "Party Games", "Brain Games", "Sports Sim".

---

## Step 6 — Verify

- [ ] `/games` shows the new game in the correct category
- [ ] `/random-game` can select it (with and without mode filter)
- [ ] Solo play works (if applicable — join alone, play to completion)
- [ ] Side pots correct with unequal bets (if multiplayer)
- [ ] Timeout during betting phase refunds all players
- [ ] Timeout during playing phase handles gracefully
- [ ] `/casino-stats` shows the correct label after a round
- [ ] Re-bet button works for returning players
- [ ] ELO updates appear in results embed (if skill-based)
- [ ] Edge cases: max players, min bet, zero balance join attempt
