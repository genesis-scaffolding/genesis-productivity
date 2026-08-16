"""CalDAV integration for tasks, projects, and calendar.

This module provides a facade for interacting with CalDAV servers.
It handles tasks, projects (as calendars), and calendar events.
"""

from .facade import CalDAVFacade

__all__ = ["CalDAVFacade"]
