"""ntfy HTTP transport.

Single-shot ``POST`` per notification. No retry, no queueing, no
persistent state. ntfy is best-effort; a missed notification on a
transient outage is acceptable. The constructor stores the
resolved URL and topic from :class:`genesis.configs.NtfyConfig`
so the call site does not have to.

ADR-027.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Final

# Default HTTP timeout for the POST. Two seconds is long enough
# to absorb a slow ntfy and short enough that the focus page's
# one-second tick is never blocked on a dead server. The focus
# page's ``_notify_safely`` wrapper swallows any failure, so a
# long timeout only hurts if the server is reachable but slow.
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 2.0


class NotifyFacade:
    """POST notifications to an ntfy server.

    Constructed by ``Facade.notify`` with the URL and topic
    already resolved from :class:`genesis.configs.NtfyConfig`.
    Direct construction is allowed (and tested) but the
    production code path goes through ``Facade.notify``.

    The facade does NOT swallow errors. The caller decides the
    failure policy. The focus page wraps every call in a
    ``try/except`` so a dead ntfy server cannot break the
    one-second tick.
    """

    def __init__(
        self,
        *,
        url: str,
        topic: str,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        # Strip a trailing slash on the URL so ``f"{url}/{topic}"``
        # never produces ``//``. ntfy is tolerant of either, but a
        # doubled slash looks like a bug.
        self._url = url.rstrip("/")
        self._topic = topic
        self._timeout = timeout

    @property
    def url(self) -> str:
        """The configured ntfy server base URL."""
        return self._url

    @property
    def topic(self) -> str:
        """The configured ntfy topic."""
        return self._topic

    def send(
        self,
        title: str,
        message: str,
        *,
        priority: int = 3,
        tags: list[str] | None = None,
    ) -> None:
        """POST ``message`` to ``{url}/{topic}``.

        Headers carry the title, priority, and tags. ntfy reads
        the body as the message text.

        Args:
            title: Notification title. ntfy uses this as the
                bold first line of the push notification.
            message: Notification body. Sent as UTF-8.
            priority: 1 (min) to 5 (max). Defaults to 3 (normal).
            tags: Optional list of short tag strings. ntfy maps
                known names to emoji (e.g. ``"hourglass"``,
                ``"check"``); unknown tags render as-is.

        Raises:
            urllib.error.URLError: On DNS / connect / read
                failure. Includes ``TimeoutError`` on socket
                timeout.
            urllib.error.HTTPError: When ntfy returns a non-2xx
                status.
            OSError: On low-level socket failures.
            ValueError: When the constructed URL is malformed.
        """
        endpoint = f"{self._url}/{self._topic}"
        headers = {
            "Title": title,
            "Priority": str(priority),
        }
        if tags:
            # ntfy accepts comma-separated tags; the message
            # builders we ship today only emit one tag at a time
            # but the API is open for future use.
            headers["Tags"] = ",".join(tags)

        request = urllib.request.Request(
            endpoint,
            data=message.encode("utf-8"),
            method="POST",
            headers=headers,
        )
        # Drain the response body so the connection can be
        # returned to the pool. ntfy's success body is small
        # ("ok") so reading is cheap.
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            response.read()
