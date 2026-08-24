# Premium & Vintage Packs — Design

**Date:** 2026-08-24
**Status:** Approved (design) — pending spec review
**Goal:** Give whales (2M+ coins) high-value coin sinks by extending the existing
card system with (a) a universal **box** product, (b) ultra-premium **sports
draft-class** sets, and (c) **Pokémon** sets including a **151 "1st Edition"**
grail box.

> The **Tamagotchi pet** is a separate subsystem and gets its own spec
> (`2026-08-2X-tamagotchi-pet-design.md`). Not covered here.

---

## 1. Context

The card system already exists and is rich (`shared/cards.py`, `bot/cogs/cards.py`,
`db/queries.py`, `scripts/seed_cards.py`):

- Sets keyed `(sport, season)` with a fixed `base_cost` (coins/pack) and a finite
  print run (`total_packs`).
- Rarity tiers `common → legendary`; holo (20%) and gem parallels
  (chrome/sapphire/ruby/black_lotus) via `roll_gem`; legendaries collapsed to 1-of-1.
- `mint_pack` (atomic, one pack), `/pack open|daily|fast|list`, marketplace,
  sell, trade-up, wants — all inherited unchanged.

Everything below **reuses** that engine. The only new *mechanic* is the box; the
rest is new **seeders** that feed curated/real subjects into the existing pipeline.

---

## 2. Universal box (`/pack box`)

A **box = 36 packs** opened at once. Available on **every** set (not gated).

- **Price:** `36 × base_cost`. No separate `box_cost` column, no schema change.
- **Incentive to buy a box** (vs 36 singles): one-click open + a **guaranteed hit**
  and a clean **summary reveal**. No coin discount (goal is to *drain* coins).
- **Guaranteed hit:** after opening 36 packs (= `36 × pack_size` cards), if none is
  **epic-or-better**, upgrade the single highest-book card to a freshly-rolled
  **epic** (re-roll its design from the set's epic pool, keep its serial/holo/gem
  roll). Statistically this rarely fires on big sets; it matters most for the tiny
  premium sets, which is the point.
- **Reveal:** a **summary embed**, NOT 180 individual card embeds — rarity
  breakdown counts + a "notable pulls" highlight list (reuse
  `engine.is_notable_pull`) + the guaranteed-hit callout. The full 180 cards land
  in the user's collection as normal; they can browse them via existing commands.

### New query: `mint_box(user, set_id, now_iso) -> {cards, notable, guaranteed}`
Mirror `mint_pack` but in one `BEGIN IMMEDIATE` transaction:
1. Refuse if set closed or `packs_opened + 36 > total_packs` ("not enough packs
   left in this box's print run").
2. Charge `36 × base_cost` (same wallet debit pattern as `mint_pack`; real sink,
   no faucet floor).
3. Draw `36 × pack_size` cards from the finite pool (loop the existing
   `engine.open_pack` draw), mint serial-numbered instances.
4. Apply the guaranteed-hit upgrade if no epic+ present.
5. `packs_opened += 36` (close set if it hits `total_packs`).
Returns the minted cards + a precomputed notable list for the summary embed.

### New command: `pack box <sport> <season>` in `bot/cogs/cards.py`
Same group as `pack open`. Confirmation step for large spends (e.g. a modal or a
"confirm" button) since a 1984 box is 250k coins — guard against fat-fingering.

---

## 3. Sports draft-class sets (curated seeder)

The existing `seed_cards.py` pulls **active** ESPN rosters, so it cannot produce
retired players. Draft classes therefore come from **hardcoded curated rosters**.

New script `scripts/seed_draft_classes.py`:
- Curated player lists (~18–22 each), all `is_rookie=True` (rookie cards are the
  appeal). Each player gets a relative `career_fame` that drives stardom→rarity
  ordering (grails rank top).
- Builds designs via `engine.build_manifest(subjects, total_cards, DRAFT_TIERS)`
  then the existing legendary→1-of-1 collapse.
- Inserts via `create_card_set(name, base_cost, total_packs, ...)` +
  `insert_card_designs`. Idempotent (skip if `(sport, season)` exists), run on the
  VPS like `seed_cards.py`.

### Premium skew — new `DRAFT_TIERS` in `shared/cards.py`
Loaded elite sets deserve more chase. Proposed (tune during impl):
```
DRAFT_TIERS = [("legendary", 0.08), ("epic", 0.15), ("rare", 0.25),
               ("uncommon", 0.30), ("common", 0.22)]
```
For a ~20-player set: ~2 legendaries (→ 1-of-1 grails), ~3 epics. Default
`PLAYER_TIERS` and all normal sets are untouched.

### Sets & pricing
Print runs are whole boxes. `base_cost = round(box_price / 36)`.

| Set (key)            | Name                         | Box price | base_cost/pack | Print run |
|----------------------|------------------------------|-----------|----------------|-----------|
| `(nba, 2003)`        | 2003 NBA Draft Class         | 50,000    | 1,389          | 20 boxes (720) |
| `(nba, 1979)`        | 1979 Rookies — Magic & Bird  | 150,000   | 4,167          | 12 boxes (432) |
| `(nba, 1984)`        | 1984 NBA Draft Class         | 250,000   | 6,944          | 6 boxes (216)  |

Curated rosters (fame rank drives rarity; exact scores finalized in impl):

- **2003:** LeBron James, Dwyane Wade, Carmelo Anthony, Chris Bosh *(top)*; David
  West, Josh Howard, Kyle Korver, Boris Diaw, Mo Williams, Leandro Barbosa,
  Kendrick Perkins, Kirk Hinrich, Nick Collison, Zaza Pachulia, T.J. Ford, Steve
  Blake, Luke Walton, Darko Milicic *(famous bust)*, Willie Green, Travis Outlaw.
- **1984:** Michael Jordan, Hakeem Olajuwon, Charles Barkley, John Stockton *(top)*;
  Sam Perkins, Otis Thorpe, Kevin Willis, Alvin Robertson, Jerome Kersey, Sam
  Bowie *(famous bust)*, Vern Fleming, Michael Cage, Jay Humphries, Tony Campbell.
- **1979 (Magic & Bird class):** Magic Johnson, Larry Bird *(top)*; Sidney
  Moncrief, Bill Cartwright, Vinnie Johnson, Jim Paxson, Calvin Natt, plus role
  players. **Note:** Bird was drafted 1978 but debuted 1979–80; the set is framed
  as the **1979–80 rookie class**, so his inclusion is intentional (documented in
  the seeder).

No card art in v1 (`headshot_url` null); reveals show text/emoji as today.

---

## 4. Pokémon sets (real-data seeder)

Source: `~/code/pokemon-cards/data.json` — 15 real Scarlet & Violet-era sets with
card **names, rarities (8 tiers), real USD market prices, image paths**. Pokémon
cards have an **intrinsic rarity and price**, so we do **not** re-rank by "fame";
we map rarity directly and price from real data.

### Vendoring
Copy a **trimmed** snapshot into the repo so the seeder is self-contained on the
VPS: `data/pokemon_cards.json` = per set `{id, name, cards:[{name, rarity,
price_usd, img}]}` (drop `owned`, `variants`, `prices` breakdown — keep max price).
~100KB. Refreshable by re-running a small extract from the source project.

### Rarity map (Pokémon 8 → engine 5)
```
Common                     -> common
Uncommon                   -> uncommon
Rare, Double rare          -> rare
Illustration rare, Ultra Rare, ACE SPEC Rare -> epic
Special illustration rare, Hyper rare, Mega Hyper Rare, Black White Rare -> legendary
```

### Value model
`book_value = max(tier_floor, round(price_usd × COIN_PER_USD))`, `COIN_PER_USD = 100`.
(151 Charizard ex SIR $397 → ~39,700 coins; commons ~$0.20 → floor. Median card
$0.92, mean $10.55.) Copies-per-design still come from `COPIES_REL` by mapped tier;
legendaries collapse to 1-of-1 as usual. `stardom = price_usd` only to order
within a tier for display.

> This is a **parallel builder** (`_build_pokemon_designs`) — it bypasses
> `build_manifest`'s fame-ranking because the rarity is already known. It still
> emits the same design dict shape `insert_card_designs` expects.

### Sets to seed (v1)
Start with the flagship + a few popular sets, extensible later:
`151` (sv03.5), `Prismatic Evolutions` (sv08.5), `Surging Sparks` (sv08). `season`
is an **integer** column, so each set id maps to a distinct synthetic integer year:
151→2023, Surging Sparks→2024, Prismatic Evolutions→2025, 151 1st Edition→1999.
`sport = "pokemon"` (new sport value; verify SPORT_CHOICES / any sport enum in the
cog accepts it, add if needed).

### 151 "1st Edition" premium box
A second, separate **premium** set of the same 151 cards: `(pokemon, 1999)` named
**"Pokémon 151 — 1st Edition"** (1999 nods to the original Base Set 1st Edition;
the *cards* are the modern 151 reprints since no vintage Base Set data exists).
- **Premium skew** (`DRAFT_TIERS` or a Pokémon-specific tier boost) + higher
  gem/holo weighting is out of scope for the roll engine (gems are global); the
  premium comes from **richer legendary/epic share** + **higher price**.
- **Box price:** **300,000** (base_cost 8,333/pack) — the priciest box in the game,
  the ultimate burn. Small print run: **5 boxes (180 packs)**.
- Standard `151` set stays at a normal price (box ~15k) so non-whales can play.

| Set (key)          | Name                        | Box price | base_cost | Print run |
|--------------------|-----------------------------|-----------|-----------|-----------|
| `(pokemon, 2023)`  | Pokémon 151                 | ~15,000   | ~417      | 30 boxes  |
| `(pokemon, 1999)`  | Pokémon 151 — 1st Edition   | 300,000   | 8,333     | 5 boxes   |
| `(pokemon, 2025)`  | Prismatic Evolutions        | ~20,000   | ~556      | 25 boxes  |
| `(pokemon, 2024)`  | Surging Sparks              | ~15,000   | ~417      | 25 boxes  |

(Exact standard-set prices calibrated so pack RTP < 1 — a sink — using
`engine.expected_pack_value`. Premium box prices are set by fiat above.)

Images: optional. If shown, use the source `img/*.webp` — but those are local to
the pokemon-cards project, not web-hosted. v1: **skip art** (null `headshot_url`),
same as sports sets. Hosting card images is a follow-up.

---

## 5. Data model

- **No schema change.** Box price is derived (`36 × base_cost`). `sport` gains a
  `"pokemon"` value (string column already; just a new value — audit any hardcoded
  sport lists/choices in `cards.py`).
- All new rows are ordinary `card_sets` / `card_designs` inserted by the seeders.

---

## 6. RTP / sink sanity

Boxes and premium sets must be **net negative EV** (coin sinks):
- Premium sports/Pokémon boxes are priced far above `expected_pack_value × 36`
  by fiat — deliberately brutal, that's the whale sink.
- Normal Pokémon set `base_cost` is calibrated so `base_cost > expected_pack_value`
  (RTP < 1), same rule the sports sets already follow.
- The guaranteed-hit epic slightly raises box RTP; account for it when calibrating
  (one epic ≈ `BOOK[epic]` = 100 coins book — negligible vs a 50k+ box).

---

## 7. Testing

- `shared/cards.py`: `DRAFT_TIERS` sums sensibly; Pokémon rarity map is total
  (every source rarity maps); `_build_pokemon_designs` emits valid design dicts and
  sets book_value from price with the tier floor.
- `mint_box`: opens exactly `36 × pack_size`, debits `36 × base_cost`, decrements
  the pool by 36 packs, `packs_opened += 36`, refuses when < 36 packs remain, and
  the guaranteed-hit path yields ≥1 epic+ (unit test with a legendary-less small
  set). Atomicity: a mid-box failure rolls back the debit (reuse `mint_pack`'s
  pattern; test the rollback).
- Seeders: idempotent re-run inserts nothing; a seeded premium set has the expected
  rarity distribution and `base_cost`.
- Money: never mints coins; box debit floors correctly; quick-selling a box's cards
  can't exceed what was paid on a negative-EV set (spot check).

## 8. Out of scope (follow-ups)

- Tamagotchi pet (separate spec).
- Card art / image hosting for Pokémon and sports sets.
- Web (`/hq`, browser) box reveal — Discord embed only in v1.
- Additional Pokémon sets beyond the v1 three + premium (seeder is extensible).
- Auto (1-of-1 signed) hit type — deferred earlier by the user.

## 9. Rollout

1. Merge engine + `mint_box` + `/pack box` + seeders (feature-flagged only by the
   sets not existing yet).
2. On the VPS: run `seed_draft_classes.py` and `seed_pokemon.py`.
3. Sync slash commands (`/pack box` appears).
4. Announce via `announce_deploy.py` (Announce: yes) — these are big, fun additions.
5. Add matching achievements (open a premium box, pull a 1-of-1 grail, complete a
   draft-class set) per the standing "achievements for big features" guidance, and
   run the achievement backfill.
