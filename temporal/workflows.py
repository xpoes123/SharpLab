from datetime import timedelta, datetime, timezone
from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

with workflow.unsafe.imports_passed_through():
    from .activities import (
        fetch_games_for_today,
        fetch_odds_batch,
        upsert_odds_snapshot,
        fetch_close_odds_snapshot,
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
    async def run(self, interval_minutes: int = 30) -> None:
        while True:
            # ── Step 1: get today's schedule ──────────────────────────────────
            games = await workflow.execute_activity(
                fetch_games_for_today,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            # ── Step 2: spawn CloseCaptureWorkflow per upcoming game ──────────
            game_ids: list[str] = []
            for game in games:
                game_ids.append(game.game_id)

                start_dt = datetime.fromisoformat(game.start_time_utc_iso)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                if start_dt <= workflow.now():
                    continue  # already tipped — don't start a new close-capture

                close_wf_id = f"close-capture-{game.game_id}"
                try:
                    await workflow.start_child_workflow(
                        "CloseCaptureWorkflow",
                        (game.game_id, game.start_time_utc_iso),
                        id=close_wf_id,
                        task_queue=workflow.info().task_queue,
                        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    )
                    workflow.logger.info(
                        f"Started CloseCaptureWorkflow for {game.away_team} @ {game.home_team} ({game.game_id})"
                    )
                except WorkflowAlreadyStartedError:
                    pass

            # ── Step 3: fetch odds for all today's games ──────────────────────
            batch = await workflow.execute_activity(
                fetch_odds_batch,
                game_ids,
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

            await workflow.sleep(timedelta(minutes=interval_minutes))


@workflow.defn
class CloseCaptureWorkflow:
    """
    One workflow per game. Sleeps until tip-off, then captures the closing line.
    """

    @workflow.run
    async def run(self, args: tuple[str, str]) -> None:
        game_id, start_time_utc_iso = args

        start_dt = datetime.fromisoformat(start_time_utc_iso)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)

        delay = start_dt - workflow.now()
        if delay.total_seconds() > 0:
            await workflow.sleep(delay)

        results = await workflow.execute_activity(
            fetch_close_odds_snapshot,
            FetchCloseSnapshotInput(
                snapshot_id=f"close:{game_id}",
                game_id=game_id,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        if not results:
            workflow.logger.warning(f"No close snapshot available for {game_id} — lines already closed")
            return

        await workflow.execute_activity(
            upsert_odds_snapshot,
            results[0],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
