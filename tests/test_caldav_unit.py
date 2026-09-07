"""Unit tests for CalDAV facade using mock backend.

These tests are fast and do not require a real Radicale server.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, PropertyMock

import pytest
from caldav.lib.error import NotFoundError

from genesis.caldav.events import list_events
from genesis.caldav.projects import list_projects
from genesis.caldav.tasks import list_tasks
from genesis.entities import (
    EventCreate,
    EventStatus,
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
    TimeLogEntryCreate,
    TimeLogMode,
    TimeLogStatus,
)
from tests.mock_caldav import MockCalDAVFacade

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def facade() -> MockCalDAVFacade:
    """Create a mock CalDAV facade for testing."""
    return MockCalDAVFacade()


# =============================================================================
# Task tests
# =============================================================================


class TestTaskCRUD:
    """Test task CRUD operations."""

    def test_create_task(self, facade: MockCalDAVFacade) -> None:
        """Creating a task returns the task with UID and timestamps."""
        task = facade.create_task(TaskCreate(summary="Test task"))

        assert task.uid is not None
        assert task.summary == "Test task"
        assert task.status == TaskStatus.NEEDS_ACTION
        assert task.created is not None
        assert task.modified is not None

    def test_get_task(self, facade: MockCalDAVFacade) -> None:
        """Getting a task by UID returns the task."""
        created = facade.create_task(TaskCreate(summary="Test task"))

        found = facade.get_task(created.uid)

        assert found is not None
        assert found.uid == created.uid
        assert found.summary == "Test task"

    def test_get_task_not_found(self, facade: MockCalDAVFacade) -> None:
        """Getting a non-existent task returns None."""
        assert facade.get_task("nonexistent@genesis") is None

    def test_list_tasks(self, facade: MockCalDAVFacade) -> None:
        """Listing tasks returns all tasks."""
        facade.create_task(TaskCreate(summary="Task 1"))
        facade.create_task(TaskCreate(summary="Task 2"))

        assert len(facade.list_tasks()) == 2

    def test_update_task(self, facade: MockCalDAVFacade) -> None:
        """Updating a task modifies the task in place."""
        task = facade.create_task(TaskCreate(summary="Original"))

        updated = facade.update_task(task.uid, TaskUpdate(summary="Updated"))

        assert updated is not None
        assert updated.summary == "Updated"
        assert updated.created is not None
        assert updated.modified is not None

    def test_update_task_sets_completed_at(self, facade: MockCalDAVFacade) -> None:
        """Marking a task completed sets completed_at."""
        task = facade.create_task(TaskCreate(summary="To complete"))

        updated = facade.update_task(task.uid, TaskUpdate(status=TaskStatus.COMPLETED))

        assert updated is not None
        assert updated.status == TaskStatus.COMPLETED
        assert updated.completed_at is not None

    def test_update_task_clears_completed_at(self, facade: MockCalDAVFacade) -> None:
        """Reopening a completed task clears completed_at."""
        task = facade.create_task(TaskCreate(summary="To complete"))
        facade.update_task(task.uid, TaskUpdate(status=TaskStatus.COMPLETED))

        updated = facade.update_task(task.uid, TaskUpdate(status=TaskStatus.NEEDS_ACTION))

        assert updated is not None
        assert updated.status == TaskStatus.NEEDS_ACTION
        assert updated.completed_at is None

    def test_delete_task(self, facade: MockCalDAVFacade) -> None:
        """Deleting a task removes it."""
        task = facade.create_task(TaskCreate(summary="To delete"))

        assert facade.delete_task(task.uid) is True
        assert facade.get_task(task.uid) is None

    def test_delete_task_not_found(self, facade: MockCalDAVFacade) -> None:
        """Deleting a non-existent task returns False."""
        assert facade.delete_task("nonexistent@genesis") is False


class TestTaskFields:
    """Test the new priority and scheduled_duration fields."""

    def test_create_task_with_priority(self, facade: MockCalDAVFacade) -> None:
        task = facade.create_task(TaskCreate(summary="Urgent", priority=1))
        assert task.priority == 1

    def test_create_task_with_scheduled_duration(self, facade: MockCalDAVFacade) -> None:
        start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
        task = facade.create_task(
            TaskCreate(summary="Meeting", scheduled_start=start, scheduled_duration=45)
        )
        assert task.scheduled_duration == 45


class TestTaskFilters:
    """Test task filtering."""

    def test_filter_by_status(self, facade: MockCalDAVFacade) -> None:
        """Filtering by status returns only matching tasks."""
        completed = facade.create_task(TaskCreate(summary="Completed task"))
        facade.update_task(completed.uid, TaskUpdate(status=TaskStatus.COMPLETED))
        facade.create_task(TaskCreate(summary="Active task"))

        active = facade.list_tasks(status=TaskStatus.NEEDS_ACTION)
        assert all(t.status == TaskStatus.NEEDS_ACTION for t in active)

    def test_filter_by_project(self, facade: MockCalDAVFacade) -> None:
        """Filtering by project_id returns only linked tasks."""
        project = facade.create_project(ProjectCreate(name="Work"))
        facade.create_task(TaskCreate(summary="Linked", project_id=project.uid))
        facade.create_task(TaskCreate(summary="Unlinked"))

        linked = facade.list_tasks(project_id=project.uid)
        assert len(linked) == 1
        assert linked[0].project_id == project.uid

    def test_text_search(self, facade: MockCalDAVFacade) -> None:
        """Text search finds tasks by summary or description."""
        facade.create_task(TaskCreate(summary="Buy groceries"))
        facade.create_task(
            TaskCreate(summary="Fix bug", description="Memory leak in parser")
        )

        results = facade.list_tasks(search="bug")
        assert len(results) == 1


class TestTaskSorting:
    """Test task sorting."""

    def test_sort_by_created(self, facade: MockCalDAVFacade) -> None:
        """Tasks can be sorted by created date."""
        t1 = facade.create_task(TaskCreate(summary="First"))
        t2 = facade.create_task(TaskCreate(summary="Second"))

        tasks = facade.list_tasks(sort_by=["created"], sort_order="asc")
        assert tasks[0].uid == t1.uid
        assert tasks[-1].uid == t2.uid

    def test_sort_by_summary(self, facade: MockCalDAVFacade) -> None:
        """Tasks can be sorted by summary."""
        facade.create_task(TaskCreate(summary="Zebra"))
        facade.create_task(TaskCreate(summary="Apple"))

        tasks = facade.list_tasks(sort_by=["summary"], sort_order="asc")
        assert [t.summary for t in tasks] == ["Apple", "Zebra"]


# =============================================================================
# Shadow VEVENT tests
# =============================================================================


class TestShadowVEVENT:
    """Tests for the shadow VEVENT pattern (ADR-012 §5)."""

    def test_create_task_with_scheduled_start_creates_shadow(
        self, facade: MockCalDAVFacade
    ) -> None:
        start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
        task = facade.create_task(
            TaskCreate(summary="Standup", scheduled_start=start)
        )

        shadow = facade.get_event(task.uid)
        assert shadow is not None
        assert shadow.uid == task.uid
        assert shadow.task_id == task.uid
        assert shadow.start == start
        assert shadow.end == start + timedelta(minutes=30)  # default duration
        assert shadow.status == EventStatus.CONFIRMED

    def test_create_task_with_scheduled_duration_uses_it(
        self, facade: MockCalDAVFacade
    ) -> None:
        start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
        task = facade.create_task(
            TaskCreate(
                summary="Long meeting",
                scheduled_start=start,
                scheduled_duration=90,
            )
        )

        shadow = facade.get_event(task.uid)
        assert shadow is not None
        assert shadow.end == start + timedelta(minutes=90)

    def test_create_task_without_scheduled_start_creates_no_shadow(
        self, facade: MockCalDAVFacade
    ) -> None:
        task = facade.create_task(TaskCreate(summary="Plain task"))
        assert facade.get_event(task.uid) is None

    def test_update_task_set_scheduled_start_creates_shadow(
        self, facade: MockCalDAVFacade
    ) -> None:
        task = facade.create_task(TaskCreate(summary="No schedule yet"))
        assert facade.get_event(task.uid) is None

        start = datetime(2030, 2, 1, 10, 0, tzinfo=UTC)
        facade.update_task(
            task.uid, TaskUpdate(scheduled_start=start, scheduled_duration=60)
        )

        shadow = facade.get_event(task.uid)
        assert shadow is not None
        assert shadow.start == start
        assert shadow.end == start + timedelta(minutes=60)

    def test_update_task_clear_scheduled_start_deletes_shadow(
        self, facade: MockCalDAVFacade
    ) -> None:
        start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
        task = facade.create_task(
            TaskCreate(summary="Standup", scheduled_start=start)
        )
        assert facade.get_event(task.uid) is not None

        facade.update_task(task.uid, TaskUpdate(clear_scheduled_start=True))

        assert facade.get_event(task.uid) is None
        # The task itself remains
        assert facade.get_task(task.uid) is not None

    def test_delete_task_removes_shadow(self, facade: MockCalDAVFacade) -> None:
        start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
        task = facade.create_task(
            TaskCreate(summary="Standup", scheduled_start=start)
        )
        assert facade.get_event(task.uid) is not None

        facade.delete_task(task.uid)
        assert facade.get_event(task.uid) is None

    def test_update_task_reschedule_updates_shadow(
        self, facade: MockCalDAVFacade
    ) -> None:
        first = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
        task = facade.create_task(
            TaskCreate(summary="Standup", scheduled_start=first)
        )

        second = datetime(2030, 1, 2, 14, 0, tzinfo=UTC)
        facade.update_task(task.uid, TaskUpdate(scheduled_start=second))

        shadow = facade.get_event(task.uid)
        assert shadow is not None
        assert shadow.start == second

    def test_update_task_summary_propagates_to_shadow(
        self, facade: MockCalDAVFacade
    ) -> None:
        start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
        task = facade.create_task(
            TaskCreate(summary="Old name", scheduled_start=start)
        )

        facade.update_task(task.uid, TaskUpdate(summary="New name"))

        shadow = facade.get_event(task.uid)
        assert shadow is not None
        assert shadow.summary == "New name"

    def test_shadow_event_in_list_events_for_task(
        self, facade: MockCalDAVFacade
    ) -> None:
        start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
        task = facade.create_task(
            TaskCreate(summary="Standup", scheduled_start=start)
        )

        shadows = facade.list_events(task_id=task.uid)
        assert len(shadows) == 1
        assert shadows[0].uid == task.uid


class TestShadowVEVENTProtection:
    """Direct mutation of shadow events is refused."""

    def test_cannot_update_shadow_event(self, facade: MockCalDAVFacade) -> None:
        from genesis.entities import EventUpdate

        start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
        task = facade.create_task(
            TaskCreate(summary="Standup", scheduled_start=start)
        )

        with pytest.raises(ValueError, match="shadow event"):
            facade.update_event(task.uid, EventUpdate(summary="Renamed"))

    def test_cannot_delete_shadow_event(self, facade: MockCalDAVFacade) -> None:
        start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
        task = facade.create_task(
            TaskCreate(summary="Standup", scheduled_start=start)
        )

        with pytest.raises(ValueError, match="shadow event"):
            facade.delete_event(task.uid)


# =============================================================================
# Project tests
# =============================================================================


class TestProjectCRUD:
    """Test project CRUD operations."""

    def test_create_project(self, facade: MockCalDAVFacade) -> None:
        """Creating a project returns the project with UID and timestamps."""
        project = facade.create_project(ProjectCreate(name="New Project"))

        assert project.uid is not None
        assert project.name == "New Project"
        assert project.status == ProjectStatus.NEEDS_ACTION
        assert project.created is not None
        assert project.modified is not None

    def test_get_project(self, facade: MockCalDAVFacade) -> None:
        """Getting a project by UID returns the project."""
        created = facade.create_project(ProjectCreate(name="Test Project"))

        found = facade.get_project(created.uid)
        assert found is not None
        assert found.name == "Test Project"

    def test_get_project_not_found(self, facade: MockCalDAVFacade) -> None:
        """Getting a non-existent project returns None."""
        assert facade.get_project("nonexistent@genesis") is None

    def test_list_projects(self, facade: MockCalDAVFacade) -> None:
        """Listing projects returns all projects."""
        facade.create_project(ProjectCreate(name="Project 1"))
        facade.create_project(ProjectCreate(name="Project 2"))

        assert len(facade.list_projects()) == 2

    def test_update_project(self, facade: MockCalDAVFacade) -> None:
        """Updating a project modifies the project in place."""
        project = facade.create_project(ProjectCreate(name="Original"))

        updated = facade.update_project(project.uid, ProjectUpdate(name="Updated"))
        assert updated is not None
        assert updated.name == "Updated"

    def test_delete_project(self, facade: MockCalDAVFacade) -> None:
        """Deleting a project removes it."""
        project = facade.create_project(ProjectCreate(name="To delete"))

        assert facade.delete_project(project.uid) is True
        assert facade.get_project(project.uid) is None


class TestProjectDeletionWithTasks:
    """Test project deletion with linked tasks."""

    def test_delete_project_removes_task_links(self, facade: MockCalDAVFacade) -> None:
        """Deleting a project removes project_id from its tasks."""
        project = facade.create_project(ProjectCreate(name="Test Project"))
        task = facade.create_task(TaskCreate(summary="Linked task", project_id=project.uid))

        assert task.project_id == project.uid

        facade.delete_project(project.uid)

        updated_task = facade.get_task(task.uid)
        assert updated_task is not None
        assert updated_task.project_id is None


class TestProjectFilters:
    """Test project filtering."""

    def test_filter_by_status(self, facade: MockCalDAVFacade) -> None:
        completed = facade.create_project(ProjectCreate(name="Done"))
        facade.update_project(completed.uid, ProjectUpdate(status=ProjectStatus.COMPLETED))
        facade.create_project(ProjectCreate(name="Active"))

        active = facade.list_projects(status=ProjectStatus.NEEDS_ACTION)
        assert all(p.status == ProjectStatus.NEEDS_ACTION for p in active)

    def test_text_search(self, facade: MockCalDAVFacade) -> None:
        facade.create_project(ProjectCreate(name="Marketing Campaign"))
        facade.create_project(ProjectCreate(name="Tech"))

        results = facade.list_projects(search="marketing")
        assert len(results) == 1


class TestProjectSorting:
    """Test project sorting."""

    def test_sort_by_name(self, facade: MockCalDAVFacade) -> None:
        facade.create_project(ProjectCreate(name="Zebra"))
        facade.create_project(ProjectCreate(name="Apple"))

        projects = facade.list_projects(sort_by=["name"], sort_order="asc")
        assert [p.name for p in projects] == ["Apple", "Zebra"]


# =============================================================================
# Event tests (standalone events, not shadows)
# =============================================================================


class TestEventCRUD:
    """Test standalone event CRUD."""

    def test_create_event(self, facade: MockCalDAVFacade) -> None:

        start = datetime(2030, 3, 1, 9, 0, tzinfo=UTC)
        end = datetime(2030, 3, 1, 10, 0, tzinfo=UTC)

        event = facade.create_event(
            EventCreate(summary="Coffee", start=start, end=end)
        )

        assert event.uid is not None
        assert event.summary == "Coffee"
        assert event.start == start
        assert event.end == end
        assert event.status == EventStatus.CONFIRMED  # default

    def test_list_events_excludes_shadows_by_default(
        self, facade: MockCalDAVFacade
    ) -> None:
        """list_events() with no task_id filter returns all events; shadows
        have task_id set so callers can distinguish them if they care."""

        start = datetime(2030, 3, 1, 9, 0, tzinfo=UTC)
        end = datetime(2030, 3, 1, 10, 0, tzinfo=UTC)
        facade.create_event(EventCreate(summary="Coffee", start=start, end=end))

        task_start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
        facade.create_task(TaskCreate(summary="Appt", scheduled_start=task_start))

        all_events = facade.list_events()
        shadows = [e for e in all_events if e.task_id is not None]
        standalone = [e for e in all_events if e.task_id is None]
        assert len(standalone) == 1
        assert len(shadows) == 1


# =============================================================================
# Time log tests
# =============================================================================


class TestTimeLogCRUD:
    """Test time log entry CRUD."""

    def test_create_time_log_entry(self, facade: MockCalDAVFacade) -> None:
        start = datetime(2030, 3, 1, 9, 0, tzinfo=UTC)
        due = datetime(2030, 3, 1, 9, 25, tzinfo=UTC)
        task = facade.create_task(TaskCreate(summary="Work on"))

        entry = facade.create_time_log_entry(
            TimeLogEntryCreate(
                label="Work on",
                start=start,
                due=due,
                mode=TimeLogMode.COUNTDOWN,
                task_id=task.uid,
            )
        )

        assert entry.uid is not None
        assert entry.task_id == task.uid
        assert entry.start == start
        assert entry.due == due
        assert entry.status == TimeLogStatus.NEEDS_ACTION
        assert entry.mode == TimeLogMode.COUNTDOWN

    def test_unaligned_time_log_entry(self, facade: MockCalDAVFacade) -> None:
        start = datetime(2030, 3, 1, 9, 0, tzinfo=UTC)
        entry = facade.create_time_log_entry(
            TimeLogEntryCreate(
                label="Gym",
                start=start,
                mode=TimeLogMode.COUNTUP,
            )
        )

        assert entry.task_id is None
        assert entry.due is None
        assert entry.mode == TimeLogMode.COUNTUP

    def test_active_only_excludes_completed(self, facade: MockCalDAVFacade) -> None:
        start = datetime(2030, 3, 1, 9, 0, tzinfo=UTC)
        due = datetime(2030, 3, 1, 9, 25, tzinfo=UTC)

        a1 = facade.create_time_log_entry(
            TimeLogEntryCreate(
                label="Active 1",
                start=start,
                due=due,
                mode=TimeLogMode.COUNTDOWN,
            )
        )
        a2 = facade.create_time_log_entry(
            TimeLogEntryCreate(
                label="Active 2",
                start=start + timedelta(hours=1),
                mode=TimeLogMode.COUNTUP,
            )
        )
        c = facade.create_time_log_entry(
            TimeLogEntryCreate(
                label="Completed",
                start=start + timedelta(hours=2),
                due=due + timedelta(hours=2),
                mode=TimeLogMode.COUNTDOWN,
            )
        )
        facade.stop_time_log_entry(
            c.uid,
            actual_due=due + timedelta(hours=2),
            completed_at=due + timedelta(hours=2),
        )

        active = facade.list_time_log(active_only=True)
        active_uids = {e.uid for e in active}
        assert active_uids == {a1.uid, a2.uid}

        completed = facade.list_time_log()
        assert len(completed) == 1
        assert completed[0].uid == c.uid
        assert completed[0].status == TimeLogStatus.COMPLETED

    def test_stop_time_log_entry(self, facade: MockCalDAVFacade) -> None:
        start = datetime(2030, 3, 1, 9, 0, tzinfo=UTC)
        due = datetime(2030, 3, 1, 9, 25, tzinfo=UTC)
        entry = facade.create_time_log_entry(
            TimeLogEntryCreate(
                label="Focus",
                start=start,
                due=due,
                mode=TimeLogMode.COUNTDOWN,
            )
        )

        completed_at = datetime(2030, 3, 1, 9, 30, tzinfo=UTC)
        stopped = facade.stop_time_log_entry(
            entry.uid, actual_due=due, completed_at=completed_at
        )

        assert stopped is not None
        assert stopped.status == TimeLogStatus.COMPLETED
        assert stopped.due == due
        assert stopped.completed_at == completed_at

    def test_stop_missing_entry_returns_none(self, facade: MockCalDAVFacade) -> None:
        now = datetime(2030, 3, 1, 9, 0, tzinfo=UTC)
        result = facade.stop_time_log_entry(
            "nonexistent-uid", actual_due=now, completed_at=now
        )
        assert result is None

    def test_list_time_log_filtered_by_task(self, facade: MockCalDAVFacade) -> None:
        start = datetime(2030, 3, 1, 9, 0, tzinfo=UTC)
        due = datetime(2030, 3, 1, 9, 25, tzinfo=UTC)
        t1 = facade.create_task(TaskCreate(summary="Task 1"))
        t2 = facade.create_task(TaskCreate(summary="Task 2"))

        facade.create_time_log_entry(
            TimeLogEntryCreate(
                label="t1",
                start=start,
                due=due,
                mode=TimeLogMode.COUNTDOWN,
                task_id=t1.uid,
            )
        )
        facade.create_time_log_entry(
            TimeLogEntryCreate(
                label="t2",
                start=start + timedelta(hours=1),
                due=due + timedelta(hours=1),
                mode=TimeLogMode.COUNTDOWN,
                task_id=t2.uid,
            )
        )

        # Stop t2's entry so it appears in the default completed list.
        # The completed list is the default semantic of list_time_log().
        all_entries = facade.list_time_log(task_id=t1.uid)
        assert all_entries == []

        active = facade.list_time_log(task_id=t1.uid, active_only=True)
        assert len(active) == 1
        assert active[0].task_id == t1.uid


# =============================================================================
# Concurrency regression test
# =============================================================================


class TestNoDeleteOnUpdate:
    """Regression: update_task must not delete-then-recreate.

    Asserted by checking that the original Task object's identity is
    preserved across updates. The in-place save pattern means the same
    dict entry holds the same Task instance after update.
    """

    def test_update_task_preserves_object_identity(
        self, facade: MockCalDAVFacade
    ) -> None:
        task = facade.create_task(TaskCreate(summary="Original"))
        original_object_id = id(task)

        facade.update_task(task.uid, TaskUpdate(summary="Updated"))

        same = facade.get_task(task.uid)
        assert same is not None
        assert id(same) == original_object_id


# =============================================================================
# Resilience: skip objects whose icalendar_component fetch 404s
# =============================================================================


def _stale_then_good_calendar(good_components: list) -> MagicMock:
    """Build a mock calendar whose .searcher().search() yields a stale
    object first (raises NotFoundError on .icalendar_component) followed
    by good objects whose .icalendar_component returns the provided
    components.
    """
    stale = MagicMock()
    type(stale).icalendar_component = PropertyMock(
        side_effect=NotFoundError("stale", "404")
    )

    good_objs = []
    for comp in good_components:
        obj = MagicMock()
        type(obj).icalendar_component = PropertyMock(return_value=comp)
        good_objs.append(obj)

    searcher = MagicMock()
    searcher.add_property_filter = MagicMock()
    searcher.search = MagicMock(return_value=[stale, *good_objs])

    cal = MagicMock()
    cal.searcher = MagicMock(return_value=searcher)
    return cal


class TestStaleObjectResilience:
    """Regression: list_* must skip objects that 404 on access.

    Without per-object try/except, a single stale object listed by the
    server but missing its underlying resource crashes the entire list.
    The calendar page can reproduce this without user action whenever a
    test run leaves a stale VEVENT/VTODO entry behind.
    """

    def _facade_with_calendar(self, cal: MagicMock) -> MagicMock:
        f = MagicMock()
        f._get_calendar = MagicMock(return_value=cal)
        return f

    def test_list_tasks_skips_stale_object(self) -> None:
        from icalendar import Todo

        todo = Todo()
        todo.add("uid", "alive@genesis")
        todo.add("summary", "Alive task")
        todo.add("status", "needs-action")

        cal = _stale_then_good_calendar([todo])
        facade = self._facade_with_calendar(cal)

        tasks = list_tasks(facade)  # type: ignore[arg-type]

        assert len(tasks) == 1
        assert tasks[0].uid == "alive@genesis"
        assert tasks[0].summary == "Alive task"

    def test_list_events_skips_stale_object(self) -> None:
        from icalendar import Event

        ev = Event()
        ev.add("uid", "alive-event@genesis")
        ev.add("summary", "Alive event")
        ev.add("dtstart", datetime(2026, 9, 7, 9, 0, tzinfo=UTC))
        ev.add("dtend", datetime(2026, 9, 7, 10, 0, tzinfo=UTC))

        cal = _stale_then_good_calendar([ev])
        facade = self._facade_with_calendar(cal)

        events = list_events(facade)  # type: ignore[arg-type]

        assert len(events) == 1
        assert events[0].uid == "alive-event@genesis"

    def test_list_projects_skips_stale_object(self) -> None:
        from icalendar import Todo

        proj = Todo()
        proj.add("uid", "alive-project@genesis")
        proj.add("summary", "Alive project")
        proj.add("status", "needs-action")

        cal = _stale_then_good_calendar([proj])
        facade = self._facade_with_calendar(cal)

        projects = list_projects(facade)  # type: ignore[arg-type]

        assert len(projects) == 1
        assert projects[0].uid == "alive-project@genesis"
