Adversarial data quality pass. Run this before trusting any result from the pipeline.

Use proactively whenever:
- A new odds source was just wired in
- CLV numbers look suspiciously good or bad
- Pipeline ran for the first time on real data
- Something feels off

Checks to run:

**Data integrity:**
1. Are there duplicate `snapshot_id` entries in `odds_snapshots`? (Should be unique)
2. Are `captured_at` timestamps sensible — in the past, within the last 24h for poll snapshots?
3. Are there games with `close` snapshots but no `poll` snapshots? (Pipeline may have started late)
4. Are all `game_id` values in `odds_snapshots` present in the `games` table?

**Odds sanity:**
5. Are spread values in a reasonable range? (NBA spreads are typically -15 to +15)
6. Are moneyline odds in American format? (-500 to +500 for typical games, not 0.65 or 65)
7. Are Kalshi/Polymarket prices being stored consistently? (probability 0–1, or American odds — pick one and document it)
8. Do spread + juice combos make sense? (both sides of a -110/-110 market should bracket 0 EV)

**Temporal pipeline:**
9. Is `OddsPollingWorkflow` running? Check for recent poll snapshots (last 30 min during game days)
10. Are `CloseCaptureWorkflow` child workflows being created for upcoming games?
11. Are there any activities that error'd or timed out? (Check Temporal UI or worker logs)

**Bets:**
12. Do all bets reference a real `game_id` in the games table?
13. Are there bets with `status = 'open'` for games that are already final? (Needs CLV resolution)

Report each check as PASS / WARN / FAIL with a brief note. Be blunt about what's broken.
