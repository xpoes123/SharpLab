# Card Trading Market — Design

**Date:** 2026-08-19
**Status:** Approved design, pending implementation plan
**Origin:** Port of the `nsba-markets` card trading market
(`backend/app/routers/cards_market.py`) onto SharpLab's existing card system.

## 1. Summary

Give SharpLab cards a **public trade board** on the web. An owner **lists** a card as
open to offers (with an optional "looking for…" note); anyone browsing the **Market**
tab can **make a trade offer** on it — their own card(s) **± coins** for the listed
card. The owner accepts or declines; accepting atomically swaps the cards **and** the
coins. This extends SharpLab's existing card-for-trade system, which today is
**directed** (`/cardtrade offer @user`) and **Discord-only**, with (a) coins on trades
and (b) a web discovery + offer surface.

**Selected scope:** web trade offers + coin-sweetened trades.
**Explicitly out of scope:** fixed-price auto-buy marketplace, timed auctions.

## 2. What already exists (reused, not rebuilt)

- `card_trades` table + queries `create_card_trade`, `get_card_trade`,
  `list_incoming_card_trades`, `accept_card_trade`, `set_card_trade_status`,
  `get_owned_instances` (`db/queries.py`). Directed card-for-card offers,
  accept/decline, atomic ownership swap verified at accept time (**no escrow**).
- Discord `/cardtrade offer|accept|decline` (`bot/cogs/cards.py`).
- Web collection page with the sell/filter/collectors features shipped in #393.
- Coin economy: `casino_wallets`, `get_casino_balance`, `adjust`/credit patterns
  used by `sell_instance` and `mint_pack`.

## 3. Model

- **Listing** — a card its owner has opted onto the public board. Lightweight: it
  carries only an optional note. Not an escrow; the owner still holds the card and
  can sell/trade/unlist it at any time.
- **Offer** — a directed `card_trade` from the offerer to the listed card's owner:
  `offer_ids` (offerer's cards, may be empty), `want_ids` (the listed card, ≥1),
  `coins` (signed sweetener). Reuses the existing trade row + accept flow.
- **Coins (signed).** `coins` on a trade = coins flowing **offerer → owner** on accept.
  `+N` = offerer adds N coins to their side. `−N` = offerer requests N coins from the
  owner. Empty `offer_ids` + `coins > 0` = a pure coin bid (a soft "buy" that still
  requires the owner to accept — this is intentionally *not* an auto-buy marketplace).

## 4. Data model (two additions)

- `card_trades` gains `coins INTEGER NOT NULL DEFAULT 0` — signed sweetener. Added as
  an idempotent `try: ALTER TABLE … ADD COLUMN … except Exception: pass` migration in
  `db/schema.py::init_db`, matching the house idiom.
- New table `card_trade_listings`:
  ```sql
  CREATE TABLE IF NOT EXISTS card_trade_listings (
      instance_id  INTEGER PRIMARY KEY REFERENCES card_instances(instance_id),
      owner_id     TEXT NOT NULL,
      note         TEXT,
      created_at   TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_card_trade_listings_owner ON card_trade_listings(owner_id);
  ```
  One row per listed card (PK = instance_id, so a card is listed at most once). A
  listing is cleared (row deleted) when its card is **traded, sold, or unlisted**, and
  is defensively skipped in market reads if `owner_id` no longer matches the instance's
  current owner (self-heals if a card changes hands outside the market).

## 5. Backend

### 5.1 Queries (`db/queries.py`)

- `create_trade_listing(instance_id, owner_id, note, now_iso)` — verify the caller owns
  the instance; upsert a listing row. Raises `ValueError` if not owned.
- `remove_trade_listing(instance_id, owner_id)` — delete the listing (owner-scoped).
- `list_trade_market(limit=200)` — all active listings joined to design/instance/owner,
  skipping any whose `owner_id` ≠ the instance's current owner. Returns card summary +
  owner name/avatar + note. Reuses the `discord_users` join pattern from `list_collectors`.
- Extend `create_card_trade(..., coins=0)` — persist the `coins` field. Callers that
  omit it default to 0 (Discord back-compat).
- Extend `get_card_trade` / `list_incoming_card_trades` — return `coins`.
- `list_outgoing_card_trades(user)` — pending trades where `from_user = user` (for the
  "sent offers" view + cancel). New.
- Extend `accept_card_trade(trade_id, accepting_user)` — inside the existing
  `BEGIN IMMEDIATE` transaction, after the ownership checks:
  - Determine payer/payee from `sign(coins)`: `coins>0` → from_user pays to_user;
    `coins<0` → to_user pays from_user; `coins==0` → no coin move.
  - Verify the payer's `casino_wallets.balance ≥ |coins|`; raise `ValueError`
    ("not enough coins to complete this trade") otherwise.
  - Debit payer, credit payee by `|coins|` (same wallet-upsert pattern as `sell_instance`).
  - Then swap card ownership as today. Delete any `card_trade_listings` rows for the
    swapped instances (both sides), so a traded card leaves the board.
  - Return dict now includes `coins`.
- `sell_instance` — also delete any `card_trade_listings` row for the sold instance
  (a sold card must leave the board). One extra `DELETE`.

### 5.2 Web endpoints (`web/cards.py`)

All session-gated via `auth.read_session` like `/sell`:

- `GET  /api/v1/cards/market` → `{listings: [...]}` (public; `list_trade_market`).
- `POST /api/v1/cards/list` `{instance_id, note?}` → list a card (owner only).
- `POST /api/v1/cards/unlist` `{instance_id}` → remove a listing.
- `POST /api/v1/cards/trade` `{want_ids, offer_ids?, coins?}` → create an offer. The
  server resolves the target owner from the listed `want_ids` (all must belong to one
  owner and be currently listed, unless the caller passes an explicit directed trade —
  v1 only supports offering on listed cards). Validates the caller owns every
  `offer_id`, owner ≠ caller, and (if `coins>0`) the caller has the balance *now* as a
  soft pre-check (authoritative check is still at accept). Returns the trade id.
- `GET  /api/v1/cards/trades` → `{incoming: [...], outgoing: [...]}` with card previews
  (name/rarity/headshot per instance id) and `coins`.
- `POST /api/v1/cards/trades/{id}/accept` → `accept_card_trade`; returns `{balance}`.
- `POST /api/v1/cards/trades/{id}/decline` → set status declined (recipient only).
- `POST /api/v1/cards/trades/{id}/cancel` → set status cancelled (sender only).

Card previews for offer/trade rows reuse a small `get_instances_public(ids)` helper
(name, rarity, headshot, serial) — one query, no per-card round-trips.

## 6. Web UI (`web/static/cards.js` + `cards.css`)

- **Market tab** (new, alongside My Collection / Packs / Collectors). Grid of listed
  cards: tile + owner chip + note + a **Make offer** button. Filter/sort bar reused
  from the collection view.
- **List for trade** — a button on your own collection tiles (next to Sell) toggling
  list/unlist, with an optional note prompt. Listed cards show a small "🔖 Listed" badge.
- **Make offer modal** — pick which of your cards to give (multi-select from your
  collection, filtered), set a coin amount (± via a signed number field), review, submit.
- **My Offers** — a section (in the Market tab or its own sub-view): **Incoming**
  (accept/decline, shows what you'd give up and get) and **Outgoing** (cancel). Each
  row previews both sides' cards + the coin delta and the resulting effect on balance.
- Balance chip updates via the existing `applyBalance` after accept.
- `?mock=1` fixtures extended for `/market`, `/trades`, and offer/accept so the page
  previews without a backend.

## 7. Discord (`bot/cogs/cards.py`)

`/cardtrade offer` gains an optional `coins: int = 0` argument (positive = you add
coins, negative = you want coins), threaded into `create_card_trade`. The accept embed
shows the coin sweetener. No other Discord changes; listings/market are web-only in v1.

## 8. Testing (`tests/test_cards.py`)

- `accept_card_trade` moves coins: from_user pays to_user (`coins>0`), balances change
  by exactly `|coins|`, cards swap. And the reverse sign (`coins<0`).
- Accept rejected when payer lacks coins (raises, nothing changes — transaction rolls back).
- List → offer (via web endpoint) → accept round-trip; the listing row is gone after.
- Pure-coin offer: `offer_ids=[]`, `coins>0`, want a listed card — accept transfers the
  card and the coins.
- `sell_instance` clears a listing for the sold card.
- `list_trade_market` skips a listing whose card changed owners (self-heal).
- Web: `POST /list` rejects listing a card you don't own (owner check).

## 9. Non-goals (v1)

- No fixed-price **auto-buy** marketplace (a listing is not a "Buy now" — every transfer
  goes through an owner-accepted offer).
- No timed **auctions** / bidding / buy-now / settlement job.
- No escrow — cards and coins are verified and moved only at accept time.
- No trade fees / house cut.
- No offering on **unlisted** cards from the browse-collection view in v1 (offers target
  listed cards only; directed Discord offers still work as before).
