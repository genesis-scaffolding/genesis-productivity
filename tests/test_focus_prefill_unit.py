"""Unit tests for focus-form prefill resolution and session state."""

from __future__ import annotations

from dataclasses import fields

from genesis.category.enums import TimeLogCategory
from genesis.entities import Task, TaskStatus, TimeLogMode
from genesis.ui._render import (
    FocusFormValues,
    FocusTemplate,
    focus_label_for_task,
    focus_template_state_key,
    load_focus_template,
    resolve_focus_form_values,
    save_focus_template,
)


def _task(uid: str, title: str) -> Task:
    return Task(
        uid=uid,
        title=title,
        status=TaskStatus.NEEDS_ACTION,
        priority=5,
    )


def test_focus_template_round_trip_preserves_reusable_values() -> None:
    state: dict[str, object] = {}
    template = FocusTemplate(
        label="Deep work",
        task_uid="task-1",
        category=TimeLogCategory.WORK,
        mode=TimeLogMode.COUNTUP,
        label_baseline="Task 1",
    )

    save_focus_template(state, "alice", template)

    assert load_focus_template(state, "alice") == template
    assert {field.name for field in fields(FocusTemplate)} == {
        "label",
        "task_uid",
        "category",
        "mode",
        "label_baseline",
    }
    assert "duration" not in {field.name for field in fields(FocusTemplate)}


def test_generic_focus_template_preselects_its_open_task() -> None:
    task = _task("task-1", "Task 1")
    template = FocusTemplate(
        label="Task 1",
        task_uid=task.uid,
        category=TimeLogCategory.WORK,
        mode=TimeLogMode.COUNTDOWN,
        label_baseline="Task 1",
    )

    values = resolve_focus_form_values(
        template=template,
        open_tasks=[task],
    )

    assert values == FocusFormValues(
        task_uid="task-1",
        label="Task 1",
        label_baseline="Task 1",
        category=TimeLogCategory.WORK,
        mode=TimeLogMode.COUNTDOWN,
    )


def test_explicit_task_overrides_the_previous_template() -> None:
    template_task = _task("task-1", "Template task")
    explicit_task = _task("task-2", "Explicit task")
    template = FocusTemplate(
        label="Custom work",
        task_uid=template_task.uid,
        category=TimeLogCategory.PLAY,
        mode=TimeLogMode.COUNTUP,
        label_baseline="Template task",
    )

    values = resolve_focus_form_values(
        template=template,
        open_tasks=[template_task, explicit_task],
        explicit_task=explicit_task,
    )

    assert values == FocusFormValues(
        task_uid=explicit_task.uid,
        label=explicit_task.title,
        label_baseline=explicit_task.title,
        category=TimeLogCategory.WORK,
        mode=TimeLogMode.COUNTDOWN,
    )


def test_missing_template_task_falls_back_to_unaligned_and_retains_values() -> None:
    template = FocusTemplate(
        label="Retain this label",
        task_uid="closed-task",
        category=TimeLogCategory.WORKOUT,
        mode=TimeLogMode.COUNTUP,
        label_baseline="Old task title",
    )

    values = resolve_focus_form_values(
        template=template,
        open_tasks=[_task("other-task", "Other task")],
    )

    assert values.task_uid is None
    assert values.label == "Retain this label"
    assert values.label_baseline == "Old task title"
    assert values.category == TimeLogCategory.WORKOUT
    assert values.mode == TimeLogMode.COUNTUP


def test_custom_label_survives_a_later_task_change() -> None:
    assert (
        focus_label_for_task(
            current_label="Deep work",
            baseline_label="Task 1",
            task_label="Task 2",
        )
        == "Deep work"
    )


def test_task_derived_label_follows_a_later_task_change() -> None:
    assert (
        focus_label_for_task(
            current_label="Task 1",
            baseline_label="Task 1",
            task_label="Task 2",
        )
        == "Task 2"
    )


def test_focus_templates_are_isolated_per_user() -> None:
    state: dict[str, object] = {}
    alice_template = FocusTemplate(
        label="Alice work",
        task_uid="alice-task",
        category=TimeLogCategory.WORK,
        mode=TimeLogMode.COUNTDOWN,
        label_baseline="Alice work",
    )
    bob_template = FocusTemplate(
        label="Bob work",
        task_uid="bob-task",
        category=TimeLogCategory.PLAY,
        mode=TimeLogMode.COUNTUP,
        label_baseline="Bob work",
    )

    save_focus_template(state, "alice", alice_template)
    save_focus_template(state, "bob", bob_template)

    assert load_focus_template(state, "alice") == alice_template
    assert load_focus_template(state, "bob") == bob_template
    assert focus_template_state_key("alice") != focus_template_state_key("bob")
