"""Unit tests for CalDAV facade using mock backend.

These tests are fast and do not require a real Radicale server.
"""

from __future__ import annotations

import pytest

from genesis.entities import (
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
)
from tests.mock_caldav import MockCalDAVFacade

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def facade() -> MockCalDAVFacade:
    """Create a mock CalDAV facade for testing."""
    return MockCalDAVFacade(
        task_calendar_name="tasks",
        project_calendar_prefix="project:",
    )


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
        found = facade.get_task("nonexistent@genesis")
        assert found is None

    def test_list_tasks(self, facade: MockCalDAVFacade) -> None:
        """Listing tasks returns all tasks."""
        facade.create_task(TaskCreate(summary="Task 1"))
        facade.create_task(TaskCreate(summary="Task 2"))

        tasks = facade.list_tasks()

        assert len(tasks) == 2

    def test_update_task(self, facade: MockCalDAVFacade) -> None:
        """Updating a task modifies the task."""
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

        result = facade.delete_task(task.uid)

        assert result is True
        assert facade.get_task(task.uid) is None

    def test_delete_task_not_found(self, facade: MockCalDAVFacade) -> None:
        """Deleting a non-existent task returns False."""
        result = facade.delete_task("nonexistent@genesis")
        assert result is False


class TestTaskFilters:
    """Test task filtering."""

    def test_filter_by_status(self, facade: MockCalDAVFacade) -> None:
        """Filtering by status returns only matching tasks."""
        facade.create_task(TaskCreate(summary="Active task"))
        completed = facade.create_task(TaskCreate(summary="Completed task"))
        facade.update_task(completed.uid, TaskUpdate(status=TaskStatus.COMPLETED))

        active = facade.list_tasks(status=TaskStatus.NEEDS_ACTION)
        assert all(t.status == TaskStatus.NEEDS_ACTION for t in active)

        completed_tasks = facade.list_tasks(status=TaskStatus.COMPLETED)
        assert all(t.status == TaskStatus.COMPLETED for t in completed_tasks)

    def test_text_search(self, facade: MockCalDAVFacade) -> None:
        """Text search finds tasks by summary or description."""
        facade.create_task(TaskCreate(summary="Buy groceries"))
        facade.create_task(
            TaskCreate(summary="Fix bug", description="Memory leak in parser")
        )

        results = facade.list_tasks(search="bug")
        assert len(results) >= 1
        assert any("bug" in t.summary.lower() or "bug" in (t.description or "").lower() for t in results)


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
        summaries = [t.summary for t in tasks]
        assert summaries == sorted(summaries)


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
        assert found.uid == created.uid
        assert found.name == "Test Project"

    def test_get_project_not_found(self, facade: MockCalDAVFacade) -> None:
        """Getting a non-existent project returns None."""
        found = facade.get_project("nonexistent@genesis")
        assert found is None

    def test_list_projects(self, facade: MockCalDAVFacade) -> None:
        """Listing projects returns all projects."""
        facade.create_project(ProjectCreate(name="Project 1"))
        facade.create_project(ProjectCreate(name="Project 2"))

        projects = facade.list_projects()

        assert len(projects) == 2

    def test_update_project(self, facade: MockCalDAVFacade) -> None:
        """Updating a project modifies the project."""
        project = facade.create_project(ProjectCreate(name="Original"))

        updated = facade.update_project(project.uid, ProjectUpdate(name="Updated"))

        assert updated is not None
        assert updated.name == "Updated"
        assert updated.created is not None
        assert updated.modified is not None

    def test_delete_project(self, facade: MockCalDAVFacade) -> None:
        """Deleting a project removes it."""
        project = facade.create_project(ProjectCreate(name="To delete"))

        result = facade.delete_project(project.uid)

        assert result is True
        assert facade.get_project(project.uid) is None


class TestProjectDeletionWithTasks:
    """Test project deletion with linked tasks."""

    def test_delete_project_removes_task_links(self, facade: MockCalDAVFacade) -> None:
        """Deleting a project removes project_id from its tasks."""
        project = facade.create_project(ProjectCreate(name="Test Project"))
        task = facade.create_task(TaskCreate(summary="Linked task", project_id=project.uid))

        assert task.project_id == project.uid

        facade.delete_project(project.uid)

        # Task should still exist but with project_id cleared
        updated_task = facade.get_task(task.uid)
        assert updated_task is not None
        assert updated_task.project_id is None


class TestProjectFilters:
    """Test project filtering."""

    def test_filter_by_status(self, facade: MockCalDAVFacade) -> None:
        """Filtering by status returns only matching projects."""
        facade.create_project(ProjectCreate(name="Active project"))
        completed = facade.create_project(ProjectCreate(name="Completed project"))
        facade.update_project(completed.uid, ProjectUpdate(status=ProjectStatus.COMPLETED))

        active_projects = facade.list_projects(status=ProjectStatus.NEEDS_ACTION)
        assert all(p.status == ProjectStatus.NEEDS_ACTION for p in active_projects)

    def test_text_search(self, facade: MockCalDAVFacade) -> None:
        """Text search finds projects by name or description."""
        facade.create_project(ProjectCreate(name="Marketing Campaign"))
        facade.create_project(
            ProjectCreate(name="Tech Project", description="Hardware development")
        )

        results = facade.list_projects(search="marketing")
        assert len(results) >= 1
        assert any("marketing" in p.name.lower() for p in results)


class TestProjectSorting:
    """Test project sorting."""

    def test_sort_by_name(self, facade: MockCalDAVFacade) -> None:
        """Projects can be sorted by name."""
        facade.create_project(ProjectCreate(name="Zebra Project"))
        facade.create_project(ProjectCreate(name="Apple Project"))

        projects = facade.list_projects(sort_by=["name"], sort_order="asc")

        names = [p.name for p in projects]
        assert names == sorted(names)
