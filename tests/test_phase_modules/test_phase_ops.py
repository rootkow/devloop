"""Unit tests for devloop.phases.phase_ops — PhaseOps shared module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from devloop.phases.phase_ops import PhaseOps
from devloop.shared import GithubNotificationInput


class TestPhaseOpsAsInt:
    """PhaseOps.as_int — safe int conversion."""

    def test_as_int_with_valid_int(self) -> None:
        """PhaseOps.as_int returns the int as-is."""
        from devloop.phases.phase_ops import PhaseOps

        assert PhaseOps().as_int(42) == 42

    def test_as_int_with_string_number(self) -> None:
        """PhaseOps.as_int parses a numeric string."""
        from devloop.phases.phase_ops import PhaseOps

        assert PhaseOps().as_int("123") == 123

    def test_as_int_with_non_numeric_string_returns_zero(self) -> None:
        """PhaseOps.as_int returns 0 for non-numeric strings."""
        from devloop.phases.phase_ops import PhaseOps

        assert PhaseOps().as_int("abc") == 0

    def test_as_int_with_none_returns_zero(self) -> None:
        """PhaseOps.as_int returns 0 for None."""
        from devloop.phases.phase_ops import PhaseOps

        assert PhaseOps().as_int(None) == 0

    def test_as_int_with_float_string_returns_zero(self) -> None:
        """PhaseOps.as_int returns 0 for float-like strings (int() raises)."""
        from devloop.phases.phase_ops import PhaseOps

        assert PhaseOps().as_int("3.7") == 0

    def test_as_int_with_float_returns_int(self) -> None:
        """PhaseOps.as_int converts a float to int."""
        assert PhaseOps().as_int(3.7) == 3


class TestPhaseOpsComment:
    """PhaseOps.comment — posts a GitHub Issue / PR comment."""

    @pytest.mark.asyncio
    async def test_comment_calls_callback_when_provided(self) -> None:
        """PhaseOps.comment invokes the callback directly."""
        cb = AsyncMock()
        await PhaseOps().comment("proj", 42, "hello", callback=cb)
        cb.assert_awaited_once_with("proj", 42, "hello")

    @pytest.mark.asyncio
    async def test_comment_uses_temporal_activity_when_no_callback(self) -> None:
        """PhaseOps.comment falls through to workflow.execute_activity."""
        ops = PhaseOps()
        activity_args: list[tuple] = []

        async def fake_execute_activity(name, payload, **kwargs):  # noqa: ANN001, ANN002
            activity_args.append((name, payload, kwargs))
            return None

        with patch(
            "devloop.phases.phase_ops.workflow.execute_activity",
            fake_execute_activity,
        ):
            await ops.comment("proj", 42, "hello")

        assert len(activity_args) == 1
        name, payload, kwargs = activity_args[0]
        assert name == "post_github_comment"
        assert isinstance(payload, GithubNotificationInput)
        assert payload.project_id == "proj"
        assert payload.issue_number == 42
        assert payload.body == "hello"

    @pytest.mark.asyncio
    async def test_cleanup_calls_callback_when_provided(self) -> None:
        """PhaseOps.cleanup invokes the callback directly."""
        cb = AsyncMock()
        await PhaseOps().cleanup("some-job", callback=cb)
        cb.assert_awaited_once_with("some-job")

    @pytest.mark.asyncio
    async def test_cleanup_empty_job_name_returns_early(self) -> None:
        """PhaseOps.cleanup does nothing when job_name is empty."""
        activity_called = []

        async def fake_act(name, payload, **kwargs):
            activity_called.append(True)

        with patch(
            "devloop.phases.phase_ops.workflow.execute_activity",
            fake_act,
        ):
            await PhaseOps().cleanup("", callback=None)

        assert len(activity_called) == 0

    @pytest.mark.asyncio
    async def test_cleanup_uses_temporal_activity_when_no_callback(self) -> None:
        """PhaseOps.cleanup falls through to workflow.execute_activity."""
        activity_called = []

        async def fake_act(name, payload, **kwargs):
            activity_called.append((name, payload, kwargs))

        with patch(
            "devloop.phases.phase_ops.workflow.execute_activity",
            fake_act,
        ):
            await PhaseOps().cleanup("my-job", callback=None)

        assert len(activity_called) == 1
        name, payload, kwargs = activity_called[0]
        assert name == "cleanup_configmap"
        assert payload == "my-job"


class TestPhaseOpsDispatch:
    """PhaseOps.dispatch_helper — generic dispatch with callback fallback."""

    @pytest.mark.asyncio
    async def test_dispatch_calls_callback_when_provided(self) -> None:
        """PhaseOps.dispatch_helper invokes the callback directly."""
        mock_result = MagicMock(status="complete", job_name="test-job", commits=0)
        mock_spec = MagicMock()
        cb = AsyncMock(return_value=mock_result)
        returned = await PhaseOps().dispatch_helper(
            "proj", mock_spec, 42, 5.0, dispatch_callback=cb
        )
        cb.assert_awaited_once_with("proj", mock_spec, 42, 5.0)
        assert returned.status == "complete"

    @pytest.mark.asyncio
    async def test_dispatch_uses_temporal_activity_when_no_callback(self) -> None:
        """PhaseOps.dispatch_helper falls through to workflow.execute_activity."""
        activity_called = []

        class _FakeResult:
            status = "complete"
            job_name = "my-job"
            commits = 0
            branch = "feat/1"

        async def fake_act(name, payload, **kwargs):  # noqa: ANN001, ANN002
            activity_called.append((name, payload, kwargs))
            return _FakeResult()

        with patch(
            "devloop.phases.phase_ops.workflow.execute_activity",
            fake_act,
        ):
            await PhaseOps().dispatch_helper(
                "proj", MagicMock(), 42, 5.0, dispatch_callback=None
            )

        assert len(activity_called) == 1
        name, payload, kwargs = activity_called[0]
        assert name == "dispatch_agent_job"
        assert payload.project_id == "proj"
        assert payload.issue_number == 42