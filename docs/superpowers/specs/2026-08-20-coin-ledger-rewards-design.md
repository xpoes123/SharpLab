# Coin Ledger + New Coin Sources — Design

**Date:** 2026-08-20
**Status:** Approved design, pending implementation.

## 1. Summary

Two things:
1. **Coin history** — clicking the coins chip shows where your coins came *from*
   (a real per-user ledger of coin gains), above the existing "ways to earn" list.
2. **Three new coin sources**, all logged to that ledger: **leveling up**,
   **unlocking achievements**, and **winning a multiplayer game**.

Approved parameters:
- Level-up: **100 coins × levels gained**.
- Achievement: **150 coins each**.
- Multiplayer game: **winner(s) +50** (winner-only — no participation reward, so no
  farming cap needed).
- History shows **gains only** (sources), not spending.

## 2. Data model

New table (in `_SCHEMA`, `CREATE TABLE IF NOT EXISTS` — covers new + existing DBs, no
migration needed):

```sql
CREATE TABLE IF NOT EXISTS coin_ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user  TEXT NOT NULL,
    amount        INTEGER NOT NULL,      -- always > 0 (gains only)
    reason        TEXT NOT NULL,         -- human label, e.g. "Reached level 5"
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coin_ledger_user ON coin_ledger(discord_user, id);
```

## 3. `db/queries.py`

- `credit_coins(user, amount, reason, now_iso) -> int` — the one helper for a *logged*
  coin gain. Creates the wallet with starting coins if new (mirrors `give_casino_coins`),
  adds `amount`, inserts a `coin_ledger` row, returns the new balance. Amount must be > 0.
- `get_coin_ledger(user, limit=25) -> list[dict]` — recent gains, newest first
  (`amount`, `reason`, `created_at`).
- **Level-up hook inside `add_xp`**: after recomputing level, if `new_level > old_level`,
  `credit_coins(user, 100 * (new_level - old_level), f"Reached level {new_level}", now)`.
  Central, so *every* XP source (chat, casino batch, achievements, duels, tournaments)
  rewards a level-up. `add_xp` returns a new `coins_awarded` field. Uses a UTC `now()`
  computed inside the function (it already writes without a passed-in timestamp).
- Route these existing **discrete** card/pickem gains through `credit_coins` so history is
  populated for the main earn paths (each is a one-line swap at the positive-grant site):
  `sell_instance` (already credits — add a ledger row via credit path), the daily-pack
  claim, set-completion reward (`cards.py`), and pickem win payout (`pickem.py:904`).
  *Out of v1 (fast-follow):* routing chat/bet/trade/stock/casino-net grants — noted, not done.

## 4. Reward hooks

- **Achievements** (`bot/cogs/progression.py::evaluate_user_achievements`): in the unlock
  loop, after `unlock_achievement` + `add_xp`, call
  `credit_coins(uid, 150, f"Achievement: {ACHIEVEMENTS_BY_ID[aid].name}", now)`.
- **Multiplayer games** (`bot/cogs/_elo_helpers.py::update_elo_multiplayer`): after ratings
  are computed, determine winner(s) — players sharing the top score when `scores` is given,
  else `finish_order[0]` — and `credit_coins(winner, 50, f"Won {game_key}", now)` for each.
  Guarded by the existing `len(finish_order) < 2` early return, so it only fires for real
  multiplayer games. Best-effort (never raises into the game).

## 5. Web

- `GET /api/v1/cards/coins` (session-gated) → `{ balance, ledger: [{amount, reason,
  created_at}] }`.

## 6. Web UI (`web/static/cards.js` + `cards.css`)

`showCoinsHub()` fetches `/coins` and renders, above the existing "ways to earn" list:
a **"Recent coins"** section — each ledger row as `+🪙N · reason · relative-time`. Empty
state: "No coins earned yet — here's how." The ways-to-earn list gains **Level up**,
**Unlock achievements**, and **Win multiplayer games** entries with the new amounts.

## 7. Discord surfacing

- Achievement announce (`announce_achievements`) appends `+150🪙` per unlock.
- The 5-level milestone level-up message notes the coins earned.
- Multiplayer game result embeds already show placement; where trivial, append the
  winner's `+50🪙`. (Best-effort; skip games where the result message is hard to reach.)

## 8. Testing (`tests/test_cards.py`)

- `credit_coins` adds balance **and** writes a ledger row; `get_coin_ledger` returns it.
- `add_xp` across a level boundary returns `coins_awarded == 100 × levels` and logs
  "Reached level N"; no coins when staying on the same level.
- Winner-only multiplayer grant: a 3-player finish credits only the winner 50 (assert via
  a direct `update_elo_multiplayer` call with a tmp DB).
- Achievement unlock credits 150 and logs the achievement name.
- `GET /coins` returns balance + ledger for the session user.

## 9. Non-goals (v1)

- No coin history for *spending* (bets, pack buys, casino wagers) — gains only.
- No routing of chat/bet/trade/stock/casino-net grants through the ledger yet (fast-follow).
- No per-page coins hub beyond the cards page (the hub lives in `cards.js`; other pages'
  nav chip is unchanged).
- No multiplayer participation reward or daily cap (winner-only makes them unnecessary).
