# Sports Card Packs — Design

**Date:** 2026-08-11
**Status:** Approved design, pending implementation plan
**Origin:** Port of the `nsba-markets` card/pack collection system, re-themed from
Science Bowl players to NBA / NFL / MLB players.

## 1. Summary

A collectible-card feature for the SharpLab Discord casino. Users spend casino
coins to open **season-based packs** of real NBA/NFL/MLB players, collect
serial-numbered cards across rarity tiers, chase **rookie cards** and holo/gem
parallels, quick-sell dupes back for coins, and trade card-for-card. A web page
renders the collection as a headshot grid.

The economics engine ports almost verbatim from `nsba-markets/backend/app/cards.py`
(pure, DB-free, unit-tested). The rest is re-homed onto SharpLab's stack (raw
aiosqlite, discord.py cogs, FastAPI + static web, casino-coin economy).

## 2. Locked decisions

| Decision | Choice |
|---|---|
| Sports at launch | NBA, NFL, MLB |
| Player/stardom data | Free-API seed scripts (balldontlie / MLB StatsAPI / ESPN) |
| Interface | Discord slash commands (buy/open/collect/trade) + web collection page (read-only browse) |
| Mechanics | Rarities + serials + collection, holo + gems, notable-pull alerts, trading + wishlist |
| Card value | Quick-sell for coins at 75% of book (net coin sink) |
| Set structure | One `card_set` per (sport, season); ~15 recent seasons per sport (~45 sets) |
| Card art | ESPN CDN headshots by athlete id |
| Currency | Existing casino coins (`casino_wallets` via `update_casino_balance`) |

## 3. Rarity model (the core mechanic)

Every card in a pack is a player from that pack's season. Rarity is assigned by
**rank on a career-fame score**, not that single season's stats — so a rookie who
*became* a legend is a legendary pull even though his rookie numbers were modest
(the whole appeal of chasing a "LeBron '03 rookie").

```
stardom(player, season) = career_fame(player) × (ROOKIE_MULT if rookie_that_year else 1.0)
```

- `career_fame` — a per-sport 0–100 score from **career** stats (stars high,
  journeymen low). Computed in each sport's seeder. Tunable; only requirement is a
  sensible ranking.
- `ROOKIE_MULT` ≈ 1.5 — boosts a player's rookie-year card above his later-season
  cards so it ranks rarer and short-prints. Multiplicative version of nsba's
  `ROOKIE_BONUS`.
- Ranked players are bucketed into tiers by `cards.build_manifest`
  (top ~1% legendary, ~3% epic, ~10% rare, ~24% uncommon, ~62% common), rarer
  tiers get fewer copies, **legendaries are 1-of-1**.

Net effect: the 2003 NBA set's chase is the legendary LeBron/Wade/Melo **rookies**;
the 1996 set has peak MJ as a legendary vet plus the Kobe/Iverson/Nash rookie
class. Every season has its own legends + rookie class.

### career_fame per sport (tunable knobs, live in each seeder)

- **NBA** (balldontlie): career PPG weighted by games played, normalized 0–100.
  Rookie = draft-year / first season on record.
- **MLB** (MLB StatsAPI): composite of career totals — WAR if exposed, else a
  hitter/pitcher blend (career HR + hits for hitters, career K + wins for
  pitchers) normalized to a common 0–100 scale. Rookie = season of `mlbDebutDate`.
- **NFL** (ESPN): position-weighted career yards/TDs (or career fantasy points).
  Rookie = draft/debut year.

## 4. Season packs & vintage pricing

One `card_set` per (sport, season). Older packs cost more — you pay a vintage
premium for the chance at a specific now-legend's rookie:

```
pack_cost(season) = round(BASE × (1 + AGE_STEP) ** years_ago)
# BASE ≈ 50 coins, AGE_STEP ≈ 0.08  →  now ~50, 10y ~108, 20y ~235
```

Stored as `card_sets.base_cost` at seed time (so pricing is fixed per set, not
recomputed). The vintage premium makes old packs mildly -EV in pure book terms —
realistic and a healthy coin sink. All constants tunable.

## 5. Port map (nsba → SharpLab)

| nsba piece | SharpLab home | Notes |
|---|---|---|
| `backend/app/cards.py` | `shared/cards.py` | Engine ports ~verbatim; stays pure + unit-tested. |
| SQLAlchemy models | new tables in `db/schema.py` | Raw aiosqlite — hand-write DDL as idempotent migrations in `init_db()`. |
| `routers/cards.py` | `bot/cogs/cards.py` (write path) + `web/cards.py` (read path) | Buying in Discord; web browses. All DB access via `db/queries.py`. |
| React frontend | `web/static/cards.html` + `.js`/`.css` | Collection grid, set browser, catalog+odds. Vanilla, dark theme. |
| `seed_cards.py` | `scripts/seed_cards.py` | One run per (sport, season); loops the ~15 seasons. |

## 6. Data model (new tables, all via `db/queries.py`)

- **`card_sets`**: `set_id` PK, `sport`, `season`, `name`, `total_packs`,
  `packs_opened`, `base_cost`, `closed`, `created_at`.
- **`card_designs`**: `design_id` PK, `set_id` FK, `subject_key` (stable, e.g.
  `nba|2003|lebron-james`), `subject_name`, `team`, `rarity`, `is_rookie`,
  `career_fame`, `total_copies`, `minted`, `stats` (JSON), `headshot_url`,
  `book_value`.
- **`card_instances`**: `instance_id` PK, `design_id` FK, `owner_id`, `serial`,
  `is_holo`, `gem`, `book_value`, `acquired_cost`, `source` (pack/daily/trade),
  `acquired_at`.
- **`card_wants`**: (`user`, `design_id`) wishlist.
- **`card_pack_claims`**: (`user`, `set_id`, `day`) — daily free pack ledger.
- **`card_trades`**: `trade_id` PK, `from_user`, `to_user`, `offer_instance_ids`
  (JSON list), `want_instance_ids` (JSON list), `status`
  (pending/accepted/declined/cancelled), `created_at`.

Minting is server-authoritative & transactional (nsba's `_open_and_mint`): draw
from the finite pool (`total_copies − minted`), decrement, assign next `serial`,
roll holo/gem, debit coins. WAL mode is already on (pipeline + bot both write).

## 7. Discord commands (`bot/cogs/cards.py`)

- `/pack open <sport> <season> [n]` — buy & open n packs (coins, floored at
  balance, no margin). Animated embed reveal; notable pulls flagged.
- `/pack daily` — one free pack/day (which set is configurable; default newest).
- `/collection [@user]` — paged collection view + total book value.
- `/cards sets` — browse sets with live prices and sold/total packs.
- `/cards catalog <sport> <season>` — checklist + pull-rate odds (from the engine
  constants + real per-rarity share).
- `/cards lookup <player>` — a design's owners + serials.
- `/cards sell <instance_id>` — quick-sell one card for 75% book to coins.
- `/cards wishlist add|remove <design>` — wishlist; DM when someone pulls it.
- `/cardtrade offer|accept|decline|cancel` — card-for-card trades.

Commands live in nested groups to respect Discord's 100-command cap (sport/season
are arguments here, not sub-groups, since the matrix is large).

## 8. Web (`web/cards.py` + `web/static/cards.*`)

Read-only, Discord-OAuth session (existing `web/auth.py`). Pages: **my
collection** (headshot grid, rarity styling, holo shimmer, book totals), **set
browser** (prices, progress), **catalog/odds** per set, **design drill-in**
(owners). Mirrors nsba's endpoints (`/mine`, `/set`, `/catalog`, `/design/{id}`)
re-implemented against the SharpLab tables. HQ-style `AsyncTTLCache` where useful.

## 9. Economy & engagement

- Packs debit casino coins via `update_casino_balance`; balance floored (no
  margin), consistent with the stock-buy floor-at-0 rule.
- Quick-sell credits 75% of `book_value`.
- **Notable pulls** (epic/legendary, holo-rare, gem ≥ sapphire, or a 1-of-1) →
  shout-out embed to a configurable channel, following the `signals` /
  `sportsnews` alert-channel pattern (channel id in `bot_settings`).
- **Achievements** (per the project rule for big features): first pack, first
  legendary, first 1-of-1, first holo, complete a set, own a card from N
  different sets. Add to `progression.py` + run the achievement backfill.

## 10. Non-goals (v1)

- No coin/USD auction house or price bands (nsba's market layer) — trades are
  card-for-card only; coins enter/leave via pack buys and quick-sell.
- No "moment"/alt-art cards.
- No web-side buying (opening stays in Discord).
- No cross-era stardom normalization beyond per-sport 0–100 scaling (15-year
  windows keep eras comparable enough).

## 11. Testing

- `tests/test_cards.py` — port nsba's engine tests: manifest sums to
  `total_cards`, copies monotonic by rarity, legendaries 1-of-1,
  `open_pack` never over-draws a finite pool, `expected_pack_value` sane,
  `is_notable_pull` thresholds, vintage `pack_cost` monotonic in age.
- One seed smoke test per sport against a small fixture (no live API in CI):
  `career_fame` ranks a known star above a scrub; rookie flag set for a known
  rookie season.
- Query-layer tests: mint decrements pool + assigns sequential serials; quick-sell
  credits 75% and removes the instance; trade transfers ownership atomically.

## 12. Tunable constants (single source per concern)

`shared/cards.py`: `RARITIES`, `BOOK`, `HOLO_RATE`/`HOLO_MULT`, `GEMS`,
`COPIES_REL`, `PLAYER_TIERS`. Packs: `BASE`, `AGE_STEP`, `ROOKIE_MULT`,
`PACK_SIZE`, `TOTAL_PACKS` per set, quick-sell fraction. Seasons-to-seed list per
sport in `scripts/seed_cards.py`.
