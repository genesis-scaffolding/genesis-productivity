# 030. CardDAV Contacts

## Context

The README and AGENTS.md list contacts as a first-class part of the
productivity system, but `genesis.carddav` is a stub. The Facade
already wires `carddav` through the shared Radicale, the dependency
is declared, and the storage layer (Radicale) speaks CardDAV natively.
The missing pieces are the entity, the storage implementation, the
API, and the UI.

The only maintained Python CardDAV client libraries are `pyCardDAV`
(2014) and `carddavclient` (2015), both dead. `vdirsyncer` is a
sync tool, not a client. The `caldav` package is the maintained
option, but its public surface (`Calendar`, `calendars()`,
`make_calendar()`) is CalDAV-shaped and has no CardDAV support.

The `caldav` library ships a generic `DAVClient` whose own
`mkcol` docstring calls out vCards as a target use case. That
`DAVClient` already handles Basic auth, URL joining, rate limiting,
and exposes `propfind` / `report` / `mkcol` / `put` / `delete` /
`request` for raw WebDAV. It is the only maintained Python HTTP
client that gives us authenticated WebDAV plumbing for free.

## Decision

### Storage

Each user gets one addressbook named `contacts` under their Radicale
principal. The addressbook is created on first access by sending a
WebDAV `MKCOL` with a `<D:resourcetype><CR:addressbook/></D:resourcetype>`
body. The user's principal URL and the addressbook-home-set URL are
discovered through `PROPFIND`. No new server, no new process, no
new auth path — the same shared Radicale serves CalDAV and CardDAV.

### Client

Use `caldav.DAVClient(server_url, username, password)` directly
(its public constructor already accepts the same three args we
already pass to `CalDAVFacade`). Drive the addressbook via:

- `client.principal()` — discover the user's principal URL.
- `client.propfind(principal_url, props=<CR:addressbook-home-set xml>)`
  — find the addressbook-home-set URL.
- `client.propfind(home_url, depth=1, props=<D:resourcetype xml>)`
  — list existing addressbooks.
- `client.mkcol(addrbook_url, body=<resourcetype xml>)` — create
  the `contacts/` addressbook on first use.
- `client.put(vcard_url, body=vcard, headers={"Content-Type": "text/vcard; charset=utf-8"})`
  — create or update a contact.
- `client.delete(vcard_url)` — delete a contact.
- `client.report(addrbook_url, query=<CR:addressbook-query xml>)`
  — server-side search when a query is provided.

We accept a small amount of hand-written XML for the three
addressbook-specific WebDAV calls. The XML is small and fully
defined by RFC 6352; no library wraps it nicely.

### Serialization

`vobject` (already a transitive dep, promoted to a direct dep in
this change) handles VCARD 3.0 parsing and serialization. We
support the v1 field set (see `genesis.entities.contact`). The
vCard on disk is whatever `vobject.vCard().serialize()` produces;
we do not require any particular property order beyond what
`vobject` writes by default.

### Entity

`Contact` lives in `genesis.entities.contact`. Fields are the
subset of RFC 6350 we want in v1: name parts (FN plus structured
N), nickname, organization, title, multi-value `emails` /
`phones` / `urls` / `addresses`, `note`, and `birthday`. The
existing `Contact` model in `carddav/facade.py` is replaced
with the new one; the four scalar fields stay as a back-compat
surface for callers that already imported them.

Multiple values are common (a person has several emails and
phones), so `emails` / `phones` / `urls` are `list[str]`.
`addresses` is a `list[str]` of single-line strings; the
underlying vCard stores them as `ADR` components, but the API
treats them as opaque blobs to keep the wire shape simple in
v1. Structured `ADR` (street / city / region / postal / country)
is out of scope for v1.

### Facade

`genesis.carddav.facade.CardDAVFacade(server_url, username, password)`
exposes `create_contact`, `get_contact`, `list_contacts`,
`update_contact`, `delete_contact`, and `search_contacts`. The
public surface mirrors `CalDAVFacade`: thin delegations to
`genesis.carddav.contacts`, no WebDAV types leak. The
`Facade.carddav` property is unchanged; it already constructs
a `CardDAVFacade` lazily on first access.

`list_contacts` uses a server-side `addressbook-query` REPORT
with a `CR:filter` clause when `search` is provided, then falls
back to client-side substring match across `name` / `emails` /
`organization` / `nickname` for the same reason the CalDAV task
list does (RFC 6352 only supports per-property OR filters, and
`vobject` field extraction is cheaper than a multi-round REPORT).

`update_contact` uses in-place mutation of the vobject, then
re-serializes and `PUT`s the same URL. UID is preserved across
edits. `REV` is set on the server side via the vobject's
`add('rev').value` at write time.

### API

New router `genesis.api.routers.contacts` mounted under
`/productivity/contacts`. Endpoints:

- `GET /productivity/contacts` — list, search via `?q=`, paginate.
- `POST /productivity/contacts` — create.
- `GET /productivity/contacts/{uid}` — read.
- `PATCH /productivity/contacts/{uid}` — update.
- `DELETE /productivity/contacts/{uid}` — delete.

Wire models in `genesis.api.schemas` follow the existing
conventions (Pydantic, `ConfigDict(from_attributes=True)` on
the `Out` model, `UtcDateTime` for `created` / `modified`).
No XP / game / statistics integration in v1 — contacts do not
trigger events. The `_fire_xp_safely` machinery is not invoked.

### UI

New page `genesis.ui.pages.contacts` registered in
`genesis.ui.app.py` in the Productivity group, after Journal.
The page follows the same `?uid=` / `?new=` / `?edit=` query-param
convention as the journal page. List view is a `st.data_editor`
with inline edit on `name` and `organization` plus an actions
column (Open / Delete). Detail view shows the full contact with
multi-value lists rendered as bullet lists and a Delete button
via the shared `delete_confirm_dialog`. Edit and Create are
`st.form`s with the field layout from the entity.

### Tests

Three layers, matching the existing convention:

- `tests/mock_carddav.py` mirrors `tests/mock_caldav.py` — an
  in-process `MockCardDAVFacade` that stores contacts in a
  `dict[uid, Contact]`.
- `tests/test_carddav_unit.py` is fast, mock-backed, and covers
  CRUD, search substring across name / email / org / nickname,
  multi-value list round-trips, sort stability, and no-op on
  unknown uid.
- `tests/test_carddav_round_trip.py` is `@pytest.mark.integration`
  and boots a real Radicale to prove the WebDAV + vobject wire
  path works end-to-end.
- `tests/test_api_contacts.py` exercises the full endpoint
  surface against the mock backend via the `api_server`
  fixture, with the mock substituted in the same way
  `MockCalDAVFacade` is substituted for the productivity router.
- `tests/test_api_contacts_round_trip.py` is one
  `@pytest.mark.integration` test against `api_server_real`.

## Status

Proposed

## Consequences

### Positive

- One HTTP/auth stack for both CalDAV and CardDAV (the same
  `caldav.DAVClient`).
- No new runtime dependency beyond promoting `vobject` from
  transitive to direct.
- Contact storage uses the same shared Radicale and the same
  htpasswd / rights / per-user-config contract as the rest of
  the system.
- The mock-backed test path keeps the fast unit suite fast.

### Negative

- The `caldav` library's `DAVClient` is not type-annotated for
  CardDAV usage. We hand-write a small amount of XML for
  addressbook-home-set PROPFIND, MKCOL resourcetype, and
  addressbook-query REPORT. The XML is small and stable per
  RFC 6352.
- vCard 3.0 only (Radicale's expected version). vCard 4.0
  features (KIND, GENDER, LANG, ANNIVERSARY) are out of scope
  for v1.
- No photo / avatar support, no structured postal address,
  no group / list contacts, no CardDAV discovery beyond the
  per-user `contacts/` addressbook, no contact↔task/project
  linking in v1.

### Neutral

- The "promote vobject to a direct runtime dep" change is a
  metadata change in `pyproject.toml`, not a new install.

## Relationship

- Builds on ADR-009 (Radicale CalDAV server) — same process,
  same auth, same storage layout.
- Builds on ADR-012 (CalDAV collection model) — CardDAV is one
  more fixed top-level collection under the per-user principal.
- Mirrors the productivity router pattern from ADR-015 (agent
  API server).
- Uses the same `new_uid` typed-UID scheme from ADR-016 with
  the new prefix `c-` for contact.
