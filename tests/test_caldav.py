"""Integration tests for CalDAV facade.

These tests spin up a real Radicale server to test the full integration.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from pathlib import Path

import pytest

from genesis.caldav.facade import CalDAVFacade
from genesis.dav.server import ServerManager, is_server_running
from genesis.entities import (
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
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
        task_calendar_name="tasks",
        project_calendar_prefix="project:",
    )

    yield fac

    # Cleanup: delete all calendars
    try:
        principal = fac._get_principal()
        for cal in principal.calendars():
            try:
                cal.delete()
            except Exception:
                pass
    except Exception:
        pass


# =============================================================================
# Task tests
# =============================================================================


class TestTaskCRUD:
    """Test task CRUD operations."""

    def test_create_task(self, facade: CalDAVFacade) -> None:
        """Creating a task returns the task with UID and timestamps."""
        task = facade.create_task(TaskCreate(summary="Buy groceries"))

        assert task.uid.endswith("@genesis")
        assert task.summary == "Buy groceries"
        assert task.status == TaskStatus.NEEDS_ACTION
        assert task.created is not None
        assert task.modified is not None

    def test_get_task(self, facade: CalDAVFacade) -> None:
        """Getting a task by UID returns the task."""
        created = facade.create_task(TaskCreate(summary="Test task"))
        fetched = facade.get_task(created.uid)

        assert fetched is not None
        assert fetched.uid == created.uid
        assert fetched.summary == "Test task"

    def test_get_task_not_found(self, facade: CalDAVFacade) -> None:
        """Getting a non-existent task returns None."""
        result = facade.get_task("nonexistent-uid@genesis")
        assert result is None

    def test_list_tasks(self, facade: CalDAVFacade) -> None:
        """Listing tasks returns all tasks."""
        facade.create_task(TaskCreate(summary="Task 1"))
        facade.create_task(TaskCreate(summary="Task 2"))

        tasks = facade.list_tasks()

        assert len(tasks) >= 2
        summaries = {t.summary for t in tasks}
        assert "Task 1" in summaries
        assert "Task 2" in summaries

    def test_update_task(self, facade: CalDAVFacade) -> None:
        """Updating a task modifies its fields."""
        task = facade.create_task(TaskCreate(summary="Original"))
        updated = facade.update_task(task.uid, TaskUpdate(summary="Modified"))

        assert updated is not None
        assert updated.summary == "Modified"
        assert updated.modified is not None and task.created is not None
        assert updated.modified > task.created

    def test_update_task_sets_completed_at(self, facade: CalDAVFacade) -> None:
        """Completing a task sets completed_at timestamp."""
        task = facade.create_task(TaskCreate(summary="To complete"))
        assert task.completed_at is None

        updated = facade.update_task(task.uid, TaskUpdate(status=TaskStatus.COMPLETED))
        assert updated is not None
        assert updated.completed_at is not None
        assert updated.status == TaskStatus.COMPLETED

    def test_update_task_clears_completed_at(self, facade: CalDAVFacade) -> None:
        """Reopening a completed task clears completed_at."""
        task = facade.create_task(TaskCreate(summary="To complete"))
        facade.update_task(task.uid, TaskUpdate(status=TaskStatus.COMPLETED))

        updated = facade.update_task(task.uid, TaskUpdate(status=TaskStatus.NEEDS_ACTION))
        assert updated is not None
        assert updated.completed_at is None

    def test_delete_task(self, facade: CalDAVFacade) -> None:
        """Deleting a task removes it."""
        task = facade.create_task(TaskCreate(summary="To delete"))
        assert facade.get_task(task.uid) is not None

        result = facade.delete_task(task.uid)
        assert result is True
        assert facade.get_task(task.uid) is None

    def test_delete_task_not_found(self, facade: CalDAVFacade) -> None:
        """Deleting a non-existent task returns False."""
        result = facade.delete_task("nonexistent-uid@genesis")
        assert result is False


# =============================================================================
# Task filters
# =============================================================================


class TestTaskFilters:
    """Test task filtering."""

    def test_filter_by_status(self, facade: CalDAVFacade) -> None:
        """Filtering by status returns only matching tasks."""
        facade.create_task(TaskCreate(summary="Active task"))
        completed = facade.create_task(TaskCreate(summary="Completed task"))
        facade.update_task(completed.uid, TaskUpdate(status=TaskStatus.COMPLETED))

        active_tasks = facade.list_tasks(status=TaskStatus.NEEDS_ACTION)
        assert all(t.status == TaskStatus.NEEDS_ACTION for t in active_tasks)

        completed_tasks = facade.list_tasks(status=TaskStatus.COMPLETED)
        assert all(t.status == TaskStatus.COMPLETED for t in completed_tasks)

    def test_text_search(self, facade: CalDAVFacade) -> None:
        """Text search finds tasks by summary or description."""
        facade.create_task(TaskCreate(summary="Buy groceries"))
        facade.create_task(TaskCreate(summary="Clean house", description="Kitchen only"))

        results = facade.list_tasks(search="groceries")
        assert len(results) >= 1
        assert any("groceries" in t.summary.lower() for t in results)

        results = facade.list_tasks(search="kitchen")
        assert len(results) >= 1
        assert any("kitchen" in (t.description or "").lower() for t in results)


# =============================================================================
# Task sorting
# =============================================================================


class TestTaskSorting:
    """Test task sorting."""

    def test_sort_by_created(self, facade: CalDAVFacade) -> None:
        """Tasks can be sorted by creation date."""
        task1 = facade.create_task(TaskCreate(summary="First"))
        task2 = facade.create_task(TaskCreate(summary="Second"))

        tasks = facade.list_tasks(sort_by=["created"], sort_order="asc")

        # task1 should come before task2
        assert tasks[0].uid == task1.uid or tasks[-1].uid == task2.uid

    def test_sort_by_summary(self, facade: CalDAVFacade) -> None:
        """Tasks can be sorted by summary."""
        facade.create_task(TaskCreate(summary="Zebra"))
        facade.create_task(TaskCreate(summary="Apple"))
        facade.create_task(TaskCreate(summary="Mango"))

        tasks = facade.list_tasks(sort_by=["summary"], sort_order="asc")

        assert len(tasks) >= 3
        # Check the order is correct
        summaries = [t.summary for t in tasks if t.summary in ["Apple", "Mango", "Zebra"]]
        assert summaries == sorted(summaries)


# =============================================================================
# Project tests
# =============================================================================


class TestProjectCRUD:
    """Test project CRUD operations."""

    def test_create_project(self, facade: CalDAVFacade) -> None:
        """Creating a project returns the project with UID and timestamps."""
        project = facade.create_project(ProjectCreate(name="New Project"))

        assert project.uid.endswith("@genesis")
        assert project.name == "New Project"
        assert project.status == ProjectStatus.NEEDS_ACTION
        assert project.created is not None
        assert project.modified is not None

    def test_get_project(self, facade: CalDAVFacade) -> None:
        """Getting a project by UID returns the project."""
        created = facade.create_project(ProjectCreate(name="Test Project"))
        fetched = facade.get_project(created.uid)

        assert fetched is not None
        assert fetched.uid == created.uid
        assert fetched.name == "Test Project"

    def test_get_project_not_found(self, facade: CalDAVFacade) -> None:
        """Getting a non-existent project returns None."""
        result = facade.get_project("nonexistent-uid@genesis")
        assert result is None

    def test_list_projects(self, facade: CalDAVFacade) -> None:
        """Listing projects returns all projects."""
        facade.create_project(ProjectCreate(name="Project 1"))
        facade.create_project(ProjectCreate(name="Project 2"))

        projects = facade.list_projects()

        assert len(projects) >= 2
        names = {p.name for p in projects}
        assert "Project 1" in names
        assert "Project 2" in names

    def test_update_project(self, facade: CalDAVFacade) -> None:
        """Updating a project modifies its fields."""
        project = facade.create_project(ProjectCreate(name="Original"))
        updated = facade.update_project(project.uid, ProjectUpdate(name="Modified"))

        assert updated is not None
        assert updated.name == "Modified"
        assert updated.modified is not None and project.created is not None
        assert updated.modified > project.created

    def test_delete_project(self, facade: CalDAVFacade) -> None:
        """Deleting a project removes it."""
        project = facade.create_project(ProjectCreate(name="To delete"))
        assert facade.get_project(project.uid) is not None

        result = facade.delete_project(project.uid)
        assert result is True
        assert facade.get_project(project.uid) is None


# =============================================================================
# Project deletion with tasks
# =============================================================================


class TestProjectDeletionWithTasks:
    """Test project deletion behavior with linked tasks."""

    def test_delete_project_removes_task_links(self, facade: CalDAVFacade) -> None:
        """Deleting a project removes project_id from its tasks."""
        project = facade.create_project(ProjectCreate(name="Test Project"))
        task = facade.create_task(TaskCreate(summary="Linked task", project_id=project.uid))

        assert task.project_id == project.uid

        facade.delete_project(project.uid)

        # Task should still exist but with project_id cleared
        updated_task = facade.get_task(task.uid)
        assert updated_task is not None
        assert updated_task.project_id is None


# =============================================================================
# Project filters
# =============================================================================


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

        results = facade.list_projects(search="hardware")
        assert len(results) >= 1
        assert any("hardware" in (p.description or "").lower() for p in results)


# =============================================================================
# Project sorting
# =============================================================================


class TestProjectSorting:
    """Test project sorting."""

    def test_sort_by_name(self, facade: CalDAVFacade) -> None:
        """Projects can be sorted by name."""
        facade.create_project(ProjectCreate(name="Zebra Project"))
        facade.create_project(ProjectCreate(name="Apple Project"))
        facade.create_project(ProjectCreate(name="Mango Project"))

        projects = facade.list_projects(sort_by=["name"], sort_order="asc")

        assert len(projects) >= 3
        # Check the order is correct
        names = [p.name for p in projects if "Project" in p.name]
        assert names == sorted(names)


# =============================================================================
# Server manager tests
# =============================================================================


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
