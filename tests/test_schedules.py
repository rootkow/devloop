"""Tests for the Temporal Schedule reconciliation in ``schedules.py``.

``ensure_schedules`` runs on every worker startup and must converge an existing
schedule to the code-defined desired state — otherwise config changes (e.g. a
gate timeout sourced from the worker environment) would never reach the nightly
sweep, since the first ``create_schedule`` would win forever.

Strategy: a fake Temporal client records ``create_schedule`` calls and hands out
a fake schedule handle whose ``update`` captures and invokes the updater, so we
can assert both the create path and the in-place update path without a server.
"""

from __future__ import annotations

import pytest
from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdateInput,
)

from devloop import schedules
from devloop.dev_loop import DevLoopInput
from devloop.projects import ProjectConfig


def _project(project_id: str = "omneval") -> ProjectConfig:
    return ProjectConfig(
        id=project_id,
        github_url=f"https://github.com/omneval/{project_id}",
        default_branch="main",
        agent_image="ghcr.io/example/agent:sha-abc",
        agent_label="agent-ready",
        discord_channel="agent-approvals",
        omneval_ingest_secret="omneval-ingest",
        github_token_secret="omneval-agent-github-token",
    )


class _FakeHandle:
    """Captures the updater passed to ``ScheduleHandle.update`` and runs it
    against a supplied current schedule (mimicking what the server would feed)."""

    def __init__(self, current: Schedule):
        self._current = current
        self.applied: Schedule | None = None

    async def update(self, updater):
        result = updater(ScheduleUpdateInput(description=_FakeDescription(self._current)))
        self.applied = result.schedule


class _FakeDescription:
    def __init__(self, schedule: Schedule):
        self.schedule = schedule


class _FakeClient:
    """Records create_schedule calls; create may be configured to raise
    AlreadyRunning so the update path is exercised."""

    def __init__(self, *, already_running: bool = False, current: Schedule | None = None):
        self._already_running = already_running
        self._current = current
        self.created: list[tuple[str, Schedule]] = []
        self.handles: dict[str, _FakeHandle] = {}

    async def create_schedule(self, schedule_id, schedule, **kwargs):
        if self._already_running:
            raise ScheduleAlreadyRunningError()
        self.created.append((schedule_id, schedule))
        return schedule_id

    def get_schedule_handle(self, schedule_id):
        handle = _FakeHandle(self._current)
        self.handles[schedule_id] = handle
        return handle


def _desired_schedule(gate_timeout: float = 42.0) -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            "DevLoopWorkflow",
            DevLoopInput(project_id="omneval", gate_timeout_seconds=gate_timeout),
            id="devloop-nightly-omneval",
            task_queue="q",
        ),
        spec=ScheduleSpec(),
    )


@pytest.mark.asyncio
async def test_ensure_creates_when_absent():
    client = _FakeClient(already_running=False)
    await schedules._ensure(client, "devloop-nightly-omneval", _desired_schedule())
    assert [sid for sid, _ in client.created] == ["devloop-nightly-omneval"]
    assert client.handles == {}  # update path never touched


@pytest.mark.asyncio
async def test_ensure_updates_when_present_preserving_pause_state():
    # Live schedule the operator has paused (e.g. pause-on-failure) carrying a
    # stale gate timeout; the desired schedule has a new one.
    current = Schedule(
        action=ScheduleActionStartWorkflow(
            "DevLoopWorkflow",
            DevLoopInput(project_id="omneval", gate_timeout_seconds=9999.0),
            id="devloop-nightly-omneval",
            task_queue="q",
        ),
        spec=ScheduleSpec(),
        state=ScheduleState(paused=True, note="paused by operator"),
    )
    client = _FakeClient(already_running=True, current=current)

    await schedules._ensure(
        client, "devloop-nightly-omneval", _desired_schedule(gate_timeout=42.0)
    )

    assert client.created == []  # create raised AlreadyRunning
    applied = client.handles["devloop-nightly-omneval"].applied
    assert applied is not None
    # New config reached the action args...
    assert applied.action.args[0].gate_timeout_seconds == 42.0
    # ...while operator-owned runtime state was preserved.
    assert applied.state.paused is True
    assert applied.state.note == "paused by operator"


@pytest.mark.asyncio
async def test_ensure_schedules_wires_env_timeout_into_schedule(monkeypatch):
    """End-to-end: an env-configured gate timeout flows through from_env into the
    nightly schedule's workflow input."""
    monkeypatch.setenv("GATE_TIMEOUT_SECONDS", "600")
    client = _FakeClient(already_running=False)

    await schedules.ensure_schedules(client, [_project("omneval")])

    nightly = next(
        sched for sid, sched in client.created if sid == "devloop-nightly-omneval"
    )
    assert nightly.action.args[0].gate_timeout_seconds == 600.0
