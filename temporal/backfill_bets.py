"""One-shot backfill for bets that missed resolution and/or CLV.

Two regressions left historical bets un-tracked:
  1. CloseCaptureWorkflow children were terminated every poll cycle (the
     continue_as_new / ParentClosePolicy bug), so no closing line was ever
     captured for the games people bet on → CLV stayed NULL.
  2. Some bets were logged after their game had already been marked final
     (resolution skips final games), so they stayed 'open' forever.

This script repairs the existing rows. It does NOT alter the live pipeline.

  Resolution:  for every open/graded bet, grade it from the final score.
               Games still lacking a score are looked up from balldontlie/ESPN.
  CLV:         for every non-void bet with NULL clv, compute CLV against a
               *proxy* close — the last poll snapshot captured before tip-off
               (real closes were never recorded for these games). Computed
               directly into bets.clv; no Discord post is triggered.

Run dry (default) to preview, then with --apply to write.

    python -m temporal.backfill_bets            # dry run, prints a report
    python -m temporal.backfill_bets --apply    # write changes
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from db import queries
from shared.models import Bet, Game
from shared.odds_utils import compute_clv, side_is_home
from temporal.activities import _fetch_nba_scores, _fetch_espn_scores, _resolve_bet


def _date_of(iso: str) -> str:
    return iso[:10]


def _close_odds_for_bet(bet: Bet, game: Game, payload: dict) -> int | None:
    """Match a bet's market+side to the right odds field in a snapshot payload.

    Mirrors bot/cogs/clv.py._close_odds_for_bet so backfilled CLV matches live CLV.
    """
    market = bet.market.lower()
    side = bet.side.lower()
    home = game.home_team.lower()
    away = game.away_team.lower()

    if market == "moneyline":
        if side in home and side not in away:
            return payload.get("ml_home")
        if side in away and side not in home:
            return payload.get("ml_away")
    elif market == "spread":
        if (side in home) ^ (side in away):
            return payload.get("spread_odds")  # away juice assumed equal to home
    elif market == "total":
        if side == "over":
            return payload.get("total_over_odds")
        if side == "under":
            return payload.get("total_under_odds")
    return None


async def _fetch_scores_retry(sport: str, date: str) -> list:
    """Fetch one day of final scores, backing off on balldontlie's 429 rate limit."""
    for attempt in range(5):
        try:
            if sport == "nba":
                return await _fetch_nba_scores([date])
            return await _fetch_espn_scores([date], sport)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt < 4:
                wait = 5 * (attempt + 1)
                print(f"    (429 from score API on {date}; retrying in {wait}s)")
                await asyncio.sleep(wait)
                continue
            raise
    return []


async def backfill_resolution(apply: bool) -> tuple[int, int]:
    """Grade open/graded bets. Returns (resolved, still_unresolved)."""
    bets = await queries.get_all_unresolved_bets()
    if not bets:
        print("  resolution: no open/graded bets")
        return (0, 0)

    # Cache games + fetched scores by date so we hit each external API once.
    games: dict[str, Game | None] = {}
    score_cache: dict[str, list] = {}  # f"{sport}:{date}" -> list[GameResult]

    async def game_for(gid: str) -> Game | None:
        if gid not in games:
            games[gid] = await queries.get_game_by_id(gid)
        return games[gid]

    async def scores_for(sport: str, date: str) -> list:
        # Fetch the day plus its neighbours: a game's UTC start date can differ
        # from the score provider's local (ET) game date by one day.
        d = datetime.fromisoformat(date)
        dates = [
            (d + timedelta(days=delta)).date().isoformat() for delta in (-1, 0, 1)
        ]
        out: list = []
        for dd in dates:
            key = f"{sport}:{dd}"
            if key not in score_cache:
                score_cache[key] = await _fetch_scores_retry(sport, dd)
            out.extend(score_cache[key])
        return out

    resolved = 0
    unresolved = 0
    for bet in bets:
        game = await game_for(bet.game_id)
        if game is None:
            print(f"  bet {bet.bet_id}: no game row — skip")
            unresolved += 1
            continue

        scores = await queries.get_game_scores(bet.game_id)
        if scores is None:
            # Game never got a final score — look it up by date + team suffix.
            date = _date_of(game.start_time_utc_iso)
            try:
                results = await scores_for(game.sport, date)
            except Exception as e:
                print(f"  bet {bet.bet_id}: score lookup failed ({e!r}) — still open")
                unresolved += 1
                continue
            home_l, away_l = game.home_team.lower(), game.away_team.lower()
            match = next(
                (r for r in results if r.home_last in home_l and r.away_last in away_l),
                None,
            )
            if match is None:
                print(f"  bet {bet.bet_id}: {game.away_team} @ {game.home_team} "
                      f"({date}) — no final score found, still open")
                unresolved += 1
                continue
            scores = (match.home_score, match.away_score)
            if apply:
                await queries.update_game_scores(bet.game_id, scores[0], scores[1])
                await queries.update_game_status(bet.game_id, "final")

        outcome = _resolve_bet(bet, game.home_team, game.away_team, scores[0], scores[1])
        print(f"  bet {bet.bet_id}: {game.away_team} @ {game.home_team} "
              f"{scores[1]}-{scores[0]} | {bet.market} {bet.side} "
              f"{('%+.1f' % bet.line) if bet.line is not None else ''} → {outcome}")
        if apply:
            await queries.update_bet_result(bet.bet_id, outcome)
        resolved += 1

    return (resolved, unresolved)


async def backfill_clv(apply: bool) -> tuple[int, int]:
    """Compute CLV for bets missing it, using last pre-tip poll as a close proxy.

    Returns (filled, skipped).
    """
    bets = await queries.get_all_bets_missing_clv()
    if not bets:
        print("  clv: no bets missing CLV")
        return (0, 0)

    filled = 0
    skipped = 0
    for bet in bets:
        game = await queries.get_game_by_id(bet.game_id)
        if game is None:
            skipped += 1
            continue

        snaps = await queries.get_last_poll_snapshots_before(
            bet.game_id, game.start_time_utc_iso
        )
        by_source = {s.source: s for s in snaps}
        market = bet.market.lower()
        # Prefer Kalshi for moneyline (cleaner two-way price), DraftKings otherwise.
        ref = None
        if market == "moneyline" and "kalshi" in by_source:
            ref = by_source["kalshi"]
        elif "draftkings" in by_source:
            ref = by_source["draftkings"]
        elif snaps:
            ref = snaps[0]

        if ref is None:
            print(f"  bet {bet.bet_id}: no pre-tip snapshot — skip CLV")
            skipped += 1
            continue

        close_odds = _close_odds_for_bet(bet, game, ref.payload)
        if close_odds is None:
            print(f"  bet {bet.bet_id}: {market} not in {ref.source} payload — skip CLV")
            skipped += 1
            continue

        is_home = side_is_home(bet.side, game.home_team, game.away_team)
        close_spread = ref.payload.get("spread")
        close_total = ref.payload.get("total")
        clv = compute_clv(
            bet.odds, close_odds,
            market=market,
            bet_line=bet.line,
            close_line=close_spread if market == "spread"
            else close_total if market == "total" else None,
            is_home=is_home if market == "spread" else None,
            is_over=(bet.side.lower() == "over") if market == "total" else None,
        )
        print(f"  bet {bet.bet_id}: {bet.market} {bet.side} @ {bet.odds} vs "
              f"{ref.source} close {close_odds} → CLV {clv:+.1f} pp")
        if apply:
            await queries.set_bet_clv(bet.bet_id, round(clv, 2))
        filled += 1

    return (filled, skipped)


async def main(apply: bool) -> None:
    mode = "APPLY" if apply else "DRY RUN"
    print(f"=== Bet backfill ({mode}) — {datetime.now(timezone.utc).isoformat()} ===")
    print("\n[1/2] Resolution (open/graded → won/lost/push)")
    resolved, unresolved = await backfill_resolution(apply)
    print("\n[2/2] CLV (NULL clv → computed from pre-tip close proxy)")
    filled, skipped = await backfill_clv(apply)

    print("\n=== Summary ===")
    print(f"  resolved:        {resolved}")
    print(f"  still unresolved:{unresolved}")
    print(f"  CLV filled:      {filled}")
    print(f"  CLV skipped:     {skipped}")
    if not apply:
        print("\n(dry run — re-run with --apply to write)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
