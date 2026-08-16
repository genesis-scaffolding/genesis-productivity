"""Tests for CalDAV facade."""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from genesis.caldav.facade import CalDAVFacade
from genesis.entities import (
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
)


@pytest.fixture
def facade() -> Generator[CalDAVFacade, None, None]:
    """Create a CalDAVFacade with a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fac = CalDAVFacade(Path(tmpdir))
        yield fac


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
        created = facade.create_task(TaskCreate(summary="Write tests"))
        retrieved = facade.get_task(created.uid)

        assert retrieved is not None
        assert retrieved.uid == created.uid
        assert retrieved.summary == "Write tests"

    def test_get_task_not_found(self, facade: CalDAVFacade) -> None:
        """Getting a non-existent task returns None."""
        result = facade.get_task("nonexistent@genesis")
        assert result is None

    def test_list_tasks(self, facade: CalDAVFacade) -> None:
        """Listing tasks returns all tasks."""
        facade.create_task(TaskCreate(summary="Task 1"))
        facade.create_task(TaskCreate(summary="Task 2"))

        tasks = facade.list_tasks()

        assert len(tasks) == 2

    def test_update_task(self, facade: CalDAVFacade) -> None:
        """Updating a task applies the changes."""
        task = facade.create_task(TaskCreate(summary="Original"))
        updated = facade.update_task(task.uid, TaskUpdate(summary="Updated"))

        assert updated is not None
        assert updated.summary == "Updated"
        assert updated.modified is not None
        assert task.created is not None

    def test_update_task_sets_completed_at(self, facade: CalDAVFacade) -> None:
        """Updating task status to completed sets completed_at."""
        task = facade.create_task(TaskCreate(summary="Do it"))
        updated = facade.update_task(task.uid, TaskUpdate(status=TaskStatus.COMPLETED))

        assert updated is not None
        assert updated.status == TaskStatus.COMPLETED
        assert updated.completed_at is not None

    def test_update_task_clears_completed_at(self, facade: CalDAVFacade) -> None:
        """Updating completed task back to needs-action clears completed_at."""
        task = facade.create_task(TaskCreate(summary="Do it"))
        facade.update_task(task.uid, TaskUpdate(status=TaskStatus.COMPLETED))
        updated = facade.update_task(task.uid, TaskUpdate(status=TaskStatus.NEEDS_ACTION))

        assert updated is not None
        assert updated.status == TaskStatus.NEEDS_ACTION
        assert updated.completed_at is None

    def test_delete_task(self, facade: CalDAVFacade) -> None:
        """Deleting a task removes it."""
        task = facade.create_task(TaskCreate(summary="To delete"))
        assert facade.get_task(task.uid) is not None

        facade.delete_task(task.uid)
        assert facade.get_task(task.uid) is None

    def test_delete_task_not_found(self, facade: CalDAVFacade) -> None:
        """Deleting a non-existent task returns False."""
        result = facade.delete_task("nonexistent@genesis")
        assert result is False


class TestTaskFilters:
    """Test task filtering."""

    def test_filter_by_project(self, facade: CalDAVFacade) -> None:
        """Filtering by project_id returns only matching tasks."""
        project = facade.create_project(ProjectCreate(name="My Project"))
        facade.create_task(TaskCreate(summary="Task 1", project_id=project.uid))
        facade.create_task(TaskCreate(summary="Task 2"))

        tasks = facade.list_tasks(project_id=project.uid)

        assert len(tasks) == 1
        assert tasks[0].summary == "Task 1"

    def test_filter_by_status(self, facade: CalDAVFacade) -> None:
        """Filtering by status returns only matching tasks."""
        facade.create_task(TaskCreate(summary="Pending"))
        done = facade.create_task(TaskCreate(summary="Done"))
        facade.update_task(done.uid, TaskUpdate(status=TaskStatus.COMPLETED))

        tasks = facade.list_tasks(status=TaskStatus.COMPLETED)

        assert len(tasks) == 1
        assert tasks[0].summary == "Done"

    def test_filter_by_due_before(self, facade: CalDAVFacade) -> None:
        """Filtering by due_before returns tasks with deadline in range."""
        now = datetime.now(UTC)
        facade.create_task(
            TaskCreate(
                summary="Past deadline",
                hard_deadline=now.replace(day=1),
            )
        )
        facade.create_task(
            TaskCreate(
                summary="Future deadline",
                hard_deadline=now.replace(day=28),
            )
        )

        tasks = facade.list_tasks(due_before=now.replace(day=15))

        assert len(tasks) == 1
        assert tasks[0].summary == "Past deadline"

    def test_filter_by_assigned_date(self, facade: CalDAVFacade) -> None:
        """Filtering by assigned_date range works."""
        facade.create_task(
            TaskCreate(summary="This week", assigned_date=date(2025, 6, 10))
        )
        facade.create_task(
            TaskCreate(summary="Next week", assigned_date=date(2025, 6, 20))
        )

        tasks = facade.list_tasks(assigned_before=date(2025, 6, 15))

        assert len(tasks) == 1
        assert tasks[0].summary == "This week"

    def test_text_search(self, facade: CalDAVFacade) -> None:
        """Text search matches summary and description."""
        facade.create_task(TaskCreate(summary="Buy milk"))
        facade.create_task(TaskCreate(summary="Buy bread"))
        facade.create_task(TaskCreate(summary="Clean house"))

        tasks = facade.list_tasks(search="buy")

        assert len(tasks) == 2

    def test_combined_filters(self, facade: CalDAVFacade) -> None:
        """Multiple filters combine with AND logic."""
        project = facade.create_project(ProjectCreate(name="Work"))
        task = facade.create_task(
            TaskCreate(summary="Important work", project_id=project.uid)
        )

        tasks = facade.list_tasks(project_id=project.uid, search="important")

        assert len(tasks) == 1
        assert tasks[0].uid == task.uid


class TestTaskSorting:
    """Test task sorting."""

    def test_sort_by_created(self, facade: CalDAVFacade) -> None:
        """Tasks can be sorted by created date."""
        t1 = facade.create_task(TaskCreate(summary="First"))
        t2 = facade.create_task(TaskCreate(summary="Second"))

        tasks = facade.list_tasks(sort_by=["created"], sort_order="asc")

        # Both tasks exist
        assert len(tasks) == 2
        uids = [t.uid for t in tasks]
        assert t1.uid in uids
        assert t2.uid in uids

    def test_sort_by_summary(self, facade: CalDAVFacade) -> None:
        """Tasks can be sorted by summary."""
        facade.create_task(TaskCreate(summary="Zebra"))
        facade.create_task(TaskCreate(summary="Apple"))
        facade.create_task(TaskCreate(summary="Mango"))

        tasks = facade.list_tasks(sort_by=["summary"], sort_order="asc")

        assert tasks[0].summary == "Apple"
        assert tasks[1].summary == "Mango"
        assert tasks[2].summary == "Zebra"

    def test_sort_by_multiple_fields(self, facade: CalDAVFacade) -> None:
        """Tasks can be sorted by multiple fields."""
        facade.create_task(TaskCreate(summary="A"))
        facade.create_task(TaskCreate(summary="B"))
        facade.create_task(TaskCreate(summary="A"))

        tasks = facade.list_tasks(sort_by=["summary", "created"], sort_order="asc")

        assert len(tasks) == 3
        # A tasks first, then B
        assert tasks[0].summary == "A"
        assert tasks[1].summary == "A"
        assert tasks[2].summary == "B"


class TestTaskSerialization:
    """Test task serialization round-trip."""

    def test_serialization_roundtrip(self, facade: CalDAVFacade) -> None:
        """A task survives serialization round-trip."""
        task = facade.create_task(
            TaskCreate(
                summary="Test task",
                description="Description here",
                hard_deadline=datetime(2025, 6, 15, 12, 0, tzinfo=UTC),
                assigned_date=date(2025, 6, 10),
            )
        )

        retrieved = facade.get_task(task.uid)

        assert retrieved is not None
        assert retrieved.summary == "Test task"
        assert retrieved.description == "Description here"
        assert retrieved.hard_deadline is not None
        assert retrieved.assigned_date == date(2025, 6, 10)


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

    def test_get_project(self, facade: CalDAVFacade) -> None:
        """Getting a project by UID returns the project."""
        created = facade.create_project(ProjectCreate(name="My Project"))
        retrieved = facade.get_project(created.uid)

        assert retrieved is not None
        assert retrieved.uid == created.uid
        assert retrieved.name == "My Project"

    def test_get_project_not_found(self, facade: CalDAVFacade) -> None:
        """Getting a non-existent project returns None."""
        result = facade.get_project("nonexistent@genesis")
        assert result is None

    def test_list_projects(self, facade: CalDAVFacade) -> None:
        """Listing projects returns all projects."""
        facade.create_project(ProjectCreate(name="Project 1"))
        facade.create_project(ProjectCreate(name="Project 2"))

        projects = facade.list_projects()

        assert len(projects) == 2

    def test_update_project(self, facade: CalDAVFacade) -> None:
        """Updating a project applies the changes."""
        project = facade.create_project(ProjectCreate(name="Original"))
        updated = facade.update_project(project.uid, ProjectUpdate(name="Updated"))

        assert updated is not None
        assert updated.name == "Updated"

    def test_delete_project(self, facade: CalDAVFacade) -> None:
        """Deleting a project removes it."""
        project = facade.create_project(ProjectCreate(name="To delete"))
        assert facade.get_project(project.uid) is not None

        facade.delete_project(project.uid)
        assert facade.get_project(project.uid) is None


class TestProjectDeletionWithTasks:
    """Test project deletion with linked tasks."""

    def test_delete_project_removes_task_links(self, facade: CalDAVFacade) -> None:
        """Deleting a project removes project_id from linked tasks."""
        project = facade.create_project(ProjectCreate(name="My Project"))
        task = facade.create_task(TaskCreate(summary="Task 1", project_id=project.uid))

        facade.delete_project(project.uid)

        updated_task = facade.get_task(task.uid)
        assert updated_task is not None
        assert updated_task.project_id is None

    def test_delete_project_with_multiple_tasks(self, facade: CalDAVFacade) -> None:
        """Deleting a project removes links from all its tasks."""
        project = facade.create_project(ProjectCreate(name="My Project"))
        task1 = facade.create_task(
            TaskCreate(summary="Task 1", project_id=project.uid)
        )
        task2 = facade.create_task(
            TaskCreate(summary="Task 2", project_id=project.uid)
        )

        facade.delete_project(project.uid)

        t1 = facade.get_task(task1.uid)
        t2 = facade.get_task(task2.uid)
        assert t1 is not None and t1.project_id is None
        assert t2 is not None and t2.project_id is None


class TestProjectFilters:
    """Test project filtering."""

    def test_filter_by_status(self, facade: CalDAVFacade) -> None:
        """Filtering by status returns only matching projects."""
        facade.create_project(ProjectCreate(name="Active"))
        done = facade.create_project(ProjectCreate(name="Done"))
        facade.update_project(done.uid, ProjectUpdate(status=ProjectStatus.COMPLETED))

        projects = facade.list_projects(status=ProjectStatus.COMPLETED)

        assert len(projects) == 1
        assert projects[0].name == "Done"

    def test_filter_by_deadline_range(self, facade: CalDAVFacade) -> None:
        """Filtering by deadline range works."""
        facade.create_project(
            ProjectCreate(name="Soon", deadline=date(2025, 6, 1))
        )
        facade.create_project(
            ProjectCreate(name="Later", deadline=date(2025, 12, 1))
        )

        projects = facade.list_projects(
            deadline_before=date(2025, 7, 1),
            deadline_after=date(2025, 1, 1),
        )

        assert len(projects) == 1
        assert projects[0].name == "Soon"

    def test_text_search(self, facade: CalDAVFacade) -> None:
        """Text search matches name and description."""
        facade.create_project(ProjectCreate(name="Website Redesign"))
        facade.create_project(ProjectCreate(name="Mobile App"))
        facade.create_project(ProjectCreate(name="API Updates"))

        projects = facade.list_projects(search="redesign")

        assert len(projects) == 1
        assert projects[0].name == "Website Redesign"


class TestProjectSorting:
    """Test project sorting."""

    def test_sort_by_name(self, facade: CalDAVFacade) -> None:
        """Projects can be sorted by name."""
        facade.create_project(ProjectCreate(name="Zebra"))
        facade.create_project(ProjectCreate(name="Apple"))
        facade.create_project(ProjectCreate(name="Mango"))

        projects = facade.list_projects(sort_by=["name"], sort_order="asc")

        assert projects[0].name == "Apple"
        assert projects[1].name == "Mango"
        assert projects[2].name == "Zebra"


class TestProjectSerialization:
    """Test project serialization round-trip."""

    def test_serialization_roundtrip(self, facade: CalDAVFacade) -> None:
        """A project survives serialization round-trip."""
        project = facade.create_project(
            ProjectCreate(
                name="Test Project",
                description="A test project",
                start_date=date(2025, 1, 1),
                deadline=date(2025, 12, 31),
            )
        )

        retrieved = facade.get_project(project.uid)

        assert retrieved is not None
        assert retrieved.name == "Test Project"
        assert retrieved.description == "A test project"
        assert retrieved.start_date == date(2025, 1, 1)
        assert retrieved.deadline == date(2025, 12, 31)
