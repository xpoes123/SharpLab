"""One-shot backfill of closing-line snapshots from existing poll history.

CloseCaptureWorkflow was broken for a long stretch (the worker crash-loop and,
before that, the continue_as_new/ParentClosePolicy bug), so most games never
got a real `kind='close'` snapshot. BUT the regular OddsPollingWorkflow kept
running the whole time, so we DO have poll history right up to tip-off.

This script promotes the last pre-tip poll per (game, source) into a canonical
`kind='close'` snapshot — i.e. it "cuts the line off" at game start, exactly
where the closing line is. It never overwrites a real close that already
exists for a source, and it's idempotent (deterministic backfill snapshot_id).

    python -m temporal.backfill_closing_lines           # dry run, prints a report
    python -m temporal.backfill_closing_lines --apply    # write the snapshots

A +2 minute grace after start_time mirrors the read-time cutoff in web/api.py.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from db import queries
from shared.models import OddsSnapshot

GRACE = timedelta(minutes=2)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


async def backfill(apply: bool, purge_late: bool) -> None:
    now = datetime.now(timezone.utc)

    if purge_late:
        # Drop live-contaminated closes (captured after tip) so the backfill below
        # can refill those sources from the clean last-pre-tip poll.
        if apply:
            n = await queries.purge_post_tip_closes()
            print(f"Purged {n} post-tip (live-contaminated) close snapshots.\n")
        else:
            # Dry run: count without deleting via a transaction we don't commit.
            print("(--purge-late) would delete post-tip closes before refilling.\n")

    games = await queries.get_games_started_before(now.isoformat())
    print(f"Scanning {len(games)} games that have already started…\n")

    written = 0
    games_touched = 0
    for game in games:
        start = _parse(game.start_time_utc_iso)
        if start is None:
            continue
        cutoff = (start + GRACE).isoformat()

        polls = await queries.get_last_poll_snapshots_before(game.game_id, cutoff)
        if not polls:
            continue

        new_for_game: list[str] = []
        for snap in polls:
            existing = await queries.get_close_snapshot(game.game_id, snap.source)
            if existing is not None:
                continue  # a real (or already-backfilled) close exists — leave it
            close = OddsSnapshot(
                snapshot_id=f"close:{game.game_id}:{snap.source}:backfill",
                game_id=game.game_id,
                kind="close",
                source=snap.source,
                # Stamp the close at the poll's own capture time (the true last
                # pre-tip observation), not "now".
                captured_at_utc_iso=snap.captured_at_utc_iso,
                payload=snap.payload,
            )
            new_for_game.append(snap.source)
            if apply:
                await queries.upsert_odds_snapshot(close)

        if new_for_game:
            games_touched += 1
            written += len(new_for_game)
            label = f"{game.away_team} @ {game.home_team}"
            print(f"  {game.start_time_utc_iso[:16]}  {label:<48} +{len(new_for_game)} "
                  f"({', '.join(sorted(new_for_game))})")

    verb = "Wrote" if apply else "Would write"
    print(f"\n{verb} {written} close snapshots across {games_touched} games.")
    if not apply:
        print("Dry run — re-run with --apply to persist.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--purge-late", action="store_true",
                    help="first delete closes captured after tip (live-contaminated), then refill")
    args = ap.parse_args()
    asyncio.run(backfill(args.apply, args.purge_late))


if __name__ == "__main__":
    main()
