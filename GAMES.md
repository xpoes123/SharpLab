# New Game Checklist

Practices and registration steps for adding a new casino game to SharpLab.

Two skills exist:
- `/new-game` — Discord-only games (buttons, modals, chat-based input)
- `/new-web-game` — browser-based games (interactive grid, real-time WebSocket)

---

## 1. Registration (4 places to update)

Every new game must be wired into the discovery system. Miss one and it's invisible or broken.

### a. `bot/main.py` — COGS list

Add `"bot.cogs.<your_game>"` to the `COGS` list so the bot loads it on startup.

### b. `bot/cogs/casino.py` — CASINO_GAMES

Add a tuple to the `CASINO_GAMES` list:

```python
("<command>", "<short description>", "<category>", "<mode>"),
```

- **command**: the slash command name (no `/`)
- **description**: one-line summary shown in `/games`
- **category**: one of `Card Games`, `Table & Arcade`, `Party Games`, `Brain Games`, `Sports Sim`
- **mode**: `solo`, `duo`, or `party`

This powers both `/games` (paginated browse) and `/random-game` (filtered random pick).

### c. `bot/cogs/casino.py` — GAME_LABELS

Add an entry to the `GAME_LABELS` dict:

```python
"<command>": "<Display Name>",
```

This is used in `/casino-stats` per-game breakdown. Without it the game name falls back to `.capitalize()` which may look wrong.

### d. `bot/main.py` — Cog file exists

Create `bot/cogs/<your_game>.py` with the standard `setup()` at the bottom:

```python
async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(YourGameCog(bot))
```

---

## 2. Solo play

If the game can be played alone, it **must** work solo. A player shouldn't need to recruit someone just to try it out.

- For games marked `mode="party"` or `mode="duo"`: decide whether solo is a valid experience. If it is, set `MIN_PLAYERS = 1` and make sure the game loop doesn't crash with a single player.
- For racing/tournament games (wordle, sudoku, etc.): solo should still run the full game loop — the player just auto-wins.
- Test it: open the game, join alone, start it. Does it complete? Does it pay out correctly?

---

## 3. Side pots

Any multiplayer game with variable bet sizes **must** use `compute_side_pot_payouts` from `bot/cogs/_pool.py`.

```python
from bot.cogs._pool import compute_side_pot_payouts

payouts = compute_side_pot_payouts(
    bets={uid: amount for uid, p in table.players.items()},
    winner_uids=[winner.user_id],
    house_edge=0.0,
)
```

Rules:
- A player can only win from each opponent **up to the amount they bet** (poker-style).
- Excess from higher bettors is refunded.
- If all bets are equal, the winner takes the full pot — but always run it through `compute_side_pot_payouts` anyway for consistency.
- **Test with unequal bets**: player A bets 100, player B bets 500. A wins. A should get 200 (their 100 + B's 100), B should get 400 back. Never let a small bettor win more than they could cover.

For games with a fixed paytable (racing games where top N places split the pot by percentage), side pots don't apply — but the paytable must handle all player counts down to 1.

---

## 4. Wager & balance handling

Follow the established pattern exactly:

```python
# Check balance
bal = await queries.get_casino_balance(uid)
if bal < wager:
    # reject

# Deduct immediately on join
await queries.update_casino_balance(uid, -wager)

# Credit winnings after resolution
await queries.update_casino_balance(uid, payout)

# Log the round
await queries.log_casino_result(uid, "game_name", wager, payout)
```

- Deduct on join, not on game start. This prevents double-joins or race conditions.
- `log_casino_result` must be called for **every** player in **every** completed round, including losers (wager=X, payout=0). This feeds `/casino-stats`.
- The `game` string in `log_casino_result` must match the key in `GAME_LABELS`.

---

## 5. Timeout & refund safety

Every `View` must implement `on_timeout()`:

- **Betting phase**: refund all joined players in full.
- **Playing phase**: depends on game state — if no meaningful play happened, refund. If mid-game, either force-resolve or refund.
- Always clean up: `self.active_tables.pop(channel_id, None)`.
- Update the embed to show the game timed out.

Test it: open a game, join, then wait for the timeout. Does the embed update? Do coins come back?

---

## 6. Re-bet support

Multiplayer games should store each player's last bet in `table.last_bets` so returning players can rejoin with one click via a Re-bet button instead of re-entering their wager through the modal.

---

## 7. One table per channel

The standard pattern: `active_tables: dict[int, Table]` keyed by `channel_id`. Only one instance of a game runs per channel at a time. Check for an existing table before allowing a new one to start.

---

## 8. ELO integration (skill-based games only)

If the game involves skill (not pure luck like roulette/slots), add ELO tracking:

### a. Choose an `elo_key`

A unique lowercase string (e.g. `"connect4"`, `"hex"`, `"wordle"`). Used as the DB key.

### b. Import helpers

```python
from bot.cogs._elo_helpers import update_elo_multiplayer, update_elo_1v1, fmt_elo_change
```

- **Party games**: `update_elo_multiplayer(finish_order, game_key, context)` — `finish_order` is a list of user IDs from 1st to last place.
- **Duo games**: `update_elo_1v1(winner_id, loser_id, game_key, context)` — returns `(w_old, w_new, l_old, l_new)`.
- **Draws**: `update_elo_draw(p1_id, p2_id, game_key, context)`.

### c. Register the label

Add to `ELO_GAME_LABELS` in `bot/cogs/_elo_helpers.py`:

```python
"your_elo_key": "Display Name",
```

### d. Display in results

Show ELO changes in the results embed:

```python
elo_text = fmt_elo_change(old_rating, new_rating)  # "1000 → 1016 (+16)"
```

### e. ELO system details

- Start: 1000, floor: 100
- K-factor: 32 (provisional, <10 games), 24 (developing, <30), 16 (established)
- Multiplayer: pairwise comparison, delta normalized by (N-1)
- ELO is updated per game, not per session — a 3-game duel produces 3 ELO updates
- Free (wager=0) games still update ELO

---

## 9. Game data curation (trivia/guessing games)

For games with a curated answer bank (trivia, guessing, identification):

### Structure data as Python constants in the cog file

```python
# (id, name, [alt_names], category, metadata, hint_text)
ITEMS: list[tuple[int, str, list[str], str, str, str]] = [
    (1, "Answer Name", ["Alt1", "Alt2"], "Category", "Origin/Meta",
     "Detailed hint text for progressive reveal (1-3 sentences)."),
    ...
]
```

### Guidelines
- **Be comprehensive** — aim for 20+ items minimum. The game gets stale fast with fewer.
- **Alt names** — include common abbreviations, nicknames, misspellings
- **Hint text** — detailed enough to support 3-stage progressive reveal (category → clue → near-giveaway)
- **Organize with comments** — group by section (e.g. `# -- Launch agents (10)`)
- **Normalize answers** — use `unicodedata.normalize("NFKD", ...).lower().strip()` for comparison
- **Section balance** — distribute items across categories/difficulty levels evenly

### Progressive hint pattern (30s rounds)

```python
HINT2_AT = 10   # seconds — reveal clue
HINT3_AT = 20   # seconds — reveal near-giveaway (e.g. first letter)
```

Used by: `pokemon.py`, `valorant.py`, `nba-trivia.py`

---

## 10. Sports sim games

For simulated sports betting (player bets on AI-generated game outcomes):

### Team ratings

```python
TEAMS: dict[str, tuple[float, float, float]] = {
    "Team Name": (offense, defense, coaching),
    ...
}
```

- Calibrate to real-world performance (current season stats)
- Include all teams in the league (30 NBA, 32 NFL, etc.)
- Ratings should produce realistic score distributions

### Simulation engine

- Period-by-period scoring (quarters, innings, sets, halves)
- Probability-based outcomes, not deterministic
- Win probability from rating differential (sigmoid or logistic function)
- Generate spread, moneyline, and total from the ratings

### Live embed updates

- Show score progression period by period
- Use `asyncio.sleep()` between periods for suspense
- Final embed shows full box score + bet resolution

### Bet resolution

- Compare final score to bet parameters (spread, ML, total)
- Moneyline: did the picked team win?
- Spread: home score - away score vs spread line
- Total: combined score vs over/under line

Reference: `bot/cogs/nbasim.py`, `bot/cogs/nflsim.py`

---

## 11. Web game architecture (browser-based games)

For games that need interactive browser UI. See `/new-web-game` for the full scaffold.

### Four-layer structure

| Layer | File | Purpose |
|-------|------|---------|
| Shared logic | `shared/<game>_logic.py` | Pure game rules, no deps |
| Web backend | `web/<game>.py` | FastAPI router + WebSocket engine |
| Static frontend | `web/static/<game>.html` | Single HTML file, dark theme |
| Discord cog | `bot/cogs/<game>.py` | Room creation, join/betting, result polling |

### Key patterns

- **Auth**: session-link tokens (unique URL per player, no Discord OAuth)
- **Coin flow**: deduct in Discord cog on join → game plays in browser → web backend computes payouts and calls `queries.update_casino_balance()` + `queries.log_casino_result()`
- **Result bridge**: cog polls `GET /api/v1/<game>/rooms/{id}/result` every 10s → posts result embed to Discord
- **Room cleanup**: `cleanup_stale_<game>_rooms()` called on startup; rooms expire after 30min TTL

### Extra registration (beyond the standard 4)

5. `web/api.py` — import router, include router, add WebSocket endpoint
6. Caddy config — SPA rewrite rule for `/<game>/*`

---

## 12. Mini-game engine integration

If the game works well as a quick 1v1 format, consider adding it to the mini-game engine used by duels and tournaments.

### Add to `bot/cogs/_minigames.py`

```python
class YourGame:
    name = "Your Game"
    emoji = "\U0001fxxx"
    stakes = 300           # 200 for luck-based, 300 for skill-based
    elo_key = "your_game"  # must match ELO_GAME_LABELS

    async def play(self, message, p1_id, p1_name, p2_id, p2_name) -> int:
        # Returns winner's user_id, or 0 for tie
        ...
```

### Protocol requirements
- Must implement the `MiniGame` protocol (name, emoji, stakes, elo_key, play method)
- `play()` takes a Discord message and both players' IDs/names
- Returns the winner's user_id (int), or 0 for a tie
- Game plays within the same message channel
- Should complete in <60 seconds

### Add to ALL_GAMES

```python
ALL_GAMES: list[MiniGame] = [
    ...,
    YourGame(),
]
```

This automatically makes it available in duels (`/duel`) and tournaments (`/tournament`).

### Best-of-3 helper

For games that work well in multiple rounds:

```python
async def play(self, message, p1_id, p1_name, p2_id, p2_name) -> int:
    return await _play_best_of_3(
        message, p1_id, p1_name, p2_id, p2_name,
        self.emoji, self.name, self._play_round,
    )
```

### Shared logic classes

If game logic is needed by both the mini-game engine AND a standalone cog, put it in `_minigames.py` as a class (e.g. `TTTBoard`, `RPSLogic`) and import it from both places.

---

## 13. Testing

Before shipping:

- [ ] Solo play works (join alone, play to completion, correct payout)
- [ ] Multi-player works (2+ players, different bet sizes)
- [ ] Side pots are correct with unequal bets
- [ ] Timeout during betting phase refunds everyone
- [ ] Timeout during play phase handles gracefully
- [ ] `/games` shows the new game in the right category with correct mode
- [ ] `/random-game` can pick it (with and without mode filter)
- [ ] `/casino-stats` shows correct game label after playing a round
- [ ] Re-bet button works for returning players
- [ ] ELO updates appear in results (if skill-based)
- [ ] Edge cases: max players, min bet, zero balance join attempt
- [ ] Web games: WebSocket connects, game plays in browser, results post to Discord
