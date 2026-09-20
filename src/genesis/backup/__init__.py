"""Backup and restore facade.

Exposes :class:`BackupFacade` and the report types. Reached via
``Facade.backup`` on the parent :class:`genesis.core.facade.Facade`.
See ADR-024.
"""

from __future__ import annotations

from .facade import ArchiveError, BackupFacade, RestoreReport, ValidationReport
from .manifest import MANIFEST_SCHEMA_VERSION, Counts, Manifest

__all__ = [
    "ArchiveError",
    "BackupFacade",
    "Counts",
    "MANIFEST_SCHEMA_VERSION",
    "Manifest",
    "RestoreReport",
    "ValidationReport",
]
