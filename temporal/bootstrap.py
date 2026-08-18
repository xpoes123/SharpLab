"""Idempotently ensure the long-running pipeline workflows are running.

These workflows (odds polling, injury polling, bet resolution) are singletons
keyed by a fixed workflow id and kept alive via continue_as_new. Historically
they were kicked off by hand (`just poll mlb`, etc.), so a Temporal restart or a
terminated workflow could silently leave a sport's slate un-ingested — which is
exactly how a pick'em slate once showed up with only the handful of games that
happened to be in the table. The worker calls `ensure_workflows` on startup, so
every deploy/restart re-establishes the full set without manual intervention.
"""

from __future__ import annotations

import logging

from temporalio.client import Client

log = logging.getLogger(__name__)

# (workflow type, fixed id, positional args). Mirrors `just dev` / the start_* scripts.
LONG_RUNNING_WORKFLOWS: list[tuple[str, str, list]] = [
    ("OddsPollingWorkflow", "odds-polling-nba-v2", [30, "nba"]),
    ("OddsPollingWorkflow", "odds-polling-mlb-v2", [30, "mlb"]),
    ("OddsPollingWorkflow", "odds-polling-nfl-v2", [30, "nfl"]),
    ("InjuryPollingWorkflow", "injury-polling-v1", [5]),
    ("BetResolutionWorkflow", "bet-resolution-nba-v2", [2, "nba"]),
    ("BetResolutionWorkflow", "bet-resolution-mlb-v2", [2, "mlb"]),
    ("BetResolutionWorkflow", "bet-resolution-nfl-v2", [2, "nfl"]),
]


async def ensure_workflows(client: Client, task_queue: str) -> None:
    """Start each long-running workflow if it isn't already running.

    `start_workflow` raises WorkflowAlreadyStartedError when the id is already
    running; we treat that as success (the desired state already holds)."""
    for wf_type, wf_id, args in LONG_RUNNING_WORKFLOWS:
        try:
            await client.start_workflow(wf_type, args=args, id=wf_id, task_queue=task_queue)
            log.info("ensured workflow %s (%s)", wf_id, wf_type)
        except Exception as exc:
            # WorkflowAlreadyStartedError is the common, expected case. Any other
            # failure shouldn't take the worker down — log and keep going so one
            # bad workflow can't block the rest from being ensured.
            if exc.__class__.__name__ == "WorkflowAlreadyStartedError":
                log.info("workflow %s already running", wf_id)
            else:
                log.warning("could not ensure workflow %s: %r", wf_id, exc)
