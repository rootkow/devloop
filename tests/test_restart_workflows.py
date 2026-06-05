"""Tests for scripts/restart_workflows.py.

Covers IssueChecker, WebhookPoster, restart_project orchestration, and
dry-run / skip / error paths.  All HTTP is intercepted by httpx.MockTransport
so no network access is required.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from scripts.restart_workflows import (
    IssueChecker,
    OpenIssuesResult,
    TriggerResult,
    WebhookPoster,
    restart_project,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO = "owner/myproject"
_LABEL = "agent-ready"
_WEBHOOK_URL = "http://temporal-worker.example.com/webhook/github"
_TOKEN = "ghp_fake_token"


def _issue_transport(*issue_numbers: int) -> httpx.MockTransport:
    """Return a transport that responds with the given issue numbers."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"number": n, "title": f"Issue {n}"} for n in issue_numbers],
        )

    return httpx.MockTransport(handler)


def _webhook_transport(status: int = 200, body: dict | None = None) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """Return (transport, captured_requests) that returns the given status."""
    captured: list[httpx.Request] = []
    response_body = body or {"workflow_id": "devloop-myproject", "project": "myproject", "issue": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status, json=response_body)

    return httpx.MockTransport(handler), captured


# ---------------------------------------------------------------------------
# IssueChecker
# ---------------------------------------------------------------------------


class TestIssueChecker:
    def test_returns_open_issue_numbers(self):
        client = httpx.Client(transport=_issue_transport(42, 43))
        result = IssueChecker(client).fetch_open(_REPO, _LABEL, _TOKEN)
        assert isinstance(result, OpenIssuesResult)
        assert result.repo == _REPO
        assert result.count == 2
        assert result.numbers == [42, 43]

    def test_empty_list_when_no_issues(self):
        client = httpx.Client(transport=_issue_transport())
        result = IssueChecker(client).fetch_open(_REPO, _LABEL, _TOKEN)
        assert result.count == 0
        assert result.numbers == []

    def test_raises_on_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "Forbidden"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            IssueChecker(client).fetch_open(_REPO, _LABEL, "bad_token")

    def test_passes_label_in_query_params(self):
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=[])

        client = httpx.Client(transport=httpx.MockTransport(handler))
        IssueChecker(client).fetch_open(_REPO, "custom-label", _TOKEN)
        assert "custom-label" in str(captured[0].url)


# ---------------------------------------------------------------------------
# WebhookPoster
# ---------------------------------------------------------------------------


class TestWebhookPoster:
    def test_posts_labeled_action_payload(self):
        transport, captured = _webhook_transport()
        client = httpx.Client(transport=transport)
        status, body = WebhookPoster(client).post(_WEBHOOK_URL, _REPO, _LABEL)
        assert status == 200
        assert body["workflow_id"] == "devloop-myproject"
        req = captured[0]
        payload = json.loads(req.content)
        assert payload["action"] == "labeled"
        assert payload["label"]["name"] == _LABEL
        assert payload["repository"]["full_name"] == _REPO
        assert req.headers["X-GitHub-Event"] == "issues"

    def test_signs_payload_when_secret_provided(self):
        transport, captured = _webhook_transport()
        client = httpx.Client(transport=transport)
        WebhookPoster(client).post(_WEBHOOK_URL, _REPO, _LABEL, webhook_secret="s3cr3t")
        req = captured[0]
        sig_header = req.headers.get("x-hub-signature-256", "")
        assert sig_header.startswith("sha256=")
        expected = hmac.new("s3cr3t".encode(), req.content, hashlib.sha256).hexdigest()
        assert sig_header == f"sha256={expected}"

    def test_no_signature_header_without_secret(self):
        transport, captured = _webhook_transport()
        client = httpx.Client(transport=transport)
        WebhookPoster(client).post(_WEBHOOK_URL, _REPO, _LABEL)
        assert "x-hub-signature-256" not in captured[0].headers

    def test_returns_status_and_body(self):
        transport, _ = _webhook_transport(status=202, body={"queued": True})
        client = httpx.Client(transport=transport)
        status, body = WebhookPoster(client).post(_WEBHOOK_URL, _REPO, _LABEL)
        assert status == 202
        assert body == {"queued": True}


# ---------------------------------------------------------------------------
# restart_project orchestration
# ---------------------------------------------------------------------------


def _make_checker(issue_numbers: list[int]) -> IssueChecker:
    return IssueChecker(httpx.Client(transport=_issue_transport(*issue_numbers)))


def _make_poster(status: int = 200, body: dict | None = None) -> tuple[WebhookPoster, list[httpx.Request]]:
    transport, captured = _webhook_transport(status, body)
    return WebhookPoster(httpx.Client(transport=transport)), captured


class TestRestartProject:
    def test_triggers_when_open_issues_exist(self):
        checker = _make_checker([1, 2])
        poster, captured = _make_poster()
        result = restart_project(
            checker, poster, _WEBHOOK_URL, _REPO, _LABEL, _TOKEN, "", dry_run=False
        )
        assert result.success
        assert result.open_issues == 2
        assert result.workflow_id == "devloop-myproject"
        assert len(captured) == 1

    def test_skips_when_no_open_issues(self):
        checker = _make_checker([])
        poster, captured = _make_poster()
        result = restart_project(
            checker, poster, _WEBHOOK_URL, _REPO, _LABEL, _TOKEN, "", dry_run=False
        )
        assert result.skipped
        assert result.open_issues == 0
        assert not result.success
        assert len(captured) == 0  # webhook not called

    def test_dry_run_does_not_post(self):
        checker = _make_checker([5, 6, 7])
        poster, captured = _make_poster()
        result = restart_project(
            checker, poster, _WEBHOOK_URL, _REPO, _LABEL, _TOKEN, "", dry_run=True
        )
        assert result.skipped
        assert result.open_issues == 3
        assert len(captured) == 0

    def test_error_on_non_2xx_webhook_response(self):
        checker = _make_checker([1])
        poster, _ = _make_poster(status=500, body={"detail": "internal error"})
        result = restart_project(
            checker, poster, _WEBHOOK_URL, _REPO, _LABEL, _TOKEN, "", dry_run=False
        )
        assert not result.success
        assert result.error == "HTTP 500"

    def test_triggers_without_github_token(self):
        checker = _make_checker([])  # would return no issues, but not called
        poster, captured = _make_poster()
        result = restart_project(
            checker, poster, _WEBHOOK_URL, _REPO, _LABEL,
            github_token=None, webhook_secret="", dry_run=False
        )
        assert result.success
        assert result.open_issues == -1  # check was skipped
        assert len(captured) == 1

    def test_uses_returned_workflow_id(self):
        checker = _make_checker([1])
        poster, _ = _make_poster(body={"workflow_id": "devloop-custom-id"})
        result = restart_project(
            checker, poster, _WEBHOOK_URL, _REPO, _LABEL, _TOKEN, "", dry_run=False
        )
        assert result.workflow_id == "devloop-custom-id"

    def test_success_property_reflects_2xx(self):
        for status in (200, 201, 204):
            checker = _make_checker([1])
            poster, _ = _make_poster(status=status)
            result = restart_project(
                checker, poster, _WEBHOOK_URL, _REPO, _LABEL, _TOKEN, "", dry_run=False
            )
            assert result.success, f"expected success for HTTP {status}"
