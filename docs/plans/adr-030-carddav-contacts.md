# Plan for adr-030

## Steps

1. Add `vobject` as a direct runtime dep via `uv add vobject`.
2. Create `src/genesis/carddav/constants.py` with
   `ADDRESSBOOK_COLLECTION = "contacts"`.
3. Create `src/genesis/entities/contact.py` with `Contact`,
   `ContactCreate`, `ContactUpdate` (Pydantic, following the
   task / project pattern).
4. Re-export `Contact` / `ContactCreate` / `ContactUpdate` from
   `src/genesis/entities/__init__.py`.
5. Create `src/genesis/carddav/vcard.py` with `contact_to_vcard`,
   `contact_from_vcard`, and `new_contact_uid` (wraps the
   existing `caldav.ical.new_uid` for the `c-` prefix).
6. Create `src/genesis/carddav/webdav.py` with a thin wrapper
   around `caldav.DAVClient` exposing the five WebDAV calls
   the addressbook needs (propfind, mkcol, put, delete, report)
   plus the `addressbook_home_set` and `find_or_create_addressbook`
   helpers.
7. Create `src/genesis/carddav/contacts.py` with the public
   `create_contact`, `get_contact`, `list_contacts`,
   `update_contact`, `delete_contact`, `search_contacts` module
   functions. Mirrors `genesis.caldav.tasks` shape.
8. Replace `src/genesis/carddav/facade.py` stub with delegations
   to the new `contacts.py` module. Keep `__init__` signature
   unchanged so `Facade.carddav` keeps working without edits.
9. Re-export `Contact` and `CardDAVFacade` from
   `src/genesis/carddav/__init__.py` (already correct).
10. Add `ContactCreateIn`, `ContactUpdateIn`, `ContactOut`,
    `ContactListOut` to `src/genesis/api/schemas.py`.
11. Create `src/genesis/api/routers/contacts.py` with the five
    endpoints, using the existing `current_subject` dep and the
    per-request `Facade` pattern from the productivity router.
12. Wire the new router in `src/genesis/api/app.py`.
13. Create `src/genesis/ui/pages/contacts.py` with the
    `_render_list` / `_render_view` / `_render_edit` /
    `_render_create` dispatch (same shape as `pages/journal.py`).
14. Register the contacts page in `src/genesis/ui/app.py`'s
    Productivity nav after Journal.
15. Create `tests/mock_carddav.py` — in-memory `MockCardDAVFacade`
    mirroring `tests/mock_caldav.py`.
16. Update `tests/conftest.py` to substitute the mock carddav
    facade into the `api_server` and `api_server_with_mock`
    fixtures (the existing `_substitute_facade` and
    `_substitute_facade` equivalents).
17. Create `tests/test_carddav_unit.py` (fast, mock-backed).
18. Create `tests/test_carddav_round_trip.py` (integration,
    real Radicale).
19. Create `tests/test_api_contacts.py` (fast, mock-backed).
20. Create `tests/test_api_contacts_round_trip.py` (integration,
    real Radicale).
21. Run `make lint`, `make typecheck`, `make test`.
