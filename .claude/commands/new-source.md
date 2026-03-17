Scaffold wiring in a new odds data source end-to-end.

Usage: /new-source <source-name>
Examples: /new-source the-odds-api, /new-source kalshi, /new-source polymarket, /new-source balldontlie

Workflow:
1. Read `temporal/activities.py` and `temporal/workflows.py` to understand the current patterns
2. Read `temporal/worker.py` to see how activities are registered
3. Determine what the source provides:
   - **the-odds-api**: spread, moneyline, total for all major books in one call. Returns `OddsBatch`. Key in env as `ODDS_API_KEY`.
   - **kalshi**: NBA game contracts (home/away win). Returns `OddsBatch` with prices in American odds. Key in env as `KALSHI_API_KEY`. Base URL: `https://api.elections.kalshi.com/trade-api/v2`
   - **polymarket**: NBA game markets via CLOB API. No key needed for reads. Base URL: `https://clob.polymarket.com`. Returns probability (0–1), convert to American odds.
   - **balldontlie**: NBA schedule and scores. Free, no key. Use for `fetch_games_for_today`. Base URL: `https://api.balldontlie.io/v1`
4. Add the activity to `temporal/activities.py`:
   - Decorate with `@activity.defn`
   - Make one HTTP call per activity using `httpx.AsyncClient`
   - Return `OddsBatch` (for poll sources) or `OddsSnapshot` (for single-game sources)
   - Log what was fetched with `activity.logger.info(...)`
5. Add `uv add httpx` if httpx isn't already a dependency
6. Add the env var to `.env.example` (not `.env`) with a comment explaining where to get the key
7. Register the new activity in `temporal/worker.py`
8. Wire it into `OddsPollingWorkflow` in `temporal/workflows.py` — call it in the polling loop alongside existing sources
9. Write a brief test stub in `tests/` following the pattern in `tests/test_activities.py`
10. Confirm the shape of the returned data matches `OddsBatch` / `OddsSnapshot` dataclasses
