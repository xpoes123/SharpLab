"""Unit tests for idempotent workflow bootstrapping."""
import pytest
from unittest.mock import AsyncMock

from temporal.bootstrap import LONG_RUNNING_WORKFLOWS, ensure_workflows


class _FakeAlreadyStarted(Exception):
    """Stand-in matched by class name (mirrors temporalio's error)."""


_FakeAlreadyStarted.__name__ = "WorkflowAlreadyStartedError"


@pytest.mark.asyncio
async def test_ensure_starts_every_workflow():
    client = AsyncMock()
    await ensure_workflows(client, "queue")
    assert client.start_workflow.await_count == len(LONG_RUNNING_WORKFLOWS)
    started_ids = {call.kwargs["id"] for call in client.start_workflow.await_args_list}
    assert started_ids == {wf_id for _, wf_id, _ in LONG_RUNNING_WORKFLOWS}


@pytest.mark.asyncio
async def test_already_running_is_not_fatal():
    client = AsyncMock()
    client.start_workflow.side_effect = _FakeAlreadyStarted()
    # Should swallow the already-started error for every workflow, no raise.
    await ensure_workflows(client, "queue")
    assert client.start_workflow.await_count == len(LONG_RUNNING_WORKFLOWS)


@pytest.mark.asyncio
async def test_one_failure_does_not_block_the_rest():
    client = AsyncMock()
    client.start_workflow.side_effect = [RuntimeError("boom")] + [
        None
    ] * (len(LONG_RUNNING_WORKFLOWS) - 1)
    await ensure_workflows(client, "queue")  # must not raise
    assert client.start_workflow.await_count == len(LONG_RUNNING_WORKFLOWS)


def test_specs_have_unique_ids_and_both_sports():
    ids = [wf_id for _, wf_id, _ in LONG_RUNNING_WORKFLOWS]
    assert len(ids) == len(set(ids))
    odds = {wf_id for t, wf_id, _ in LONG_RUNNING_WORKFLOWS if t == "OddsPollingWorkflow"}
    assert odds == {"odds-polling-nba-v2", "odds-polling-mlb-v2"}
