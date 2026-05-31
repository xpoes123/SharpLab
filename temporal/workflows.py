from datetime import timedelta, datetime, timezone
from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.workflow import ParentClosePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

with workflow.unsafe.imports_passed_through():
    from .activities import (
        fetch_games_for_today,
        fetch_odds_batch,
        fetch_injuries,
        record_injury_changes,
        upsert_odds_snapshot,
        fetch_close_odds_snapshot,
        fetch_kalshi_odds_batch,
        fetch_kalshi_close_snapshot,
        fetch_polymarket_odds_batch,
        fetch_polymarket_close_snapshot,
        fetch_final_scores,
        resolve_bets_for_game,
        FetchCloseSnapshotInput,
    )


@workflow.defn
class OddsPollingWorkflow:
    """
    Durable polling loop:
    1. Fetch today's game schedule (lightweight events call)
    2. Start a CloseCaptureWorkflow for any game that hasn't tipped yet
    3. Fetch live odds for all today's games (one call, all books)
    4. Persist every (game, bookmaker) snapshot to the DB
    5. Sleep until next interval
    """

    @workflow.run
    async def run(self, interval_minutes: int = 30, sport: str = "nba") -> None:
        while True:
            # ── Step 1: get today's schedule ──────────────────────────────────
            games = await workflow.execute_activity(
                fetch_games_for_today,
                sport,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            # ── Step 2: spawn CloseCaptureWorkflow per upcoming game ──────────
            # Only poll odds for games that haven't tipped yet. Once a game
            # starts, The Odds API serves LIVE in-game prices — polling those
            # pollutes the closing-line history and burns quota. The last
            # pre-tip poll IS the closing line.
            upcoming_games = []
            for game in games:
                start_dt = datetime.fromisoformat(game.start_time_utc_iso)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                if start_dt <= workflow.now():
                    continue  # already tipped — don't poll or start close-capture
                upcoming_games.append(game)

                close_wf_id = f"close-capture-{game.game_id}"
                try:
                    await workflow.start_child_workflow(
                        "CloseCaptureWorkflow",
                        (game.game_id, game.start_time_utc_iso, sport),
                        id=close_wf_id,
                        task_queue=workflow.info().task_queue,
                        # ALLOW_DUPLICATE so a close-capture can be recreated once a
                        # prior run with the same id has closed. REJECT_DUPLICATE
                        # permanently blocked recreation after a child was terminated,
                        # so any game whose close-capture died once never got another.
                        # Concurrent duplicates are still prevented by the
                        # WorkflowAlreadyStartedError catch below.
                        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
                        # ABANDON so the child survives this parent's continue_as_new.
                        # The default (TERMINATE) killed every pending close-capture
                        # each 30-min poll cycle, so closing lines were never captured.
                        parent_close_policy=ParentClosePolicy.ABANDON,
                    )
                    workflow.logger.info(
                        f"Started CloseCaptureWorkflow for {game.away_team} @ {game.home_team} ({game.game_id})"
                    )
                except WorkflowAlreadyStartedError:
                    pass

            game_ids = [g.game_id for g in upcoming_games]
            if not game_ids:
                # Nothing left to poll this cycle — skip the odds fetches.
                await workflow.sleep(timedelta(minutes=interval_minutes))
                workflow.continue_as_new(args=[interval_minutes, sport])

            # ── Step 3: fetch odds for pre-tip games only ─────────────────────
            batch = await workflow.execute_activity(
                fetch_odds_batch,
                args=[game_ids, sport],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            # ── Step 4: persist each (game, bookmaker) snapshot ───────────────
            for snapshot in batch.snapshots:
                await workflow.execute_activity(
                    upsert_odds_snapshot,
                    snapshot,
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )

            # ── Step 5: fetch Kalshi ML for pre-tip games only ────────────────
            kalshi_batch = await workflow.execute_activity(
                fetch_kalshi_odds_batch,
                args=[upcoming_games, sport],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            # ── Step 6: persist Kalshi snapshots ──────────────────────────────
            for snapshot in kalshi_batch.snapshots:
                await workflow.execute_activity(
                    upsert_odds_snapshot,
                    snapshot,
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )

            # ── Step 7: fetch Polymarket ML for pre-tip games only ────────────
            polymarket_batch = await workflow.execute_activity(
                fetch_polymarket_odds_batch,
                upcoming_games,
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            # ── Step 8: persist Polymarket snapshots ──────────────────────────
            for snapshot in polymarket_batch.snapshots:
                await workflow.execute_activity(
                    upsert_odds_snapshot,
                    snapshot,
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )

            await workflow.sleep(timedelta(minutes=interval_minutes))
            workflow.continue_as_new(args=[interval_minutes, sport])


# Capture the closing line this many seconds BEFORE tip-off. Capturing AT tip
# is too late: sportsbooks suspend and the Odds API event briefly drops out, so
# the activity retries into the first LIVE in-game price (~2 min after tip).
# Kalshi exposes this worst — it's a continuous market that reprices the instant
# the ball is in the air. 90s before tip is effectively the close, but clean.
CLOSE_LEAD = timedelta(seconds=90)


@workflow.defn
class CloseCaptureWorkflow:
    """
    One workflow per game. Sleeps until just before tip-off, then captures the
    closing line (the last pre-game market state).
    """

    @workflow.run
    async def run(self, args: tuple[str, str, str]) -> None:
        game_id = args[0]
        start_time_utc_iso = args[1]
        sport = args[2] if len(args) > 2 else "nba"

        start_dt = datetime.fromisoformat(start_time_utc_iso)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)

        # Patched so close-capture children already in flight (which slept using
        # the old "capture at tip" math) replay deterministically; only new runs
        # lead by CLOSE_LEAD.
        target = start_dt - CLOSE_LEAD if workflow.patched("close-capture-lead-90s") else start_dt
        delay = target - workflow.now()
        if delay.total_seconds() > 0:
            await workflow.sleep(delay)

        results = await workflow.execute_activity(
            fetch_close_odds_snapshot,
            FetchCloseSnapshotInput(
                snapshot_id=f"close:{game_id}",
                game_id=game_id,
                sport=sport,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        if not results:
            workflow.logger.warning(f"No close snapshot available for {game_id} — lines already closed")
        else:
            await workflow.execute_activity(
                upsert_odds_snapshot,
                results[0],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

        # Also capture Kalshi close (ML only) — used as source of truth for CLV
        kalshi_results = await workflow.execute_activity(
            fetch_kalshi_close_snapshot,
            FetchCloseSnapshotInput(
                snapshot_id=f"close:kalshi:{game_id}",
                game_id=game_id,
                sport=sport,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        if kalshi_results:
            await workflow.execute_activity(
                upsert_odds_snapshot,
                kalshi_results[0],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

        # Also capture Polymarket close (ML only) — secondary prediction market signal
        polymarket_results = await workflow.execute_activity(
            fetch_polymarket_close_snapshot,
            FetchCloseSnapshotInput(
                snapshot_id=f"close:polymarket:{game_id}",
                game_id=game_id,
                sport=sport,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        if polymarket_results:
            await workflow.execute_activity(
                upsert_odds_snapshot,
                polymarket_results[0],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )


@workflow.defn
class BetResolutionWorkflow:
    """
    Polling loop: every N hours, fetch final scores for yesterday + today and
    resolve any open/graded bets whose games have finished.
    """

    @workflow.run
    async def run(self, interval_hours: int = 2, sport: str = "nba") -> None:
        while True:
            now = workflow.now()
            yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            today = now.strftime("%Y-%m-%d")

            results = await workflow.execute_activity(
                fetch_final_scores,
                args=[[yesterday, today], sport],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            for result in results:
                await workflow.execute_activity(
                    resolve_bets_for_game,
                    result,
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )

            await workflow.sleep(timedelta(hours=interval_hours))
            workflow.continue_as_new(args=[interval_hours, sport])


@workflow.defn
class InjuryPollingWorkflow:
    """
    Polls ESPN for NBA injury updates every N minutes.
    When status changes are detected, immediately re-fetches odds so the bot's
    InjuryCog notification shows fresh lines alongside the injury news.
    """

    @workflow.run
    async def run(self, interval_minutes: int = 5) -> None:
        sport = "nba"  # injuries are NBA-only for now
        while True:
            games = await workflow.execute_activity(
                fetch_games_for_today,
                sport,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            if games:
                changes = await workflow.execute_activity(
                    fetch_injuries,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )

                if changes:
                    # Persist ALL status changes (including silent recoveries) so
                    # future Out detections see the correct previous status and
                    # Out → Active → Out correctly re-alerts.
                    await workflow.execute_activity(
                        record_injury_changes,
                        changes,
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )

                # Re-poll odds only for alert-worthy changes (Out events move
                # lines; recoveries typically don't need a fresh snapshot).
                if any(c.should_notify for c in changes):
                    # Re-poll odds so the notification shows lines post-news
                    game_ids = [g.game_id for g in games]
                    batch = await workflow.execute_activity(
                        fetch_odds_batch,
                        args=[game_ids, sport],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                    for snapshot in batch.snapshots:
                        await workflow.execute_activity(
                            upsert_odds_snapshot,
                            snapshot,
                            start_to_close_timeout=timedelta(seconds=10),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )

                    kalshi_batch = await workflow.execute_activity(
                        fetch_kalshi_odds_batch,
                        args=[games, sport],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                    for snapshot in kalshi_batch.snapshots:
                        await workflow.execute_activity(
                            upsert_odds_snapshot,
                            snapshot,
                            start_to_close_timeout=timedelta(seconds=10),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )

                    polymarket_batch = await workflow.execute_activity(
                        fetch_polymarket_odds_batch,
                        games,
                        start_to_close_timeout=timedelta(seconds=60),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                    for snapshot in polymarket_batch.snapshots:
                        await workflow.execute_activity(
                            upsert_odds_snapshot,
                            snapshot,
                            start_to_close_timeout=timedelta(seconds=10),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )

            await workflow.sleep(timedelta(minutes=interval_minutes))
            workflow.continue_as_new(args=[interval_minutes])
