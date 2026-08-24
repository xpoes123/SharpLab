# Economy Faucets (Package A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore healthy coin faucets (~3–5k/day for an active player) via login streak, achievement bounties, a per-message bump, a skill-leaderboard payout, a daily-challenge bump, and a sub-50 coin-history filter — all exploit-proof.

**Architecture:** Tune existing knobs (`ACTIVITY_REWARDS`, challenge constants, the achievement credit) and add three small new pieces (login-streak table+query+command, a daily skill-payout loop, a ledger display filter). All coin credits go through the existing ledgered helpers `credit_coins` / `grant_activity_reward`.

**Tech Stack:** Python 3.14, `aiosqlite`, `discord.py` (cogs + `tasks.loop`), `pytest`, `uv`.

## Global Constraints

- Package manager `uv`; run tests via `uv run pytest ...`.
- All DB access in `db/queries.py`. Times UTC ISO 8601 / `YYYY-MM-DD` day keys.
- Every faucet must be capped-per-day / rate-gated / idempotent. Never top up a balance on action.
- Coin credits go through `credit_coins(uid, amount, reason, now_iso)` (ledgered) or `grant_activity_reward`.
- DB-backed tests use the `tmp_db` fixture pattern (swap `db.schema.DB_PATH`/`db.queries.DB_PATH` to a temp file + `init_db()`), `_run = asyncio.run`.

---

### Task 1: Hide sub-50 coin gains in history

**Files:**
- Modify: `db/queries.py` (`get_coin_ledger`, ~line 1373)
- Test: `tests/test_coin_ledger_filter.py` (create)

**Interfaces:**
- Produces: `get_coin_ledger` excludes positive rows < 50; keeps gains ≥ 50 and all debits.

- [ ] **Step 1: Write the failing test**

Create `tests/test_coin_ledger_filter.py`:

```python
"""get_coin_ledger hides sub-50 gains (the per-message trickle) from the page."""
from __future__ import annotations
import asyncio
import aiosqlite
import pytest
import db.schema as _schema
import db.queries as _queries


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path):
    p = str(tmp_path / "t.db")
    a, b = _schema.DB_PATH, _queries.DB_PATH
    _schema.DB_PATH = _queries.DB_PATH = p
    _run(_schema.init_db())
    yield p
    _schema.DB_PATH, _queries.DB_PATH = a, b


async def _add(uid, amt, reason):
    async with aiosqlite.connect(_queries.DB_PATH) as db:
        await db.execute(
            "INSERT INTO coin_ledger (discord_user, amount, reason, created_at) VALUES (?,?,?,?)",
            (uid, amt, reason, "2026-01-01T00:00:00Z"))
        await db.commit()


def test_sub_50_gains_hidden(tmp_db):
    async def go():
        await _add("u1", 10, "Message")       # hidden
        await _add("u1", 49, "Message")       # hidden
        await _add("u1", 50, "Login streak")  # shown
        await _add("u1", 500, "Box")          # shown
        await _add("u1", -100, "Bet")         # shown (debit)
        rows = await _queries.get_coin_ledger("u1")
        amounts = sorted(r["amount"] for r in rows)
        assert amounts == [-100, 50, 500]
    _run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_coin_ledger_filter.py -v`
Expected: FAIL (10 and 49 present).

- [ ] **Step 3: Implement the filter**

In `db/queries.py` `get_coin_ledger`, change the SQL WHERE clause:

```python
        cur = await db.execute(
            "SELECT amount, reason, created_at FROM coin_ledger WHERE discord_user = ? "
            "AND NOT (amount > 0 AND amount < 50) "
            "ORDER BY id DESC LIMIT ?",
            (discord_user, limit),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_coin_ledger_filter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/queries.py tests/test_coin_ledger_filter.py
git commit -m "feat(economy): hide sub-50 coin gains from on-page history"
```

---

### Task 2: Per-message reward → 10, uncapped, 30s gate

**Files:**
- Modify: `db/queries.py` (`ACTIVITY_REWARDS`, ~line 4973)
- Modify: `bot/cogs/progression.py` (`__init__` + `on_message`, ~line 333/340)
- Test: `tests/test_activity_reward_message.py` (create)

**Interfaces:**
- Consumes: existing `grant_activity_reward`.
- Produces: `ACTIVITY_REWARDS["message"] == (10, 10_000_000)`; message coins gated by a 30s per-user cooldown (`self._msg_coin_cd`) separate from the 5s XP cooldown.

- [ ] **Step 1: Write the failing test**

Create `tests/test_activity_reward_message.py`:

```python
"""Per-message reward is 10 and effectively uncapped (past the old 500 cap)."""
from __future__ import annotations
import asyncio
import pytest
import db.schema as _schema
import db.queries as _queries


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path):
    p = str(tmp_path / "t.db")
    a, b = _schema.DB_PATH, _queries.DB_PATH
    _schema.DB_PATH = _queries.DB_PATH = p
    _run(_schema.init_db())
    yield p
    _schema.DB_PATH, _queries.DB_PATH = a, b


def test_message_reward_is_ten_and_uncapped(tmp_db):
    async def go():
        total = 0
        for _ in range(200):  # 200 * 10 = 2000, far past the old 500 cap
            total += await _queries.grant_activity_reward("u1", "message", "2026-01-01")
        assert total == 2000
    _run(go())


def test_message_reward_amount():
    assert _queries.ACTIVITY_REWARDS["message"][0] == 10
    assert _queries.ACTIVITY_REWARDS["message"][1] >= 1_000_000  # effectively uncapped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_activity_reward_message.py -v`
Expected: FAIL (caps at 500; amount is 5).

- [ ] **Step 3: Implement**

3a. In `db/queries.py` change the `message` entry:
```python
    "message": (10, 10_000_000),   # 10/msg, effectively uncapped; spam-gated by a 30s cooldown in on_message
```

3b. In `bot/cogs/progression.py` `__init__` (where `self._msg_cd` is set, ~line 333) add a second cooldown map:
```python
        self._msg_cd: dict[int, float] = {}       # uid → last message-XP monotonic ts (5s)
        self._msg_coin_cd: dict[int, float] = {}  # uid → last message-COIN monotonic ts (30s)
```

3c. In `on_message`, replace the coin-grant block so the coin reward has its own 30s gate (leave the 5s XP path as-is):
```python
        await award_xp(self.bot, uid, XP_MESSAGE, message.channel)
        # Per-message coin reward (10, uncapped/day) — gated to 1 / 30s per user so it can't be
        # spam-farmed. Silent; coins accrue to your balance.
        from datetime import datetime, timezone
        if now - self._msg_coin_cd.get(message.author.id, 0) >= 30:
            self._msg_coin_cd[message.author.id] = now
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            await queries.grant_activity_reward(uid, "message", day)
```
(`now` is the `time.monotonic()` already computed above for the XP gate.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_activity_reward_message.py -v`
Expected: PASS

Import check: `uv run python -c "import bot.cogs.progression"`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add db/queries.py bot/cogs/progression.py tests/test_activity_reward_message.py
git commit -m "feat(economy): per-message reward 10/msg uncapped with 30s anti-spam gate"
```

---

### Task 3: Achievement bounties (XP×5) + retroactive backfill

**Files:**
- Modify: `bot/cogs/progression.py` (`evaluate_user_achievements` ~228, `announce_achievements` ~249/253)
- Create: `scripts/backfill_achievement_bounties.py`
- Test: `tests/test_achievement_bounty.py` (create)

**Interfaces:**
- Consumes: `credit_coins`, `ACHIEVEMENTS_BY_ID`, `unlock_achievement`, `get_user_achievements`.
- Produces: unlock credits `xp_reward * 5`; backfill credits `max(0, xp*5 - 150)` once per already-unlocked (user, achievement).

- [ ] **Step 1: Write the failing test**

Create `tests/test_achievement_bounty.py`:

```python
"""Achievement unlock pays XP*5; backfill tops up already-earned by the delta, once."""
from __future__ import annotations
import asyncio
import pytest
import db.schema as _schema
import db.queries as _queries


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path):
    p = str(tmp_path / "t.db")
    a, b = _schema.DB_PATH, _queries.DB_PATH
    _schema.DB_PATH = _queries.DB_PATH = p
    _run(_schema.init_db())
    yield p
    _schema.DB_PATH, _queries.DB_PATH = a, b


def test_unlock_pays_xp_times_five(tmp_db):
    # Real path: log a casino round → first_game unlocks via evaluate_user_achievements,
    # which must credit xp_reward*5 (not the old flat 150).
    async def go():
        from bot.cogs.progression import evaluate_user_achievements
        from shared.achievements import ACHIEVEMENTS_BY_ID
        await _queries.get_or_create_casino_wallet("u1")
        b0 = await _queries.get_casino_balance("u1")
        await _queries.log_casino_result("u1", "slots", 10, 0)  # rounds>=1 → first_game; payout 0 so no first_win
        newly = await evaluate_user_achievements("u1")
        assert "first_game" in newly
        b1 = await _queries.get_casino_balance("u1")
        # only first_game should unlock from all-zero stats; bounty = 10*5 = 50
        assert b1 - b0 == ACHIEVEMENTS_BY_ID["first_game"].xp_reward * 5
    _run(go())


def test_backfill_tops_up_by_delta_once(tmp_db):
    async def go():
        from scripts.backfill_achievement_bounties import backfill
        from shared.achievements import ACHIEVEMENTS_BY_ID
        await _queries.get_or_create_casino_wallet("u2")
        # user already unlocked a high-XP achievement (got the old flat 150)
        await _queries.unlock_achievement("u2", "level_50")  # xp 1000 -> new bounty 5000
        b0 = await _queries.get_casino_balance("u2")
        n1 = await backfill()
        b1 = await _queries.get_casino_balance("u2")
        assert b1 - b0 == ACHIEVEMENTS_BY_ID["level_50"].xp_reward * 5 - 150  # 4850
        # idempotent: a second run pays nothing
        n2 = await backfill()
        b2 = await _queries.get_casino_balance("u2")
        assert b2 == b1
    _run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_achievement_bounty.py -v`
Expected: FAIL (ModuleNotFoundError: scripts.backfill_achievement_bounties; and unlock path not asserting).

- [ ] **Step 3: Implement the unlock bounty**

In `bot/cogs/progression.py` `evaluate_user_achievements`, change the credit amount:
```python
                bounty = ACHIEVEMENTS_BY_ID[aid].xp_reward * 5
                await queries.credit_coins(
                    uid, bounty, f"Achievement: {ACHIEVEMENTS_BY_ID[aid].name}",
                    datetime.now(timezone.utc).isoformat(),
                )
```

In `announce_achievements`, replace the hardcoded `+150 🪙` with the real bounty (two spots — single and multi):
```python
        desc = f"{a.emoji} **{a.name}** — {a.description}\n`+{a.xp_reward} XP · +{a.xp_reward*5} 🪙`"
```
```python
        desc = "\n".join(
            f"{a.emoji} **{a.name}** — {a.description} `+{a.xp_reward} XP · +{a.xp_reward*5} 🪙`" for a in achs
        )
```

- [ ] **Step 4: Add the backfill table + script**

4a. In `db/schema.py` `init_db()`, add an idempotent table (follow the existing try/except CREATE pattern):
```python
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS bounty_backfilled ("
                "discord_user TEXT NOT NULL, achievement_id TEXT NOT NULL, "
                "PRIMARY KEY (discord_user, achievement_id))")
            await db.commit()
        except Exception:
            pass
```

4b. Create `scripts/backfill_achievement_bounties.py`:
```python
"""One-time: top up already-unlocked achievements from the old flat 150 to the new
XP*5 bounty. Credits max(0, xp*5 - 150) per (user, achievement), guarded by the
bounty_backfilled table so re-runs pay nothing. Run on the VPS:
    cd /opt/sharplab && venv/bin/python scripts/backfill_achievement_bounties.py
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite  # noqa: E402
from db import queries  # noqa: E402
from db.schema import init_db, DB_PATH  # noqa: E402
from shared.achievements import ACHIEVEMENTS_BY_ID  # noqa: E402


async def backfill() -> int:
    """Returns the number of (user, achievement) top-ups paid this run."""
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT ua.discord_user, ua.achievement_id FROM user_achievements ua "
            "LEFT JOIN bounty_backfilled bb "
            "  ON bb.discord_user = ua.discord_user AND bb.achievement_id = ua.achievement_id "
            "WHERE bb.discord_user IS NULL")).fetchall()
    paid = 0
    for r in rows:
        aid = r["achievement_id"]
        ach = ACHIEVEMENTS_BY_ID.get(aid)
        if ach is None:
            continue
        delta = max(0, ach.xp_reward * 5 - 150)
        if delta > 0:
            await queries.credit_coins(
                r["discord_user"], delta, f"Achievement bounty top-up: {ach.name}",
                datetime.now(timezone.utc).isoformat())
            paid += 1
        # mark handled either way so we never revisit
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO bounty_backfilled (discord_user, achievement_id) VALUES (?, ?)",
                (r["discord_user"], aid))
            await db.commit()
    return paid


async def main() -> None:
    n = await backfill()
    print(f"backfilled {n} achievement bounty top-up(s)")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_achievement_bounty.py -v`
Expected: PASS (2 tests)

Import check: `uv run python -c "import bot.cogs.progression, scripts.backfill_achievement_bounties"`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add bot/cogs/progression.py db/schema.py scripts/backfill_achievement_bounties.py tests/test_achievement_bounty.py
git commit -m "feat(economy): achievement bounties XP*5 + retroactive backfill"
```

---

### Task 4: Login streak + daily-challenge bump

**Files:**
- Modify: `bot/cogs/challenges.py` (constants + a `/claim` command)
- Modify: `db/schema.py` (`login_streak` table)
- Modify: `db/queries.py` (`claim_login_streak`)
- Test: `tests/test_login_streak.py` (create)

**Interfaces:**
- Produces: `claim_login_streak(uid, day) -> {"granted": int, "streak": int, "longest": int, "already": bool}`.
  Amount `min(1000, 200 + (streak-1)*100)`. Credits via `credit_coins`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_login_streak.py`:

```python
"""Login streak: ramps +100 to 1000, resets on a gap, idempotent per day."""
from __future__ import annotations
import asyncio
import pytest
import db.schema as _schema
import db.queries as _queries


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path):
    p = str(tmp_path / "t.db")
    a, b = _schema.DB_PATH, _queries.DB_PATH
    _schema.DB_PATH = _queries.DB_PATH = p
    _run(_schema.init_db())
    yield p
    _schema.DB_PATH, _queries.DB_PATH = a, b


def test_streak_ramps_resets_and_is_idempotent(tmp_db):
    async def go():
        r1 = await _queries.claim_login_streak("u1", "2026-01-01")
        assert (r1["granted"], r1["streak"], r1["already"]) == (200, 1, False)
        # same day again → already, 0 granted
        r1b = await _queries.claim_login_streak("u1", "2026-01-01")
        assert (r1b["granted"], r1b["already"]) == (0, True)
        # next day → +100
        r2 = await _queries.claim_login_streak("u1", "2026-01-02")
        assert (r2["granted"], r2["streak"]) == (300, 2)
        # jump to a far day → reset to 1
        r3 = await _queries.claim_login_streak("u1", "2026-01-10")
        assert (r3["granted"], r3["streak"]) == (200, 1)
        # verify the cap at 1000 (day1=200 .. day9=1000)
        await _queries.get_or_create_casino_wallet("u2")
        day = 1
        last = None
        from datetime import date, timedelta
        d = date(2026, 2, 1)
        for i in range(12):
            last = await _queries.claim_login_streak("u2", (d + timedelta(days=i)).isoformat())
        assert last["granted"] == 1000  # capped
        assert last["streak"] == 12
    _run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_login_streak.py -v`
Expected: FAIL (no `claim_login_streak`).

- [ ] **Step 3: Add the table**

In `db/schema.py` `init_db()` (follow the try/except CREATE pattern):
```python
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS login_streak ("
                "discord_user TEXT PRIMARY KEY, last_day TEXT NOT NULL, "
                "streak INTEGER NOT NULL DEFAULT 1, longest INTEGER NOT NULL DEFAULT 1)")
            await db.commit()
        except Exception:
            pass
```

- [ ] **Step 4: Add the query**

In `db/queries.py`:
```python
async def claim_login_streak(discord_user: str, day: str) -> dict:
    """Claim the daily login-streak reward. Ramps min(1000, 200 + (streak-1)*100),
    resets to 1 if yesterday wasn't the last claim. Idempotent per day."""
    from datetime import date, timedelta
    yesterday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            row = await (await db.execute(
                "SELECT last_day, streak, longest FROM login_streak WHERE discord_user = ?",
                (discord_user,))).fetchone()
            if row and row["last_day"] == day:
                await db.execute("ROLLBACK")
                return {"granted": 0, "streak": row["streak"], "longest": row["longest"], "already": True}
            if row and row["last_day"] == yesterday:
                streak = row["streak"] + 1
            else:
                streak = 1
            longest = max(streak, row["longest"] if row else 1)
            granted = min(1000, 200 + (streak - 1) * 100)
            await db.execute(
                "INSERT INTO login_streak (discord_user, last_day, streak, longest) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(discord_user) DO UPDATE SET last_day = ?, streak = ?, longest = ?",
                (discord_user, day, streak, longest, day, streak, longest))
            await db.commit()
        except Exception:
            await db.execute("ROLLBACK")
            raise
    # credit outside the streak txn (credit_coins opens its own connection)
    from datetime import datetime, timezone
    await credit_coins(discord_user, granted, f"Login streak (day {streak})",
                       datetime.now(timezone.utc).isoformat())
    return {"granted": granted, "streak": streak, "longest": longest, "already": False}
```

- [ ] **Step 5: Bump daily-challenge constants + add the /claim command**

5a. In `bot/cogs/challenges.py`:
```python
CHALLENGE_COINS = 300
BONUS_COINS = 600
```

5b. Add a `/claim` command to `ChallengesCog` (uses `datetime` already imported in the cog; if not, import it):
```python
    @app_commands.command(name="claim", description="Claim your daily login-streak coins")
    async def claim(self, interaction: discord.Interaction) -> None:
        from datetime import datetime, timezone
        uid = str(interaction.user.id)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        res = await queries.claim_login_streak(uid, day)
        if res["already"]:
            await interaction.response.send_message(
                f"You already claimed today. Current streak: **{res['streak']} day"
                f"{'s' if res['streak'] != 1 else ''}**. Come back tomorrow!", ephemeral=True)
            return
        await interaction.response.send_message(
            f"🎁 **+{res['granted']:,}** 🪙 — login streak **day {res['streak']}** "
            f"(best: {res['longest']}). Keep the streak alive!", ephemeral=True)
```
(Ensure `discord`, `app_commands`, `queries` are imported in the cog — `app_commands` and `queries` already are; `discord` is standard.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_login_streak.py -v`
Expected: PASS

Import check: `uv run python -c "import bot.cogs.challenges"`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add db/schema.py db/queries.py bot/cogs/challenges.py tests/test_login_streak.py
git commit -m "feat(economy): /claim login streak (200->1000 ramp) + daily-challenge bump"
```

---

### Task 5: Skill-leaderboard daily payout

**Files:**
- Modify: `db/queries.py` (`record_skill_payout_day`, `get_all_skill_game_ids`)
- Modify: `bot/cogs/progression.py` (a daily `@tasks.loop` that pays out)
- Test: `tests/test_skill_payout.py` (create)

**Interfaces:**
- Consumes: `get_skill_leaderboard(game_id, limit)`, `credit_coins`, `get_bot_setting`/`set_bot_setting`.
- Produces: `record_skill_payout_day(day) -> bool` (True iff this call claimed the day);
  `get_all_skill_game_ids() -> list[str]`; a loop paying top-3 `[2000,1000,500]` per game.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skill_payout.py`:

```python
"""Skill payout: once per day; top-3 per game get 2000/1000/500."""
from __future__ import annotations
import asyncio
import pytest
import db.schema as _schema
import db.queries as _queries


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path):
    p = str(tmp_path / "t.db")
    a, b = _schema.DB_PATH, _queries.DB_PATH
    _schema.DB_PATH = _queries.DB_PATH = p
    _run(_schema.init_db())
    yield p
    _schema.DB_PATH, _queries.DB_PATH = a, b


def test_payout_day_claimed_once(tmp_db):
    async def go():
        assert await _queries.record_skill_payout_day("2026-01-01") is True
        assert await _queries.record_skill_payout_day("2026-01-01") is False
        assert await _queries.record_skill_payout_day("2026-01-02") is True
    _run(go())


def test_pay_skill_leaderboards_top3(tmp_db):
    async def go():
        from bot.cogs.progression import PRIZES, pay_skill_leaderboards
        # three ranked runs on one game (lower ms = better)
        await _queries.record_skill_best("mastermind", "a", 1000)
        await _queries.record_skill_best("mastermind", "b", 2000)
        await _queries.record_skill_best("mastermind", "c", 3000)
        paid = await pay_skill_leaderboards()  # returns total coins minted
        assert paid == sum(PRIZES)  # 2000+1000+500
        assert await _queries.get_casino_balance("a") == _queries.CASINO_STARTING_COINS + PRIZES[0]
        assert await _queries.get_casino_balance("c") == _queries.CASINO_STARTING_COINS + PRIZES[2]
    _run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_skill_payout.py -v`
Expected: FAIL (missing `record_skill_payout_day` / `pay_skill_leaderboards`).

- [ ] **Step 3: Add the queries**

In `db/queries.py` (near the skill-leaderboard section ~5822):
```python
async def get_all_skill_game_ids() -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT DISTINCT game_id FROM skill_scores")
        return [r[0] for r in await cur.fetchall()]


async def record_skill_payout_day(day: str) -> bool:
    """Claim `day` for the skill payout. True iff this call is the one that claimed it
    (so the daily payout runs at most once per UTC day, even across restarts)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute(
                "INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('skill_payout_day', ?)",
                (day,))
            if cur.rowcount == 0:  # key already exists — check whether it's an older day
                row = await (await db.execute(
                    "SELECT value FROM bot_settings WHERE key = 'skill_payout_day'")).fetchone()
                if row and row[0] == day:
                    await db.execute("ROLLBACK")
                    return False
                await db.execute(
                    "UPDATE bot_settings SET value = ? WHERE key = 'skill_payout_day'", (day,))
            await db.commit()
            return True
        except Exception:
            await db.execute("ROLLBACK")
            raise
```
(`bot_settings` is `(key, value)` with `ON CONFLICT(key)` — confirmed.)

- [ ] **Step 4: Add the payout loop**

In `bot/cogs/progression.py`, add the constant and functions, and start the loop in `cog_load`:
```python
PRIZES = [2000, 1000, 500]  # skill-leaderboard daily payout, ranks 1-3


async def pay_skill_leaderboards() -> int:
    """Pay the top 3 of every skill leaderboard from the house. Returns coins minted."""
    from datetime import datetime, timezone
    minted = 0
    for game_id in await queries.get_all_skill_game_ids():
        top = await queries.get_skill_leaderboard(game_id, len(PRIZES))
        for rank, row in enumerate(top):
            prize = PRIZES[rank]
            await queries.credit_coins(
                row["discord_user"], prize, f"Skill leaderboard: {game_id} (#{rank + 1})",
                datetime.now(timezone.utc).isoformat())
            minted += prize
    return minted
```
Add a method on `ProgressionCog`:
```python
    @tasks.loop(minutes=60)
    async def skill_payout(self) -> None:
        from datetime import datetime, timezone
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            if await queries.record_skill_payout_day(day):
                await pay_skill_leaderboards()
        except Exception:
            log.debug("skill payout tick failed", exc_info=True)

    @skill_payout.before_loop
    async def _before_skill_payout(self) -> None:
        await self.bot.wait_until_ready()
```
And in `cog_load` add `self.skill_payout.start()` alongside the other `.start()` calls.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_skill_payout.py -v`
Expected: PASS

Import check: `uv run python -c "import bot.cogs.progression, db.queries"`
Expected: exit 0.

- [ ] **Step 6: Full economy-faucet suite + commit**

Run: `uv run pytest tests/test_coin_ledger_filter.py tests/test_activity_reward_message.py tests/test_achievement_bounty.py tests/test_login_streak.py tests/test_skill_payout.py -v`
Expected: PASS

```bash
git add db/queries.py bot/cogs/progression.py tests/test_skill_payout.py
git commit -m "feat(economy): daily skill-leaderboard payout (top-3, once/day)"
```

---

## Deploy (after merge)

1. Pull on the VPS; restart `sharplab-bot` + `sharplab-web`.
2. Run `scripts/backfill_achievement_bounties.py` (one-time bounty top-up).
3. Announce (`Announce: yes`) — new ways to earn (login streak, bigger dailies, achievement bounties, skill payouts).
