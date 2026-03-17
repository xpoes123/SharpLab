import asyncio
from datetime import timedelta, datetime, timezone
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from temporalio.client import WorkflowFailureError
from temporal.workflows import CloseCaptureWorkflow, OddsPollingWorkflow
from temporal.activities import FetchCloseSnapshotInput
from shared.models import Game, OddsBatch, OddsSnapshot


# ── CloseCaptureWorkflow ───────────────────────────────────────────────────────

close_upsert_calls: list[OddsSnapshot] = []


@activity.defn(name="fetch_close_odds_snapshot")
async def stub_fetch_close(inp: FetchCloseSnapshotInput) -> list[OddsSnapshot]:
    return [OddsSnapshot(
        snapshot_id=inp.snapshot_id,
        kind="close",
        game_id=inp.game_id,
        source="draftkings",
        captured_at_utc_iso="2026-01-01T00:00:00+00:00",
        payload={"spread": -4.5, "spread_odds": -110},
    )]


@activity.defn(name="upsert_odds_snapshot")
async def stub_upsert_close(snapshot: OddsSnapshot) -> None:
    close_upsert_calls.append(snapshot)


async def test_close_capture_workflow():
    close_upsert_calls.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[CloseCaptureWorkflow],
            activities=[stub_fetch_close, stub_upsert_close],
        ):
            future_start = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
            await env.client.execute_workflow(
                CloseCaptureWorkflow.run,
                args=[("GAME123", future_start)],
                id="test-close-wf",
                task_queue="test-queue",
            )

    assert len(close_upsert_calls) == 1
    assert close_upsert_calls[0].game_id == "GAME123"
    assert close_upsert_calls[0].kind == "close"


async def test_close_capture_workflow_handles_none():
    """CloseCaptureWorkflow should exit cleanly when lines have already closed."""
    close_upsert_calls.clear()

    @activity.defn(name="fetch_close_odds_snapshot")
    async def stub_fetch_close_none(_inp: FetchCloseSnapshotInput) -> list[OddsSnapshot]:
        return []

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-queue-none",
            workflows=[CloseCaptureWorkflow],
            activities=[stub_fetch_close_none, stub_upsert_close],
        ):
            future_start = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
            await env.client.execute_workflow(
                CloseCaptureWorkflow.run,
                args=[("GAME_CLOSED", future_start)],
                id="test-close-wf-none",
                task_queue="test-queue-none",
            )

    assert len(close_upsert_calls) == 0


# ── OddsPollingWorkflow ────────────────────────────────────────────────────────

poll_upsert_calls: list[OddsSnapshot] = []
_one_iteration = asyncio.Event()


@activity.defn(name="fetch_games_for_today")
async def stub_fetch_games() -> list[Game]:
    # Past start time so no child workflow is spawned
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return [
        Game(
            game_id="GAME123",
            home_team="Boston Celtics",
            away_team="Los Angeles Lakers",
            start_time_utc_iso=past,
        )
    ]


@activity.defn(name="fetch_odds_batch")
async def stub_fetch_odds(_game_ids: list[str]) -> OddsBatch:
    now = datetime.now(timezone.utc).isoformat()
    snapshots = [
        OddsSnapshot(
            snapshot_id=f"poll:GAME123:draftkings:{now}",
            game_id="GAME123",
            kind="poll",
            source="draftkings",
            captured_at_utc_iso=now,
            payload={"spread": -3.0, "spread_odds": -110},
        )
    ]
    return OddsBatch(
        source="the-odds-api",
        captured_at_utc_iso=now,
        snapshots=snapshots,
    )


@activity.defn(name="upsert_odds_snapshot")
async def stub_poll_upsert(snapshot: OddsSnapshot) -> None:
    poll_upsert_calls.append(snapshot)
    _one_iteration.set()


async def test_odds_polling_one_iteration():
    poll_upsert_calls.clear()
    _one_iteration.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[OddsPollingWorkflow],
            activities=[stub_fetch_games, stub_fetch_odds, stub_poll_upsert],
        ):
            handle = await env.client.start_workflow(
                OddsPollingWorkflow.run,
                args=[1],
                id="test-polling-wf",
                task_queue="test-queue",
            )
            await _one_iteration.wait()
            await handle.cancel()
            try:
                await handle.result()
            except WorkflowFailureError:
                pass  # expected: workflow was cancelled

    assert len(poll_upsert_calls) >= 1
    assert poll_upsert_calls[0].kind == "poll"
    assert poll_upsert_calls[0].source == "draftkings"
