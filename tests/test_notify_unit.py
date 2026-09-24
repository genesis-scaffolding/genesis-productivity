"""Unit tests for ``genesis.notify``.

Covers:

- ``NtfyConfig`` defaults and ``resolved_topic`` resolution.
- ``_safe_hostname`` hostname normalisation and fallback.
- ``NotifyFacade.send`` URL, method, body, and headers; error
  propagation; URL trailing-slash normalisation.
- ``messages`` builders for the three event kinds (started,
  milestone, finished) under aligned and unaligned sessions.

HTTP is mocked by monkeypatching ``urllib.request.urlopen`` so
we exercise the real call path (the focus page uses stdlib, not
``requests``). ``responses`` (dev dep) is for ``requests``-based
APIs and is not appropriate here.
"""

from __future__ import annotations

import urllib.error
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from genesis.category.enums import TimeLogCategory
from genesis.configs import (
    DEFAULT_NTFY_TOPIC_HOST,
    DEFAULT_NTFY_URL,
    NtfyConfig,
    _safe_hostname,
)
from genesis.entities import TimeLogEntry, TimeLogMode, TimeLogStatus
from genesis.notify import NotifyFacade, session_finished, session_milestone, session_started

# -----------------------------------------------------------------------------
# Fixtures and helpers
# -----------------------------------------------------------------------------


@pytest.fixture
def session_unaligned() -> TimeLogEntry:
    """An unaligned, countdown session in progress."""
    return TimeLogEntry(
        uid="t-1",
        label="Deep work",
        start=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        due=datetime(2026, 1, 1, 9, 25, tzinfo=UTC),
        mode=TimeLogMode.COUNTDOWN,
        status=TimeLogStatus.IN_PROGRESS,
        task_id=None,
        category=TimeLogCategory.WORK,
    )


@pytest.fixture
def session_aligned() -> TimeLogEntry:
    """A task-aligned, countdown session in progress."""
    return TimeLogEntry(
        uid="t-2",
        label="Focus session",
        start=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        due=datetime(2026, 1, 1, 10, 25, tzinfo=UTC),
        mode=TimeLogMode.COUNTDOWN,
        status=TimeLogStatus.IN_PROGRESS,
        task_id="task-42",
        category=TimeLogCategory.WORK,
    )


@contextmanager
def captured_urlopen() -> Generator[list[dict[str, Any]], None, None]:
    """Capture every urlopen invocation and yield the captured list.

    Yields a list that grows by one entry per fake urlopen call.
    Each entry is ``{"request": <urllib Request>, "timeout": <float>}``.
    The fake urlopen returns a context-manager-like object whose
    ``read()`` returns ``b"ok"`` so the facade's
    ``with urlopen(...) as response: response.read()`` block
    completes normally.
    """
    captured: list[dict[str, Any]] = []

    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
            return None

        def read(self) -> bytes:
            return b"ok"

    def fake_urlopen(request: object, timeout: float = 0) -> _FakeResponse:
        captured.append({"request": request, "timeout": timeout})
        return _FakeResponse()

    with patch("genesis.notify.facade.urllib.request.urlopen", side_effect=fake_urlopen):
        yield captured


# -----------------------------------------------------------------------------
# NtfyConfig
# -----------------------------------------------------------------------------


class TestNtfyConfig:
    def test_defaults_match_constants(self) -> None:
        cfg = NtfyConfig()
        assert cfg.url == DEFAULT_NTFY_URL
        assert cfg.topic == ""

    def test_resolved_topic_uses_default_when_empty(self) -> None:
        cfg = NtfyConfig()
        with patch("genesis.configs.socket.gethostname", return_value="alpha.local"):
            assert cfg.resolved_topic("alice") == "genesis-alpha-alice"

    def test_resolved_topic_returns_explicit_value(self) -> None:
        cfg = NtfyConfig(topic="custom-topic")
        assert cfg.resolved_topic("alice") == "custom-topic"

    def test_resolved_topic_lowercases_hostname(self) -> None:
        cfg = NtfyConfig()
        with patch("genesis.configs.socket.gethostname", return_value="BETA.example.com"):
            assert cfg.resolved_topic("alice") == "genesis-beta-alice"

    def test_resolved_topic_sanitises_non_alphanumer(self) -> None:
        cfg = NtfyConfig()
        with patch("genesis.configs.socket.gethostname", return_value="my host_01!"):
            # spaces, underscores, and exclamation marks become
            # dashes; underscores are a topic-safe character but
            # the policy here is `[a-z0-9-]`.
            assert cfg.resolved_topic("alice") == "genesis-my-host-01-alice"


# -----------------------------------------------------------------------------
# _safe_hostname
# -----------------------------------------------------------------------------


class TestSafeHostname:
    def test_lowercases_and_takes_first_label(self) -> None:
        with patch("genesis.configs.socket.gethostname", return_value="Alpha.Example.com"):
            assert _safe_hostname() == "alpha"

    def test_sanitises_non_alphanumer(self) -> None:
        with patch("genesis.configs.socket.gethostname", return_value="my host!?"):
            assert _safe_hostname() == "my-host"

    def test_falls_back_when_socket_raises(self) -> None:
        with patch("genesis.configs.socket.gethostname", side_effect=OSError("nope")):
            assert _safe_hostname() == DEFAULT_NTFY_TOPIC_HOST

    def test_falls_back_when_hostname_empty(self) -> None:
        with patch("genesis.configs.socket.gethostname", return_value=""):
            assert _safe_hostname() == DEFAULT_NTFY_TOPIC_HOST

    def test_falls_back_when_sanitisation_empties_string(self) -> None:
        with patch("genesis.configs.socket.gethostname", return_value="!!!"):
            assert _safe_hostname() == DEFAULT_NTFY_TOPIC_HOST


# -----------------------------------------------------------------------------
# NotifyFacade.send
# -----------------------------------------------------------------------------


class TestNotifyFacadeSend:
    def test_send_posts_to_url_slash_topic(self) -> None:
        facade = NotifyFacade(url="http://p3:8092", topic="genesis-host-alice")
        with captured_urlopen() as calls:
            facade.send("Title", "Body")
        assert len(calls) == 1
        request = calls[0]["request"]
        assert request.full_url == "http://p3:8092/genesis-host-alice"
        assert request.method == "POST"

    def test_send_strips_trailing_slash_on_url(self) -> None:
        facade = NotifyFacade(url="http://p3:8092/", topic="t")
        with captured_urlopen() as calls:
            facade.send("T", "B")
        request = calls[0]["request"]
        # Two slashes in a row would be a malformed URL; the
        # facade guarantees one.
        assert request.full_url == "http://p3:8092/t"
        assert "//t" not in request.full_url.replace("http://", "", 1)

    def test_send_encodes_body_as_utf8(self) -> None:
        facade = NotifyFacade(url="http://p3:8092", topic="t")
        with captured_urlopen() as calls:
            facade.send("T", "café ✓")
        request = calls[0]["request"]
        assert request.data == "café ✓".encode()

    def test_send_sets_title_priority_tags_headers(self) -> None:
        facade = NotifyFacade(url="http://p3:8092", topic="t")
        with captured_urlopen() as calls:
            facade.send("My Title", "My Body", priority=4, tags=["hourglass"])
        request = calls[0]["request"]
        assert request.get_header("Title") == "My Title"
        assert request.get_header("Priority") == "4"
        assert request.get_header("Tags") == "hourglass"

    def test_send_omits_tags_header_when_no_tags(self) -> None:
        facade = NotifyFacade(url="http://p3:8092", topic="t")
        with captured_urlopen() as calls:
            facade.send("T", "B")
        request = calls[0]["request"]
        assert request.get_header("Tags") is None

    def test_send_default_priority_is_3(self) -> None:
        facade = NotifyFacade(url="http://p3:8092", topic="t")
        with captured_urlopen() as calls:
            facade.send("T", "B")
        assert calls[0]["request"].get_header("Priority") == "3"

    def test_send_propagates_url_error(self) -> None:
        facade = NotifyFacade(url="http://p3:8092", topic="t")
        with patch(
            "genesis.notify.facade.urllib.request.urlopen",
            side_effect=urllib.error.URLError("dns"),
        ):
            with pytest.raises(urllib.error.URLError):
                facade.send("T", "B")

    def test_send_propagates_timeout_error(self) -> None:
        facade = NotifyFacade(url="http://p3:8092", topic="t")
        with patch(
            "genesis.notify.facade.urllib.request.urlopen",
            side_effect=TimeoutError("slow"),
        ):
            with pytest.raises(TimeoutError):
                facade.send("T", "B")

    def test_send_passes_timeout_to_urlopen(self) -> None:
        facade = NotifyFacade(url="http://p3:8092", topic="t", timeout=0.5)
        with captured_urlopen() as calls:
            facade.send("T", "B")
        assert calls[0]["timeout"] == 0.5

    def test_url_and_topic_properties(self) -> None:
        facade = NotifyFacade(url="http://x/", topic="y")
        assert facade.url == "http://x"
        assert facade.topic == "y"


# -----------------------------------------------------------------------------
# Message builders
# -----------------------------------------------------------------------------


class TestMessageBuilders:
    def test_started_unaligned(self, session_unaligned: TimeLogEntry) -> None:
        title, message, tags = session_started(session_unaligned)
        assert title == "Focus session started"
        assert message == "Deep work"
        assert tags == ["hourglass"]

    def test_started_aligned(self, session_aligned: TimeLogEntry) -> None:
        title, message, tags = session_started(session_aligned, task_title="Write the report")
        assert title == "Focus session started"
        assert message == "Task: Write the report"
        assert tags == ["hourglass"]

    def test_started_aligned_without_task_title_falls_back_to_label(self, session_aligned: TimeLogEntry) -> None:
        # No task_title passed even though session has a task_id.
        # Builder falls back to the label so the notification is
        # still informative.
        title, message, tags = session_started(session_aligned)
        assert title == "Focus session started"
        assert message == "Focus session"
        assert tags == ["hourglass"]

    def test_milestone_unaligned(self, session_unaligned: TimeLogEntry) -> None:
        title, message, tags = session_milestone(session_unaligned, 30)
        assert title == "Focus milestone"
        assert message == "Deep work · 30 min"
        assert tags == ["hourglass"]

    def test_milestone_aligned(self, session_aligned: TimeLogEntry) -> None:
        title, message, tags = session_milestone(session_aligned, 60, task_title="Write the report")
        assert title == "Focus milestone"
        assert message == "Task: Write the report · 60 min"
        assert tags == ["hourglass"]

    def test_finished_unaligned(self, session_unaligned: TimeLogEntry) -> None:
        title, message, tags = session_finished(session_unaligned)
        assert title == "Focus session finished"
        assert message == "Deep work"
        assert tags == ["check"]

    def test_finished_aligned(self, session_aligned: TimeLogEntry) -> None:
        title, message, tags = session_finished(session_aligned, task_title="Write the report")
        assert title == "Focus session finished"
        assert message == "Task: Write the report"
        assert tags == ["check"]


# -----------------------------------------------------------------------------
# Sanity: package re-exports
# -----------------------------------------------------------------------------


def test_package_reexports_match_dunder_all() -> None:
    """The package __init__ advertises the same names it re-exports."""
    import genesis.notify as notify

    assert set(notify.__all__) == {
        "NotifyFacade",
        "session_finished",
        "session_milestone",
        "session_started",
    }
    for name in notify.__all__:
        assert getattr(notify, name) is not None
