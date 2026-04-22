# New Game Checklist

Practices and registration steps for adding a new casino game to SharpLab.

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

## 8. Testing

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
- [ ] Edge cases: max players, min bet, zero balance join attempt
