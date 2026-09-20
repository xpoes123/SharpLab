# Windmill — daily tile-placement game (design)

Date: 2026-09-18

## Summary

A new Daily Games plugin, **Windmill**, inspired by IMO 2025 P6. Each day the player is
handed a shuffled **bag of rectangular tiles** and must place them on an n×n grid so that:

- every tile in the bag is placed (rotations allowed),
- no two tiles overlap and all stay in-bounds,
- **exactly one unit square in each row and each column is left uncovered.**

That final arrangement is a win. It's a placement/packing puzzle — the player does **not**
design tiles, they fit the given ones.

This is the platform's 4th game (after trap-the-pig, rush-hour, mastermind). It is a plain
**offline** game: the player builds locally, submits the final placements, and the server
validates — so it needs **no new endpoints and no DB changes** (unlike Mastermind).

## Ranking

Windmill ranks by **time** (fastest valid arrangement). The tile count is fixed by the day's
bag, so a move-count metric would be constant across clean solvers and can't be server-verified
offline. `RANK_ORDER = ("secondary_score", "primary_score")` (time first, tile count as an inert
tiebreak). `primary` = N (tile count, for display); `secondary` = server-timed elapsed.

### Accompanying change: Mastermind → time

Flip `mastermind.RANK_ORDER` to time-first as well (community preference; a running server clock
is the honest, uncheatable metric and makes the guess-count cheese moot). The existing per-guess
server-side history (`mm_state`, `/mm-guess`) stays — it's still needed to hide the code and to
resume/display guesses — it's just no longer ranking-critical. HOWTO copy updated to say time.

Rush Hour + Trap the Pig keep move-count-first (their move count is real & server-verified).

## Plugin: `shared/daily_games/windmill.py`

Duck-typed like the others: `ID="windmill"`, `NAME="Windmill"`, `ICON="🌀"`, `SURFACE="web"`,
`DIFFICULTIES=["easy","medium","hard"]`, `RANK_ORDER=("secondary_score","primary_score")`, `HOWTO`.

### Board payload

```json
{"n": 6, "tiles": [[w,h], ...]}   // tiles = shuffled bag of rectangle dims; no solution leaked
```

Gap positions are NOT revealed — discovering them is the puzzle.

### `build_solvable(seed, difficulty) -> payload`

Deterministic (seeded), always solvable by construction:

1. `n` from difficulty: easy 6, medium 8, hard 10.
2. Random permutation π of `0..n-1` → gaps at `(row i, col π[i])` (one per row/col).
3. Randomized greedy rectangle partition of the remaining cells:
   - scan for the top-left-most uncovered non-gap cell;
   - grow a rectangle: max width rightward until a gap/covered/edge; then max height downward
     while the whole width stays clear; pick a size in `[1..maxW]×[1..maxH]` biased toward larger;
   - place it, mark covered; repeat until every non-gap cell is covered (1×1 is the always-available
     fallback, so it terminates and fully covers).
4. `tiles` = the rectangles' dims, shuffled by the rng.

Result: gaps are the only uncovered cells → exactly one per row/col. Provably solvable (the
generated arrangement is a witness).

### `par(payload) -> (N, False)`

`N = len(tiles)`, exact.

### `validate(puzzle, solution) -> dict | None`

`solution["moves"]` = list of placements `[x, y, w, h]` (x=col, y=row; w,h already reflect any
rotation the player applied). Returns `{"solved":True,"primary":N,"secondary":0}` iff:

- `len(placements) == N`;
- every placement in-bounds (`0<=x, x+w<=n, 0<=y, y+h<=n, w>=1, h>=1`);
- no two placements overlap;
- the multiset of placed dims, each normalized to `(min(w,h), max(w,h))`, equals the bag's
  (rotations allowed, exact bag enforced);
- uncovered cells number exactly `n`, one in every row and one in every column.

Otherwise `None`. `secondary` is overwritten server-side with elapsed time (existing flow).

### `share_grid(result, meta) -> str`

`🌀 Windmill #N · <difficulty> · <N> tiles · M:SS`.

### Self-check (`__main__`)

Build a board, assert the generated witness placements validate, assert a wrong bag / overlap /
out-of-bounds / bad-gap-count each fail.

## Registration: `shared/daily.py`

Import `windmill`; add to `DAILY_GAMES` and `DAILY_POOL` → 4-game rotation. Offline game, so
`build_puzzle` uses the unsalted seed (no hidden info).

## Frontend: `DailyRenderers["windmill"]` (in `web/static/daily.js`)

Offline renderer, same contract as trappig/rushhour (`mount`, `getMoves`, `teardown`).

- **Layout**: the n×n grid + a tray of remaining tiles (each drawn as a mini rectangle labeled
  `w×h`).
- **Placement**: click a tray tile to select, click a grid cell to drop it (top-left anchor);
  click a placed tile to pick it back up. Rotate the selected tile with a **Rotate** button / `R`
  key (swaps w/h). Illegal drops (overlap / out of bounds) are rejected with a nudge.
- **Live feedback**: per-row and per-column indicator turns green only when that line has exactly
  one uncovered cell; a "tiles left: X" counter; `onMove(placedCount)` drives the stat box.
- **Win**: when all tiles are placed and the arrangement is valid, a **Submit** button enables →
  `onSolved()`. `getMoves()` returns the placements list. No `onEscaped` (no loss state).
- **Reset**: returns all tiles to the tray (re-fetches the same board; clock keeps running).
- Game-aware copy in daily.js: `unitWord`/`unitLabel` = "tiles", solved title "🎉 Tiled!", rule
  text. Since Windmill ranks by time, the tile counter is progress, not score.

## Tutorial

A short **interactive first-run walkthrough** on a fixed 4×4 mini board with 2–3 tiles:

1. Explain the rule: "Each row and each column must have exactly ONE empty square."
2. Guide the player to place + rotate the sample tiles, highlighting rows/cols as they satisfy
   the rule.
3. On completion, "Got it!" sets a `localStorage` flag so it doesn't nag on future days; a "?"
   link in the header replays it.

Shown before the real board on first play. Built from the same placement widget on a scripted
mini board (self-contained in the frontend).

## Difficulty

| Difficulty | Grid | Gaps | Bag size (approx) |
|---|---|---|---|
| easy | 6×6 | 6 | ~8–12 |
| medium | 8×8 | 8 | ~12–18 |
| hard | 10×10 | 10 | ~16–24 |

## Tests

- `tests/test_daily_windmill.py`: generation is deterministic + fully covers (gaps = only
  uncovered, one per row/col); the generated witness validates; validate rejects wrong bag,
  overlap, out-of-bounds, and wrong gap distribution; par == N; RANK_ORDER is time-first.
- Update `tests/test_daily_mastermind.py` for the RANK_ORDER flip.

## Out of scope

- No standalone practice/arcade page (the daily + tutorial cover it); can add later via the
  existing `/preview` endpoint if wanted.
- No new achievements (incremental rotation add).
