"""Hardcoded CardDAV collection names.

The single fixed top-level addressbook each user has under their
CardDAV principal. Mirrors the fixed-collection approach in
:mod:`genesis.caldav.constants`.
"""

ADDRESSBOOK_COLLECTION = "contacts"

# iCalendar / vCard PRODID. The CalDAV module writes its own
# PRODID; we keep the same convention here for parity.
PRODID = "-//Genesis//Productivity//EN"
