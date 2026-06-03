"""Messaging platform abstraction tests (issue #19).

Verifies the MessagingPlatform protocol and the generic activity wrapper
that any messaging bridge (Discord, Slack, etc.) must conform to.
"""

from __future__ import annotations

from devloop.messaging import (
    ArchiveThreadInput,
    MessagingActivities,
    MessagingPlatform,
    SendMessageInput,
    SendMessageOutput,
    SendNotificationInput,
    StubPlatform,
)


# --------------------------------------------------------------------------- #
# Protocol conformance: StubPlatform satisfies MessagingPlatform
# --------------------------------------------------------------------------- #

def test_stub_platform_is_a_messaging_platform():
    """A minimal stub implementation should satisfy the MessagingPlatform
    protocol so consumers can verify conformance at runtime."""
    stub = StubPlatform()
    assert isinstance(stub, MessagingPlatform)


def test_stub_open_thread_returns_thread_id():
    stub = StubPlatform()
    tid = stub.open_thread("approvals", "test-thread", "hello")
    assert isinstance(tid, str)
    assert len(tid) > 0


def test_stub_post_to_thread_succeeds():
    stub = StubPlatform()
    stub.post_to_thread("thread-123", "notification text")  # no exception


def test_stub_archive_thread_succeeds():
    stub = StubPlatform()
    stub.archive_thread("thread-123")  # no exception


# --------------------------------------------------------------------------- #
# MessagingActivities wraps any MessagingPlatform
# --------------------------------------------------------------------------- #

def test_messaging_activities_exposes_send_message():
    """The activity wrapper should expose the three core Temporal activities."""
    platform = StubPlatform()
    acts = MessagingActivities(platform)
    assert hasattr(acts, "send_message")
    assert hasattr(acts, "send_notification")
    assert hasattr(acts, "archive_thread")


def test_send_message_activity_calls_open_thread_and_returns_output():
    """send_message activity delegates to platform.open_thread and wraps the
    result in SendMessageOutput."""
    stub = StubPlatform()
    acts = MessagingActivities(stub)

    inp = SendMessageInput(
        workflow_id="wf-001",
        message="plan ready",
        channel="approvals",
        thread_name="test-plan",
    )
    result = acts.send_message_sync(inp)
    assert isinstance(result, SendMessageOutput)
    assert isinstance(result.thread_id, str)
    assert len(result.thread_id) > 0


def test_send_notification_activity_calls_post_to_thread():
    stub = StubPlatform()
    acts = MessagingActivities(stub)

    inp = SendNotificationInput(workflow_id="wf-001", message="review done")
    acts.send_notification_sync(inp)  # no exception


def test_archive_thread_activity_calls_archive():
    stub = StubPlatform()
    acts = MessagingActivities(stub)

    inp = ArchiveThreadInput(workflow_id="wf-001")
    acts.archive_thread_sync(inp)  # no exception


# --------------------------------------------------------------------------- #
# Activity signatures round-trip correctly
# --------------------------------------------------------------------------- #

def test_send_message_input_roundtrip():
    """Activity input dataclasses carry the expected fields."""
    inp = SendMessageInput(
        workflow_id="wf-test",
        message="approve?",
        channel="alerts",
        thread_name="my-plan",
    )
    assert inp.workflow_id == "wf-test"
    assert inp.message == "approve?"
    assert inp.channel == "alerts"
    assert inp.thread_name == "my-plan"


def test_send_message_output_roundtrip():
    out = SendMessageOutput(thread_id="slack-abc123")
    assert out.thread_id == "slack-abc123"


def test_send_notification_input_roundtrip():
    inp = SendNotificationInput(workflow_id="wf-notify", message="deployed")
    assert inp.workflow_id == "wf-notify"
    assert inp.message == "deployed"


def test_archive_thread_input_roundtrip():
    inp = ArchiveThreadInput(workflow_id="wf-archive")
    assert inp.workflow_id == "wf-archive"


# --------------------------------------------------------------------------- #
# StubPlatform records calls for verification
# --------------------------------------------------------------------------- #

def test_stub_records_open_thread_calls():
    stub = StubPlatform()
    stub.open_thread("channel-1", "name", "msg")
    assert len(stub.calls["open_thread"]) == 1
    call = stub.calls["open_thread"][0]
    assert call["channel_name"] == "channel-1"
    assert call["thread_name"] == "name"
    assert call["initial_message"] == "msg"


def test_stub_records_post_to_thread_calls():
    stub = StubPlatform()
    stub.post_to_thread("t-1", "hello")
    assert len(stub.calls["post_to_thread"]) == 1
    assert stub.calls["post_to_thread"][0]["thread_id"] == "t-1"
    assert stub.calls["post_to_thread"][0]["message"] == "hello"


def test_stub_records_archive_thread_calls():
    stub = StubPlatform()
    stub.archive_thread("t-99")
    assert len(stub.calls["archive_thread"]) == 1
    assert stub.calls["archive_thread"][0]["thread_id"] == "t-99"


# --------------------------------------------------------------------------- #
# DiscordActivities: @activity.defn decorators present
# --------------------------------------------------------------------------- #

def _import_discord_activities():
    """Import DiscordActivities with the discord package stubbed out."""
    import sys
    from unittest.mock import MagicMock, patch

    discord_mock = MagicMock()
    discord_mock.Client = object  # real class so BotClient can subclass it
    discord_mock.Intents = MagicMock()

    with patch.dict(sys.modules, {"discord": discord_mock}):
        sys.modules.pop("devloop.messaging.discord_bot", None)
        from devloop.messaging.discord_bot import DiscordActivities
        sys.modules.pop("devloop.messaging.discord_bot", None)
    return DiscordActivities


def test_discord_activities_have_activity_defn():
    """DiscordActivities methods must carry @activity.defn so Temporal's Worker
    can register them.  Without the decorator the worker raises at startup."""
    DiscordActivities = _import_discord_activities()
    from unittest.mock import MagicMock

    bot = MagicMock()
    acts = DiscordActivities(bot)

    for method_name in ("send_message", "send_notification", "archive_thread"):
        method = getattr(acts, method_name)
        assert hasattr(method, "__temporal_activity_definition"), (
            f"DiscordActivities.{method_name} is missing @activity.defn"
        )


# --------------------------------------------------------------------------- #
# SlackActivities: thread store is written and read correctly
# --------------------------------------------------------------------------- #

def _make_slack_activities():
    """Import SlackActivities with slack_bolt stubbed out.

    Returns the class with platform packages mocked so tests can run without
    the Slack SDK installed.
    """
    import sys
    from unittest.mock import MagicMock, patch

    mocks = {
        "slack_bolt": MagicMock(),
        "slack_bolt.adapter": MagicMock(),
        "slack_bolt.adapter.socket_mode": MagicMock(),
    }
    with patch.dict(sys.modules, mocks):
        sys.modules.pop("devloop.messaging.slack_bot", None)
        from devloop.messaging.slack_bot import SlackActivities
        sys.modules.pop("devloop.messaging.slack_bot", None)
    return SlackActivities


def test_slack_activities_writes_thread_store_on_send_message():
    """After send_message, _thread_store must contain the workflow→thread mapping
    so that handle_message can route replies and pod restarts don't lose threads."""
    from unittest.mock import MagicMock
    from devloop.messaging.core import SendMessageInput

    SlackActivities = _make_slack_activities()

    bot = MagicMock()
    bot.open_thread.return_value = "C123:1700000000.000100"
    bot.post_to_thread.return_value = None

    store = MagicMock()
    store.get_thread.return_value = None  # no pre-existing mapping

    acts = SlackActivities(bot, thread_store=store)
    inp = SendMessageInput(
        workflow_id="wf-slack-001",
        message="approve?",
        channel="approvals",
        thread_name="test-plan",
    )
    result = acts.send_message(inp)

    # Durable store must have been written with thread_ts as the reverse key
    store.put.assert_called_once_with(
        "wf-slack-001",
        "C123:1700000000.000100",
        reverse_key="1700000000.000100",
    )
    assert result.thread_id == "C123:1700000000.000100"


def test_slack_activities_restores_thread_from_store_on_cache_miss():
    """On pod restart, _messaging._thread_map is empty.  send_message must
    restore the existing thread from _thread_store instead of opening a new one."""
    from unittest.mock import MagicMock
    from devloop.messaging.core import SendMessageInput

    SlackActivities = _make_slack_activities()

    bot = MagicMock()
    bot.post_to_thread.return_value = None

    store = MagicMock()
    # Simulate a previously stored mapping surviving a pod restart
    store.get_thread.return_value = "C123:1700000000.000100"

    acts = SlackActivities(bot, thread_store=store)
    inp = SendMessageInput(
        workflow_id="wf-slack-001",
        message="follow-up",
        channel="approvals",
        thread_name="test-plan",
    )
    result = acts.send_message(inp)

    # Must reuse the existing thread, not open a new one
    bot.open_thread.assert_not_called()
    bot.post_to_thread.assert_called_once_with(
        "C123:1700000000.000100", "follow-up"
    )
    assert result.thread_id == "C123:1700000000.000100"


def test_slack_activities_archive_deletes_from_store():
    """archive_thread must remove the mapping from _thread_store."""
    from unittest.mock import MagicMock
    from devloop.messaging.core import ArchiveThreadInput, SendMessageInput

    SlackActivities = _make_slack_activities()

    bot = MagicMock()
    bot.open_thread.return_value = "C123:1700000000.000100"
    bot.post_to_thread.return_value = None

    store = MagicMock()
    store.get_thread.return_value = None

    acts = SlackActivities(bot, thread_store=store)
    acts.send_message(
        SendMessageInput(workflow_id="wf-slack-002", message="hi",
                         channel="approvals", thread_name="")
    )
    acts.archive_thread(ArchiveThreadInput(workflow_id="wf-slack-002"))

    store.delete.assert_called_once_with("wf-slack-002")
