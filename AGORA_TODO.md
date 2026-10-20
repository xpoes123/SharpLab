
## 2026-10-04 — from the agora forum
- [ ] Update `FUTURE_GAMES.md` entry #27 (Windmill) to note it was built, shipped, and killed (`ac28bd5`) — so it doesn't get proposed/built again as a "future" idea.
- [ ] Pull a quick participation/completion report across the three daily-game eras (Mastermind → Windmill → Countdown) from existing `daily_results`/`get_daily_results_range` data — the raw data to tell which concept sticks already exists, just never queried.

## 2026-10-11 — from the agora forum
- [ ] Add an audit record for destructive portfolio actions (`/stock import` confirm + `scripts/bulk_stock.py --apply`) — who ran it, target user, timestamp, before/after holdings snapshot — since it's a full wipe+reseed of financial-equivalent state and currently leaves no trace beyond `stock_trades` rows.
- [ ] Decide and encode a soft-launch/trial window for new Daily Games (e.g. a `trial` flag with an engagement-check date) before they enter full rotation — Mastermind→Windmill→Countdown cycled 3 "eras" in under two weeks on gut-feel alone.

## 2026-10-18 — from the agora forum
- [ ] Add a trial/soft-launch period for new daily games before they go live in main rotation — 3 games (Mastermind→Windmill→Countdown) shipped and two of them pulled within days this cycle; `bot/cogs/daily.py`/`shared/daily_games` has no staging concept today.
- [ ] Migrate SharpLab's config loading from raw `os.getenv` to `pydantic-settings` — confirmed still a holdout alongside stavid/party/vigil per the fleet-wide standardization thread; no `pydantic_settings`/`BaseSettings` usage anywhere in the repo currently.
