# Economy Faucets (Package A) — Design

**Date:** 2026-08-24
**Status:** Approved (design) — pending spec review
**Goal:** Make coins earnable again for regular players (~3–5k/day for an active
player) after the sink-heavy pack update + exploit closures, without
reintroducing farmable holes. All faucets are time/skill capped and idempotent.

> **Package B — Zetamac timed-sprint leaderboards** is a separate, bigger project
> (score-based sprint mode across Countdown / Math 24 / Quiz Bowl / guessers /
> Geography). Its own spec next. Not covered here.

---

## Context: what already exists (audited)

- `credit_coins(uid, amount, reason, now_iso)` — the ledgered faucet-credit helper
  (writes `coin_ledger` + wallet, gains only). **All faucets use this.**
- `grant_activity_reward(uid, source, day, amount_override=, reason=)` — per-event
  reward up to a per-source daily cap (`ACTIVITY_REWARDS[source] = (amount, cap)`,
  tracked in `daily_coin_earn`).
- `add_xp(uid, amount) -> {total_xp, level, leveled_up, old_level}` — **already
  credits a level-up reward (100/level gained), ledgered.** ✅ Level-up faucet is
  DONE; left as-is (optional tuning only, see §6).
- Achievements already credit a **flat 150 coins** on unlock (in
  `evaluate_user_achievements`) via `credit_coins`.
- Daily challenges (`challenges.py`): `CHALLENGE_COINS=100` ×3 + `BONUS_COINS=200`
  all-three bonus = 500/day.
- Per-message reward EXISTS: `ACTIVITY_REWARDS["message"] = (5, 500)`, granted in
  `progression.py` `on_message` (ignores bots/commands/empty; 5s cooldown).
- Skill leaderboards EXIST: `skill_scores(game_id, discord_user, best_ms, runs)` +
  `get_skill_leaderboard(game_id, limit)` — speed-based (fastest run). Not yet a
  faucet.
- Coin history shown on the web via `get_coin_ledger(uid, limit)` (read by
  `web/cards.py`).

So most of Package A is **tuning existing knobs**; the new builds are the login
streak, the skill-payout job, and the history filter.

---

## Changes

### 1. Daily bump (tuning) — `bot/cogs/challenges.py`
- `CHALLENGE_COINS = 100 → 300`
- `BONUS_COINS = 200 → 600`
- New daily max from dailies: **1,500** (was 500).

### 2. Login streak (NEW)
A once-per-day claim that ramps and resets on a missed day.

- **Amount:** `min(1000, 200 + (streak-1) * 100)` → day1 200 … day9+ 1,000.
- **Reset:** if the last claim day is not "yesterday" (UTC), streak resets to 1.
- **State:** new table
  `login_streak(discord_user TEXT PRIMARY KEY, last_day TEXT, streak INT, longest INT)`.
- **Query:** `claim_login_streak(uid, day) -> {granted:int, streak:int, longest:int, already:bool}`
  — atomic; `already=True` (granted 0) if `last_day == day`. Credits via
  `credit_coins(uid, granted, "Login streak (day N)", now_iso)`.
- **Command:** `/daily claim` (or `/claim`) in a small new cog (or fold into
  `challenges.py`). Ephemeral reply showing streak + coins. Idempotent per day.

### 3. Achievement bounties (tuning + backfill) — `bot/cogs/progression.py`, new script
- In `evaluate_user_achievements`, replace the flat `150` with
  `ACHIEVEMENTS_BY_ID[aid].xp_reward * 5` (range 50–5,000). Update the
  `announce_achievements` text (currently hardcodes `+150 🪙`) to show the actual
  per-achievement bounty.
- **Retroactive backfill** (`scripts/backfill_achievement_bounties.py`): for each
  user's already-unlocked achievements, credit `max(0, xp*5 - 150)` once (they
  already received 150). Ledger reason `"Achievement bounty top-up: <name>"`.
  Idempotent: guard on a distinct marker so a re-run pays nothing — e.g. skip a
  (user, achievement) whose top-up ledger reason already exists, or track in a
  small `bounty_backfilled(discord_user, achievement_id)` table.

### 4. Per-message reward (tuning) — `db/queries.py`, `bot/cogs/progression.py`
- `ACTIVITY_REWARDS["message"] = (5, 500) → (10, <uncapped>)`. "Uncapped" =
  a large sentinel cap (e.g. `10_000_000`) so `grant_activity_reward`'s
  `min(amount, cap-earned)` never binds in practice. (Keeps the existing
  daily_coin_earn accounting; no schema/logic change.)
- **Anti-spam gate (approved):** in `on_message`, gate the *coin* grant behind a
  separate **30s** per-user cooldown (`self._msg_coin_cd`), leaving the existing
  5s XP cooldown as-is. Message must be non-empty, non-command, non-bot (already
  enforced). Result: uncapped over a day for genuine chat, but a spammer earns at
  most 1 reward / 30s.

### 5. Skill-leaderboard payout job (NEW) — `db/queries.py`, `bot/cogs/progression.py` (or challenges.py)
A daily job pays the top of every skill leaderboard, minted from the house.

- **Prizes:** top 3 per `game_id` → **2,000 / 1,000 / 500**.
- **Coverage:** `SELECT DISTINCT game_id FROM skill_scores`; for each, take
  `get_skill_leaderboard(game_id, 3)` and pay via `credit_coins(uid, prize,
  f"Skill leaderboard: {game_id} (#{rank})", now_iso)`.
- **Cadence & idempotency:** a `@tasks.loop` that fires once/day (e.g. checks a
  stored `last_skill_payout_day` in `bot_settings`; pays and advances it — pays at
  most once per UTC day even across restarts). New query
  `record_skill_payout_day(day) -> bool` (True if this call claimed the day).
- Tiering by game difficulty (Mastermind/hard puzzles paying more) is deferred to
  **Package B**, which restructures these leaderboards; flat top-3 here is the
  immediate faucet.

### 6. Level-up rewards — ALREADY DONE
`add_xp` already credits 100/level gained (ledgered). Left unchanged. (If we want
it larger later, bump that constant — out of scope.)

### 7. Coin history: hide sub-50 gains (NEW) — `db/queries.py`
Per request, tiny gains (the 10/msg trickle, small guess rewards) shouldn't clutter
the on-page history. Filter in `get_coin_ledger` so **positive** entries below 50
are excluded; keep all debits and gains ≥ 50:
```sql
WHERE discord_user = ? AND NOT (amount > 0 AND amount < 50)
```
Entries are still written to `coin_ledger` (accounting intact); only the displayed
query hides them. (Web `web/cards.py` reads this — no web change needed.)

---

## Economy math (target check)

Active daily player: 1,500 (challenges) + ~800 avg (login streak) ≈ **2,300/day
floor**, plus achievement unlocks (50–5,000 each, front-loaded), level-ups
(100/level), skill-leaderboard finishes (up to 2,000), and chat (10/msg, ~1,200/hr
of real chatting). Lands an engaged player at **3–5k/day**, more for the skilled —
matching the target. One-time achievement backfill: ~20k for a mid player, ~42k
fully-achieved (trivial vs a whale's 2M).

## Guardrails (why this won't become the next exploit)
- Every faucet is **capped per day / per level / per achievement** or **rate-gated**
  (30s message cooldown), and **idempotent** (daily markers, one-time backfill,
  per-day payout claim).
- **Nothing tops up a balance on action** (the removed 1,000 floor bug stays gone).
- No path where sell value exceeds cost.
- All credits go through `credit_coins` / `grant_activity_reward` (ledgered).

## Testing
- `claim_login_streak`: first claim day1=200; consecutive days ramp +100 to 1,000
  cap; same-day re-claim → `already=True`, 0 granted; a gap resets to 1. (tmp_db)
- Achievement bounty: unlock credits `xp*5`; announce text shows the real amount;
  backfill credits `max(0, xp*5-150)` once and a second run pays 0. (tmp_db)
- Per-message: `ACTIVITY_REWARDS["message"]` grants 10 and doesn't cap at 500;
  30s coin cooldown blocks a second grant, allows one after. (unit + tmp_db)
- Skill payout: top-3 per game paid 2000/1000/500; `record_skill_payout_day` is
  once-per-day (second call same day → False, pays nothing). (tmp_db)
- `get_coin_ledger`: a +10 gain is hidden, a +50 gain and a −100 debit are shown.
  (tmp_db)

## Out of scope (follow-ups)
- Package B: Zetamac score-based sprint leaderboards + difficulty-tiered payouts.
- Any web UI beyond the history filter (it reuses the existing ledger read).
- Level-up reward re-tuning.

## Rollout
1. Merge; deploy (pull, restart `sharplab-bot` + `sharplab-web`).
2. Run `scripts/backfill_achievement_bounties.py` on the VPS (one-time top-up).
3. Announce (this one IS player-facing — new ways to earn): `Announce: yes`.
