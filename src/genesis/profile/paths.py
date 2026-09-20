"""Pure path resolution for the profile subpackage.

Single source of truth for the two targets (``profile``,
``life_story``) and their canonical filenames. No I/O — every
function here is deterministic on its inputs.

The cwd-vs-project scope concept from the out-of-tree personalize
CLI is intentionally dropped. There is exactly one canonical
location per target per user; the server's per-user data tree is
the only path that matters.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ProfileTarget(StrEnum):
    """The two profile targets served by this module.

    Wire values are the lowercase enum names. ``life_story`` is
    the wire form (matches the out-of-tree tool's
    ``life-story.md`` filename via the mapping below); ``LIFE_STORY``
    is the Python identifier.
    """

    PROFILE = "profile"
    LIFE_STORY = "life_story"


# Canonical filenames per target. Lives here, not in the API or
# facade, so every layer agrees on the filename without duplicating
# a string.
TARGET_FILENAME: dict[ProfileTarget, str] = {
    ProfileTarget.PROFILE: "profile.md",
    ProfileTarget.LIFE_STORY: "life-story.md",
}

# Snapshot directory name (a hidden dotfile inside the profile root).
# Append-only; one snapshot per successful write/edit plus one per
# restore-pre.
VERSIONS_DIR_NAME = ".versions"


# Ordered tuple of every target served. Used by ``list_targets`` and
# by any iterator that needs to walk "all of them" in a stable order.
TARGETS: tuple[ProfileTarget, ...] = (
    ProfileTarget.PROFILE,
    ProfileTarget.LIFE_STORY,
)


@dataclass(frozen=True)
class ResolvedLocation:
    """A fully-resolved canonical file location on disk.

    ``path`` is what callers write to / read from. ``dir`` and
    ``filename`` are split out for snapshot sidecars (so the
    versions module can build ``<dir>/.versions/<target>/...``
    without re-implementing the join).
    """

    path: Path
    dir: Path
    filename: str
    target: ProfileTarget


def resolve_location(target: ProfileTarget, profile_root: Path) -> ResolvedLocation:
    """Resolve the canonical file for ``target`` under ``profile_root``.

    ``profile_root`` is the per-user profile directory created by
    :class:`genesis.core.facade.Facade` from
    ``Configs.paths.profile_root``. The directory is not created
    here; the storage layer creates it on first write.
    """
    filename = TARGET_FILENAME[target]
    return ResolvedLocation(
        path=profile_root / filename,
        dir=profile_root,
        filename=filename,
        target=target,
    )


def versions_dir(profile_root: Path, target: ProfileTarget) -> Path:
    """Return the snapshot directory for ``target`` under ``profile_root``."""
    return profile_root / VERSIONS_DIR_NAME / target.value


def parse_target(value: str) -> ProfileTarget:
    """Validate a wire target string and return the enum member.

    Raises :class:`ValueError` (with the valid set in the message)
    when the input does not match a known target.
    """
    try:
        return ProfileTarget(value)
    except ValueError as e:
        valid = ", ".join(t.value for t in TARGETS)
        raise ValueError(f"Invalid profile target {value!r}; valid: {valid}") from e


__all__ = [
    "ProfileTarget",
    "ResolvedLocation",
    "TARGETS",
    "TARGET_FILENAME",
    "VERSIONS_DIR_NAME",
    "parse_target",
    "resolve_location",
    "versions_dir",
]
