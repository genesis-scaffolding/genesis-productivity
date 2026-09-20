"""Per-user profile and life-story storage.

One canonical markdown file per target per user, with versioned
auto-snapshots for rollback. Mounted on the FastAPI app under
``/profile`` for agent clients.

See ADR-025 for the design.
"""

from .facade import (
    ProfileFacade,
    ProfileHistory,
    ProfileList,
    ProfileListRow,
    ProfileRead,
    ProfileRestore,
    ProfileSnapshot,
    ProfileWrite,
)
from .paths import (
    TARGET_FILENAME,
    TARGETS,
    VERSIONS_DIR_NAME,
    ProfileTarget,
    ResolvedLocation,
    parse_target,
    resolve_location,
    versions_dir,
)

__all__ = [
    "ProfileFacade",
    "ProfileHistory",
    "ProfileList",
    "ProfileListRow",
    "ProfileRead",
    "ProfileRestore",
    "ProfileSnapshot",
    "ProfileTarget",
    "ProfileWrite",
    "ResolvedLocation",
    "TARGETS",
    "TARGET_FILENAME",
    "VERSIONS_DIR_NAME",
    "parse_target",
    "resolve_location",
    "versions_dir",
]
