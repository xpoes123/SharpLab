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
        FetchPollSnapshotInput,
        OddsSnapshot,
        FetchCloseSnapshotInput
    )
    
@workflow.defn
class OddsPollingWorkflow:
    """
    Durable Loop:
        - Fetch today's games
        - Snapshot odds
        - persist
        -sleep interval
    """
    @workflow.run
    async def run(self, interval_minutes: int = 1) -> None:
        while True:
            games = await workflow.execute_activity(
                fetch_games_for_today,
                start_to_close_timeout=timedelta(seconds=10),
            )

            for g in games:
                start_dt = datetime.fromisoformat(g.start_time_utc_iso)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                if start_dt <= workflow.now():
                    continue
                close_wf_id = f"close-capture-{g.game_id}"
                
                try:
                    await workflow.start_child_workflow(
                        "CloseCaptureWorkflow",
                        (g.game_id, g.start_time_utc_iso),
                        id=close_wf_id,
                        task_queue=workflow.info().task_queue,
                        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    )
                    workflow.logger.info(f"Started CloseCaptureWorkflow for game {g.game_id} with workflow id {close_wf_id}")
                except WorkflowAlreadyStartedError:
                    pass
            
            game_ids = [g.game_id for g in games]
            
            now = workflow.now()
            bucket = now.replace(second=0, microsecond=0)
            snapshot_id = f"poll:{g.game_id}:{bucket.isoformat()}"
            
            batch = await workflow.execute_activity(
                fetch_odds_batch,
                FetchPollSnapshotInput(snapshot_id=snapshot_id, game_ids=game_ids),
                start_to_close_timeout=timedelta(seconds=10)
            )
            
            for gid in game_ids:
                per_game_snapshot = OddsSnapshot(
                    snapshot_id=f"{snapshot_id}:{gid}",
                    kind="poll",
                    game_id=gid,
                    source=batch.source,
                    captured_at_utc_iso=batch.captured_at_utc_iso,
                    payload=batch.games.get(gid, {}),
                )
                
                await workflow.execute_activity(
                    upsert_odds_snapshot,
                    per_game_snapshot,
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )

            await workflow.sleep(timedelta(minutes=interval_minutes))

@workflow.defn
class CloseCaptureWorkflow:
    """
    One workflow per game.
    Sleeps until start time, then captures one final 'close' snapshot
    """
    @workflow.run
    async def run(self, args: tuple[str, str]) -> None:
        game_id, start_time_utc_iso = args
        
        snapshot_id = f"close:{game_id}"
        # Parse timestamp in a determinisitic-safe way
        # We avoid datetime.now() in workflows. We only use provided inputs + workflow time
        start_dt = datetime.fromisoformat(start_time_utc_iso)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
            
        delay = start_dt - workflow.now()
        
        if delay.total_seconds() > 0:
            await workflow.sleep(delay)
        
        snapshot = await workflow.execute_activity(
            fetch_close_odds_snapshot,
            FetchCloseSnapshotInput(snapshot_id, game_id),
            start_to_close_timeout=timedelta(seconds=10)
        )
        
        await workflow.execute_activity(
            upsert_odds_snapshot,
            snapshot,
            start_to_close_timeout=timedelta(seconds=10)
        )