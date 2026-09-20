"""Unit tests for the task table sort/cache helpers.

Tests the pure helper ``_apply_cached_sort`` so the regression model
holds without spinning up Streamlit. Covers:

- first render computes the sort and returns the cache UID list;
- subsequent renders walk the cache in order;
- subsequent renders preserve cached positions even when the sort
  keys would otherwise re-order the rows (the row-drift fix for
  streamlit/streamlit#17026);
- a task whose UID is not in the cache is appended at the end and
  its UID is appended to the returned cache list;
- a task whose UID is in the cache but absent from ``tasks`` is
  silently skipped (deleted task);
- a combined add-and-remove scenario keeps cached positions stable
  and appends the new task.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from genesis.entities import Task, TaskStatus
from genesis.ui._table import (
    TASK_TABLE_DEFAULT_SORT_KEYS,
    _apply_cached_sort,
)

_DATETIME_MAX_UTC = datetime.max.replace(tzinfo=UTC)
SORT_KEYS: list[tuple[str, object] | tuple[str, object, bool]] = [
    ("hard_deadline", _DATETIME_MAX_UTC),
    ("priority", 5),
    ("created", _DATETIME_MAX_UTC),
]


def _task(
    uid: str,
    *,
    hard_deadline: datetime | None = None,
    priority: int | None = None,
    created: datetime | None = None,
    title: str = "t",
) -> Task:
    return Task(
        uid=uid,
        title=title,
        hard_deadline=hard_deadline,
        priority=priority,
        created=created,
        status=TaskStatus.NEEDS_ACTION,
    )


def _uids(tasks: list[Task]) -> list[str]:
    return [t.uid for t in tasks]


def test_first_render_sorts_and_returns_cache() -> None:
    tasks = [
        _task("b", hard_deadline=datetime(2026, 4, 1, tzinfo=UTC)),
        _task("a", hard_deadline=datetime(2026, 1, 1, tzinfo=UTC)),
        _task("c", priority=1),  # No deadline -> sorts last
    ]
    ordered, cache = _apply_cached_sort(
        tasks,
        cached_uids=None,
        sort_keys=SORT_KEYS,
    )
    assert _uids(ordered) == ["a", "b", "c"]
    assert cache == ["a", "b", "c"]


def test_first_render_priority_descending_puts_higher_value_first() -> None:
    """Descending priority: 9 first, 5 next, 1 last (project convention)."""
    sort_keys: list[tuple] = [
        ("priority", 5, True),
    ]
    tasks = [
        _task("low", priority=1),
        _task("mid", priority=5),
        _task("hi", priority=9),
    ]
    ordered, _ = _apply_cached_sort(
        tasks,
        cached_uids=None,
        sort_keys=sort_keys,
    )
    assert _uids(ordered) == ["hi", "mid", "low"]


def test_first_render_mixed_ascending_descending() -> None:
    """Three-level sort with mixed directions respects per-level order."""
    sort_keys: list[tuple] = [
        ("hard_deadline", _DATETIME_MAX_UTC),  # ascending: nearest first
        ("priority", 5, True),  # descending: 9 first, 1 last
        ("created", _DATETIME_MAX_UTC),  # ascending: oldest first
    ]
    same_dl = datetime(2026, 3, 1, tzinfo=UTC)
    tasks = [
        _task(
            "d_hi_old",
            hard_deadline=same_dl,
            priority=9,
            created=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        _task(
            "d_hi_new",
            hard_deadline=same_dl,
            priority=9,
            created=datetime(2026, 2, 1, tzinfo=UTC),
        ),
        _task("d_lo", hard_deadline=same_dl, priority=1),
        _task("no_dl_hi", priority=9),
    ]
    ordered, _ = _apply_cached_sort(
        tasks,
        cached_uids=None,
        sort_keys=sort_keys,
    )
    # Same deadline -> priority descending; within priority 9 ties
    # -> created ascending (older first); tasks with no deadline sort
    # last.
    assert _uids(ordered) == ["d_hi_old", "d_hi_new", "d_lo", "no_dl_hi"]


def test_parse_sort_key_entry_two_tuple_means_ascending() -> None:
    from genesis.ui._table import _parse_sort_key_entry

    field, default, reverse = _parse_sort_key_entry(("priority", 5))
    assert field == "priority"
    assert default == 5
    assert reverse is False


def test_parse_sort_key_entry_three_tuple_respects_reverse_flag() -> None:
    from genesis.ui._table import _parse_sort_key_entry

    field, default, reverse = _parse_sort_key_entry(("priority", 5, True))
    assert field == "priority"
    assert default == 5
    assert reverse is True
    field, default, reverse = _parse_sort_key_entry(("priority", 5, False))
    assert reverse is False


def test_parse_sort_key_entry_rejects_bad_length() -> None:
    from genesis.ui._table import _parse_sort_key_entry

    with pytest.raises(ValueError, match="must be"):
        _parse_sort_key_entry(("only_one",))  # type: ignore[arg-type]


def test_first_render_uses_none_default_for_missing_fields() -> None:
    a_created = datetime(2026, 1, 1, tzinfo=UTC)
    tasks = [
        _task("low", priority=9, created=a_created),
        _task("high", priority=1, created=a_created),
    ]
    ordered, _ = _apply_cached_sort(
        tasks,
        cached_uids=None,
        sort_keys=SORT_KEYS,
    )
    assert _uids(ordered) == ["high", "low"]


def test_first_render_with_all_none_preserves_input_order() -> None:
    """All sort fields None -> stable sort keeps input order."""
    tasks = [_task("c"), _task("a"), _task("b")]
    ordered, cache = _apply_cached_sort(
        tasks,
        cached_uids=None,
        sort_keys=SORT_KEYS,
    )
    assert _uids(ordered) == ["c", "a", "b"]
    assert cache == ["c", "a", "b"]


def test_cache_hit_preserves_cached_order() -> None:
    """Regression: subsequent renders must not re-sort."""
    tasks = [
        _task("a", priority=9),
        _task("b", priority=1),  # would be first if re-sorted
        _task("c", priority=5),
    ]
    cached = ["a", "b", "c"]
    ordered, returned_cache = _apply_cached_sort(
        tasks,
        cached_uids=cached,
        sort_keys=SORT_KEYS,
    )
    assert _uids(ordered) == ["a", "b", "c"]
    assert returned_cache == ["a", "b", "c"]


def test_cache_hit_preserves_order_even_when_sort_key_changed() -> None:
    """The row-drift fix: the cache overrides re-sort.

    Task 'b' now has priority 1 (the highest), which would put it
    first if we re-sorted. With the cache present, 'b' must stay in
    its cached position so the data_editor's positional ``edited_rows``
    payload keeps mapping to the same task.
    """
    tasks = [
        _task("a", priority=5),
        _task("b", priority=1),
        _task("c", priority=9),
    ]
    cached = ["a", "b", "c"]
    ordered, _ = _apply_cached_sort(
        tasks,
        cached_uids=cached,
        sort_keys=SORT_KEYS,
    )
    assert _uids(ordered) == ["a", "b", "c"]


def test_cache_hit_appends_new_task_at_end() -> None:
    cached = ["a", "b"]
    tasks = [
        _task("a"),
        _task("b"),
        _task("c"),  # newly created
    ]
    ordered, returned_cache = _apply_cached_sort(
        tasks,
        cached_uids=cached,
        sort_keys=SORT_KEYS,
    )
    assert _uids(ordered) == ["a", "b", "c"]
    assert returned_cache == ["a", "b", "c"]


def test_cache_hit_skips_deleted_task() -> None:
    cached = ["a", "b", "c"]
    tasks = [
        _task("a"),
        _task("c"),
        # 'b' was deleted
    ]
    ordered, returned_cache = _apply_cached_sort(
        tasks,
        cached_uids=cached,
        sort_keys=SORT_KEYS,
    )
    assert _uids(ordered) == ["a", "c"]
    assert returned_cache == ["a", "c"]


def test_cache_hit_with_add_and_remove() -> None:
    """Combined: cached positions stable, new task appended."""
    cached = ["a", "b", "c"]
    tasks = [
        _task("a"),
        _task("c"),
        # 'b' was deleted
        _task("d"),  # newly created
    ]
    ordered, returned_cache = _apply_cached_sort(
        tasks,
        cached_uids=cached,
        sort_keys=SORT_KEYS,
    )
    assert _uids(ordered) == ["a", "c", "d"]
    assert returned_cache == ["a", "c", "d"]


def test_cache_hit_with_multiple_new_tasks_appends_in_input_order() -> None:
    cached = ["a"]
    tasks = [
        _task("a"),
        _task("c"),
        _task("b"),
        _task("d"),
    ]
    ordered, returned_cache = _apply_cached_sort(
        tasks,
        cached_uids=cached,
        sort_keys=SORT_KEYS,
    )
    assert _uids(ordered) == ["a", "c", "b", "d"]
    assert returned_cache == ["a", "c", "b", "d"]


def test_first_render_with_empty_tasks_returns_empty_cache() -> None:
    ordered, cache = _apply_cached_sort(
        [],
        cached_uids=None,
        sort_keys=SORT_KEYS,
    )
    assert ordered == []
    assert cache == []


def test_cache_hit_with_empty_tasks_returns_full_cache() -> None:
    cached = ["a", "b", "c"]
    ordered, returned_cache = _apply_cached_sort(
        [],
        cached_uids=cached,
        sort_keys=SORT_KEYS,
    )
    assert ordered == []
    # All cached UIDs are skipped (not in empty tasks list), so the
    # returned cache is empty too.
    assert returned_cache == []


def test_default_sort_keys_constant_is_three_levels() -> None:
    """Guard against accidental edits to TASK_TABLE_DEFAULT_SORT_KEYS."""
    fields = [entry[0] for entry in TASK_TABLE_DEFAULT_SORT_KEYS]
    assert fields == ["hard_deadline", "priority", "created"]


def test_default_sort_keys_priority_is_descending() -> None:
    """Priority is the one level we sort descending (project convention)."""
    priority_entry = next(entry for entry in TASK_TABLE_DEFAULT_SORT_KEYS if entry[0] == "priority")
    assert len(priority_entry) == 3
    assert priority_entry[2] is True


@pytest.mark.parametrize(
    "field,expected",
    [
        ("hard_deadline", _DATETIME_MAX_UTC),
        ("priority", 5),
        ("created", _DATETIME_MAX_UTC),
    ],
)
def test_default_sort_keys_none_defaults(field: str, expected: object) -> None:
    """Each level's None default matches the documented convention."""
    for entry in TASK_TABLE_DEFAULT_SORT_KEYS:
        if entry[0] == field:
            assert entry[1] == expected
            return
    raise AssertionError(f"{field!r} not in TASK_TABLE_DEFAULT_SORT_KEYS")
