# 016. Short typed UID format

## Context

ADR-012 established the immutable primary key for every iCal object as
`{uuid4}@genesis` (45 chars). Real use surfaced two problems when the
agent (LLM) drives the API:

1. **Typos.** A long opaque string is easy to misread and misquote.
   One wrong character either produces a 404 or, worse, points at the
   wrong record.
2. **Cross-entity confusion.** A task UID is visually identical to a
   project UID; nothing in the string tells the agent which kind of
   entity it is dealing with.

Journal entries had a parallel problem: their default UID was a 32-char
UUID4 hex string with no type signal and no domain suffix.

RFC 5545 requires the UID property to be globally unique. It does not
require the `@domain` suffix or any particular length. Radicale and the
`caldav` library treat UID as an opaque string. Nothing in the codebase
parses the suffix, so it can be dropped without breaking compatibility.

## Decision

UID format becomes `{type}-{tail}` where:

- `type` is a single lower-case letter that encodes the entity kind.
- `tail` is 6 characters from `[a-z0-9]` (base36), generated from a
  cryptographically secure source (`secrets`).
- No `@genesis` suffix.

Mapping:

| Entity        | UID prefix | Example              |
|---------------|------------|----------------------|
| Task          | `t-`       | `t-4f7a2b`           |
| Project       | `p-`       | `p-7c3d8e`           |
| Event         | `e-`       | `e-a1b2c3`           |
| Time log entry | `l-`       | `l-9d8e7f`           |
| Journal entry | `j-`       | `j-q1w2e3`           |

Per-type entropy: 36^6 = ~2.2 billion combinations. The type prefix
disambiguates between entity kinds, so collisions are only possible
within a single type. Birthday-paradox collision probability becomes
non-negligible (~1%) around 5000 IDs per type, which is well above
realistic usage for a personal productivity system.

The UID generator is centralised in `src/genesis/caldav/ical.py` as
`new_uid(prefix: str) -> str` and consumed by every CalDAV facade.
Journal entries use the same generator.

`tests/mock_caldav.py` exposes a parallel `_new_uid(prefix)` method
that produces IDs of the same shape.

`scripts/migrate_from_legacy_sqlite.py` already calls into the public
`create_*` API, so it picks up the new format automatically when it
repopulates from the legacy sqlite database.

## Status

Accepted.

Supersedes the UID format in ADR-012 (section 4, Identity). ADR-012's
immutability rule (UID is never regenerated) and the shadow VEVENT
lifecycle are unchanged.

## Consequences

Positive:

- LLM-facing IDs are 8 chars instead of 45, with the entity kind
  encoded in a single-character prefix. Agents can self-validate
  intent before issuing a request.
- No `@` symbol in IDs means cleaner URL path params, no ambiguity in
  shell quoting, and shorter log lines.
- Centralised generation makes future ID-format changes a one-file edit.

Negative:

- Existing CalDAV data (tasks, projects, events, time log) and journal
  markdown files use the old format. They remain readable but new
  entities will not collide with old ones by accident (old `t-` IDs
  did not exist before this ADR). If a user re-imports the legacy
  sqlite with the migration script, every row gets a freshly generated
  short UID; cross-referencing old IDs is not possible.
- A future change to the format (e.g., longer tail) requires another
  migration. The choice of `secrets` + base36 makes the generator
  trivial to tune.

Neutral:

- Per-type entropy (~31 bits per type) is lower than the previous UUID4
  (122 bits per ID, shared across types). Sufficient for the
  foreseeable scale of a personal productivity system; revisit if the
  system ever becomes multi-user or multi-tenant.
