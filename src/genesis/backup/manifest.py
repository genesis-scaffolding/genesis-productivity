"""Manifest model for a genesis backup archive.

The manifest is the single JSON document at the root of every backup
zip. It carries enough metadata for ``validate_archive`` to reject
archives the caller did not intend to apply, and for ``import_from_bytes``
to scope counts in the resulting ``RestoreReport``.

Schemas are versioned via ``schema_version``. Bumping the version is a
breaking change; a v1 archive is always interpretable by a v1 loader,
and a loader built against a different version refuses the archive
rather than guess.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

MANIFEST_SCHEMA_VERSION = 1
"""Current manifest schema version. Bumped on backwards-incompatible changes."""

MANIFEST_FILENAME = "MANIFEST.json"
"""Path inside the zip that holds the manifest."""


class Counts(BaseModel):
    """Per-collection item counts captured at export time.

    These are informational. They do not restrict what ``import_from_bytes``
    writes: the on-disk files in the archive are the source of truth.
    """

    projects: int = 0
    tasks: int = 0
    events: int = 0
    time_log: int = 0
    journal: int = 0
    profile: int = 0

    model_config = ConfigDict(extra="ignore")


class Manifest(BaseModel):
    """The top-level manifest entry of a backup archive.

    Attributes:
        schema_version: Integer version tag. ``validate_archive`` rejects
            anything other than the version this loader was built against.
        generator: Identifier of the tool that produced the archive.
        username: Account the archive belongs to. Used for the
            cross-user rejection check in ``validate_archive``.
        created_at: When the archive was produced (UTC ISO 8601).
        counts: Per-collection item counts at export time.
    """

    schema_version: int = Field(..., description="Manifest schema version.")
    generator: str = Field(..., description="Producer tag (e.g. 'genesis-backup').")
    username: str = Field(..., description="Account the archive belongs to.")
    created_at: datetime = Field(..., description="When the archive was produced (UTC).")
    counts: Counts = Field(default_factory=Counts, description="Per-collection counts.")

    model_config = ConfigDict(extra="ignore")
