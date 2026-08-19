# Big Moment Cards — Design

**Date:** 2026-08-19
**Status:** Approved design, pending implementation plan
**Extends:** [`2026-08-11-sports-card-packs-design.md`](2026-08-11-sports-card-packs-design.md)
(lifts that spec's §10 non-goal "No 'moment'/alt-art cards")
**Origin:** Port of the `nsba-markets` moment mechanic
(`backend/scripts/seed_cards.py::_player_moments` + `MOMENT_TIERS`) onto SharpLab's
already-shipped sports-card system.

## 1. Summary

Add **"Big Moment"** cards to the sports-card packs. Each existing `(sport, season)`
pack gains that season's **biggest single-game performances** as premium alt-art
cards of the same player+season. Moments are a short-printed, all-rare-or-better
slice: the very biggest games go **legendary** (1-of-1), then epic, then rare. This
is a direct port of the proven nsba-markets mechanic, re-sourced from real per-game
box scores.

The card engine (`shared/cards.py`) needs **no change** — `build_manifest` already
accepts a custom `tiers` table. The real new work is three per-game-log fetchers.

## 2. Locked decisions

| Decision | Choice |
|---|---|
| Sports | NBA, NFL, MLB (all three at launch) |
| Scope | **Per season set** — each `(sport, season)` pack gets THAT season's biggest single games. Mirrors nsba exactly. |
| "10 biggest" | Top ~10 single-game performances of the season, ranked by a per-sport game score (`MOMENT_POOL = 10`). |
| Tiers | `MOMENT_TIERS = [("legendary",0.3),("epic",0.3),("rare",0.4)]` → ~3 legendary / 3 epic / 4 rare on a 10-pool. Fraction-based, degrades gracefully on thin seasons. |
| Print share | `MOMENT_SHARE ≈ 0.016` of the set's print run (short-print; a moment is a rare pull). |
| Legendaries | 1-of-1, freed copies flow to commons (existing `_build_designs` logic). |
| Data source | Per-game logs: NBA BallDontLie `/stats`; MLB StatsAPI game logs; NFL ESPN athlete gamelog. |
| Card art | Reuse the player's ESPN headshot; a "BIG MOMENT" badge + game line distinguishes it. |

## 3. Data source — per-game fetchers (`scripts/card_sources.py`)

The one genuinely new piece. Existing fetchers return season/career aggregates; moments
need **single-game** lines. Add one coroutine per sport returning moment "subject" dicts:

```
{
  "subject_key": "nba|2026|lebron-james-1966|moment",   # distinct from the player card key
  "name": "LeBron James",
  "team": "Los Angeles Lakers",
  "card_type": "moment",
  "is_rookie": False,
  "stardom": 61.0,                      # the game score — ranks moments WITHIN the moment pool
  "stats": {"PTS": 48, "REB": 11, "AST": 9, "Game": "vs BOS · 2026-01-14"},
  "headshot_url": ".../1966.png",
}
```

Per sport:
- **NBA** — BallDontLie `/stats?seasons={yr}&player_ids=...` (key already in `.env`).
  Game score = points (optionally `pts + 0.4·reb + 0.7·ast` composite; points is the
  lazy default). Take each player's max-game, keep the top `MOMENT_POOL`.
- **MLB** — MLB StatsAPI game logs (`/people/{id}/stats?stats=gameLog&season={yr}`).
  Hitter game score (HR·4 + H + RBI) or pitcher (K + IP·0.5, capped). Per-player best game.
- **NFL** — ESPN athlete gamelog (`.../athletes/{id}/gamelog?season={yr}`). Fantasy-style
  single-game score (same weights as the season `career_fame` formula, per game).

Ponytail notes:
- Reuse each sport's existing season fetch to get the roster + athlete ids — don't
  re-discover players. Game logs are **one extra call per player**, so this is the heavy
  part; only run it for the season being seeded, and stop at the top-10 pool.
- `# ponytail: game-score is a simple per-sport weighted sum; swap for a fancier
  metric only if the ranking looks wrong.`

## 4. Engine (`shared/cards.py`) — one constant, no logic change

`build_manifest(subjects, total_cards, tiers=...)` already exists. Add:

```python
MOMENT_TIERS = [("legendary", 0.3), ("epic", 0.3), ("rare", 0.4)]
MOMENT_SHARE = 0.016
MOMENT_POOL = 10
```

## 5. Seeding (`scripts/seed_cards.py`)

Mirror nsba's `_manifest` split inside `_build_designs` (or a sibling):
1. `subjects` now includes both player and moment dicts (moments appended by the new fetcher).
2. `players = [s for s in subjects if s.get("card_type") != "moment"]`;
   `moments = [... == "moment"]`.
3. `moment_cards = round(total_cards * engine.MOMENT_SHARE) if moments else 0`.
4. `manifest = build_manifest(players, total_cards - moment_cards)`
   `+ build_manifest(moments, moment_cards, tiers=engine.MOMENT_TIERS)`.
5. Collapse legendaries to 1-of-1 (existing loop, now covers moment legendaries too).
6. Carry `card_type` through into the insert dict.

`seed_one` calls the new moment fetcher for the sport/season and appends its subjects
before `_build_designs`. A season with no game data yet (e.g. NFL current-year offseason)
simply yields zero moments — never aborts.

## 6. Data model — one column

`card_designs` gains `card_type TEXT NOT NULL DEFAULT 'player'`, added as an idempotent
`ALTER TABLE ... ADD COLUMN` in `db/schema.py::init_db` (SQLite ignores the add if the
column exists — guard by checking `PRAGMA table_info`). `insert_card_designs` writes it;
read queries return it. The moment's box score lives in the existing `stats` JSON. Nothing
else changes — `card_instances`, minting, quick-sell, and trades are all type-agnostic.

## 7. Display (cosmetic)

- **Discord** (`bot/cogs/cards.py`): when `card_type == 'moment'`, the reveal/collection
  embed shows the game line (`stats["Game"]`) and a "🔥 BIG MOMENT" tag. Notable-pull
  alerts already fire on epic/legendary, so moment legendaries shout out for free.
- **Web** (`web/static/cards.*`): moment tiles get a badge + the game line under the name.
  Reuses the player headshot; alt-art = the badge + accent, not a new image.

## 8. Testing (`tests/test_cards.py` + fetcher fixtures)

- Engine: `MOMENT_TIERS` on a 10-subject pool yields ~3 legendary / 3 epic / 4 rare and
  the manifest sums to `moment_cards`; combined player+moment manifest sums to `total_cards`;
  moment legendaries collapse to 1-of-1.
- One fixture-based test per sport fetcher (saved JSON sample, no live API): the biggest
  single game ranks first, the box score is captured in `stats`, `card_type == "moment"`.
- Query layer: a moment design round-trips `card_type` through insert + read.

## 9. Non-goals (v1)

- No new set type — moments ride the existing per-season sets.
- No "greatest game ever" across seasons (the ESPN/BallDontLie roster is constrained to
  currently-active players, so all-time isn't reachable anyway).
- No new card art pipeline — headshot + badge only.
- No moment-specific pack or drop-rate UI beyond the shared catalog/odds view.
