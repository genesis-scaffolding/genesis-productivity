"""Hardcoded CalDAV collection names and tunables.

The five fixed top-level collections each user has under their CalDAV
principal. See ADR-012; ``XP_EVENTS_COLLECTION`` is added by ADR-026
(the life-gamification module).
"""

PROJECTS_COLLECTION = "projects"
TASKS_COLLECTION = "tasks"
EVENTS_COLLECTION = "events"
TIME_LOG_COLLECTION = "time_log"
XP_EVENTS_COLLECTION = "xp_events"

# Default duration for a shadow VEVENT when the source task has
# scheduled_start set but scheduled_duration is None. Per ADR-012.
DEFAULT_SHADOW_DURATION_MINUTES = 30

# iCal PRODID string written into every component we emit.
PRODID = "-//Genesis//Productivity//EN"
