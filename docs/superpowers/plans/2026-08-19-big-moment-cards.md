# Big Moment Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add short-printed "Big Moment" alt-art cards — each `(sport, season)` pack gains that season's biggest single-game performances as rare-or-better cards.

**Architecture:** The card economics engine (`shared/cards.py`) already accepts a custom `tiers` table in `build_manifest`, so it needs only three new constants. The real new work is one per-game-log fetcher in `scripts/card_sources.py` that reuses the **existing ESPN athlete IDs** and HTTP helpers. The seeder splits subjects into player vs moment pools, builds two manifests, and merges them. A single `card_type` column carries the distinction through the DB into cosmetic Discord/web display.

**Tech Stack:** Python 3.14 (async), `httpx`, `aiosqlite` (SQLite), `discord.py`, vanilla JS frontend, `pytest`.

## Global Constants (verbatim from spec)

- `MOMENT_TIERS = [("legendary", 0.3), ("epic", 0.3), ("rare", 0.4)]` — all-rare-or-better moment pool → ~3 legendary / 3 epic / 4 rare on a 10-pool.
- `MOMENT_SHARE = 0.016` — moment cards' share of the set print run (short-print). On the current run (`500 packs × 5 = 2500 cards`) → `round(2500 × 0.016) = 40` moment cards.
- `MOMENT_POOL = 10` — top-N single-game performances kept per set.
- `MOMENT_CANDIDATES = 40` — only the top-40 players by `career_fame` have their game logs fetched (bounds the per-player gamelog calls). `# ponytail: a huge game from an obscure player is missed; raise this constant if the ranking looks wrong.`
- Moment legendaries collapse to **1-of-1** (existing `_build_designs` loop, unchanged — it flows freed copies to player commons).
- Data source: **ESPN athlete `gamelog` endpoint, uniform across all three leagues** — `https://site.web.api.espn.com/apis/common/v3/sports/{sport}/{league}/athletes/{id}/gamelog?season={year}`. **Documented deviation from spec §2** (which locked NBA→BallDontLie, MLB→StatsAPI, NFL→ESPN): the ESPN gamelog returns the same per-game box scores for all three leagues in the same `names`/`stats` shape the existing parsers already use, and reuses the ESPN athlete IDs the season fetchers already fetch — eliminating three separate API integrations and two ID-mapping layers. Same box-score data, one code path.
- All new fetcher code must **never raise** — on any failure return whatever was gathered (matches existing `card_sources.py` contract). A season with no game data yields **zero moments** and never aborts a seed run.

---

### Task 1: Engine constants for the moment pool

**Files:**
- Modify: `shared/cards.py` (add constants after `PLAYER_TIERS`, ~line 81)
- Test: `tests/test_cards.py` (add after `test_build_manifest_sums_and_orders`, ~line 84)

**Interfaces:**
- Produces: `cards.MOMENT_TIERS: list[tuple[str, float]]`, `cards.MOMENT_SHARE: float`. Consumed by `scripts/seed_cards.py` (Task 5).
- Note: `MOMENT_POOL` and `MOMENT_CANDIDATES` live in `card_sources.py` (Task 4), NOT here — they bound the fetcher, not the economics.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cards.py`:

```python
def test_moment_tiers_yield_rare_or_better_split():
    """MOMENT_TIERS on a 10-subject pool -> ~3 legendary / 3 epic / 4 rare, no commons,
    and the manifest sums to the requested moment_cards total."""
    subjects = [
        {"subject_key": f"m{i}", "name": f"m{i}", "season": 2026,
         "stardom": float(i), "card_type": "moment"}
        for i in range(10)
    ]
    m = cards.build_manifest(subjects, total_cards=40, tiers=cards.MOMENT_TIERS)
    counts = {}
    for d in m:
        counts[d["rarity"]] = counts.get(d["rarity"], 0) + 1
    assert counts == {"legendary": 3, "epic": 3, "rare": 4}
    assert sum(d["copies"] for d in m) == 40
    assert all(d["card_type"] == "moment" for d in m)  # card_type carried through
    assert abs(cards.MOMENT_SHARE - 0.016) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cards.py::test_moment_tiers_yield_rare_or_better_split -v`
Expected: FAIL with `AttributeError: module 'shared.cards' has no attribute 'MOMENT_TIERS'`

- [ ] **Step 3: Write minimal implementation**

In `shared/cards.py`, immediately after the `PLAYER_TIERS = [...]` block (~line 81), add:

```python
# --- Big Moment cards (per-season biggest single games) ---------------------
# A short-printed, all-rare-or-better slice: the very biggest games go legendary
# (1-of-1), then epic, then rare. Fraction-based so it degrades gracefully on thin
# seasons. MOMENT_POOL / MOMENT_CANDIDATES (the fetcher bounds) live in card_sources.
MOMENT_TIERS = [("legendary", 0.3), ("epic", 0.3), ("rare", 0.4)]
MOMENT_SHARE = 0.016  # moments' share of a set's print run (a moment is a rare pull)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cards.py::test_moment_tiers_yield_rare_or_better_split -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add shared/cards.py tests/test_cards.py
git commit -m "feat(cards): MOMENT_TIERS + MOMENT_SHARE engine constants"
```

---

### Task 2: `card_type` column on `card_designs`

**Files:**
- Modify: `db/schema.py` (the `card_designs` CREATE TABLE ~line 473, and add an idempotent migration in `init_db`)
- Test: `tests/test_cards.py` (add a DB-backed test near the other `tmp_db` tests, after `_count_instances`)

**Interfaces:**
- Produces: `card_designs.card_type TEXT NOT NULL DEFAULT 'player'`. Consumed by Task 3 (queries) and Task 5 (seeder).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cards.py` (after the `tmp_db` fixture is defined, alongside the other DB tests):

```python
def test_card_designs_has_card_type_column(tmp_db):
    async def go():
        async with aiosqlite.connect(_queries.DB_PATH) as db:
            cur = await db.execute("PRAGMA table_info(card_designs)")
            cols = {r[1] for r in await cur.fetchall()}
        return cols
    cols = _run(go())
    assert "card_type" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cards.py::test_card_designs_has_card_type_column -v`
Expected: FAIL with `assert 'card_type' in {...}` (column missing)

- [ ] **Step 3: Write minimal implementation**

In `db/schema.py`, add the column to the `card_designs` CREATE TABLE so fresh DBs get it. Change (~line 486):

```sql
    headshot_url TEXT,
    book_value   REAL NOT NULL,
    UNIQUE(set_id, subject_key)
);
```

to:

```sql
    headshot_url TEXT,
    book_value   REAL NOT NULL,
    card_type    TEXT NOT NULL DEFAULT 'player',   -- 'player' | 'moment'
    UNIQUE(set_id, subject_key)
);
```

Then add a migration inside `init_db()` for existing DBs, matching the house idiom (a bare `try: ALTER … except Exception: pass` — see the `clv_posted` / `sport` migrations near the top of `init_db`). Add it anywhere in the migration block:

```python
        # Migration: Big Moment cards — distinguish alt-art moment cards from player cards.
        try:
            await db.execute(
                "ALTER TABLE card_designs ADD COLUMN card_type TEXT NOT NULL DEFAULT 'player'"
            )
            await db.commit()
        except Exception:
            pass  # column already exists
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cards.py::test_card_designs_has_card_type_column -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/schema.py tests/test_cards.py
git commit -m "feat(cards): card_type column on card_designs"
```

---

### Task 3: Carry `card_type` through the query layer

**Files:**
- Modify: `db/queries.py`
  - `insert_card_designs` (~line 4460) — write `card_type`
  - `_design_public` (~line 4532) — read `card_type`. **This is the shared read path** — `get_catalog`, `find_designs_by_name`, and `get_design_owners` all build their dicts from `_design_public`, so this one edit covers all three.
  - `mint_pack` out dict (~line 4612) — read `card_type` (its own dict, not `_design_public`)
  - `get_collection` out dict (~line 4654) and its SELECT (~line 4646) — read `card_type` (its own dict + join)
- Test: `tests/test_cards.py`
- **Intentionally NOT touched:** `get_card_instances_after` (the rare-pull watcher) builds its own dict but doesn't need `card_type` — moment legendaries already trigger notable-pull alerts by rarity, and the spec (§7) wants no special alert text. YAGNI.

**Interfaces:**
- Consumes: `card_designs.card_type` column (Task 2).
- Produces: every card dict returned by these functions carries `"card_type"` (defaulting to `"player"`). Consumed by Discord display (Task 6) and web display (Task 7). `insert_card_designs` reads `d.get("card_type", "player")` from each design dict (Task 5 supplies it).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cards.py`:

```python
def test_card_type_round_trips_through_insert_and_read(tmp_db):
    async def go():
        sid = await _queries.create_card_set("nba", 2026, "NBA 2025-26", 1000, 50, "t")
        await _queries.insert_card_designs(sid, [
            {"subject_key": "nba|2026|star-1", "subject_name": "Star", "team": "LAL",
             "rarity": "legendary", "is_rookie": False, "career_fame": 90.0,
             "total_copies": 1, "stats": {"PTS": 61, "Game": "vs BOS · 2026-01-14"},
             "headshot_url": None, "book_value": 260.0, "card_type": "moment"},
            {"subject_key": "nba|2026|guy-2", "subject_name": "Guy", "team": "LAL",
             "rarity": "common", "is_rookie": False, "career_fame": 5.0,
             "total_copies": 50, "stats": {}, "headshot_url": None,
             "book_value": 3.5},  # no card_type key -> defaults to 'player'
        ])
        cat = await _queries.get_catalog(sid)
        return {d["name"]: d["card_type"] for d in cat["designs"]}
    types = _run(go())
    assert types["Star"] == "moment"
    assert types["Guy"] == "player"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cards.py::test_card_type_round_trips_through_insert_and_read -v`
Expected: FAIL — `KeyError: 'card_type'` (get_catalog rows don't include it yet)

- [ ] **Step 3: Write minimal implementation**

**3a.** `insert_card_designs` — add the column to the INSERT. Change the column list, the `VALUES` placeholders, and the row tuple:

```python
            "INSERT OR IGNORE INTO card_designs "
            "(set_id, subject_key, subject_name, team, rarity, is_rookie, career_fame, "
            " total_copies, stats, headshot_url, book_value, card_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
```

and append `d.get("card_type", "player"),` as the last field in the per-design tuple (after `d["book_value"],`).

**3b.** `_design_public` (~line 4532) — add to the returned dict. This one edit also flows `card_type` into `get_catalog`, `find_designs_by_name`, and `get_design_owners`, which all reuse `_design_public`:

```python
        "card_type": r["card_type"],
```

**3c.** `mint_pack` out dict (~line 4612, the `out.append({...})`) — add:

```python
                    "card_type": d["card_type"],
```

(`d` there is a `card_designs` row / dict from `SELECT * FROM card_designs`, so `d["card_type"]` is present.)

**3d.** `get_collection` — add `d.card_type` to the SELECT column list (~line 4646):

```python
            "SELECT i.*, d.subject_name, d.team, d.rarity, d.headshot_url, d.stats, d.total_copies, "
            "       d.card_type, s.sport, s.season "
```

and add to its out dict (~line 4658):

```python
            "card_type": r["card_type"],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cards.py::test_card_type_round_trips_through_insert_and_read -v`
Expected: PASS

- [ ] **Step 5: Run the full card test file (no regressions)**

Run: `uv run pytest tests/test_cards.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add db/queries.py tests/test_cards.py
git commit -m "feat(cards): carry card_type through insert + read queries"
```

---

### Task 4: Per-game moment fetcher (`card_sources.py`)

The one genuinely new piece. A pure `_best_moment` parser (fixture-testable, no network) plus a network `fetch_moments` driver that reuses the existing ESPN IDs + `_get_json`/`_num`/semaphore helpers.

**Files:**
- Modify: `scripts/card_sources.py` (add constants + `_game_score`, `_moment_stats`, `_game_line`, `_best_moment`, `fetch_moments` after the public `fetch_*_season` functions, ~line 457)
- Create: `tests/fixtures/nba_gamelog_sample.json` (a trimmed real gamelog — capture instructions in Step 1)
- Test: `tests/test_cards.py`

**Interfaces:**
- Consumes: player subject dicts from `fetch_{sport}_season(year)` (each has `subject_key` ending `-{espn_id}`, `name`, `team`, `career_fame`, `headshot_url`).
- Produces:
  - `card_sources.MOMENT_POOL = 10`, `card_sources.MOMENT_CANDIDATES = 40`
  - `_best_moment(sport_key: str, gamelog: dict, player: dict) -> dict | None` — pure. Returns a moment subject dict `{subject_key, name, team, card_type: "moment", is_rookie: False, stardom: float, stats: dict, headshot_url}` or `None` if the player has no scoring game.
  - `async fetch_moments(sport_key: str, year: int, players: list[dict]) -> list[dict]` — top ≤`MOMENT_POOL` moment subjects for the set, ranked by game score desc. Never raises.
- The moment `subject_key` is `f"{player['subject_key']}|moment"` (distinct from the player card key). `stardom` = the per-sport game score (ranks moments **within** the moment pool). `stats["Game"]` is the game line, e.g. `"vs MIN · 2025-05-01"`.

- [ ] **Step 1: Capture the fixture, then write the failing test**

Capture a real trimmed NBA gamelog to `tests/fixtures/nba_gamelog_sample.json` by running this one-off (network) and saving its stdout:

```bash
mkdir -p tests/fixtures
uv run python -c "
import asyncio, json, httpx
H={'User-Agent':'sharplab-cards/1.0 (python-httpx)'}
async def m():
    url='https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/1966/gamelog?season=2025'
    async with httpx.AsyncClient(timeout=15,headers=H,follow_redirects=True) as c:
        d=(await c.get(url)).json()
    # trim to what _best_moment reads: names, seasonTypes (events: eventId+stats), events meta
    keep={'names':d['names'],'seasonTypes':[],'events':{}}
    for st in d.get('seasonTypes',[]):
        cats=[{'events':[{'eventId':e['eventId'],'stats':e['stats']} for e in c.get('events',[])]} for c in st.get('categories',[])]
        keep['seasonTypes'].append({'displayName':st.get('displayName'),'categories':cats})
    for eid,meta in (d.get('events') or {}).items():
        keep['events'][eid]={'opponent':{'abbreviation':(meta.get('opponent') or {}).get('abbreviation')},'atVs':meta.get('atVs'),'gameDate':meta.get('gameDate')}
    print(json.dumps(keep))
asyncio.run(m())
" > tests/fixtures/nba_gamelog_sample.json
```

Verify it's non-empty JSON: `uv run python -c "import json;print(len(json.load(open('tests/fixtures/nba_gamelog_sample.json'))['seasonTypes']))"` (expect a small positive number).

Then add to `tests/test_cards.py`:

```python
import json as _json  # if not already imported at top; the file imports json elsewhere

def test_best_moment_ranks_top_game_and_captures_line():
    import card_sources  # scripts/ is on sys.path via the seed script's convention; see note below
    gl = _json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "nba_gamelog_sample.json")))
    player = {"subject_key": "nba|2025|lebron-james-1966", "name": "LeBron James",
              "team": "Los Angeles Lakers", "career_fame": 90.0,
              "headshot_url": "https://a.espncdn.com/i/headshots/nba/players/full/1966.png"}
    m = card_sources._best_moment("nba", gl, player)
    assert m is not None
    assert m["card_type"] == "moment"
    assert m["subject_key"] == "nba|2025|lebron-james-1966|moment"
    assert m["stardom"] > 0
    assert "PTS" in m["stats"] and "Game" in m["stats"]
    # the chosen game must be this player's highest game score in the fixture
    best_pts = 0
    for st in gl["seasonTypes"]:
        if "Regular Season" not in (st.get("displayName") or ""):
            continue
        for cat in st["categories"]:
            for ev in cat["events"]:
                row = dict(zip(gl["names"], ev["stats"]))
                best_pts = max(best_pts, float(row["points"]))
    assert m["stats"]["PTS"] == int(best_pts)
```

**Note on `import card_sources`:** `tests/test_cards.py` already does `sys.path.insert(0, <repo root>)`. Add `sys.path.insert(0, os.path.join(<repo root>, "scripts"))` near the top of the test file (mirror the path insert `seed_cards.py` uses) so `import card_sources` resolves. Put this next to the existing `sys.path.insert` at the top.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cards.py::test_best_moment_ranks_top_game_and_captures_line -v`
Expected: FAIL with `AttributeError: module 'card_sources' has no attribute '_best_moment'`

- [ ] **Step 3: Write minimal implementation**

In `scripts/card_sources.py`, after the three `fetch_*_season` public functions (~line 457, before the `manual smoke test` section), add:

```python
# ---------------------------------------------------------------------------
# Big Moment cards — per-game logs (biggest single game of the season)
# ---------------------------------------------------------------------------
# Reuses the SAME ESPN athlete ids the season fetchers already have, via one
# uniform gamelog endpoint across all three leagues:
#   {_WEB}/{sport}/{league}/athletes/{id}/gamelog?season={year}
# Each event carries a `stats` array aligned to the top-level `names` list (same
# shape _rows_by_year parses). `events` (dict, keyed by eventId) gives opponent +
# date for the game line.

MOMENT_POOL = 10          # top-N single games kept per set
MOMENT_CANDIDATES = 40    # only the top-N players by fame get a gamelog fetch (bounds calls)
# ponytail: game-score is a simple per-sport weighted sum, and only the top-40
# famous players' logs are fetched — a monster game from an obscure player is
# missed. Swap the metric / raise MOMENT_CANDIDATES only if the ranking looks wrong.


def _game_score(sport_key: str, row: dict, names: list) -> float:
    """Per-sport single-game score. `row` = dict(zip(names, event_stats))."""
    if sport_key == "nba":
        return _num(row.get("points")) + 0.4 * _num(row.get("totalRebounds")) + 0.7 * _num(row.get("assists"))
    if sport_key == "nfl":
        return max(0.0, (
            _num(row.get("passingYards")) / 25.0
            + _num(row.get("passingTouchdowns")) * 4.0
            - _num(row.get("interceptions")) * 2.0
            + _num(row.get("rushingYards")) / 10.0
            + _num(row.get("rushingTouchdowns")) * 6.0
            + _num(row.get("receivingYards")) / 10.0
            + _num(row.get("receivingTouchdowns")) * 6.0
        ))
    # mlb: hitter gamelogs carry 'atBats'; pitcher gamelogs don't.
    if "atBats" in names:
        return _num(row.get("homeRuns")) * 4.0 + _num(row.get("hits")) + _num(row.get("RBIs"))
    return _num(row.get("strikeouts")) + _num(row.get("innings")) * 0.5


def _moment_stats(sport_key: str, row: dict, names: list) -> dict:
    """Display box score for the moment card."""
    if sport_key == "nba":
        return {
            "PTS": int(_num(row.get("points"))),
            "REB": int(_num(row.get("totalRebounds"))),
            "AST": int(_num(row.get("assists"))),
        }
    if sport_key == "nfl":
        s: dict = {}
        if _num(row.get("passingYards")):
            s["PassYds"] = int(_num(row.get("passingYards")))
            s["PassTD"] = int(_num(row.get("passingTouchdowns")))
        if _num(row.get("rushingYards")):
            s["RushYds"] = int(_num(row.get("rushingYards")))
            s["RushTD"] = int(_num(row.get("rushingTouchdowns")))
        if _num(row.get("receivingYards")):
            s["Rec"] = int(_num(row.get("receptions")))
            s["RecYds"] = int(_num(row.get("receivingYards")))
            s["RecTD"] = int(_num(row.get("receivingTouchdowns")))
        return s
    if "atBats" in names:
        return {
            "HR": int(_num(row.get("homeRuns"))),
            "H": int(_num(row.get("hits"))),
            "RBI": int(_num(row.get("RBIs"))),
        }
    return {"K": int(_num(row.get("strikeouts"))), "IP": _num(row.get("innings"))}


def _game_line(meta: dict) -> str:
    """'vs MIN · 2025-05-01' from a gamelog `events[eventId]` metadata dict."""
    opp = (meta.get("opponent") or {}).get("abbreviation") or "?"
    atvs = meta.get("atVs") or "vs"
    date = (meta.get("gameDate") or "")[:10]
    return f"{atvs} {opp}" + (f" · {date}" if date else "")


def _best_moment(sport_key: str, gamelog: dict, player: dict) -> dict | None:
    """Pure: the player's single biggest regular-season game -> a moment subject dict,
    or None if they have no scoring game in the log."""
    names = gamelog.get("names") or []
    meta = gamelog.get("events") or {}
    best = None  # (score, eventId, row)
    for st in gamelog.get("seasonTypes") or []:
        if "Regular Season" not in (st.get("displayName") or ""):
            continue  # skip preseason / postseason
        for cat in st.get("categories") or []:
            for ev in cat.get("events") or []:
                row = dict(zip(names, ev.get("stats") or []))
                score = _game_score(sport_key, row, names)
                if best is None or score > best[0]:
                    best = (score, str(ev.get("eventId") or ""), row)
    if not best or best[0] <= 0:
        return None
    score, eid, row = best
    stats = _moment_stats(sport_key, row, names)
    stats["Game"] = _game_line(meta.get(eid) or {})
    return {
        "subject_key": f"{player['subject_key']}|moment",
        "name": player["name"],
        "team": player.get("team"),
        "card_type": "moment",
        "is_rookie": False,
        "stardom": round(score, 1),
        "stats": stats,
        "headshot_url": player.get("headshot_url"),
    }


async def fetch_moments(sport_key: str, year: int, players: list[dict]) -> list[dict]:
    """Top <=MOMENT_POOL biggest single games of the season, as moment subjects.

    Reuses the ESPN athlete ids embedded in each player's subject_key (`...-{id}`).
    Only fetches game logs for the top MOMENT_CANDIDATES players by career_fame.
    Never raises; returns [] on total failure or empty input.
    """
    if not players:
        return []
    sport, league = _LEAGUES[sport_key]
    cands = sorted(players, key=lambda p: p.get("career_fame") or 0.0, reverse=True)[:MOMENT_CANDIDATES]
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True
        ) as client:
            sem = asyncio.Semaphore(_CONCURRENCY)

            async def _one(p: dict):
                pid = p["subject_key"].rsplit("-", 1)[-1]
                async with sem:
                    gl = await _get_json(
                        client, f"{_WEB}/{sport}/{league}/athletes/{pid}/gamelog?season={year}"
                    )
                if not gl:
                    return None
                try:
                    return _best_moment(sport_key, gl, p)
                except Exception:
                    return None

            res = await asyncio.gather(*(_one(p) for p in cands), return_exceptions=True)
            out = [r for r in res if isinstance(r, dict)]
    except Exception:
        return out
    out.sort(key=lambda m: m["stardom"], reverse=True)
    return out[:MOMENT_POOL]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cards.py::test_best_moment_ranks_top_game_and_captures_line -v`
Expected: PASS

- [ ] **Step 5: (Optional, network) live smoke of `fetch_moments`**

Run: `uv run python -c "import asyncio,sys; sys.path.insert(0,'scripts'); import card_sources as cs; ps=asyncio.run(cs.fetch_nba_season(2025)); ms=asyncio.run(cs.fetch_moments('nba',2025,ps)); print(len(ms)); [print(m['name'], m['stardom'], m['stats']) for m in ms[:5]]"`
Expected: prints up to 10; top rows are star scoring nights (e.g. 40+ PTS) with a `Game` line. Skip if offline.

- [ ] **Step 6: Commit**

```bash
git add scripts/card_sources.py tests/fixtures/nba_gamelog_sample.json tests/test_cards.py
git commit -m "feat(cards): per-game Big Moment fetcher (uniform ESPN gamelog)"
```

---

### Task 5: Seed moments into each set (`seed_cards.py`)

**Files:**
- Modify: `scripts/seed_cards.py` — `_build_designs` (split player/moment pools, ~line 64) and `seed_one` (call `fetch_moments`, ~line 101)
- Test: `tests/test_cards.py`

**Interfaces:**
- Consumes: `engine.MOMENT_TIERS`, `engine.MOMENT_SHARE` (Task 1); `card_sources.fetch_moments` (Task 4); the `card_type` insert field (Task 3).
- Produces: seeded sets whose designs include ~40 moment cards (`card_type="moment"`) ranked by game score, moment legendaries at 1-of-1, player + moment manifests summing to `total_cards`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cards.py` (unit-level, no network — call `_build_designs` directly):

```python
def test_build_designs_splits_players_and_moments():
    import seed_cards  # scripts/ on sys.path (added in Task 4 step 1)
    players = [
        {"subject_key": f"nba|2026|p{i}-{i}", "name": f"P{i}", "team": "LAL",
         "career_fame": float(i), "is_rookie": False}
        for i in range(60)
    ]
    moments = [
        {"subject_key": f"nba|2026|p{i}-{i}|moment", "name": f"P{i}", "team": "LAL",
         "card_type": "moment", "is_rookie": False, "stardom": float(i),
         "stats": {"PTS": 40 + i, "Game": "vs BOS · 2026-01-14"},
         "headshot_url": None}
        for i in range(10)
    ]
    designs = seed_cards._build_designs(players + moments)
    total = seed_cards.TOTAL_PACKS * seed_cards.PACK_SIZE
    assert sum(d["total_copies"] for d in designs) == total  # combined run still totals exactly
    moment_designs = [d for d in designs if d["card_type"] == "moment"]
    assert len(moment_designs) == 10
    # moment legendaries are 1-of-1
    assert all(d["total_copies"] == 1 for d in moment_designs if d["rarity"] == "legendary")
    # moment cards are rare-or-better only (never common/uncommon)
    assert all(d["rarity"] in ("rare", "epic", "legendary") for d in moment_designs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cards.py::test_build_designs_splits_players_and_moments -v`
Expected: FAIL — either `KeyError`/wrong counts, or moments get common/uncommon rarities (current `_build_designs` builds one PLAYER_TIERS manifest over everything).

- [ ] **Step 3: Write minimal implementation**

Replace `_build_designs` in `scripts/seed_cards.py` (the whole function, ~lines 64–98) with the split version:

```python
def _build_designs(subjects: list[dict]) -> list[dict]:
    """subjects (players + moments) -> manifest designs with rarity/copies/book_value.

    Players and moments are ranked in SEPARATE manifests: players by career-fame
    stardom (PLAYER_TIERS), moments by game-score stardom (MOMENT_TIERS, all-rare+).
    Moments take MOMENT_SHARE of the print run. Legendaries collapse to 1-of-1;
    freed copies flow to player commons so the run still totals total_cards.
    """
    players = [s for s in subjects if s.get("card_type") != "moment"]
    moments = [s for s in subjects if s.get("card_type") == "moment"]
    for s in players:
        fame = s.get("career_fame") or 0.0
        s["stardom"] = fame * (engine.ROOKIE_MULT if s.get("is_rookie") else 1.0)
    # moments already carry stardom (= game score) from the fetcher

    total_cards = TOTAL_PACKS * PACK_SIZE
    moment_cards = round(total_cards * engine.MOMENT_SHARE) if moments else 0
    manifest = engine.build_manifest(players, total_cards - moment_cards)
    if moments:
        manifest += engine.build_manifest(moments, moment_cards, tiers=engine.MOMENT_TIERS)

    # Legendaries are 1-of-1 grails; freed copies flow to commons (player commons exist
    # in the combined manifest) so the run still totals total_cards.
    commons = [d for d in manifest if d["rarity"] == "common"] or manifest
    freed = 0
    for d in manifest:
        if d["rarity"] == "legendary" and d["copies"] > 1:
            freed += d["copies"] - 1
            d["copies"] = 1
    i = 0
    while freed > 0 and commons:
        commons[i % len(commons)]["copies"] += 1
        freed -= 1
        i += 1

    out = []
    for d in manifest:
        out.append({
            "subject_key": d["subject_key"],
            "subject_name": d["name"],
            "team": d.get("team"),
            "rarity": d["rarity"],
            "is_rookie": d.get("is_rookie", False),
            "career_fame": d.get("career_fame"),
            "total_copies": d["copies"],
            "stats": d.get("stats") or {},
            "headshot_url": d.get("headshot_url"),
            "book_value": d["book_value"],
            "card_type": d.get("card_type", "player"),
        })
    return out
```

Then wire the fetcher into `seed_one`. After the `uniq` player list is built and passes the `MIN_PLAYERS` check (~line 119, right before `designs = _build_designs(uniq)`), insert:

```python
    try:
        moments = await card_sources.fetch_moments(sport, season, uniq)
    except Exception:
        log.exception("  moment fetch failed for %s %s", sport, season)
        moments = []
    if moments:
        log.info("  %s %s: +%d Big Moment cards", sport.upper(), season, len(moments))
    designs = _build_designs(uniq + moments)
```

(Replace the existing bare `designs = _build_designs(uniq)` line with the block above.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cards.py::test_build_designs_splits_players_and_moments -v`
Expected: PASS

- [ ] **Step 5: Run the full card test file**

Run: `uv run pytest tests/test_cards.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add scripts/seed_cards.py tests/test_cards.py
git commit -m "feat(cards): seed Big Moment cards per set (player/moment manifest split)"
```

---

### Task 6: Discord display — moment tag + game line (`bot/cogs/cards.py`)

**Files:**
- Modify: `bot/cogs/cards.py` — `_card_line` (~line 80), `_badges` (~line 97), `_card_embed` (~line 132)
- Test: `tests/test_cards.py`

**Interfaces:**
- Consumes: `card_type` + `stats["Game"]` on card dicts (Tasks 3–5). Card dicts from `mint_pack` / `get_collection` now carry `card_type`; moment `stats` carry a `"Game"` key.
- Produces: cosmetic only — a `🔥 BIG MOMENT` tag + the game line. No behavior change. (Notable-pull alerts already fire on epic/legendary, so moment legendaries shout out for free — no change needed there.)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cards.py`:

```python
def test_card_line_marks_moment():
    from bot.cogs.cards import _card_line
    moment = {"rarity": "legendary", "name": "LeBron James", "card_type": "moment",
              "serial": 1, "total_copies": 1, "book_value": 260,
              "stats": {"PTS": 61, "Game": "vs BOS · 2026-01-14"}}
    line = _card_line(moment)
    assert "BIG MOMENT" in line or "🔥" in line
    player = {"rarity": "common", "name": "Bench Guy", "serial": 5, "total_copies": 900,
              "book_value": 3.5, "stats": {}}
    assert "BIG MOMENT" not in _card_line(player)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cards.py::test_card_line_marks_moment -v`
Expected: FAIL — `assert 'BIG MOMENT' in line` (no moment tag yet)

- [ ] **Step 3: Write minimal implementation**

In `bot/cogs/cards.py`, in `_card_line`, add a `🔥` prefix bit for moments. Change the `bits = [...]` line:

```python
    bits = [RARITY_EMOJI.get(c["rarity"], "")]
    if c.get("card_type") == "moment":
        bits.append("🔥")
```

In `_badges`, prepend a moment badge:

```python
def _badges(c: dict) -> str:
    bits = []
    if c.get("card_type") == "moment":
        bits.append("🔥 BIG MOMENT")
    if c.get("is_rookie"):
        bits.append("🌟 RC")
```

In `_card_embed`, surface the game line for moments. After the existing `emb.add_field(name="Serial", ...)` block (before the `if c.get("headshot_url"):` line), add:

```python
        if c.get("card_type") == "moment" and c.get("stats", {}).get("Game"):
            emb.add_field(name="🔥 Big Moment", value=c["stats"]["Game"], inline=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cards.py::test_card_line_marks_moment -v`
Expected: PASS

- [ ] **Step 5: Import-smoke the cog (no syntax/interaction breakage)**

Run: `uv run python -c "import bot.cogs.cards"`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add bot/cogs/cards.py tests/test_cards.py
git commit -m "feat(cards): Discord moment tag + game line on reveal/collection"
```

---

### Task 7: Web display — moment badge + game line (`web/static/cards.js` + `cards.css`)

**Files:**
- Modify: `web/static/cards.js` — `cardTile` (~line 55)
- Modify: `web/static/cards.css` — add a `.moment-badge` + `.cgame` style
- Test: manual browser check (no JS test harness in this repo — the JS `__main__`-style demo data at the bottom of `cards.js` is the existing pattern)

**Interfaces:**
- Consumes: `card_type` + `stats.Game` on card JSON from the catalog/collection/open API (Tasks 3–5).
- Produces: cosmetic — moment tiles get a `🔥 BIG MOMENT` badge and the game line under the name; reuses the player headshot (alt-art = badge + accent, not a new image).

- [ ] **Step 1: Implement the tile change**

In `web/static/cards.js`, in `cardTile(c)`, after the `const rookie = ...` line add:

```javascript
  const moment = c.card_type === "moment"
    ? `<span class="moment-badge">🔥 BIG MOMENT</span>` : "";
  const game = (c.card_type === "moment" && c.stats && c.stats.Game)
    ? `<div class="cgame">${esc(c.stats.Game)}</div>` : "";
```

Add `moment` into the image overlay (next to `gem`/`rookie`) and add the `moment` class + `game` line to the body. Change the returned template: put `${moment}` alongside `${gem}${rookie}` inside `.cimg`, add `moment` to the tile class, and insert `${game}` under `.cname`:

```javascript
  return `<div class="ctile rarity-${rarity}${holo}${moment ? " moment" : ""}">
    <div class="cimg">
      <img src="${esc(src)}" alt="${esc(c.name)}" loading="lazy"
           onerror="this.onerror=null;this.src=window.__cardSilh;this.classList.add('silh');">
      <span class="sport-badge" title="${esc(sport.toUpperCase())}">${emoji}</span>
      ${gem}${rookie}${moment}
    </div>
    <div class="cbody">
      <div class="cname">${esc(c.name)}</div>
      ${game}
      <div class="cteam">${esc(c.team || "")}</div>
      <div class="cmeta">
        <span class="rarity-label">${esc(rarity)}</span>
        ${serial}
      </div>
      <div class="cvalue">${coins(c.book_value)}</div>
    </div>
  </div>`;
```

- [ ] **Step 2: Add the CSS**

In `web/static/cards.css`, append (match the existing dark-theme accent vars — mirror how `.rookie-badge` is styled, which is already in this file):

```css
/* Big Moment cards */
.moment-badge {
  position: absolute; left: 6px; bottom: 6px;
  background: linear-gradient(90deg, #f7768e, #e0af68);
  color: #1a1b26; font-weight: 700; font-size: 10px;
  padding: 2px 6px; border-radius: 6px; letter-spacing: .3px;
}
.cgame { font-size: 11px; color: #e0af68; margin: 2px 0; }
.ctile.moment { outline: 1px solid rgba(224,175,104,.6); }
```

- [ ] **Step 3: Verify in the browser**

Run the web server and open the cards page with the built-in demo data, or seed one real set locally. Confirm a moment tile shows the `🔥 BIG MOMENT` badge, the game line, and keeps the player headshot.

```bash
just web   # serves on :8000
```

Then load `http://localhost:8000/static/cards.html` (or the app's cards route). Optionally extend one entry in the demo-data block at the bottom of `cards.js` with `card_type: "moment", stats: { PTS: 61, Game: "vs BOS · 2026-01-14" }` to preview without seeding. Take a screenshot for the record (per the "always screenshot HQ/web changes" convention).

- [ ] **Step 4: Commit**

```bash
git add web/static/cards.js web/static/cards.css
git commit -m "feat(cards): web moment badge + game line on card tiles"
```

---

## Final verification

- [ ] **Run the whole card test suite**

Run: `uv run pytest tests/test_cards.py -v`
Expected: PASS (all — engine, DB, fetcher, seeder, Discord).

- [ ] **Import-smoke the changed modules**

Run: `uv run python -c "import bot.cogs.cards; import scripts.seed_cards" 2>/dev/null || uv run python -c "import sys; sys.path.insert(0,'scripts'); import bot.cogs.cards, card_sources, seed_cards"`
Expected: exit 0.

- [ ] **(Network, on the VPS at deploy time) seed one set and eyeball the moments**

Run: `venv/bin/python scripts/seed_cards.py nba 2025`
Expected: log line `+N Big Moment cards` and a normal `✅ NBA 2025` summary. Then `/cards catalog nba 2025` in Discord shows moment cards with the 🔥 tag.

---

## Notes for the executor

- **Data-source deviation is intentional** — see Global Constants. Do not re-introduce BallDontLie / MLB StatsAPI; the uniform ESPN gamelog is the whole point of the simplification.
- **Deploy is manual + moments need a re-seed.** Existing seeded sets won't have moments until re-seeded; seeding is idempotent per `(sport, season)` (skips existing sets), so to add moments to an already-seeded set you must delete + re-seed it, or seed a not-yet-seeded season. Flag this to David before touching production data — decide whether to wipe & re-seed or only add moments to new seasons.
- **Achievements:** per the standing convention (`feedback_achievements_for_features`), consider adding a "pull a Big Moment" achievement in a follow-up — out of scope for this plan unless requested.
