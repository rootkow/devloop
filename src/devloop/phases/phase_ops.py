"""PhaseOps — shared helper methods for Dev Loop phase modules.

Consolidates helper methods that were duplicated across ExecutePhase,
ReviewPhase, ReviewFixPass, CICycle, and Notifier so they live in a
single deep module with a small interface::

    ops = PhaseOps()
    ops.as_int(value)
    await ops.comment(project_id, issue_number, body)
    await ops.cleanup(job_name)

Every method accepts an injectable callback so tests can inject mocks.
When the callback is ``None``, the default Temporal activity path is used.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Callable, Coroutine, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

from .._constants import _DISPATCH_TIMEOUT, _GITHUB_COMMENT_TIMEOUT, _RETRY
from ..shared import AgentJobResult, DispatchInput, GithubNotificationInput

log = logging.getLogger(__name__)


class PhaseOps:
    """Stateless collection of shared helpers for phase modules.

    Instantiate (``PhaseOps()``) and call methods directly — no state
    is kept between calls.
    """

    # ------------------------------------------------------------------
    # _as_int
    # ------------------------------------------------------------------

    def as_int(self, value: Any) -> int:
        """Safely convert *value* to ``int``, returning ``0`` on failure."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------------
    # _comment
    # ------------------------------------------------------------------

    async def comment(
        self,
        project_id: str,
        issue_number: int,
        body: str,
        *,
        callback: Optional[Callable[[str, int, str], Coroutine[Any, Any, None]]] = None,
        timeout: Optional[timedelta] = None,
        retry_policy: Optional[RetryPolicy] = None,
    ) -> None:
        """Post a GitHub Issue / PR comment.

        When *callback* is provided it is called directly; otherwise the
        ``post_github_comment`` Temporal activity is invoked.
        """
        if callback is not None:
            await callback(project_id, issue_number, body)
            return
        await workflow.execute_activity(
            "post_github_comment",
            GithubNotificationInput(
                issue_number=issue_number,
                project_id=project_id,
                body=body,
            ),
            start_to_close_timeout=timeout or _GITHUB_COMMENT_TIMEOUT,
            retry_policy=retry_policy or _RETRY,
        )

    # ------------------------------------------------------------------
    # _cleanup
    # ------------------------------------------------------------------

    async def cleanup(
        self,
        job_name: str,
        *,
        callback: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None,
    ) -> None:
        """Delete the output ConfigMap for a completed job.

        Fire-and-forget: failures are logged, never raised.
        """
        if callback is not None:
            await callback(job_name)
            return
        if not job_name:
            return
        try:
            await workflow.execute_activity(
                "cleanup_configmap",
                job_name,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except Exception:  # noqa: BLE001
            workflow.logger.warning("cleanup_configmap failed for %s", job_name)

    # ------------------------------------------------------------------
    # _dispatch_helper
    # ------------------------------------------------------------------

    async def dispatch_helper(
        self,
        project_id: str,
        spec: Any,  # TaskSpec
        issue_number: int,
        poll_interval_seconds: float,
        *,
        dispatch_callback: Optional[
            Callable[[str, Any, int, float], Coroutine[Any, Any, AgentJobResult]]
        ] = None,
        activity_name: str = "dispatch_agent_job",
    ) -> AgentJobResult:
        """Generic dispatch: check callback first, fall back to Temporal activity.

        Parameters
        ----------
        project_id : str
            Target repository owner / name.
        spec : TaskSpec
            The task specification to pass to the dispatch activity.
        issue_number : int
            GitHub issue number.
        poll_interval_seconds : float
            How often to poll the job for status.
        dispatch_callback : callable, optional
            When provided it is invoked directly with the same arguments.
        activity_name : str
            Temporal activity name (default ``dispatch_agent_job``).
        """
        if dispatch_callback is not None:
            return await dispatch_callback(
                project_id, spec, issue_number, poll_interval_seconds
            )
        return await workflow.execute_activity(
            activity_name,
            DispatchInput(
                project_id,
                issue_number,
                spec,
                poll_interval_seconds=poll_interval_seconds,
            ),
            result_type=AgentJobResult,
            start_to_close_timeout=_DISPATCH_TIMEOUT,
            retry_policy=_RETRY,
        )
