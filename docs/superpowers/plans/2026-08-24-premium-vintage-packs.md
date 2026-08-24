# Premium & Vintage Packs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add high-value coin sinks: a universal `/pack box` (36 packs, guaranteed hit) plus ultra-premium sports draft-class sets and Pokémon sets (incl. a 151 "1st Edition" grail box), all on the existing card engine.

**Architecture:** Reuse `shared/cards.py` (engine), `db/queries.py` (`mint_pack` pattern), and `bot/cogs/cards.py` (`pack` command group). New: pure engine helpers, a `mint_box` query, a `/pack box` command with a summary reveal, and three seeders that insert ordinary `card_sets`/`card_designs` rows. No schema change (box price = `36 × base_cost`).

**Tech Stack:** Python 3.14, `aiosqlite`, `discord.py` app_commands, `pytest`, `uv`.

## Global Constraints

- Package manager: `uv` — run tests with `uv run pytest ...`, never bare `pytest`.
- All DB access through `db/queries.py`. No raw SQL elsewhere.
- American/coins are integers; times UTC ISO 8601 via `_now_iso()`.
- Box price is **derived** as `36 × base_cost` — do NOT add a DB column.
- A box = **36 packs**; `pack_size = 5` (so 180 cards/box).
- Money paths never mint coins; premium sets are deliberately negative-EV sinks.
- Engine (`shared/cards.py`) stays pure: no DB, no I/O, deterministic given an rng.
- Follow existing patterns in `cards.py`/`queries.py`; match surrounding style.

---

### Task 1: Engine helpers (pure)

**Files:**
- Modify: `shared/cards.py` (append after existing constants/helpers)
- Test: `tests/test_cards.py`

**Interfaces:**
- Consumes: existing `RARITIES`, `BOOK`, `book_value`, `PLAYER_TIERS`.
- Produces:
  - `DRAFT_TIERS: list[tuple[str, float]]`
  - `POKEMON_RARITY_MAP: dict[str, str]`
  - `map_pokemon_rarity(src: str) -> str`
  - `COIN_PER_USD: int` (= 100)
  - `pokemon_book_value(price_usd: float, tier: str) -> float`
  - `needs_guaranteed_hit(cards: list[dict]) -> bool`  (True if no card has rarity epic/legendary)
  - `box_price(base_cost: int) -> int`  (= 36 * base_cost)
  - `PACKS_PER_BOX: int` (= 36)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cards.py`:

```python
def test_draft_tiers_are_valid_and_premium():
    keys = [k for k, _ in cards.DRAFT_TIERS]
    assert set(keys) == set(cards.RARITIES)
    frac = dict(cards.DRAFT_TIERS)
    assert abs(sum(frac.values()) - 1.0) < 1e-9
    # premium: more legendary/epic than the default player mix
    default = dict(cards.PLAYER_TIERS)
    assert frac["legendary"] > default["legendary"]
    assert frac["epic"] > default["epic"]


def test_pokemon_rarity_map_is_total():
    src = ["Common", "Uncommon", "Rare", "Double rare", "Illustration rare",
           "Ultra Rare", "ACE SPEC Rare", "Special illustration rare",
           "Hyper rare", "Mega Hyper Rare", "Black White Rare"]
    for s in src:
        assert cards.map_pokemon_rarity(s) in cards.RARITIES
    assert cards.map_pokemon_rarity("Common") == "common"
    assert cards.map_pokemon_rarity("Hyper rare") == "legendary"
    assert cards.map_pokemon_rarity("Ultra Rare") == "epic"
    # unknown falls back to common (never crash a seed run)
    assert cards.map_pokemon_rarity("Nonsense") == "common"


def test_pokemon_book_value_uses_price_with_tier_floor():
    # $397 Charizard -> 39700 coins
    assert cards.pokemon_book_value(397.07, "legendary") == 39707.0
    # a $0.19 common floors at the common book value, never below
    assert cards.pokemon_book_value(0.19, "common") == cards.BOOK["common"]


def test_needs_guaranteed_hit():
    assert cards.needs_guaranteed_hit([{"rarity": "common"}, {"rarity": "rare"}])
    assert not cards.needs_guaranteed_hit([{"rarity": "common"}, {"rarity": "epic"}])
    assert not cards.needs_guaranteed_hit([{"rarity": "legendary"}])
    assert cards.needs_guaranteed_hit([])  # empty box would need a hit (defensive)


def test_box_price():
    assert cards.PACKS_PER_BOX == 36
    assert cards.box_price(1389) == 49_996
    assert cards.box_price(6944) == 249_984
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cards.py -k "draft_tiers or pokemon_rarity or pokemon_book or guaranteed_hit or box_price" -v`
Expected: FAIL (AttributeError: module has no attribute 'DRAFT_TIERS', etc.)

- [ ] **Step 3: Implement the helpers**

Append to `shared/cards.py` (after `PLAYER_TIERS` and near the other constants):

```python
# Premium skew for curated elite sets (draft classes, Pokémon 1st-Edition).
# More legendary/epic share than PLAYER_TIERS — a loaded set for a big price.
DRAFT_TIERS: list[tuple[str, float]] = [
    ("legendary", 0.08),
    ("epic", 0.15),
    ("rare", 0.25),
    ("uncommon", 0.30),
    ("common", 0.22),
]

# Pokémon cards carry an intrinsic rarity (8 tiers) — map onto our 5.
POKEMON_RARITY_MAP: dict[str, str] = {
    "Common": "common",
    "Uncommon": "uncommon",
    "Rare": "rare",
    "Double rare": "rare",
    "Illustration rare": "epic",
    "Ultra Rare": "epic",
    "ACE SPEC Rare": "epic",
    "Special illustration rare": "legendary",
    "Hyper rare": "legendary",
    "Mega Hyper Rare": "legendary",
    "Black White Rare": "legendary",
}


def map_pokemon_rarity(src: str) -> str:
    """Map a Pokémon source rarity to our tier; unknown -> common (never crash a seed)."""
    return POKEMON_RARITY_MAP.get(src, "common")


COIN_PER_USD = 100  # real card USD price -> coin book value


def pokemon_book_value(price_usd: float, tier: str) -> float:
    """Book value from real market price, never below the tier's floor."""
    return max(BOOK[tier], round((price_usd or 0.0) * COIN_PER_USD))


def needs_guaranteed_hit(cards: list[dict]) -> bool:
    """True if a box haul has no epic-or-better and should get a guaranteed epic."""
    return not any(c.get("rarity") in ("epic", "legendary") for c in cards)


PACKS_PER_BOX = 36


def box_price(base_cost: int) -> int:
    """Coins for a full box (36 packs)."""
    return PACKS_PER_BOX * base_cost
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cards.py -k "draft_tiers or pokemon_rarity or pokemon_book or guaranteed_hit or box_price" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/cards.py tests/test_cards.py
git commit -m "feat(cards): engine helpers for boxes + premium/pokemon seeding"
```

---

### Task 2: `mint_box` query

**Files:**
- Modify: `db/queries.py` (add `mint_box` after `mint_pack`, ~line 4694)
- Test: `tests/test_mint_box.py` (create)

**Interfaces:**
- Consumes: `engine.open_pack`, `engine.needs_guaranteed_hit`, `engine.box_price`,
  `engine.PACKS_PER_BOX`, existing `card_sets`/`card_designs` schema, `casino_wallets`.
- Produces: `mint_box(user: str, set_id: int, now_iso: str) -> dict` returning
  `{"cards": list[dict], "guaranteed_upgraded": bool}` where each card dict matches
  the `mint_pack` element shape (adds nothing new). Raises `ValueError` on refusal.

- [ ] **Step 1: Write the failing test**

Create `tests/test_mint_box.py`:

```python
"""mint_box: 36-pack box open — charge, draw, guaranteed hit, atomicity."""
from __future__ import annotations

import asyncio

import pytest

import db.schema as _schema
import db.queries as _queries
from shared import cards as engine


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    orig_s, orig_q = _schema.DB_PATH, _queries.DB_PATH
    _schema.DB_PATH = _queries.DB_PATH = db_path
    _run(_schema.init_db())
    yield db_path
    _schema.DB_PATH, _queries.DB_PATH = orig_s, orig_q


async def _seed_set(base_cost: int, total_packs: int, designs: list[dict]) -> int:
    set_id = await _queries.create_card_set("nba", 2003, "Test Set", total_packs, base_cost, "2026-01-01T00:00:00Z")
    await _queries.insert_card_designs(set_id, designs)
    return set_id


def _common_only_designs(n: int, copies: int) -> list[dict]:
    return [{
        "subject_key": f"c{i}", "subject_name": f"C{i}", "team": None,
        "rarity": "common", "is_rookie": False, "career_fame": 0.0,
        "total_copies": copies, "stats": {}, "headshot_url": None,
        "book_value": engine.BOOK["common"],
    } for i in range(n)]


def test_box_charges_and_opens_180(tmp_db):
    async def go():
        # rich enough pool: 200 commons x 3 copies + some epics
        designs = _common_only_designs(200, 3)
        designs += [{
            "subject_key": f"e{i}", "subject_name": f"E{i}", "team": None,
            "rarity": "epic", "is_rookie": False, "career_fame": 0.0,
            "total_copies": 10, "stats": {}, "headshot_url": None,
            "book_value": engine.BOOK["epic"],
        } for i in range(10)]
        set_id = await _seed_set(base_cost=100, total_packs=50, designs=designs)
        await _queries.update_casino_balance("u1", 1_000_000)
        res = await _queries.mint_box("u1", set_id, "2026-01-01T00:00:00Z")
        assert len(res["cards"]) >= 180  # 36*5, plus maybe a guaranteed bonus
        bal = await _queries.get_casino_balance("u1")
        assert bal == 1_000_000 - engine.box_price(100)  # 3600 charged
        cset = await _queries.get_card_set("nba", 2003)
        assert cset["packs_opened"] == 36
    _run(go())


def test_box_always_contains_epic_when_set_has_one(tmp_db):
    # Invariant (the user-facing guarantee), deterministic regardless of draw path:
    # a set that contains an epic yields at least one epic+ in every box.
    async def go():
        designs = _common_only_designs(300, 5)
        designs.append({
            "subject_key": "epic1", "subject_name": "GrailEpic", "team": None,
            "rarity": "epic", "is_rookie": False, "career_fame": 0.0,
            "total_copies": 5, "stats": {}, "headshot_url": None,
            "book_value": engine.BOOK["epic"],
        })
        set_id = await _seed_set(base_cost=10, total_packs=50, designs=designs)
        await _queries.update_casino_balance("u2", 1_000_000)
        res = await _queries.mint_box("u2", set_id, "2026-01-01T00:00:00Z")
        assert any(c["rarity"] in ("epic", "legendary") for c in res["cards"])
    _run(go())


def test_box_no_epic_available_does_not_crash(tmp_db):
    # Commons-only set: guarantee can't fire (no epic pool), stays False, no crash.
    async def go():
        designs = _common_only_designs(300, 5)
        set_id = await _seed_set(base_cost=10, total_packs=50, designs=designs)
        await _queries.update_casino_balance("u5", 1_000_000)
        res = await _queries.mint_box("u5", set_id, "2026-01-01T00:00:00Z")
        assert res["guaranteed_upgraded"] is False
        assert all(c["rarity"] == "common" for c in res["cards"])
    _run(go())


def test_box_refuses_when_fewer_than_36_packs_left(tmp_db):
    async def go():
        designs = _common_only_designs(50, 2)  # 100 cards = 20 packs of pool
        set_id = await _seed_set(base_cost=10, total_packs=20, designs=designs)
        await _queries.update_casino_balance("u3", 1_000_000)
        with pytest.raises(ValueError):
            await _queries.mint_box("u3", set_id, "2026-01-01T00:00:00Z")
        # charge rolled back
        assert await _queries.get_casino_balance("u3") == 1_000_000
    _run(go())


def test_box_refuses_when_broke(tmp_db):
    async def go():
        designs = _common_only_designs(300, 5)
        set_id = await _seed_set(base_cost=100000, total_packs=50, designs=designs)
        await _queries.update_casino_balance("u4", 500)
        with pytest.raises(ValueError):
            await _queries.mint_box("u4", set_id, "2026-01-01T00:00:00Z")
    _run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mint_box.py -v`
Expected: FAIL (AttributeError: module 'db.queries' has no attribute 'mint_box')

- [ ] **Step 3: Implement `mint_box`**

Add to `db/queries.py` immediately after `mint_pack` (after ~line 4693). This mirrors
`mint_pack`'s atomic pattern but loops 36 packs and appends a guaranteed epic if needed:

```python
async def mint_box(user: str, set_id: int, now_iso: str) -> dict:
    """Open a full box = 36 packs in one transaction. Charges 36 * base_cost, draws
    36 * pack_size cards from the finite pool, and if the haul has no epic+ appends one
    guaranteed epic (from the set's epic pool, if any remains). Atomic; ValueError on refusal.
    Returns {"cards": [...], "guaranteed_upgraded": bool}."""
    from shared import cards as engine

    PACK_SIZE = 5
    packs = engine.PACKS_PER_BOX  # 36

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            srow = (await (await db.execute(
                "SELECT * FROM card_sets WHERE set_id = ?", (set_id,))).fetchone())
            if srow is None:
                raise ValueError("no such set")
            if srow["closed"] or srow["packs_opened"] + packs > srow["total_packs"]:
                raise ValueError("not enough packs left in this set for a full box")
            cost = engine.box_price(srow["base_cost"])
            bal = (await (await db.execute(
                "SELECT balance FROM casino_wallets WHERE discord_user = ?", (user,))).fetchone())
            have = bal["balance"] if bal else 0
            if have < cost:
                raise ValueError(f"need {cost} coins for a box — you have {have}")
            await db.execute(
                "INSERT INTO casino_wallets (discord_user, balance) VALUES (?, ?) "
                "ON CONFLICT(discord_user) DO UPDATE SET balance = balance - ?",
                (user, CASINO_STARTING_COINS - cost, cost),
            )

            drows = (await (await db.execute(
                "SELECT * FROM card_designs WHERE set_id = ?", (set_id,))).fetchall())
            drows = [dict(d) for d in drows]
            manifest = [{"rarity": d["rarity"]} for d in drows]
            pool = {i: (d["total_copies"] - d["minted"])
                    for i, d in enumerate(drows) if d["total_copies"] > d["minted"]}

            import random as _random
            rng = _random.Random()

            async def _mint_one(design_index: int, is_holo: bool, gem, book: float) -> dict:
                d = drows[design_index]
                serial = d["minted"] + 1
                await db.execute(
                    "UPDATE card_designs SET minted = minted + 1 WHERE design_id = ?",
                    (d["design_id"],))
                drows[design_index] = {**d, "minted": serial}
                cur = await db.execute(
                    "INSERT INTO card_instances "
                    "(design_id, owner_id, serial, is_holo, gem, book_value, acquired_cost, source, acquired_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (d["design_id"], user, serial, 1 if is_holo else 0, gem, book,
                     (cost / (packs * PACK_SIZE)), "box", now_iso))
                return {
                    "instance_id": cur.lastrowid, "design_id": d["design_id"],
                    "name": d["subject_name"], "team": d["team"], "sport": srow["sport"],
                    "season": srow["season"], "rarity": d["rarity"], "is_rookie": bool(d["is_rookie"]),
                    "is_holo": is_holo, "gem": gem, "serial": serial,
                    "total_copies": d["total_copies"], "book_value": book,
                    "headshot_url": d["headshot_url"],
                    "stats": json.loads(d["stats"]) if d["stats"] else {},
                }

            cards_out: list[dict] = []
            for _ in range(packs):
                drawn = engine.open_pack(pool, manifest, rng, PACK_SIZE)
                for c in drawn:
                    cards_out.append(await _mint_one(
                        c["design_index"], c["is_holo"], c["gem"], c["book_value"]))
            if not cards_out:
                raise ValueError("no cards left in this set")

            guaranteed = False
            if engine.needs_guaranteed_hit(cards_out):
                epic_pool = [i for i, cnt in pool.items() if cnt > 0 and drows[i]["rarity"] == "epic"]
                if epic_pool:
                    idx = rng.choice(epic_pool)
                    pool[idx] -= 1
                    is_holo = engine.roll_holo(rng)
                    gem = engine.roll_gem(rng)
                    book = engine.book_value("epic", is_holo, gem)
                    cards_out.append(await _mint_one(idx, is_holo, gem, book))
                    guaranteed = True

            await db.execute(
                "UPDATE card_sets SET packs_opened = packs_opened + ?, "
                "closed = CASE WHEN packs_opened + ? >= total_packs THEN 1 ELSE closed END "
                "WHERE set_id = ?",
                (packs, packs, set_id))
            await db.commit()
            return {"cards": cards_out, "guaranteed_upgraded": guaranteed}
        except Exception:
            await db.execute("ROLLBACK")
            raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mint_box.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add db/queries.py tests/test_mint_box.py
git commit -m "feat(cards): mint_box — atomic 36-pack open with guaranteed epic"
```

---

### Task 3: `/pack box` command + summary reveal + `pokemon` sport

**Files:**
- Modify: `bot/cogs/cards.py` (add `"pokemon"` to `SPORTS`, add emoji, add `pack_box` command + `_send_box_summary` + a confirm view)
- Test: `tests/test_cards.py` (add a pure test for the summary embed builder)

**Interfaces:**
- Consumes: `queries.mint_box`, `queries.get_card_set`, `engine.is_notable_pull`,
  `engine.box_price`, existing `SPORT_EMOJI`, `RARITY_EMOJI`, `engine.RARITIES`.
- Produces: `pack_box` slash command; `_box_summary_embed(cards, guaranteed, title, set_name) -> discord.Embed`
  (pure, testable — takes plain dicts, returns an Embed).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cards.py` (top imports already load `cards` engine; add cog import):

```python
def test_box_summary_embed_counts_and_notables():
    from bot.cogs.cards import _box_summary_embed
    haul = (
        [{"rarity": "common", "name": "C", "is_holo": False, "gem": None, "book_value": 3.5, "serial": 1, "total_copies": 99}] * 170
        + [{"rarity": "epic", "name": "Grail", "is_holo": False, "gem": None, "book_value": 100, "serial": 1, "total_copies": 6}]
        + [{"rarity": "legendary", "name": "Big", "is_holo": True, "gem": "ruby", "book_value": 5000, "serial": 1, "total_copies": 1}] * 1
    )
    emb = _box_summary_embed(haul, guaranteed=False, title="Test Box", set_name="Test Set")
    body = emb.description + "".join(f.name + f.value for f in emb.fields)
    assert "171" in body or "170" in body  # commons count shown
    assert "Grail" in body      # epic highlighted
    assert "Big" in body        # legendary highlighted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cards.py -k box_summary_embed -v`
Expected: FAIL (ImportError: cannot import name '_box_summary_embed')

- [ ] **Step 3: Implement the sport addition, embed builder, confirm view, and command**

In `bot/cogs/cards.py`:

3a. Change line 23 from `SPORTS = ["nba", "nfl", "mlb"]` to:
```python
SPORTS = ["nba", "nfl", "mlb", "pokemon"]
```
3b. In `SPORT_EMOJI` (line ~35) add: `"pokemon": "🔴"` (Poké Ball red).

3c. Add a module-level embed builder (near other module functions, after `_now_iso`):
```python
def _box_summary_embed(cards: list[dict], guaranteed: bool, title: str, set_name: str) -> discord.Embed:
    """Summary reveal for a 36-pack box: rarity counts + highlighted notable pulls."""
    counts: dict[str, int] = {}
    for c in cards:
        counts[c["rarity"]] = counts.get(c["rarity"], 0) + 1
    breakdown = "  ".join(
        f"{RARITY_EMOJI.get(r, '')}{counts.get(r, 0)}" for r in engine.RARITIES if counts.get(r))
    notables = [c for c in cards if engine.is_notable_pull(c)]
    notables.sort(key=lambda c: c["book_value"], reverse=True)
    emb = discord.Embed(
        title=f"📦 {title}",
        description=f"Opened **{len(cards)}** cards from **{set_name}**\n{breakdown}",
        color=0xBB9AF7,
    )
    if notables:
        lines = []
        for c in notables[:12]:
            holo = "✨" if c.get("is_holo") else ""
            gem = f" 💎{c['gem']}" if c.get("gem") else ""
            lines.append(
                f"{RARITY_EMOJI.get(c['rarity'], '')} **{c['name']}** {holo}{gem} "
                f"#{c['serial']}/{c['total_copies']} · {round(c['book_value'])}🪙")
        emb.add_field(name=f"🔥 Notable pulls ({len(notables)})", value="\n".join(lines), inline=False)
    if guaranteed:
        emb.set_footer(text="Box guarantee: an epic was added — every box hits.")
    return emb
```

3d. Add a confirm view + the command inside `CardsCog`, right after `pack_open` (~line 368):
```python
    class _BoxConfirm(discord.ui.View):
        def __init__(self, opener: discord.abc.User):
            super().__init__(timeout=30)
            self.opener = opener
            self.value = False

        @discord.ui.button(label="Open box", style=discord.ButtonStyle.danger, emoji="📦")
        async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
            if interaction.user.id != self.opener.id:
                await interaction.response.send_message("Not your box.", ephemeral=True)
                return
            self.value = True
            for c in self.children:
                c.disabled = True
            await interaction.response.edit_message(view=self)
            self.stop()

    @pack.command(name="box", description="Buy & open a full box (36 packs) with a guaranteed hit")
    @app_commands.describe(sport="League", season="Season year")
    @app_commands.choices(sport=SPORT_CHOICES)
    async def pack_box(
        self, interaction: discord.Interaction, sport: app_commands.Choice[str], season: int,
    ):
        await interaction.response.defer()
        uid = str(interaction.user.id)
        cset = await queries.get_card_set(sport.value, season)
        if not cset:
            avail = [s for s in await queries.list_card_sets() if s["sport"] == sport.value]
            seasons = ", ".join(str(s["season"]) for s in avail) or "none yet"
            await interaction.followup.send(
                f"No **{sport.name} {season}** set exists. Available {sport.name} seasons: {seasons}")
            return
        price = engine.box_price(cset["pack_cost"])
        view = self._BoxConfirm(interaction.user)
        prompt = await interaction.followup.send(
            f"📦 **{cset['name']}** box = **36 packs** for **{price:,}** 🪙. "
            f"Guaranteed epic+ per box. Confirm?", view=view, wait=True)
        await view.wait()
        if not view.value:
            await prompt.edit(content="Box purchase cancelled.", view=None)
            return
        try:
            res = await queries.mint_box(uid, cset["set_id"], _now_iso())
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}")
            return
        title = f"{SPORT_EMOJI.get(sport.value, '🎴')} {cset['name']} — Box"
        emb = _box_summary_embed(res["cards"], res["guaranteed_upgraded"], title, cset["name"])
        await interaction.followup.send(embed=emb)
        await self._dm_wanters(res["cards"], interaction.user)
        await self._check_set_completion(uid, cset["set_id"], interaction)
        await self._grant_achievements(uid, interaction.channel)
```

Note: `cset["pack_cost"]` is the per-pack `base_cost` (see `_set_row`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cards.py -k box_summary_embed -v`
Expected: PASS

Run the whole card suite to catch regressions:
Run: `uv run pytest tests/test_cards.py tests/test_mint_box.py -v`
Expected: PASS

- [ ] **Step 5: Verify the bot imports cleanly**

Run: `uv run python -c "import bot.cogs.cards"`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add bot/cogs/cards.py tests/test_cards.py
git commit -m "feat(cards): /pack box with confirm + summary reveal; add pokemon sport"
```

---

### Task 4: Sports draft-class seeder

**Files:**
- Create: `scripts/seed_draft_classes.py`
- Create: `scripts/draft_rosters.py` (curated data, kept separate so the seeder stays small)
- Test: `tests/test_draft_seed.py`

**Interfaces:**
- Consumes: `engine.build_manifest`, `engine.DRAFT_TIERS`, `engine.book_value`,
  `queries.create_card_set`, `queries.insert_card_designs`, `queries.card_set_exists`.
- Produces: `draft_rosters.ROSTERS: dict[tuple[str, int], dict]` where each value is
  `{"name": str, "box_price": int, "boxes": int, "players": list[tuple[str, float]]}`
  (player name, fame score); and `seed_draft_classes.build_designs(players) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_draft_seed.py`:

```python
"""Curated draft-class seeding: manifest shape + premium skew."""
import importlib

seed = importlib.import_module("scripts.seed_draft_classes")
rosters = importlib.import_module("scripts.draft_rosters")
from shared import cards as engine


def test_rosters_present_and_priced():
    for key in [("nba", 2003), ("nba", 1979), ("nba", 1984)]:
        r = rosters.ROSTERS[key]
        assert r["players"] and r["name"] and r["box_price"] > 0 and r["boxes"] > 0
    # Jordan class is the priciest
    assert rosters.ROSTERS[("nba", 1984)]["box_price"] == 250_000


def test_build_designs_shape_and_premium():
    players = rosters.ROSTERS[("nba", 1984)]["players"]
    designs = seed.build_designs(players)
    assert len(designs) == len(players)
    keys = {"subject_key", "subject_name", "rarity", "is_rookie", "total_copies", "book_value"}
    assert keys <= set(designs[0])
    assert all(d["is_rookie"] for d in designs)
    # premium skew yields at least one legendary in a ~15-player elite set
    assert any(d["rarity"] == "legendary" for d in designs)
    # legendaries are 1-of-1 grails
    assert all(d["total_copies"] == 1 for d in designs if d["rarity"] == "legendary")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_draft_seed.py -v`
Expected: FAIL (ModuleNotFoundError: scripts.seed_draft_classes)

- [ ] **Step 3: Create the curated rosters**

Create `scripts/draft_rosters.py` (fame scores are relative; higher = rarer). Add
`__init__.py` if `scripts/` is not importable — check with `ls scripts/__init__.py`;
if missing, `touch scripts/__init__.py` and commit it.

```python
"""Curated NBA draft-class rosters for premium card sets. Fame score drives rarity
(higher = rarer). All are rookie cards. 1979 is framed as the 1979-80 rookie class,
so Larry Bird (drafted 1978, debuted 1979-80) is intentionally included."""

ROSTERS: dict[tuple[str, int], dict] = {
    ("nba", 2003): {
        "name": "2003 NBA Draft Class",
        "box_price": 50_000,
        "boxes": 20,
        "players": [
            ("LeBron James", 100.0), ("Dwyane Wade", 92.0), ("Carmelo Anthony", 85.0),
            ("Chris Bosh", 75.0), ("Kyle Korver", 45.0), ("David West", 44.0),
            ("Josh Howard", 41.0), ("Boris Diaw", 39.0), ("Mo Williams", 38.0),
            ("Leandro Barbosa", 36.0), ("Kendrick Perkins", 34.0), ("Kirk Hinrich", 33.0),
            ("Nick Collison", 26.0), ("Zaza Pachulia", 24.0), ("T.J. Ford", 23.0),
            ("Steve Blake", 22.0), ("Luke Walton", 20.0), ("Darko Milicic", 19.0),
            ("Willie Green", 15.0), ("Travis Outlaw", 14.0),
        ],
    },
    ("nba", 1979): {
        "name": "1979 Rookies — Magic & Bird",
        "box_price": 150_000,
        "boxes": 12,
        "players": [
            ("Magic Johnson", 100.0), ("Larry Bird", 98.0), ("Sidney Moncrief", 55.0),
            ("Bill Cartwright", 45.0), ("Vinnie Johnson", 40.0), ("Jim Paxson", 38.0),
            ("Calvin Natt", 36.0), ("Bill Laimbeer", 34.0), ("James Bailey", 20.0),
            ("Larry Demic", 15.0), ("Roger Phegley", 13.0), ("Cliff Robinson", 22.0),
        ],
    },
    ("nba", 1984): {
        "name": "1984 NBA Draft Class",
        "box_price": 250_000,
        "boxes": 6,
        "players": [
            ("Michael Jordan", 100.0), ("Hakeem Olajuwon", 92.0), ("Charles Barkley", 88.0),
            ("John Stockton", 84.0), ("Sam Perkins", 45.0), ("Otis Thorpe", 43.0),
            ("Kevin Willis", 42.0), ("Alvin Robertson", 40.0), ("Jerome Kersey", 35.0),
            ("Sam Bowie", 30.0), ("Vern Fleming", 27.0), ("Michael Cage", 26.0),
            ("Jay Humphries", 22.0), ("Tony Campbell", 20.0),
        ],
    },
}
```

- [ ] **Step 4: Create the seeder**

Create `scripts/seed_draft_classes.py`:

```python
"""Seed premium NBA draft-class card sets from curated rosters (scripts/draft_rosters.py).

Idempotent: a (sport, season) that already exists is skipped. Run on the VPS:
    cd /opt/sharplab && venv/bin/python scripts/seed_draft_classes.py
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import queries  # noqa: E402
from db.schema import init_db  # noqa: E402
from shared import cards as engine  # noqa: E402
from scripts.draft_rosters import ROSTERS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed_draft")

PACK_SIZE = 5


def build_designs(players: list[tuple[str, float]]) -> list[dict]:
    """Curated (name, fame) -> manifest designs with premium skew, legendaries 1-of-1."""
    total_cards = len(players) * PACK_SIZE  # ~one design-copy budget per player
    subjects = [{
        "subject_key": name.lower().replace(" ", "_").replace(".", ""),
        "name": name, "stardom": fame, "is_rookie": True, "career_fame": fame,
    } for name, fame in players]
    manifest = engine.build_manifest(subjects, total_cards, engine.DRAFT_TIERS)
    for d in manifest:
        if d["rarity"] == "legendary":
            d["copies"] = 1  # grails
    out = []
    for d in manifest:
        out.append({
            "subject_key": d["subject_key"], "subject_name": d["name"], "team": None,
            "rarity": d["rarity"], "is_rookie": True, "career_fame": d.get("career_fame"),
            "total_copies": d["copies"], "stats": {}, "headshot_url": None,
            "book_value": d["book_value"],
        })
    return out


async def seed_one(sport: str, season: int, cfg: dict) -> bool:
    if await queries.card_set_exists(sport, season):
        log.info("  %s %s already seeded — skipping", sport.upper(), season)
        return False
    designs = build_designs(cfg["players"])
    base_cost = round(cfg["box_price"] / engine.PACKS_PER_BOX)
    total_packs = cfg["boxes"] * engine.PACKS_PER_BOX
    now = datetime.now(timezone.utc).isoformat()
    set_id = await queries.create_card_set(sport, season, cfg["name"], total_packs, base_cost, now)
    await queries.insert_card_designs(set_id, designs)
    log.info("  seeded %s (%d designs, base_cost=%d, box=%d, packs=%d)",
             cfg["name"], len(designs), base_cost, cfg["box_price"], total_packs)
    return True


async def main() -> None:
    await init_db()
    for (sport, season), cfg in ROSTERS.items():
        await seed_one(sport, season, cfg)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_draft_seed.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add scripts/seed_draft_classes.py scripts/draft_rosters.py tests/test_draft_seed.py
[ -f scripts/__init__.py ] && git add scripts/__init__.py
git commit -m "feat(cards): seeder for premium NBA draft-class sets (2003/1979/1984)"
```

---

### Task 5: Pokémon data vendoring + seeder

**Files:**
- Create: `scripts/extract_pokemon_data.py` (one-off extractor; reads the pokemon-cards project)
- Create: `data/pokemon_cards.json` (vendored trimmed snapshot, produced by the extractor)
- Create: `scripts/seed_pokemon.py`
- Test: `tests/test_pokemon_seed.py`

**Interfaces:**
- Consumes: `engine.map_pokemon_rarity`, `engine.pokemon_book_value`, `engine.COPIES_REL`,
  `queries.create_card_set`, `queries.insert_card_designs`, `queries.card_set_exists`.
- Produces: `seed_pokemon.build_pokemon_designs(cards: list[dict]) -> list[dict]`
  where each input card is `{"name", "rarity", "price_usd"}`; and
  `seed_pokemon.SETS: dict[tuple[str, int], dict]` config
  (`{"src_id": str, "name": str, "box_price": int, "boxes": int, "premium": bool}`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_pokemon_seed.py`. It uses a small inline fixture (no dependency on
the vendored file), so it runs anywhere:

```python
"""Pokémon design building: rarity mapping + real-price book values."""
import importlib

seed = importlib.import_module("scripts.seed_pokemon")
from shared import cards as engine


FIXTURE = [
    {"name": "Charizard ex", "rarity": "Special illustration rare", "price_usd": 397.07},
    {"name": "Pikachu", "rarity": "Illustration rare", "price_usd": 95.36},
    {"name": "Ivysaur", "rarity": "Uncommon", "price_usd": 0.22},
    {"name": "Geodude", "rarity": "Common", "price_usd": 0.19},
]


def test_build_maps_rarity_and_prices():
    designs = seed.build_pokemon_designs(FIXTURE)
    by_name = {d["subject_name"]: d for d in designs}
    assert by_name["Charizard ex"]["rarity"] == "legendary"
    assert by_name["Pikachu"]["rarity"] == "epic"
    assert by_name["Ivysaur"]["rarity"] == "uncommon"
    assert by_name["Charizard ex"]["book_value"] == 39707.0
    # common floors, never below tier book
    assert by_name["Geodude"]["book_value"] == engine.BOOK["common"]
    # legendaries collapse to 1-of-1
    assert by_name["Charizard ex"]["total_copies"] == 1


def test_sets_config_has_151_and_first_edition():
    keys = seed.SETS
    assert ("pokemon", 2023) in keys      # standard 151
    assert ("pokemon", 1999) in keys      # 1st Edition grail
    assert keys[("pokemon", 1999)]["box_price"] == 300_000
    assert keys[("pokemon", 1999)]["premium"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pokemon_seed.py -v`
Expected: FAIL (ModuleNotFoundError: scripts.seed_pokemon)

- [ ] **Step 3: Create the extractor (one-off, run on David's desktop)**

Create `scripts/extract_pokemon_data.py`:

```python
"""One-off: trim ~/code/pokemon-cards/data.json into data/pokemon_cards.json.

Keeps only {id, name, cards:[{name, rarity, price_usd}]} — max price across variants.
Run locally (has the source project), commit the output:
    python scripts/extract_pokemon_data.py
"""
import json
import os

SRC = os.path.expanduser("~/code/pokemon-cards/data.json")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "pokemon_cards.json")


def _max_price(card: dict) -> float:
    prices = card.get("prices") or {}
    return max(prices.values()) if prices else 0.0


def main() -> None:
    d = json.load(open(SRC))
    out = []
    for s in d["sets"]:
        out.append({
            "id": s["id"], "name": s["name"],
            "cards": [{"name": c["name"], "rarity": c.get("rarity", "Common"),
                       "price_usd": round(_max_price(c), 2)} for c in s.get("cards", [])],
        })
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"sets": out}, open(OUT, "w"), ensure_ascii=False)
    print(f"wrote {OUT}: {len(out)} sets, {sum(len(s['cards']) for s in out)} cards")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Produce the vendored data file**

Run: `python scripts/extract_pokemon_data.py`
Expected: `wrote .../data/pokemon_cards.json: 15 sets, ... cards`
Verify: `uv run python -c "import json; d=json.load(open('data/pokemon_cards.json')); print(len(d['sets']))"` → `15`

- [ ] **Step 5: Create the seeder**

Create `scripts/seed_pokemon.py`:

```python
"""Seed Pokémon card sets from the vendored data/pokemon_cards.json (real names,
rarities, market prices). Standard sets + a premium 151 '1st Edition' grail box.

Idempotent. Run on the VPS:
    cd /opt/sharplab && venv/bin/python scripts/seed_pokemon.py
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import queries  # noqa: E402
from db.schema import init_db  # noqa: E402
from shared import cards as engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed_pokemon")

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "pokemon_cards.json")

# (sport, season) -> config. src_id matches data/pokemon_cards.json set ids.
SETS: dict[tuple[str, int], dict] = {
    ("pokemon", 2023): {"src_id": "sv03.5", "name": "Pokémon 151", "box_price": 15_000, "boxes": 30, "premium": False},
    ("pokemon", 1999): {"src_id": "sv03.5", "name": "Pokémon 151 — 1st Edition", "box_price": 300_000, "boxes": 5, "premium": True},
    ("pokemon", 2025): {"src_id": "sv08.5", "name": "Prismatic Evolutions", "box_price": 20_000, "boxes": 25, "premium": False},
    ("pokemon", 2024): {"src_id": "sv08", "name": "Surging Sparks", "box_price": 15_000, "boxes": 25, "premium": False},
}

PACK_SIZE = 5


def _load_cards(src_id: str) -> list[dict]:
    d = json.load(open(DATA))
    for s in d["sets"]:
        if s["id"] == src_id:
            return s["cards"]
    raise ValueError(f"set {src_id} not in {DATA}")


def build_pokemon_designs(cards: list[dict]) -> list[dict]:
    """Real-card dicts {name, rarity, price_usd} -> designs. Rarity mapped directly,
    book_value from price, copies from COPIES_REL by tier, legendaries 1-of-1."""
    out = []
    for c in cards:
        tier = engine.map_pokemon_rarity(c["rarity"])
        copies = 1 if tier == "legendary" else engine.COPIES_REL[tier]
        out.append({
            "subject_key": c["name"].lower().replace(" ", "_"),
            "subject_name": c["name"], "team": None, "rarity": tier, "is_rookie": False,
            "career_fame": c.get("price_usd", 0.0), "total_copies": copies,
            "stats": {}, "headshot_url": None,
            "book_value": engine.pokemon_book_value(c.get("price_usd", 0.0), tier),
        })
    return out


async def seed_one(sport: str, season: int, cfg: dict) -> bool:
    if await queries.card_set_exists(sport, season):
        log.info("  %s %s already seeded — skipping", sport.upper(), season)
        return False
    designs = build_pokemon_designs(_load_cards(cfg["src_id"]))
    base_cost = round(cfg["box_price"] / engine.PACKS_PER_BOX)
    total_packs = cfg["boxes"] * engine.PACKS_PER_BOX
    now = datetime.now(timezone.utc).isoformat()
    set_id = await queries.create_card_set(sport, season, cfg["name"], total_packs, base_cost, now)
    await queries.insert_card_designs(set_id, designs)
    log.info("  seeded %s (%d designs, base_cost=%d, box=%d)",
             cfg["name"], len(designs), base_cost, cfg["box_price"])
    return True


async def main() -> None:
    await init_db()
    for (sport, season), cfg in SETS.items():
        await seed_one(sport, season, cfg)


if __name__ == "__main__":
    asyncio.run(main())
```

Note: the standard 151 and the 1st-Edition set share `src_id` "sv03.5" (same cards);
they differ only by price/print-run. `insert_card_designs` uses `INSERT OR IGNORE` on
subject_key **per set_id**, so the duplicate names across the two sets are fine.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_pokemon_seed.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add scripts/extract_pokemon_data.py scripts/seed_pokemon.py data/pokemon_cards.json tests/test_pokemon_seed.py
git commit -m "feat(cards): Pokémon sets seeder + 151 1st-Edition grail box"
```

---

### Task 6: Achievements for premium packs

**Files:**
- Modify: `bot/cogs/progression.py` (add achievement defs) — verify the exact
  registry name first with `grep -n "ACHIEVEMENTS\|def .*achiev\|register" bot/cogs/progression.py shared/achievements.py`
- Modify: `bot/cogs/cards.py` `_grant_achievements` if it gates on specific keys
- Test: extend `tests/test_cards.py` or the progression test if one exists

**Interfaces:**
- Consumes: existing achievement-definition structure (match its shape exactly).
- Produces: three achievements: `box_opener` (open any box), `grail_pull`
  (pull a 1-of-1 legendary), `draft_master` (complete a draft-class set).

- [ ] **Step 1: Inspect the achievements structure**

Run: `grep -n "ACHIEVEMENTS\|category\|\"id\"\|def unlock\|CATEGORIES" shared/achievements.py | head -40`
Read the surrounding definitions so the new entries match the existing schema
(id, name, description, category, points/threshold fields).

- [ ] **Step 2: Write the failing test**

Add to `tests/test_cards.py` (adjust import/shape to the real registry discovered in Step 1):

```python
def test_premium_pack_achievements_exist():
    from shared import achievements
    ids = {a["id"] for a in achievements.ALL} if hasattr(achievements, "ALL") else set()
    assert {"box_opener", "grail_pull", "draft_master"} <= ids
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_cards.py -k premium_pack_achievements -v`
Expected: FAIL

- [ ] **Step 4: Add the achievements**

Add three entries to the achievements registry matching the existing schema (from
Step 1). Wire their unlock checks where card pulls/box opens are handled (in
`_grant_achievements` / the box command). Example shape (adapt to real fields):

```python
{"id": "box_opener", "name": "Case Cracker", "description": "Open a full 36-pack box.", "category": "cards"},
{"id": "grail_pull", "name": "Grail Hunter", "description": "Pull a 1-of-1 legendary card.", "category": "cards"},
{"id": "draft_master", "name": "Draft Historian", "description": "Complete a draft-class set.", "category": "cards"},
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_cards.py -k premium_pack_achievements -v`
Expected: PASS

- [ ] **Step 6: Full suite + commit**

Run: `uv run pytest tests/test_cards.py tests/test_mint_box.py tests/test_draft_seed.py tests/test_pokemon_seed.py -v`
Expected: PASS

```bash
git add bot/cogs/progression.py bot/cogs/cards.py shared/achievements.py tests/test_cards.py
git commit -m "feat(cards): achievements for boxes, grails, draft-class completion"
```

---

## Deploy (after all tasks merge)

Not a code task — run during the deploy (`/deploy` skill):
1. On the VPS: `venv/bin/python scripts/seed_draft_classes.py` and `venv/bin/python scripts/seed_pokemon.py`.
2. Restart `sharplab-bot`; the bot syncs slash commands so `/pack box` appears.
3. Run the achievement backfill (`scripts/backfill_achievements.py`).
4. `scripts/announce_deploy.py --post` (Announce: yes).
