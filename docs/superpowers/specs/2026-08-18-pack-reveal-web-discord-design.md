# Animated pack opening — web + Discord

**Date:** 2026-08-18
**Builds on:** `2026-08-11-sports-card-packs-design.md` (the card system this animates).

## Goal

Port the nsba-markets pack-opening reveal to SharpLab. Make the web `/cards` page a
real place to open packs (deduct coins, mint, animate), and make the existing Discord
`/pack open` reveal animated (step through cards with arrows). Both surfaces reveal in
**ascending rarity** (climax on the best card) and show, per card, the **odds to pull it**
("1 in N") and its **price = book value** (the card's EV — `book_value(rarity, holo, gem)`
already is exactly this; no new number).

Backend already mirrors nsba: `queries.mint_pack` does the atomic coin-debit + weighted
draw + mint. Discord already opens packs. Missing: web opening + reveal animation, and
Discord animation.

## Shared engine (`shared/cards.py`) — one source of truth for both surfaces

- `set_odds(designs) -> dict` — the odds block (`holo_pct`, per-rarity `pull_rates` %,
  `gems`). `web/cards.py`'s catalog endpoint currently computes this inline; refactor it
  to call this (removes duplication).
- `reveal_order(cards) -> list` — cards sorted ascending by (rarity index, is_holo,
  book_value), so a reveal always ends on the chase card.
- `pull_label(card, pull_rates) -> str` — "1 in N" for the card: rarity pull-rate; a gem
  overrides with `1 in GEMS[g][0]` (rarest); holo appends "· holo 1 in 5".

## Discord — animated arrow reveal (`bot/cogs/cards.py`)

Replace the single `_reveal_embed` send in `/pack open` and `/pack daily` with a
`discord.ui.View`:
- One card per page, `◀`/`▶` buttons, ascending-rarity order.
- Per-card embed: art thumbnail, name/team, rarity color, odds "1 in N", price (book
  value), serial `#/total`, holo/gem/RC badges.
- A **Summary** button (and auto-land after the last card) → the existing full-haul embed.
- Buttons locked to the opener; View times out (~180s) and disables its buttons.
- Post-reveal side effects (notable broadcast, wanter DMs, set-completion, achievements)
  unchanged.

## Web — open + reveal (`web/cards.py`, `web/static/cards.*`)

- `POST /api/v1/cards/open` — session-auth. Body `{sport, season, n(1-10)}`. Loops
  `mint_pack(uid, set_id, 5, "paid", now)` (same as Discord → same ValueErrors → 400 with
  message). Returns `{cards, odds, balance}`.
- `POST /api/v1/cards/daily` — mirrors `/pack daily`: `has_claimed_daily_pack` → 409 else
  mint `"daily"` + `record_daily_pack_claim`.
- `web/static/cardSfx.js` — direct port of nsba `cardSfx.ts` (pure Web Audio: pack rip,
  rarity-scaled reveal chime, holo shimmer, mute toggle). No audio files.
- Reveal overlay in `cards.js` (ported `.cd-reveal`): pack-burst (~900ms) → tap/`▶` to
  flip, ascending rarity, each card shows odds + price on flip → haul summary. Respects
  `prefers-reduced-motion`.
- **Open pack** + **Free daily pack** buttons on the Sets tab (signed-out → existing
  sign-in prompt).
- Port nsba's `.cd-*` / `.ct-flip` CSS into `cards.css`, adapted to SharpLab's `.ctile`
  markup and `--r-<rarity>` vars.

## Out of scope (dropped in nsba too, YAGNI)

Bulk-discount store math, RTP/net-worth modal, web quick-sell, web→Discord big-pull
broadcast (web stays silent v1).

## Testing

- `tests/`: `reveal_order` sorts ascending; `pull_label` matches known odds; `/open` mints
  n×5 + debits coins + 400s on insufficient/sold-out.
- Manual: Discord arrow-step a real open; browser reveal with a minted session cookie +
  screenshot (HQ-screenshot rule).
