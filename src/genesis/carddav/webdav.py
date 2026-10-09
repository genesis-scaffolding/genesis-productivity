"""Low-level WebDAV plumbing for the addressbook.

Wraps :class:`caldav.DAVClient` to expose the five WebDAV
operations the addressbook needs: ``PROPFIND`` (to discover the
addressbook-home-set and list addressbooks), ``MKCOL`` (to create
the per-user ``contacts/`` addressbook), ``PUT`` (to create or
update a vCard), ``DELETE`` (to remove a vCard), and ``REPORT``
(addressbook-query for server-side search).

The :class:`caldav.DAVClient` already handles Basic auth, URL
joining, and rate-limit retries, so this module is a thin
adapter. The WebDAV XML envelopes for the three addressbook-
specific calls (PROPFIND for ``CR:addressbook-home-set``, MKCOL
with the ``CR:addressbook`` resourcetype, REPORT
``CR:addressbook-query``) are defined as module-level constants
because the XML is stable per RFC 6352 and small enough to read
in place.
"""

from __future__ import annotations

import logging
from typing import Any

from caldav.davclient import DAVClient


class _UnexpectedContentTypeFilter(logging.Filter):
    """Drop the caldav library's ``text/vcard`` content-type warning.

    The filter is narrowly scoped to messages whose first segment
    starts with the well-known text. Anything else from the
    ``caldav`` logger still passes through unchanged.
    """

    def __init__(self, needle: str) -> None:
        super().__init__()
        self._needle = needle

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage() if isinstance(record.msg, str) else str(record.msg)
        return self._needle not in msg


# The ``caldav`` library's response parser keeps a hard-coded
# allow-list of "expected" content types (``text/xml``,
# ``application/xml``, ``text/plain``, ``text/calendar``,
# ``application/octet-stream``) and logs a WARNING on every
# response that does not match. ``text/vcard`` is not in that
# list even though it is the canonical content type for a vCard
# resource (RFC 6352), so every GET on a contact produces a
# ``Deviation from expectations found: Unexpected content type:
# text/vcard; charset=utf-8`` warning in the user's log.
#
# The warning is benign — the response body is read correctly
# via the ``_raw`` attribute. Installing a filter on the
# ``caldav`` logger at import time drops the noise without
# affecting any other ``caldav`` warnings.
logging.getLogger("caldav").addFilter(
    _UnexpectedContentTypeFilter("Deviation from expectations found: Unexpected content type")
)

# PROPFIND body asking for the CardDAV addressbook-home-set
# (RFC 6352 §6.2.2). Used on the principal URL with depth 0 to
# find the per-user addressbook home.
_ADDRESSBOOK_HOME_SET_PROPFIND = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<D:propfind xmlns:D="DAV:" xmlns:CR="urn:ietf:params:xml:ns:carddav">'
    "<D:prop>"
    "<CR:addressbook-home-set/>"
    "</D:prop>"
    "</D:propfind>"
)

# PROPFIND body asking for the resourcetype and displayname of
# every immediate child of the home URL (depth 1). Used to
# detect an existing addressbook with a given name.
_RESOURCETYPE_PROPFIND = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<D:propfind xmlns:D="DAV:">'
    "<D:prop>"
    "<D:resourcetype/>"
    "<D:displayname/>"
    "</D:prop>"
    "</D:propfind>"
)

# MKCOL body declaring a new addressbook collection. The empty
# ``D:prop`` element is required by RFC 3744; the resourcetype
# child tells Radicale this is a CardDAV collection, not a plain
# WebDAV folder.
_MKCOL_ADDRESSBOOK_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<D:mkcol xmlns:D="DAV:" xmlns:CR="urn:ietf:params:xml:ns:carddav">'
    "<D:set>"
    "<D:prop>"
    "<D:resourcetype>"
    "<D:collection/>"
    "<CR:addressbook/>"
    "</D:resourcetype>"
    "<D:displayname>contacts</D:displayname>"
    "</D:prop>"
    "</D:set>"
    "</D:mkcol>"
)


class AddressbookNotFoundError(Exception):
    """No addressbook with the given name exists under the home set.

    Raised by :meth:`CardDAVWebDAV.find_addressbook` when the
    addressbook-home-set is reachable but the named addressbook
    is not. The caller is expected to MKCOL it in response.
    """


def _propfind_href(elem: Any) -> str | None:
    """Extract the ``D:href`` text from a PROPFIND response element."""
    for child in elem:
        # The ``D:href`` element is unqualified in the response; the
        # local-name match is more reliable than a namespaced lookup
        # because the response XML uses a mix of ``DAV:`` and the
        # bare namespace depending on the server.
        if child.tag.endswith("href") and child.tag.startswith("{"):
            return child.text
    return None


class CardDAVWebDAV:
    """Thin wrapper around :class:`caldav.DAVClient` for CardDAV.

    The wrapper is constructed lazily from the same server URL,
    username, and password the CalDAV facade uses. It owns a
    single :class:`caldav.DAVClient` for the lifetime of the
    CardDAV facade so the Basic auth handshake happens once.

    All public methods either return parsed data (``list[str]``,
    ``bytes``) or ``None`` when the server's response indicates
    "not found". Errors that the caller cannot recover from
    (network, 5xx, malformed XML) propagate as exceptions.
    """

    def __init__(self, server_url: str, username: str, password: str) -> None:
        self._client = DAVClient(url=server_url, username=username, password=password)

    @property
    def client(self) -> DAVClient:
        """Return the underlying :class:`caldav.DAVClient`."""
        return self._client

    def principal_url(self) -> str:
        """Return the URL of the authenticated user's principal.

        The principal is the per-user anchor that owns calendars
        and addressbooks. Cached after the first call so the
        multiple ``_get_calendar``-style lookups in the
        :class:`CardDAVFacade` only pay the discovery cost once.
        """
        if not hasattr(self, "_principal_url") or self._principal_url is None:
            self._principal_url = str(self._client.get_principal().url)
        return self._principal_url

    def addressbook_home_url(self) -> str:
        """Return the absolute URL of the user's addressbook-home-set.

        Sends a ``PROPFIND`` on the principal URL asking for the
        ``CR:addressbook-home-set``. Falls back to the principal
        URL itself on servers that do not advertise the
        addressbook-home-set (Radicale does, but the fallback
        keeps the path identical to :class:`caldav.collection`).

        Radicale's response carries a relative ``href`` (e.g.
        ``/default/``) for the home set; the caldav library's
        private ``_make_absolute_url`` joins it against the
        client base URL so downstream calls get a fully-qualified
        URL the HTTP layer can resolve.
        """
        response = self._client.propfind(
            self.principal_url(),
            props=_ADDRESSBOOK_HOME_SET_PROPFIND,
            depth=0,
        )
        if response.status not in (200, 207):
            return self.principal_url()
        tree = response.tree
        if tree is None:
            return self.principal_url()
        for elem in tree.iter():
            if elem.tag.endswith("addressbook-home-set") or elem.tag.endswith("}addressbook-home-set"):
                for child in elem:
                    if child.tag.endswith("href") and child.text:
                        return self._client._make_absolute_url(child.text)
        return self.principal_url()

    def list_addressbooks(self) -> list[str]:
        """Return the absolute URLs of every addressbook under the home set.

        Radicale returns relative ``href`` values in PROPFIND
        responses; each one is joined against the client base
        URL via the caldav library's private
        ``_make_absolute_url`` helper so the returned list is
        ready to feed into MKCOL / PROPFIND without further
        URL wrangling. Plain WebDAV folders and calendars are
        excluded.
        """
        response = self._client.propfind(
            self.addressbook_home_url(),
            props=_RESOURCETYPE_PROPFIND,
            depth=1,
        )
        if response.status not in (200, 207):
            return []
        tree = response.tree
        if tree is None:
            return []
        results: list[str] = []
        home = self.addressbook_home_url().rstrip("/")
        for response_elem in tree.iter():
            if not response_elem.tag.endswith("response") or not response_elem.tag.startswith("{"):
                continue
            href_text = _propfind_href(response_elem)
            if href_text is None:
                continue
            absolute = self._client._make_absolute_url(href_text)
            is_addressbook = False
            for prop in response_elem.iter():
                if prop.tag.endswith("resourcetype") or prop.tag.endswith("}resourcetype"):
                    for child in prop:
                        if child.tag.endswith("addressbook") and child.tag.startswith("{"):
                            is_addressbook = True
                            break
            if is_addressbook and absolute.rstrip("/") != home:
                results.append(absolute)
        return results

    def find_addressbook(self, name: str) -> str:
        """Return the URL of the addressbook named ``name``.

        Sends a depth-1 ``PROPFIND`` on the addressbook home and
        matches by the trailing URL segment (which Radicale sets
        from the MKCOL body, not the ``displayname``). Raises
        :class:`AddressbookNotFoundError` when the named
        addressbook is not present so the caller can decide
        whether to MKCOL.
        """
        for href in self.list_addressbooks():
            last = href.rstrip("/").rsplit("/", 1)[-1]
            if last == name:
                return href
        raise AddressbookNotFoundError(f"no addressbook named {name!r} under {self.addressbook_home_url()}")

    def ensure_addressbook(self, name: str) -> str:
        """Return the URL of ``name``, MKCOL-ing it if missing.

        Idempotent: a second call returns the existing URL
        without a round-trip write. Used by the facade at first
        access so a fresh user gets an empty addressbook.
        """
        try:
            return self.find_addressbook(name)
        except AddressbookNotFoundError:
            pass
        home = self.addressbook_home_url().rstrip("/")
        url = f"{home}/{name}/"
        self._client.mkcol(url, _MKCOL_ADDRESSBOOK_BODY)
        return url

    def put_vcard(self, addressbook_url: str, uid: str, vcard_text: str) -> str:
        """PUT a vCard under the addressbook at ``uid``.vcf.

        Returns the resource URL the server assigned. The
        resource name is ``{uid}.vcf`` so it is stable across
        PUTs and so the URL is recognisable in Radicale's
        filesystem storage for debugging.
        """
        url = f"{addressbook_url.rstrip('/')}/{uid}.vcf"
        self._client.put(
            url,
            vcard_text,
            headers={"Content-Type": "text/vcard; charset=utf-8"},
        )
        return url

    def get_vcard(self, vcard_url: str) -> str:
        """GET a vCard resource and return the raw VCARD text.

        Raises on 404 so the caller can map "not found" to
        ``None`` at the higher level. The caldav client already
        follows auth + redirects; we just issue a plain HTTP
        GET via :meth:`DAVClient.request`. The
        :class:`caldav.response.DAVResponse` exposes the body
        as ``_raw`` (bytes) — the library parses it as XML by
        default and silently falls back to a non-XML body, so
        we decode the raw bytes directly to preserve the
        VCARD text verbatim.
        """
        response = self._client.request(vcard_url, "GET")
        if response.status == 404:
            raise FileNotFoundError(vcard_url)
        if response.status not in (200, 207):
            response.raise_for_status()  # type: ignore[attr-defined]
        raw = response._raw
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    def delete_vcard(self, vcard_url: str) -> None:
        """DELETE a vCard resource. No-op on 404."""
        response = self._client.request(vcard_url, "DELETE")
        if response.status not in (200, 202, 204, 404):
            response.raise_for_status()  # type: ignore[attr-defined]

    def list_addressbook_items(self, addressbook_url: str) -> list[str]:
        """Return the absolute hrefs of every item in the addressbook.

        Uses a depth-1 PROPFIND on the addressbook URL asking
        for the resource type only; non-addressbook responses
        are filtered out. The returned hrefs are absolute URLs
        suitable for a direct GET. The caldav library's private
        ``_make_absolute_url`` helper resolves Radicale's
        relative ``href`` values against the client base URL.
        """
        response = self._client.propfind(
            addressbook_url,
            props=_RESOURCETYPE_PROPFIND,
            depth=1,
        )
        if response.status not in (200, 207):
            return []
        tree = response.tree
        if tree is None:
            return []
        hrefs: list[str] = []
        home = addressbook_url.rstrip("/")
        for response_elem in tree.iter():
            if not response_elem.tag.endswith("response") or not response_elem.tag.startswith("{"):
                continue
            href_text = _propfind_href(response_elem)
            if href_text is None:
                continue
            absolute = self._client._make_absolute_url(href_text)
            if absolute.rstrip("/") == home:
                continue
            hrefs.append(absolute)
        return hrefs

    def report_addressbook_query(self, addressbook_url: str, body: str) -> list[str]:
        """REPORT addressbook-query and return the hrefs that match.

        ``body`` is the full XML request body the caller builds
        (filter on N, FN, EMAIL, etc.). We only parse the
        response multistatus for ``D:href`` elements and return
        the list, leaving the higher-level facade to GET each
        vCard and build entities.
        """
        response = self._client.report(addressbook_url, body, depth=1)
        if response.status not in (200, 207):
            return []
        tree = response.tree
        if tree is None:
            return []
        hrefs: list[str] = []
        for elem in tree.iter():
            if elem.tag.endswith("href") and elem.tag.startswith("{") and elem.text:
                hrefs.append(elem.text)
        return hrefs
