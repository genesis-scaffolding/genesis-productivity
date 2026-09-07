"""Tests for CalDAV facade.

Unit tests use MockCalDAVFacade for speed.
Integration tests (marked @pytest.mark.integration) spin up a real Radicale server.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from genesis.caldav.facade import CalDAVFacade
from genesis.dav.server import ServerManager, is_server_running
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


@pytest.fixture(scope="function")
def radicale_server() -> Generator[tuple[str, int], None, None]:
    """Start a Radicale server for testing.

    Yields (host, port) tuple.
    """
    import tempfile

    from genesis.configs import DAV

    host = "127.0.0.1"
    port = 39232  # Use a high port for testing

    with tempfile.TemporaryDirectory() as tmpdir:
        dav_config = DAV(
            host=host,
            port=port,
            username="test",
            password="test",
        )
        manager = ServerManager(
            dav_config=dav_config,
            data_path=Path(tmpdir) / "radicale",
            startup_timeout=15,
        )
        manager.start()

        # Wait a bit for server to be fully ready
        time.sleep(1)

        yield host, port

        manager.stop()


@pytest.fixture
def facade(radicale_server: tuple[str, int]) -> Generator[CalDAVFacade, None, None]:
    """Create a CalDAVFacade connected to the test server."""
    host, port = radicale_server
    server_url = f"http://{host}:{port}"

    fac = CalDAVFacade(
        server_url=server_url,
        username="test",
        password="test",
    )

    yield fac

    # Cleanup: delete all calendars
    try:
        principal = fac._get_principal()
        for cal in principal.calendars():  # type: ignore[attr-defined]
            try:
                cal.delete()
            except Exception:
                pass
    except Exception:
        pass


# =============================================================================
# Task tests
# =============================================================================


@pytest.mark.integration
class TestTaskCRUD:
    """Test task CRUD operations."""

    def test_create_task(self, facade: CalDAVFacade) -> None:
        """Creating a task returns the task with UID and timestamps."""
        task = facade.create_task(TaskCreate(title="Test task"))

        assert task.uid is not None
        assert task.title == "Test task"
        assert task.status == TaskStatus.NEEDS_ACTION
        assert task.created is not None
        assert task.modified is not None

    def test_get_task(self, facade: CalDAVFacade) -> None:
        """Getting a task by UID returns the task."""
        created = facade.create_task(TaskCreate(title="Test task"))

        found = facade.get_task(created.uid)

        assert found is not None
        assert found.uid == created.uid
        assert found.title == "Test task"

    def test_get_task_not_found(self, facade: CalDAVFacade) -> None:
        """Getting a non-existent task returns None."""
        found = facade.get_task("nonexistent@genesis")
        assert found is None

    def test_list_tasks(self, facade: CalDAVFacade) -> None:
        """Listing tasks returns all tasks."""
        facade.create_task(TaskCreate(title="Task 1"))
        facade.create_task(TaskCreate(title="Task 2"))

        tasks = facade.list_tasks()

        assert len(tasks) == 2

    def test_update_task(self, facade: CalDAVFacade) -> None:
        """Updating a task modifies the task."""
        task = facade.create_task(TaskCreate(title="Original"))

        updated = facade.update_task(task.uid, TaskUpdate(title="Updated"))

        assert updated is not None
        assert updated.title == "Updated"
        assert updated.created is not None
        assert updated.modified is not None

    def test_update_task_sets_completed_at(self, facade: CalDAVFacade) -> None:
        """Marking a task completed sets completed_at."""
        task = facade.create_task(TaskCreate(title="To complete"))

        updated = facade.update_task(task.uid, TaskUpdate(status=TaskStatus.COMPLETED))

        assert updated is not None
        assert updated.status == TaskStatus.COMPLETED
        assert updated.completed_at is not None

    def test_update_task_clears_completed_at(self, facade: CalDAVFacade) -> None:
        """Reopening a completed task clears completed_at."""
        task = facade.create_task(TaskCreate(title="To complete"))
        facade.update_task(task.uid, TaskUpdate(status=TaskStatus.COMPLETED))

        updated = facade.update_task(task.uid, TaskUpdate(status=TaskStatus.NEEDS_ACTION))

        assert updated is not None
        assert updated.status == TaskStatus.NEEDS_ACTION
        assert updated.completed_at is None

    def test_delete_task(self, facade: CalDAVFacade) -> None:
        """Deleting a task removes it."""
        task = facade.create_task(TaskCreate(title="To delete"))

        result = facade.delete_task(task.uid)

        assert result is True
        assert facade.get_task(task.uid) is None

    def test_delete_task_not_found(self, facade: CalDAVFacade) -> None:
        """Deleting a non-existent task returns False."""
        result = facade.delete_task("nonexistent@genesis")
        assert result is False


@pytest.mark.integration
class TestTaskFilters:
    """Test task filtering."""

    def test_filter_by_status(self, facade: CalDAVFacade) -> None:
        """Filtering by status returns only matching tasks."""
        facade.create_task(TaskCreate(title="Active task"))
        completed = facade.create_task(TaskCreate(title="Completed task"))
        facade.update_task(completed.uid, TaskUpdate(status=TaskStatus.COMPLETED))

        active = facade.list_tasks(status=TaskStatus.NEEDS_ACTION)
        assert all(t.status == TaskStatus.NEEDS_ACTION for t in active)

        completed_tasks = facade.list_tasks(status=TaskStatus.COMPLETED)
        assert all(t.status == TaskStatus.COMPLETED for t in completed_tasks)

    def test_text_search(self, facade: CalDAVFacade) -> None:
        """Text search finds tasks by summary or description."""
        facade.create_task(TaskCreate(title="Buy groceries"))
        facade.create_task(
            TaskCreate(title="Fix bug", description="Memory leak in parser")
        )

        results = facade.list_tasks(search="bug")
        assert len(results) >= 1
        assert any("bug" in t.title.lower() or "bug" in (t.description or "").lower() for t in results)


@pytest.mark.integration
class TestTaskSorting:
    """Test task sorting."""

    def test_sort_by_created(self, facade: CalDAVFacade) -> None:
        """Tasks can be sorted by created date."""
        t1 = facade.create_task(TaskCreate(title="First"))
        t2 = facade.create_task(TaskCreate(title="Second"))

        tasks = facade.list_tasks(sort_by=["created"], sort_order="asc")
        assert tasks[0].uid == t1.uid
        assert tasks[-1].uid == t2.uid

    def test_sort_by_summary(self, facade: CalDAVFacade) -> None:
        """Tasks can be sorted by summary."""
        facade.create_task(TaskCreate(title="Zebra"))
        facade.create_task(TaskCreate(title="Apple"))

        tasks = facade.list_tasks(sort_by=["summary"], sort_order="asc")
        summaries = [t.title for t in tasks]
        assert summaries == sorted(summaries)


@pytest.mark.integration
class TestServerSideFilters:
    """Verify that list_* uses server-side calendar-query against Radicale.

    These tests check filters that the new searcher-based implementation
    pushes down to RFC 4791 REPORT:
      - VTODO X-PROJECT-ID exact match
      - VTODO time-range (DTSTART/DUE/DTEND within window)
      - VEVENT time-range (event overlap with window)
    """

    def test_tasks_filter_by_project_id_server_side(
        self, facade: CalDAVFacade
    ) -> None:
        """list_tasks(project_id=uid) returns only tasks with that X-PROJECT-ID."""
        proj_a = facade.create_project(ProjectCreate(name="Alpha"))
        proj_b = facade.create_project(ProjectCreate(name="Beta"))

        facade.create_task(TaskCreate(title="A-1", project_id=proj_a.uid))
        facade.create_task(TaskCreate(title="A-2", project_id=proj_a.uid))
        facade.create_task(TaskCreate(title="B-1", project_id=proj_b.uid))

        alpha_tasks = facade.list_tasks(project_id=proj_a.uid)
        assert {t.title for t in alpha_tasks} == {"A-1", "A-2"}

        beta_tasks = facade.list_tasks(project_id=proj_b.uid)
        assert {t.title for t in beta_tasks} == {"B-1"}

    def test_tasks_time_range_filter_server_side(
        self, facade: CalDAVFacade
    ) -> None:
        """list_tasks(start=..., end=...) narrows to tasks with DTSTART/DUE/DTEND in window.

        The server-side filter is a SUPERSET of DTSTART-only matching; we
        additionally apply a client-side refinement to scheduled_start.
        """
        from genesis.entities import TaskUpdate

        # In-window: scheduled Tuesday at 10:00
        in_window = facade.create_task(TaskCreate(title="In window"))
        tuesday_10 = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
        facade.update_task(
            in_window.uid, TaskUpdate(scheduled_start=tuesday_10)
        )

        # Out of window: scheduled the following week
        next_week = facade.create_task(TaskCreate(title="Next week"))
        next_monday_10 = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
        facade.update_task(
            next_week.uid, TaskUpdate(scheduled_start=next_monday_10)
        )

        # In-window by DUE only (no scheduled_start)
        due_in_window = facade.create_task(TaskCreate(title="Due in window"))
        next_monday_due = datetime(2026, 9, 13, 23, 59, tzinfo=UTC)
        facade.update_task(
            due_in_window.uid,
            TaskUpdate(hard_deadline=next_monday_due, clear_scheduled_start=True),
        )

        week_start = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)
        week_end = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
        week_tasks = facade.list_tasks(start=week_start, end=week_end)

        # Server returns in-window AND due-in-window (by DUE).
        uids = {t.uid for t in week_tasks}
        assert in_window.uid in uids
        assert due_in_window.uid in uids
        assert next_week.uid not in uids

        # Apply the same strict DTSTART refinement the calendar page does.
        scheduled_in_window = [
            t for t in week_tasks
            if t.scheduled_start is not None
            and week_start <= t.scheduled_start < week_end
        ]
        assert {t.title for t in scheduled_in_window} == {"In window"}

    def test_events_time_range_filter_server_side(
        self, facade: CalDAVFacade
    ) -> None:
        """list_events(start=..., end=...) returns events that overlap the window."""
        from genesis.entities import EventCreate

        # Event fully inside the window
        facade.create_event(EventCreate(
            summary="Mid-week meeting",
            start=datetime(2026, 9, 9, 10, 0, tzinfo=UTC),
            end=datetime(2026, 9, 9, 11, 0, tzinfo=UTC),
        ))
        # Event that starts before the window and ends inside it
        facade.create_event(EventCreate(
            summary="Overlapping start",
            start=datetime(2026, 9, 6, 23, 0, tzinfo=UTC),
            end=datetime(2026, 9, 7, 1, 0, tzinfo=UTC),
        ))
        # Event entirely before the window
        facade.create_event(EventCreate(
            summary="Before window",
            start=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
            end=datetime(2026, 9, 1, 11, 0, tzinfo=UTC),
        ))
        # Event entirely after the window
        facade.create_event(EventCreate(
            summary="After window",
            start=datetime(2026, 9, 20, 10, 0, tzinfo=UTC),
            end=datetime(2026, 9, 20, 11, 0, tzinfo=UTC),
        ))

        week_start = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)
        week_end = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
        week_events = facade.list_events(
            start=week_start, end=week_end,
            status=EventStatus.CONFIRMED,
        )

        summaries = {e.summary for e in week_events}
        assert "Mid-week meeting" in summaries
        # Overlapping-start was entirely before the window (start=Sun 23:00,
        # end=Mon 01:00). The Mon 01:00 end IS within [Mon 00:00, Mon+7 00:00),
        # so RFC 4791 §9.9 VEVENT overlap keeps this event.
        assert "Overlapping start" in summaries
        assert "Before window" not in summaries
        assert "After window" not in summaries


# =============================================================================
# Project tests
# =============================================================================


@pytest.mark.integration
class TestProjectCRUD:
    """Test project CRUD operations."""

    def test_create_project(self, facade: CalDAVFacade) -> None:
        """Creating a project returns the project with UID and timestamps."""
        project = facade.create_project(ProjectCreate(name="New Project"))

        assert project.uid is not None
        assert project.name == "New Project"
        assert project.status == ProjectStatus.NEEDS_ACTION
        assert project.created is not None
        assert project.modified is not None

    def test_get_project(self, facade: CalDAVFacade) -> None:
        """Getting a project by UID returns the project."""
        created = facade.create_project(ProjectCreate(name="Test Project"))

        found = facade.get_project(created.uid)

        assert found is not None
        assert found.uid == created.uid
        assert found.name == "Test Project"

    def test_get_project_not_found(self, facade: CalDAVFacade) -> None:
        """Getting a non-existent project returns None."""
        found = facade.get_project("nonexistent@genesis")
        assert found is None

    def test_list_projects(self, facade: CalDAVFacade) -> None:
        """Listing projects returns all projects."""
        facade.create_project(ProjectCreate(name="Project 1"))
        facade.create_project(ProjectCreate(name="Project 2"))

        projects = facade.list_projects()

        assert len(projects) == 2

    def test_update_project(self, facade: CalDAVFacade) -> None:
        """Updating a project modifies the project."""
        project = facade.create_project(ProjectCreate(name="Original"))

        updated = facade.update_project(project.uid, ProjectUpdate(name="Updated"))

        assert updated is not None
        assert updated.name == "Updated"
        assert updated.created is not None
        assert updated.modified is not None

    def test_delete_project(self, facade: CalDAVFacade) -> None:
        """Deleting a project removes it."""
        project = facade.create_project(ProjectCreate(name="To delete"))

        result = facade.delete_project(project.uid)

        assert result is True
        assert facade.get_project(project.uid) is None


@pytest.mark.integration
class TestProjectDeletionWithTasks:
    """Test project deletion with linked tasks."""

    def test_delete_project_removes_task_links(self, facade: CalDAVFacade) -> None:
        """Deleting a project removes project_id from its tasks."""
        project = facade.create_project(ProjectCreate(name="Test Project"))
        task = facade.create_task(TaskCreate(title="Linked task", project_id=project.uid))

        assert task.project_id == project.uid

        facade.delete_project(project.uid)

        # Task should still exist but with project_id cleared
        updated_task = facade.get_task(task.uid)
        assert updated_task is not None
        assert updated_task.project_id is None


@pytest.mark.integration
class TestProjectFilters:
    """Test project filtering."""

    def test_filter_by_status(self, facade: CalDAVFacade) -> None:
        """Filtering by status returns only matching projects."""
        facade.create_project(ProjectCreate(name="Active project"))
        completed = facade.create_project(ProjectCreate(name="Completed project"))
        facade.update_project(completed.uid, ProjectUpdate(status=ProjectStatus.COMPLETED))

        active_projects = facade.list_projects(status=ProjectStatus.NEEDS_ACTION)
        assert all(p.status == ProjectStatus.NEEDS_ACTION for p in active_projects)

        completed_projects = facade.list_projects(status=ProjectStatus.COMPLETED)
        assert all(p.status == ProjectStatus.COMPLETED for p in completed_projects)

    def test_text_search(self, facade: CalDAVFacade) -> None:
        """Text search finds projects by name or description."""
        facade.create_project(ProjectCreate(name="Marketing Campaign"))
        facade.create_project(
            ProjectCreate(name="Tech Project", description="Hardware development")
        )

        results = facade.list_projects(search="marketing")
        assert len(results) >= 1
        assert any("marketing" in p.name.lower() for p in results)


@pytest.mark.integration
class TestProjectSorting:
    """Test project sorting."""

    def test_sort_by_name(self, facade: CalDAVFacade) -> None:
        """Projects can be sorted by name."""
        facade.create_project(ProjectCreate(name="Zebra Project"))
        facade.create_project(ProjectCreate(name="Apple Project"))

        projects = facade.list_projects(sort_by=["name"], sort_order="asc")

        names = [p.name for p in projects]
        assert names == sorted(names)


# =============================================================================
# Server manager tests
# =============================================================================


@pytest.mark.integration
class TestServerManager:
    """Test server manager functionality."""

    def test_is_server_running(self, radicale_server: tuple[str, int]) -> None:
        """Server is running when started."""
        host, port = radicale_server
        assert is_server_running(host, port) is True

    def test_is_server_not_running(self) -> None:
        """Server is not running on invalid port."""
        assert is_server_running("127.0.0.1", 19999) is False

    def test_ensure_user_adds_new_user(self, tmp_path: Path) -> None:
        """ensure_user adds a new user to htpasswd."""
        from genesis.configs import DAV
        from genesis.dav.server import ServerManager

        # Use a unique port to avoid conflict with other servers
        dav_config = DAV(host="127.0.0.1", port=39500, username="test", password="test")
        manager = ServerManager(dav_config=dav_config, data_path=tmp_path / "radicale")

        # Start server (creates initial user)
        manager.start()

        try:
            # Check initial user exists
            htpasswd_path = tmp_path / "radicale" / "users"
            assert htpasswd_path.exists()
            with open(htpasswd_path) as f:
                content = f.read()
            assert "test:" in content

            # Ensure another user
            manager.ensure_user("alice")

            # Check alice was added
            with open(htpasswd_path) as f:
                content = f.read()
            assert "alice:" in content

            # Ensure existing user (should not add duplicate)
            content_before = content
            manager.ensure_user("alice")
            with open(htpasswd_path) as f:
                content_after = f.read()
            assert content_before == content_after  # No change

        finally:
            manager.stop()


# =============================================================================
# Shadow VEVENT (integration)
# =============================================================================


@pytest.mark.integration
class TestShadowVEVENTIntegration:
    """Verify the shadow VEVENT pattern actually round-trips through Radicale."""

    def test_create_task_with_schedule_writes_shadow_to_events_collection(
        self, facade: CalDAVFacade
    ) -> None:
        start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
        task = facade.create_task(
            TaskCreate(title="Standup", scheduled_start=start)
        )

        # The shadow should appear in the events collection with the same UID
        shadow = facade.get_event(task.uid)
        assert shadow is not None
        assert shadow.task_id == task.uid
        assert shadow.start == start

    def test_clear_scheduled_start_removes_shadow(
        self, facade: CalDAVFacade
    ) -> None:

        start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
        task = facade.create_task(
            TaskCreate(title="Standup", scheduled_start=start)
        )
        assert facade.get_event(task.uid) is not None

        facade.update_task(task.uid, TaskUpdate(clear_scheduled_start=True))
        assert facade.get_event(task.uid) is None

    def test_delete_task_removes_shadow(self, facade: CalDAVFacade) -> None:
        start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
        task = facade.create_task(
            TaskCreate(title="Standup", scheduled_start=start)
        )
        facade.delete_task(task.uid)
        assert facade.get_event(task.uid) is None

    def test_reschedule_updates_shadow(self, facade: CalDAVFacade) -> None:
        first = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
        task = facade.create_task(
            TaskCreate(title="Standup", scheduled_start=first)
        )

        second = datetime(2030, 1, 2, 14, 0, tzinfo=UTC)
        facade.update_task(task.uid, TaskUpdate(scheduled_start=second))

        shadow = facade.get_event(task.uid)
        assert shadow is not None
        assert shadow.start == second


# =============================================================================
# Event CRUD (integration)
# =============================================================================


@pytest.mark.integration
class TestEventCRUDIntegration:
    """Verify standalone event CRUD against Radicale."""

    def test_create_and_get_event(self, facade: CalDAVFacade) -> None:
        start = datetime(2030, 3, 1, 9, 0, tzinfo=UTC)
        end = datetime(2030, 3, 1, 10, 0, tzinfo=UTC)

        event = facade.create_event(
            EventCreate(summary="Coffee", start=start, end=end)
        )
        assert event.status == EventStatus.CONFIRMED

        fetched = facade.get_event(event.uid)
        assert fetched is not None
        assert fetched.summary == "Coffee"

    def test_delete_event(self, facade: CalDAVFacade) -> None:
        start = datetime(2030, 3, 1, 9, 0, tzinfo=UTC)
        end = datetime(2030, 3, 1, 10, 0, tzinfo=UTC)

        event = facade.create_event(
            EventCreate(summary="Coffee", start=start, end=end)
        )
        assert facade.delete_event(event.uid) is True
        assert facade.get_event(event.uid) is None


# =============================================================================
# Time log (integration)
# =============================================================================


@pytest.mark.integration
class TestTimeLogIntegration:
    """Verify time log CRUD against Radicale."""

    def test_create_and_list_time_log(self, facade: CalDAVFacade) -> None:
        start = datetime(2030, 3, 1, 9, 0, tzinfo=UTC)
        due = datetime(2030, 3, 1, 9, 25, tzinfo=UTC)
        task = facade.create_task(TaskCreate(title="Work on"))

        entry = facade.create_time_log_entry(
            TimeLogEntryCreate(
                label="Work on",
                start=start,
                due=due,
                mode=TimeLogMode.COUNTDOWN,
                status=TimeLogStatus.NEEDS_ACTION,
                task_id=task.uid,
            )
        )
        assert entry.task_id == task.uid
        assert entry.mode == TimeLogMode.COUNTDOWN
        assert entry.status == TimeLogStatus.NEEDS_ACTION

        # Default list filters to COMPLETED, so this active entry is invisible.
        assert facade.list_time_log(task_id=task.uid) == []

        # active_only returns the entry.
        active = facade.list_time_log(task_id=task.uid, active_only=True)
        assert len(active) == 1
        assert active[0].uid == entry.uid

    def test_stop_time_log_entry(self, facade: CalDAVFacade) -> None:
        start = datetime(2030, 3, 1, 9, 0, tzinfo=UTC)
        due = datetime(2030, 3, 1, 9, 25, tzinfo=UTC)
        task = facade.create_task(TaskCreate(title="Work on"))

        entry = facade.create_time_log_entry(
            TimeLogEntryCreate(
                label="Work on",
                start=start,
                due=due,
                mode=TimeLogMode.COUNTDOWN,
                task_id=task.uid,
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

        # No longer in active_only list
        assert facade.list_time_log(task_id=task.uid, active_only=True) == []

        # Visible in default (completed) list
        completed = facade.list_time_log(task_id=task.uid)
        assert len(completed) == 1
        assert completed[0].uid == entry.uid

    def test_unaligned_time_log(self, facade: CalDAVFacade) -> None:
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

        completed_at = datetime(2030, 3, 1, 10, 20, tzinfo=UTC)
        stopped = facade.stop_time_log_entry(
            entry.uid,
            actual_due=completed_at,
            completed_at=completed_at,
        )
        assert stopped is not None
        assert stopped.task_id is None
        assert stopped.due == completed_at

        unaligned = facade.list_time_log(active_only=True)
        assert unaligned == []

        history = facade.list_time_log()
        assert len(history) == 1
        assert history[0].task_id is None
        assert history[0].label == "Gym"


# =============================================================================
# Four-collection shape (integration)
# =============================================================================


@pytest.mark.integration
class TestFourCollectionsIntegration:
    """Verify the four fixed collections exist and are separate."""

    def test_each_collection_round_trips_independently(
        self, facade: CalDAVFacade
    ) -> None:
        # Create one of each entity type
        project = facade.create_project(ProjectCreate(name="P"))
        task = facade.create_task(TaskCreate(title="T"))
        start = datetime(2030, 3, 1, 9, 0, tzinfo=UTC)
        end = datetime(2030, 3, 1, 10, 0, tzinfo=UTC)
        event = facade.create_event(
            EventCreate(summary="E", start=start, end=end)
        )
        entry = facade.create_time_log_entry(
            TimeLogEntryCreate(
                label="T",
                start=start,
                due=end,
                mode=TimeLogMode.COUNTDOWN,
                task_id=task.uid,
            )
        )

        # Each lives in its own collection
        assert facade.get_project(project.uid) is not None
        assert facade.get_task(task.uid) is not None
        assert facade.get_event(event.uid) is not None
        assert facade.get_time_log_entry(entry.uid) is not None

        # Lists return only the right type
        assert len(facade.list_projects()) == 1
        assert len(facade.list_tasks()) == 1
        assert len(facade.list_events()) == 1  # standalone only; task has no scheduled_start
        # Active time-log entry; default list filters to COMPLETED.
        assert len(facade.list_time_log(active_only=True)) == 1
        assert len(facade.list_time_log()) == 0

