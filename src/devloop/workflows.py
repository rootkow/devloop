"""Workflow and activity definitions for the homelab orchestration worker.

This module is scanned by the Temporal workflow sandbox and must not import
any non-deterministic or restricted modules (threading, http, etc.).
"""

from datetime import timedelta

from temporalio import activity, workflow


@activity.defn
async def noop_activity() -> str:
    activity.logger.info("noop_activity invoked")
    return "ok"


@workflow.defn
class NoopWorkflow:
    @workflow.run
    async def run(self) -> str:
        return await workflow.execute_activity(
            noop_activity,
            schedule_to_close_timeout=timedelta(seconds=30),
        )
