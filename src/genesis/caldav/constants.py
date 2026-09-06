"""Hardcoded CalDAV collection names and tunables.

The four fixed top-level collections each user has under their CalDAV
principal. See ADR-012.
"""

PROJECTS_COLLECTION = "projects"
TASKS_COLLECTION = "tasks"
EVENTS_COLLECTION = "events"
TIME_LOG_COLLECTION = "time_log"

# Default duration for a shadow VEVENT when the source task has
# scheduled_start set but scheduled_duration is None. Per ADR-012.
DEFAULT_SHADOW_DURATION_MINUTES = 30

# iCal PRODID string written into every component we emit.
PRODID = "-//Genesis//Productivity//EN"
