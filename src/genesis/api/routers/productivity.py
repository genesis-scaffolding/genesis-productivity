"""Productivity endpoints: projects, tasks, journals.

The wire model in :mod:`genesis.api.schemas` is the agent extension's
contract. Each handler:
  1. Validates the bearer token (via the ``current_subject`` dep).
  2. Builds an in-process :class:`Facade` (which starts Radicale on
     demand).
  3. Calls the matching entity method.
  4. Translates the entity model to the wire model.

The Streamlit frontend does not call this router; it uses
``Facade`` directly.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ...configs import resolve_config_path
from ...core.facade import Facade
from ...entities import (
    ProjectCreate,
    ProjectUpdate,
    TaskCreate,
    TaskUpdate,
)
from ...journal.facade import Entry, EntryType
from ...stats import Period as StatsPeriod
from ...stats import compute_rollup
from ..auth import current_subject
from ..schemas import (
    PROJECT_STATUS_TO_WIRE,
    TASK_STATUS_TO_WIRE,
    ByCategoryOut,
    ByTaskOut,
    DailyPointOut,
    JournalEntryCreateIn,
    JournalEntryOut,
    JournalEntryUpdateIn,
    JournalListOut,
    KpisOut,
    ProjectCreateIn,
    ProjectListOut,
    ProjectOut,
    ProjectUpdateIn,
    RecentSessionOut,
    StatisticsOut,
    StatisticsPeriod,
    Status,
    TaskCreateIn,
    TaskListOut,
    TaskOut,
    TaskUpdateIn,
    TimeLogCategory,
    TimeLogEntryOut,
    TimeLogListOut,
    TimeLogMode,
    wire_to_project_status,
    wire_to_task_status,
)

router = APIRouter(prefix="/productivity", tags=["productivity"])

_PROJECT_FOOTER_RE = re.compile(r"^> Project: (.+)$", re.MULTILINE)


def _facade() -> Facade:
    """Build a per-request :class:`Facade` for the default user."""
    config_path = resolve_config_path("default")
    return Facade(config_path)


def _to_caldav_datetime(value: date | datetime | None) -> datetime | None:
    """Coerce a date or datetime query param to a UTC datetime for the
    CalDAV facade.

    The CalDAV library only accepts ``datetime``; date-only inputs
    become the start of day in UTC. Naive datetimes are assumed to
    be UTC (the API wire format is ``YYYY-MM-DDTHH:MM:SS.mmmZ``).
    None passes through.

    Pydantic parses the query string for us — invalid input returns
    a 422 before this function runs.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    return datetime.combine(value, datetime.min.time(), tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _paginate[T](
    items: list[T],
    offset: int | None,
    limit: int | None,
) -> tuple[list[T], int, int, int]:
    """Slice ``items`` for the response and return ``(slice, total, offset, limit)``.

    A missing ``offset`` starts at 0. A missing ``limit`` returns
    everything from ``offset`` to the end. When the wire response
    reports ``limit`` (the size of the page returned to the client),
    it is the actual page length even when the caller passed no
    ``limit``.
    """
    effective_offset = offset if offset is not None else 0
    total = len(items)
    if limit is not None:
        page = items[effective_offset : effective_offset + limit]
        return page, total, effective_offset, limit
    page = items[effective_offset:]
    return page, total, effective_offset, total - effective_offset


def _project_to_wire(project) -> ProjectOut:
    return ProjectOut(
        uid=project.uid,
        name=project.name,
        description=project.description,
        status=PROJECT_STATUS_TO_WIRE.get(project.status, Status.NEEDS_ACTION),
        start_date=project.start_date,
        deadline=project.deadline,
        created=project.created,
        modified=project.modified,
    )


def _task_to_wire(task) -> TaskOut:
    return TaskOut(
        uid=task.uid,
        title=task.title,
        description=task.description,
        status=TASK_STATUS_TO_WIRE.get(task.status, Status.NEEDS_ACTION),
        assigned_date=task.assigned_date,
        hard_deadline=task.hard_deadline,
        scheduled_start=task.scheduled_start,
        duration_minutes=task.scheduled_duration,
        priority=task.priority,
        project_id=task.project_id,
        completed_at=task.completed_at,
        created=task.created,
        modified=task.modified,
    )


def _project_name_lookup(facade: Facade) -> dict[str, str]:
    """Map project UID -> name for resolving journal footers."""
    return {p.uid: p.name for p in facade.caldav.list_projects()}


def _parse_project_footer(body: str, project_names: dict[str, str]) -> tuple[str | None, str]:
    """Strip ``> Project: <name>`` footer from a journal body.

    Returns ``(project_uid, body_without_footer)``. If the footer
    references a project name not in the current project set, the
    project uid is ``None`` but the footer is still stripped.
    """
    match = _PROJECT_FOOTER_RE.search(body)
    if not match:
        return None, body
    name = match.group(1).strip()
    body_without = (body[: match.start()] + body[match.end() :]).rstrip("\n")
    uid = next((uid for uid, n in project_names.items() if n == name), None)
    return uid, body_without


def _apply_project_footer(body: str, project_uid: str | None, project_names: dict[str, str]) -> str:
    """Strip any existing footer, optionally re-add one for ``project_uid``."""
    _, stripped = _parse_project_footer(body, project_names)
    if project_uid is None:
        return stripped
    name = project_names.get(project_uid, project_uid)
    return stripped.rstrip("\n") + f"\n\n> Project: {name}\n"


def _journal_to_wire(entry: Entry, project_names: dict[str, str]) -> JournalEntryOut:
    project_uid, content = _parse_project_footer(entry.content, project_names)
    return JournalEntryOut(
        uid=entry.uid,
        type=entry.type,
        date=entry.date,
        title=entry.title,
        content=content,
        project_id=project_uid,
        created=entry.created,
        modified=entry.modified,
    )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@router.get("/projects", response_model=ProjectListOut)
def list_projects(
    _: Annotated[str, Depends(current_subject)],
    search_query: Annotated[str | None, Query()] = None,
    project_status: Annotated[Status | None, Query(alias="status")] = None,
    sort_by: Annotated[list[str] | None, Query()] = None,
    order: Annotated[str, Query()] = "asc",
    offset: Annotated[int | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
) -> ProjectListOut:
    facade = _facade()
    try:
        entity_status = (
            wire_to_project_status(project_status) if project_status is not None else None
        )
        all_projects = facade.caldav.list_projects(
            status=entity_status,
            search=search_query,
            sort_by=sort_by,
            sort_order=order,
        )
        items = [_project_to_wire(p) for p in all_projects]
        page, total, offset, limit = _paginate(items, offset, limit)
        return ProjectListOut(
            items=page,
            total=total,
            offset=offset,
            limit=limit,
        )
    finally:
        facade.shutdown()


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreateIn,
    _: Annotated[str, Depends(current_subject)],
) -> ProjectOut:
    facade = _facade()
    try:
        project = facade.caldav.create_project(
            ProjectCreate(
                name=body.name,
                description=body.description,
                start_date=body.start_date,
                deadline=body.deadline,
            )
        )
        # Status is only settable via update on the entity side.
        if body.status != Status.NEEDS_ACTION:
            project = facade.caldav.update_project(
                project.uid,
                ProjectUpdate(status=wire_to_project_status(body.status)),
            )
        return _project_to_wire(project)
    finally:
        facade.shutdown()


@router.get("/projects/{uid}", response_model=ProjectOut)
def read_project(
    uid: str,
    _: Annotated[str, Depends(current_subject)],
) -> ProjectOut:
    facade = _facade()
    try:
        project = facade.caldav.get_project(uid)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return _project_to_wire(project)
    finally:
        facade.shutdown()


@router.patch("/projects/{uid}", response_model=ProjectOut)
def update_project(
    uid: str,
    body: ProjectUpdateIn,
    _: Annotated[str, Depends(current_subject)],
) -> ProjectOut:
    facade = _facade()
    try:
        project = facade.caldav.get_project(uid)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        update_kwargs: dict[str, Any] = {}
        if body.name is not None:
            update_kwargs["name"] = body.name
        if body.description is not None:
            update_kwargs["description"] = body.description
        if body.start_date is not None:
            update_kwargs["start_date"] = body.start_date
        if body.deadline is not None:
            update_kwargs["deadline"] = body.deadline
        if body.status is not None:
            update_kwargs["status"] = wire_to_project_status(body.status)

        if update_kwargs:
            project = facade.caldav.update_project(uid, ProjectUpdate(**update_kwargs))
        return _project_to_wire(project)
    finally:
        facade.shutdown()


@router.delete("/projects/{uid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    uid: str,
    _: Annotated[str, Depends(current_subject)],
) -> None:
    facade = _facade()
    try:
        if not facade.caldav.delete_project(uid):
            raise HTTPException(status_code=404, detail="Project not found")
    finally:
        facade.shutdown()


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def _task_sort_field(sort_by: list[str] | None) -> list[str] | None:
    """Map wire sort fields to entity attribute names.

    Currently both sides use the same names (``title``, ``status``,
    ``assigned_date``, ``hard_deadline``, ``scheduled_start``,
    ``priority``, ``created``). Returns the list unchanged.
    """
    return sort_by


@router.get("/tasks", response_model=TaskListOut)
def list_tasks(
    _: Annotated[str, Depends(current_subject)],
    task_status: Annotated[Status | None, Query(alias="status")] = None,
    search_query: Annotated[str | None, Query()] = None,
    project_id: Annotated[str | None, Query()] = None,
    # ``assigned_date`` is a calendar-day field on the entity, so the
    # matching query param is also a date. Pydantic rejects datetime
    # inputs here with a 422. The other two are full datetimes.
    assigned_date_start: Annotated[date | None, Query()] = None,
    assigned_date_end: Annotated[date | None, Query()] = None,
    hard_deadline_start: Annotated[datetime | None, Query()] = None,
    hard_deadline_end: Annotated[datetime | None, Query()] = None,
    scheduled_start_start: Annotated[datetime | None, Query()] = None,
    scheduled_start_end: Annotated[datetime | None, Query()] = None,
    sort_by: Annotated[list[str] | None, Query()] = None,
    order: Annotated[str, Query()] = "asc",
    offset: Annotated[int | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
) -> TaskListOut:
    facade = _facade()
    try:
        all_tasks = facade.caldav.list_tasks(
            project_id=project_id,
            status=wire_to_task_status(task_status) if task_status is not None else None,
            start=_to_caldav_datetime(
                assigned_date_start
                or hard_deadline_start
                or scheduled_start_start
            ),
            end=_to_caldav_datetime(
                assigned_date_end
                or hard_deadline_end
                or scheduled_start_end
            ),
            sort_by=_task_sort_field(sort_by),
            sort_order=order,
        )

        # The CalDAV SUMMARY filter only matches the iCal SUMMARY
        # field. For a useful full-text search_query we extend the
        # match to also cover the description. Done client-side here
        # so the iCal-side SUMMARY filter doesn't pre-truncate the
        # candidate set.
        if search_query:
            needle = search_query.lower()
            all_tasks = [
                t
                for t in all_tasks
                if needle in t.title.lower()
                or (t.description is not None and needle in t.description.lower())
            ]

        # Date filters that span multiple fields can't be expressed by
        # a single Facade.list_tasks(start, end) call. Apply those
        # client-side here.
        def in_range(
            dt: date | datetime | None,
            start: date | datetime | None,
            end: date | datetime | None,
        ) -> bool:
            if dt is None:
                return start is None and end is None
            # Normalize all three to ``date`` so a calendar-day bound
            # on one side compares cleanly against either a ``date``
            # field (assigned_date) or a ``datetime`` field
            # (hard_deadline / scheduled_start) on the other.
            d = dt.date() if isinstance(dt, datetime) else dt
            if start:
                s = start.date() if isinstance(start, datetime) else start
                if d < s:
                    return False
            if end:
                e = end.date() if isinstance(end, datetime) else end
                if d > e:
                    return False
            return True

        if (
            assigned_date_start
            or assigned_date_end
            or hard_deadline_start
            or hard_deadline_end
            or scheduled_start_start
            or scheduled_start_end
        ):
            filtered = []
            for t in all_tasks:
                if not in_range(t.assigned_date, assigned_date_start, assigned_date_end):
                    continue
                if not in_range(t.hard_deadline, hard_deadline_start, hard_deadline_end):
                    continue
                if not in_range(t.scheduled_start, scheduled_start_start, scheduled_start_end):
                    continue
                filtered.append(t)
            all_tasks = filtered

        items = [_task_to_wire(t) for t in all_tasks]
        page, total, offset, limit = _paginate(items, offset, limit)

        return TaskListOut(
            items=page,
            total=total,
            offset=offset,
            limit=limit,
        )
    finally:
        facade.shutdown()


@router.post("/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskCreateIn,
    _: Annotated[str, Depends(current_subject)],
) -> TaskOut:
    facade = _facade()
    try:
        data = TaskCreate(
            title=body.title,
            description=body.description,
            hard_deadline=body.hard_deadline,
            assigned_date=body.assigned_date,
            scheduled_start=body.scheduled_start,
            scheduled_duration=body.duration_minutes,
            priority=body.priority,
            project_id=body.project_id,
        )
        task = facade.caldav.create_task(data)
        if body.status != Status.NEEDS_ACTION:
            task = facade.caldav.update_task(
                task.uid,
                TaskUpdate(status=wire_to_task_status(body.status)),
            )
        return _task_to_wire(task)
    finally:
        facade.shutdown()


@router.get("/tasks/{uid}", response_model=TaskOut)
def read_task(
    uid: str,
    _: Annotated[str, Depends(current_subject)],
) -> TaskOut:
    facade = _facade()
    try:
        task = facade.caldav.get_task(uid)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return _task_to_wire(task)
    finally:
        facade.shutdown()


@router.patch("/tasks/{uid}", response_model=TaskOut)
def update_task(
    uid: str,
    body: TaskUpdateIn,
    _: Annotated[str, Depends(current_subject)],
) -> TaskOut:
    facade = _facade()
    try:
        existing = facade.caldav.get_task(uid)
        if existing is None:
            raise HTTPException(status_code=404, detail="Task not found")

        kwargs: dict[str, Any] = {}
        if body.title is not None:
            kwargs["title"] = body.title
        if body.description is not None:
            kwargs["description"] = body.description
        if body.assigned_date is not None:
            kwargs["assigned_date"] = body.assigned_date
        if body.hard_deadline is not None:
            kwargs["hard_deadline"] = body.hard_deadline
        if body.scheduled_start is not None:
            kwargs["scheduled_start"] = body.scheduled_start
        if body.clear_scheduled_start:
            kwargs["clear_scheduled_start"] = True
        if body.duration_minutes is not None:
            kwargs["scheduled_duration"] = body.duration_minutes
        if body.priority is not None:
            kwargs["priority"] = body.priority
        if body.project_id is not None:
            kwargs["project_id"] = body.project_id
        if body.clear_project_id:
            kwargs["clear_project_id"] = True
        if body.status is not None:
            kwargs["status"] = wire_to_task_status(body.status)

        if not kwargs:
            return _task_to_wire(existing)

        task = facade.caldav.update_task(uid, TaskUpdate(**kwargs))
        return _task_to_wire(task)
    finally:
        facade.shutdown()


@router.delete("/tasks/{uid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    uid: str,
    _: Annotated[str, Depends(current_subject)],
) -> None:
    facade = _facade()
    try:
        if not facade.caldav.delete_task(uid):
            raise HTTPException(status_code=404, detail="Task not found")
    finally:
        facade.shutdown()


# ---------------------------------------------------------------------------
# Journals
# ---------------------------------------------------------------------------


@router.get("/journals", response_model=JournalListOut)
def list_journals(
    _: Annotated[str, Depends(current_subject)],
    entry_type: Annotated[str | None, Query(alias="type")] = None,
    project_id: Annotated[str | None, Query()] = None,
    search_query: Annotated[str | None, Query()] = None,
    reference_date_start: Annotated[str | None, Query(alias="date_start")] = None,
    reference_date_end: Annotated[str | None, Query(alias="date_end")] = None,
    sort_by: Annotated[list[str] | None, Query()] = None,
    order: Annotated[str, Query()] = "desc",
    offset: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query()] = 50,
) -> JournalListOut:
    facade = _facade()
    try:
        project_names = _project_name_lookup(facade)
        all_entries = facade.journal.list_entries()

        if entry_type:
            try:
                wanted = EntryType(entry_type)
            except ValueError as e:
                raise HTTPException(
                    status_code=400, detail=f"unknown entry_type: {entry_type}"
                ) from e
            all_entries = [e for e in all_entries if e.type == wanted]

        if reference_date_start:
            start = datetime.fromisoformat(reference_date_start).date()
            all_entries = [e for e in all_entries if e.date >= start]
        if reference_date_end:
            end = datetime.fromisoformat(reference_date_end).date()
            all_entries = [e for e in all_entries if e.date <= end]

        if search_query:
            needle = search_query.lower()
            all_entries = [
                e
                for e in all_entries
                if (e.title and needle in e.title.lower())
                or needle in e.content.lower()
            ]

        if project_id:
            name_to_match = next((n for uid, n in project_names.items() if uid == project_id), None)
            if name_to_match is None:
                all_entries = []
            else:
                needle = f"> Project: {name_to_match}"
                all_entries = [e for e in all_entries if needle in e.content]

        all_wire = [_journal_to_wire(e, project_names) for e in all_entries]
        if project_id is not None:
            all_wire = [i for i in all_wire if i.project_id == project_id]
        page, total, offset, limit = _paginate(all_wire, offset, limit)
        return JournalListOut(
            items=page,
            total=total,
            offset=offset,
            limit=limit,
        )
    finally:
        facade.shutdown()


@router.post("/journals", response_model=JournalEntryOut, status_code=status.HTTP_201_CREATED)
def create_journal(
    body: JournalEntryCreateIn,
    _: Annotated[str, Depends(current_subject)],
) -> JournalEntryOut:
    facade = _facade()
    try:
        project_names = _project_name_lookup(facade)
        content = body.content
        if body.project_id is not None:
            if body.project_id not in project_names:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown project_id: {body.project_id}",
                )
            content = _apply_project_footer(content, body.project_id, project_names)

        entry = Entry(
            date=body.date,
            title=body.title,
            type=body.type,
            content=content,
        )
        facade.journal.save_entry(entry)
        saved = facade.journal.get_entry(entry.uid)
        if saved is None:
            raise HTTPException(status_code=500, detail="Failed to read back created entry")
        return _journal_to_wire(saved, project_names)
    finally:
        facade.shutdown()


@router.get("/journals/{uid}", response_model=JournalEntryOut)
def read_journal(
    uid: str,
    _: Annotated[str, Depends(current_subject)],
) -> JournalEntryOut:
    facade = _facade()
    try:
        entry = facade.journal.get_entry(uid)
        if entry is None:
            raise HTTPException(status_code=404, detail="Journal entry not found")
        return _journal_to_wire(entry, _project_name_lookup(facade))
    finally:
        facade.shutdown()


@router.patch("/journals/{uid}", response_model=JournalEntryOut)
def update_journal(
    uid: str,
    body: JournalEntryUpdateIn,
    _: Annotated[str, Depends(current_subject)],
) -> JournalEntryOut:
    facade = _facade()
    try:
        existing = facade.journal.get_entry(uid)
        if existing is None:
            raise HTTPException(status_code=404, detail="Journal entry not found")

        project_names = _project_name_lookup(facade)

        title = body.title if body.title is not None else existing.title
        content = body.content if body.content is not None else existing.content
        date = body.date if body.date is not None else existing.date
        type_ = existing.type

        if body.project_id is not None or body.clear_project_id:
            new_uid: str | None = None
            if body.clear_project_id:
                new_uid = None
            elif body.project_id is not None:
                if body.project_id not in project_names:
                    raise HTTPException(
                        status_code=400,
                        detail=f"unknown project_id: {body.project_id}",
                    )
                new_uid = body.project_id
            content = _apply_project_footer(content, new_uid, project_names)

        entry = Entry(
            uid=uid,
            date=date,
            title=title,
            type=type_,
            content=content,
        )
        facade.journal.save_entry(entry)
        saved = facade.journal.get_entry(uid)
        if saved is None:
            raise HTTPException(status_code=500, detail="Failed to read back updated entry")
        return _journal_to_wire(saved, project_names)
    finally:
        facade.shutdown()


@router.delete("/journals/{uid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_journal(
    uid: str,
    _: Annotated[str, Depends(current_subject)],
) -> None:
    facade = _facade()
    try:
        if not facade.journal.delete_entry(uid):
            raise HTTPException(status_code=404, detail="Journal entry not found")
    finally:
        facade.shutdown()


# ---------------------------------------------------------------------------
# Time log (read-only)
# ---------------------------------------------------------------------------


def _time_log_to_wire(entry) -> TimeLogEntryOut:
    return TimeLogEntryOut.model_validate(entry)


@router.get("/time_log", response_model=TimeLogListOut)
def list_time_log(
    _: Annotated[str, Depends(current_subject)],
    task_id: Annotated[str | None, Query()] = None,
    time_log_status: Annotated[Status | None, Query(alias="status")] = None,
    active_only: Annotated[bool, Query()] = False,
    sort_by: Annotated[list[str] | None, Query()] = None,
    order: Annotated[str, Query()] = "asc",
    offset: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query()] = 100,
) -> TimeLogListOut:
    facade = _facade()
    try:
        entity_status = (
            wire_to_task_status(time_log_status) if time_log_status is not None else None
        )
        all_entries = facade.caldav.list_time_log(
            task_id=task_id,
            status=entity_status,
            active_only=active_only,
            sort_by=sort_by,
            sort_order=order,
        )
        items = [_time_log_to_wire(e) for e in all_entries]
        page, total, offset, limit = _paginate(items, offset, limit)
        return TimeLogListOut(
            items=page,
            total=total,
            offset=offset,
            limit=limit,
        )
    finally:
        facade.shutdown()


@router.get("/time_log/{uid}", response_model=TimeLogEntryOut)
def read_time_log(
    uid: str,
    _: Annotated[str, Depends(current_subject)],
) -> TimeLogEntryOut:
    facade = _facade()
    try:
        entry = facade.caldav.get_time_log_entry(uid)
        if entry is None:
            raise HTTPException(status_code=404, detail="Time log entry not found")
        return _time_log_to_wire(entry)
    finally:
        facade.shutdown()


# ---------------------------------------------------------------------------
# Statistics (read-only)
# ---------------------------------------------------------------------------


@router.get("/statistics", response_model=StatisticsOut)
def get_statistics(
    _: Annotated[str, Depends(current_subject)],
    period: Annotated[StatisticsPeriod, Query()] = StatisticsPeriod.THIS_WEEK,
    top_n: Annotated[int, Query(ge=1, le=100)] = 10,
    recent_limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> StatisticsOut:
    """Pre-computed statistics rollup for completed focus sessions.

    Mirrors the Statistics page (KPIs, daily activity, by-task,
    recent sessions). ``period`` filters every section of the
    response; there is no period-independent section. Default is
    ``this-week`` to match the UI. ``top_n`` and ``recent_limit``
    override the default 10/20 caps for the by-task and
    recent-sessions lists.
    """
    facade = _facade()
    try:
        completed = facade.caldav.list_time_log()
        task_ids = {e.task_id for e in completed if e.task_id is not None}
        task_titles: dict[str, str] = {}
        for tid in task_ids:
            task = facade.caldav.get_task(tid)
            task_titles[tid] = task.title if task else "(deleted)"

        rollup = compute_rollup(
            entries=completed,
            period=StatsPeriod(period.value),
            today=date.today(),
            task_titles=task_titles,
            top_n=top_n,
            recent_n=recent_limit,
        )
        return StatisticsOut(
            period=StatisticsPeriod(rollup.period.value),
            kpis=KpisOut(
                total_minutes=rollup.kpis.total_minutes,
                session_count=rollup.kpis.session_count,
                avg_minutes=rollup.kpis.avg_minutes,
                tasks_touched=rollup.kpis.tasks_touched,
                minutes_by_category=rollup.kpis.minutes_by_category,
            ),
            daily=[
                DailyPointOut(
                    day=p.day,
                    minutes=p.minutes,
                    minutes_by_category=p.minutes_by_category,
                )
                for p in rollup.daily
            ],
            by_task=[
                ByTaskOut(
                    title=b.title,
                    count=b.count,
                    total_seconds=b.total_seconds,
                    avg_seconds=b.avg_seconds,
                )
                for b in rollup.by_task
            ],
            by_category=[
                ByCategoryOut(
                    category=TimeLogCategory(b.category.value),
                    total_seconds=b.total_seconds,
                    count=b.count,
                )
                for b in rollup.by_category
            ],
            recent=[
                RecentSessionOut(
                    uid=s.uid,
                    start=s.start,
                    label=s.label,
                    task_title=s.task_title,
                    mode=TimeLogMode(s.mode),
                    duration_seconds=s.duration_seconds,
                )
                for s in rollup.recent
            ],
        )
    finally:
        facade.shutdown()
