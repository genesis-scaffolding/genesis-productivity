"""Unit tests for the profile subpackage.

Pure logic + FsOps-injection. No FastAPI server, no Radicale, no
real disk beyond what the in-memory FakeFs simulates. Tests the
public contracts of:

- paths: target/filename resolution, parse_target
- storage: read/write/edit, error shapes, list_targets
- versions: create / list / load, dedup rules, restore-pre
- facade: end-to-end read/write/edit/history/restore, dedup
  behaviour, error translation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from genesis.profile import (
    TARGET_FILENAME,
    TARGETS,
    ProfileFacade,
    ProfileTarget,
    parse_target,
    resolve_location,
    versions_dir,
)
from genesis.profile.dates import now_iso_utc_millis, parse_iso_filename, split_counter
from genesis.profile.storage import (
    DefaultFsOps,
    EditOpts,
    FsOps,
    edit_location,
    list_targets,
    read_location,
    write_location,
)
from genesis.profile.versions import (
    SnapshotMeta,
    create_snapshot,
    list_snapshots,
    load_snapshot_content,
    sha256,
)

# ---------------------------------------------------------------------------
# Fake filesystem (in-memory)
# ---------------------------------------------------------------------------


@dataclass
class FakeFs(FsOps):
    """In-memory FsOps for unit tests.

    Holds a dict ``path -> content`` and a dict ``dir -> [names]``
    derived lazily. Tests ``seed`` files; storage / versions
    operate against the fake.
    """

    files: dict[Path, str] = field(default_factory=dict)

    def seed(self, path: Path | str, content: str = "") -> None:
        self.files[Path(path)] = content

    def _parent_chain(self, path: Path) -> list[Path]:
        out: list[Path] = []
        p = path.parent
        while p != p.parent:
            out.append(p)
            p = p.parent
        return out

    def exists(self, path: Path) -> bool:
        return Path(path) in self.files or any(
            # An empty directory "exists" if any seed file lives under it.
            path == p or path in p.parents
            for p in self.files
        )

    def read(self, path: Path) -> str:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write(self, path: Path, content: str) -> None:
        self.files[Path(path)] = content

    def mkdir(self, path: Path, *, parents: bool, exist_ok: bool) -> None:
        # In-memory: nothing to do.
        return None

    def list_dir(self, path: Path) -> list[str]:
        return sorted(name for p in self.files if p.parent == path for name in [p.name])

    def stat_size(self, path: Path) -> int:
        return len(self.files[path].encode("utf-8"))


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------


def test_targets_lists_both_profile_and_life_story():
    assert TARGETS == (ProfileTarget.PROFILE, ProfileTarget.LIFE_STORY)


def test_target_filename_mapping():
    assert TARGET_FILENAME[ProfileTarget.PROFILE] == "profile.md"
    assert TARGET_FILENAME[ProfileTarget.LIFE_STORY] == "life-story.md"


def test_resolve_location_uses_canonical_filename(tmp_path: Path):
    loc = resolve_location(ProfileTarget.PROFILE, tmp_path)
    assert loc.path == tmp_path / "profile.md"
    assert loc.filename == "profile.md"
    assert loc.dir == tmp_path
    assert loc.target is ProfileTarget.PROFILE


def test_resolve_location_life_story(tmp_path: Path):
    loc = resolve_location(ProfileTarget.LIFE_STORY, tmp_path)
    assert loc.path == tmp_path / "life-story.md"


def test_parse_target_accepts_known_values():
    assert parse_target("profile") is ProfileTarget.PROFILE
    assert parse_target("life_story") is ProfileTarget.LIFE_STORY


def test_parse_target_rejects_unknown():
    with pytest.raises(ValueError, match="Invalid profile target"):
        parse_target("persona")


def test_versions_dir_layout(tmp_path: Path):
    assert versions_dir(tmp_path, ProfileTarget.PROFILE) == tmp_path / ".versions" / "profile"
    assert versions_dir(tmp_path, ProfileTarget.LIFE_STORY) == tmp_path / ".versions" / "life_story"


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------


def test_now_iso_utc_millis_is_lex_sortable():
    a = now_iso_utc_millis()
    b = now_iso_utc_millis()
    assert a <= b
    assert a.endswith("Z")
    assert len(a) == len("YYYY-MM-DDTHHMMSSfffZ")


def test_parse_iso_filename_round_trip():
    s = now_iso_utc_millis()
    dt = parse_iso_filename(s)
    assert dt.tzinfo is not None
    # Round-trip strips the period but keeps the instant.
    assert parse_iso_filename(s).timestamp() == dt.timestamp()


def test_parse_iso_filename_accepts_md_and_meta_suffix():
    s = "2026-01-16T091500000Z"
    assert parse_iso_filename(s + ".md") == parse_iso_filename(s)
    assert parse_iso_filename(s + ".meta.json") == parse_iso_filename(s)


def test_parse_iso_filename_rejects_non_utc():
    with pytest.raises(ValueError, match="no Z suffix"):
        parse_iso_filename("2026-01-16T091500000")


def test_split_counter_no_counter():
    assert split_counter("2026-01-16T091500000Z") == ("2026-01-16T091500000Z", 0)


def test_split_counter_with_counter():
    assert split_counter("2026-01-16T091500000Z-2") == ("2026-01-16T091500000Z", 2)


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


def test_read_location_returns_trimmed_content(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "  hello world\n")
    r = read_location(ProfileTarget.PROFILE, tmp_path, fs)
    assert r.ok is True
    assert r.content == "hello world"


def test_read_location_missing_returns_error(tmp_path: Path):
    fs = FakeFs()
    r = read_location(ProfileTarget.PROFILE, tmp_path, fs)
    assert r.ok is False
    assert "does not exist" in r.error


def test_read_location_empty_returns_error(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "")
    r = read_location(ProfileTarget.PROFILE, tmp_path, fs)
    assert r.ok is False
    assert "empty" in r.error


def test_read_location_whitespace_only_is_empty(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "   \n\t\n  ")
    r = read_location(ProfileTarget.PROFILE, tmp_path, fs)
    assert r.ok is False


def test_write_location_creates_file(tmp_path: Path):
    fs = FakeFs()
    r = write_location(ProfileTarget.PROFILE, tmp_path, "hi", fs)
    assert r.ok is True
    assert r.bytes_written == 2
    assert fs.read(tmp_path / "profile.md") == "hi"


def test_write_location_real_disk(tmp_path: Path):
    r = write_location(ProfileTarget.PROFILE, tmp_path, "real\n", DefaultFsOps())
    assert r.ok is True
    assert (tmp_path / "profile.md").read_text() == "real\n"


def test_edit_location_first_occurrence(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "hello world\nhello again\n")
    r = edit_location(ProfileTarget.PROFILE, tmp_path, "world", "WORLD", EditOpts(), fs)
    assert r.ok is True
    assert r.replacements == 1
    assert fs.read(tmp_path / "profile.md") == "hello WORLD\nhello again\n"


def test_edit_location_global_replace(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "hello hello hello\n")
    r = edit_location(ProfileTarget.PROFILE, tmp_path, "hello", "HI", EditOpts(global_replace=True), fs)
    assert r.ok is True
    assert r.replacements == 3
    assert fs.read(tmp_path / "profile.md") == "HI HI HI\n"


def test_edit_location_empty_old_returns_error(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "anything")
    r = edit_location(ProfileTarget.PROFILE, tmp_path, "", "x", EditOpts(), fs)
    assert r.ok is False
    assert "empty" in r.error


def test_edit_location_no_match_returns_error(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "anything")
    r = edit_location(ProfileTarget.PROFILE, tmp_path, "missing", "x", EditOpts(), fs)
    assert r.ok is False
    assert "oldText not found" in r.error


def test_edit_location_multiple_matches_without_global_replace(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "foo foo foo")
    r = edit_location(ProfileTarget.PROFILE, tmp_path, "foo", "bar", EditOpts(), fs)
    assert r.ok is False
    assert "matches 3 places" in r.error


def test_edit_location_missing_file(tmp_path: Path):
    fs = FakeFs()
    r = edit_location(ProfileTarget.PROFILE, tmp_path, "x", "y", EditOpts(), fs)
    assert r.ok is False
    assert "does not exist" in r.error


def test_list_targets_returns_both_rows(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "abc")
    rows = list_targets(tmp_path, fs)
    assert len(rows) == 2
    by_target = {r.target: r for r in rows}
    assert by_target[ProfileTarget.PROFILE].exists is True
    assert by_target[ProfileTarget.PROFILE].bytes == 3
    assert by_target[ProfileTarget.LIFE_STORY].exists is False
    assert by_target[ProfileTarget.LIFE_STORY].bytes == 0


# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------


def _fixed_now(s: str):
    """Return a now_iso_fn that always emits ``s``."""
    return lambda: s


def test_create_snapshot_writes_md_and_meta(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "v1")
    snap = create_snapshot(
        ProfileTarget.PROFILE,
        tmp_path,
        filename="profile.md",
        canonical_path=tmp_path / "profile.md",
        new_content="v2",
        prev_content="v1",
        command="write",
        note="initial",
        fs=fs,
        now_iso_fn=_fixed_now("2026-01-16T091500000Z"),
    )
    assert snap.ok is True
    assert snap.filename == "2026-01-16T091500000Z.md"
    assert snap.meta_filename == "2026-01-16T091500000Z.meta.json"
    assert fs.read(versions_dir(tmp_path, ProfileTarget.PROFILE) / snap.filename) == "v2"
    meta = SnapshotMeta.from_json(fs.read(versions_dir(tmp_path, ProfileTarget.PROFILE) / snap.meta_filename))
    assert meta.command == "write"
    assert meta.this_sha == sha256("v2")
    assert meta.prev_sha == sha256("v1")
    assert meta.note == "initial"


def test_create_snapshot_dedup_against_previous(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "same")
    snap = create_snapshot(
        ProfileTarget.PROFILE,
        tmp_path,
        filename="profile.md",
        canonical_path=tmp_path / "profile.md",
        new_content="same",
        prev_content="same",
        command="write",
        note=None,
        fs=fs,
        now_iso_fn=_fixed_now("2026-01-16T091500000Z"),
    )
    assert snap.ok is False
    assert snap.skipped_reason == "dedup-previous"


def test_create_snapshot_dedup_against_last_snapshot(tmp_path: Path):
    fs = FakeFs()
    # First snapshot writes "v1".
    fs.seed(tmp_path / "profile.md", "v1")
    create_snapshot(
        ProfileTarget.PROFILE,
        tmp_path,
        filename="profile.md",
        canonical_path=tmp_path / "profile.md",
        new_content="v1",
        prev_content=None,
        command="write",
        note=None,
        fs=fs,
        now_iso_fn=_fixed_now("2026-01-16T091500000Z"),
    )
    # Second write with the same content but a different prev_content
    # (e.g. user rewrote from scratch): dedup against last snapshot.
    fs.write(tmp_path / "profile.md", "v2-then-v1-again")
    snap = create_snapshot(
        ProfileTarget.PROFILE,
        tmp_path,
        filename="profile.md",
        canonical_path=tmp_path / "profile.md",
        new_content="v1",
        prev_content="v2-then-v1-again",
        command="write",
        note=None,
        fs=fs,
        now_iso_fn=_fixed_now("2026-01-16T091501000Z"),
    )
    assert snap.ok is False
    assert snap.skipped_reason == "dedup-last-snapshot"


def test_create_snapshot_force_bypasses_dedup(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "same")
    snap = create_snapshot(
        ProfileTarget.PROFILE,
        tmp_path,
        filename="profile.md",
        canonical_path=tmp_path / "profile.md",
        new_content="same",
        prev_content="same",
        command="restore-pre",
        note="pre-restore",
        fs=fs,
        now_iso_fn=_fixed_now("2026-01-16T091500000Z"),
        force=True,
    )
    assert snap.ok is True


def test_create_snapshot_handles_millisecond_collision(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "v1")
    snap1 = create_snapshot(
        ProfileTarget.PROFILE,
        tmp_path,
        filename="profile.md",
        canonical_path=tmp_path / "profile.md",
        new_content="v2",
        prev_content="v1",
        command="write",
        note=None,
        fs=fs,
        now_iso_fn=_fixed_now("2026-01-16T091500000Z"),
    )
    # Same timestamp, different content: counter suffix kicks in.
    fs.write(tmp_path / "profile.md", "v3")
    snap2 = create_snapshot(
        ProfileTarget.PROFILE,
        tmp_path,
        filename="profile.md",
        canonical_path=tmp_path / "profile.md",
        new_content="v3",
        prev_content="v2",
        command="write",
        note=None,
        fs=fs,
        now_iso_fn=_fixed_now("2026-01-16T091500000Z"),
    )
    assert snap1.filename == "2026-01-16T091500000Z.md"
    assert snap2.filename == "2026-01-16T091500000Z-1.md"
    # Meta filename derives from the bare filename.
    assert snap2.meta_filename == "2026-01-16T091500000Z-1.meta.json"


def test_list_snapshots_newest_first_with_counters(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "v0")
    for ts, content in [
        ("2026-01-15T091500000Z", "v1"),
        ("2026-01-16T091500000Z", "v2"),
        ("2026-01-16T091500000Z", "v2b"),  # collision -> -1 suffix
    ]:
        fs.write(tmp_path / "profile.md", content)
        create_snapshot(
            ProfileTarget.PROFILE,
            tmp_path,
            filename="profile.md",
            canonical_path=tmp_path / "profile.md",
            new_content=content,
            prev_content=None,
            command="write",
            note=None,
            fs=fs,
            now_iso_fn=_fixed_now(ts),
        )
    entries = list_snapshots(ProfileTarget.PROFILE, tmp_path, fs)
    # Newest first chronologically. The two same-instant snapshots
    # sort by counter DESC: -1 first, then bare.
    assert [e.ts for e in entries] == [
        "2026-01-16T091500000Z-1",
        "2026-01-16T091500000Z",
        "2026-01-15T091500000Z",
    ]


def test_list_snapshots_returns_entries_with_missing_meta(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "v0")
    create_snapshot(
        ProfileTarget.PROFILE,
        tmp_path,
        filename="profile.md",
        canonical_path=tmp_path / "profile.md",
        new_content="v1",
        prev_content=None,
        command="write",
        note=None,
        fs=fs,
        now_iso_fn=_fixed_now("2026-01-16T091500000Z"),
    )
    # Delete the meta sidecar but keep the .md.
    target_dir = versions_dir(tmp_path, ProfileTarget.PROFILE)
    fs.files.pop(target_dir / "2026-01-16T091500000Z.meta.json")
    entries = list_snapshots(ProfileTarget.PROFILE, tmp_path, fs)
    assert len(entries) == 1
    assert entries[0].meta is None


def test_load_snapshot_content_by_exact_ts(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "v0")
    create_snapshot(
        ProfileTarget.PROFILE,
        tmp_path,
        filename="profile.md",
        canonical_path=tmp_path / "profile.md",
        new_content="v1",
        prev_content=None,
        command="write",
        note=None,
        fs=fs,
        now_iso_fn=_fixed_now("2026-01-16T091500000Z"),
    )
    entry, content = load_snapshot_content(ProfileTarget.PROFILE, tmp_path, "2026-01-16T091500000Z", fs)
    assert content == "v1"
    assert entry.filename == "2026-01-16T091500000Z.md"


def test_load_snapshot_content_by_unique_prefix(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "v0")
    create_snapshot(
        ProfileTarget.PROFILE,
        tmp_path,
        filename="profile.md",
        canonical_path=tmp_path / "profile.md",
        new_content="v1",
        prev_content=None,
        command="write",
        note=None,
        fs=fs,
        now_iso_fn=_fixed_now("2026-01-16T091500000Z"),
    )
    entry, content = load_snapshot_content(ProfileTarget.PROFILE, tmp_path, "2026-01-16", fs)
    assert content == "v1"


def test_load_snapshot_content_ambiguous_prefix(tmp_path: Path):
    fs = FakeFs()
    fs.seed(tmp_path / "profile.md", "v0")
    create_snapshot(
        ProfileTarget.PROFILE,
        tmp_path,
        filename="profile.md",
        canonical_path=tmp_path / "profile.md",
        new_content="v1",
        prev_content=None,
        command="write",
        note=None,
        fs=fs,
        now_iso_fn=_fixed_now("2026-01-16T091500000Z"),
    )
    fs.write(tmp_path / "profile.md", "v2")
    create_snapshot(
        ProfileTarget.PROFILE,
        tmp_path,
        filename="profile.md",
        canonical_path=tmp_path / "profile.md",
        new_content="v2",
        prev_content="v1",
        command="write",
        note=None,
        fs=fs,
        now_iso_fn=_fixed_now("2026-01-17T091500000Z"),
    )
    with pytest.raises(ValueError, match="matches 2"):
        load_snapshot_content(ProfileTarget.PROFILE, tmp_path, "2026-01", fs)


def test_load_snapshot_content_no_snapshots(tmp_path: Path):
    fs = FakeFs()
    with pytest.raises(ValueError, match="No snapshots exist"):
        load_snapshot_content(ProfileTarget.PROFILE, tmp_path, "anything", fs)


# ---------------------------------------------------------------------------
# facade end-to-end
# ---------------------------------------------------------------------------


def test_facade_list_empty(tmp_path: Path):
    facade = ProfileFacade(tmp_path, fs=FakeFs())
    listed = facade.list()
    assert listed.profile_root == str(tmp_path)
    assert len(listed.rows) == 2
    assert all(not row.exists for row in listed.rows)


def test_facade_write_then_read_round_trip(tmp_path: Path):
    fs = FakeFs()
    facade = ProfileFacade(tmp_path, fs=fs)
    wr = facade.write(ProfileTarget.PROFILE, "hello\n", note="first")
    assert wr.ok is True
    assert wr.snapshot is not None and wr.snapshot.ok is True
    rd = facade.read(ProfileTarget.PROFILE)
    assert rd.ok is True
    assert rd.content == "hello"


def test_facade_write_records_snapshot_meta(tmp_path: Path):
    fs = FakeFs()
    facade = ProfileFacade(tmp_path, fs=fs)
    facade.write(ProfileTarget.PROFILE, "v1", note="initial")
    history = facade.history(ProfileTarget.PROFILE)
    assert history.ok is True
    assert history.total == 1
    snap = history.snapshots[0]
    assert snap.meta is not None
    assert snap.meta.command == "write"
    assert snap.meta.note == "initial"


def test_facace_edit_first_occurrence(tmp_path: Path):
    fs = FakeFs()
    facade = ProfileFacade(tmp_path, fs=fs)
    facade.write(ProfileTarget.PROFILE, "hello world\nhello again\n")
    er = facade.edit(ProfileTarget.PROFILE, "world", "WORLD")
    assert er.ok is True
    assert er.replacements == 1
    # read() trims trailing whitespace per the storage contract.
    assert facade.read(ProfileTarget.PROFILE).content == "hello WORLD\nhello again"


def test_facade_edit_no_match_returns_error(tmp_path: Path):
    fs = FakeFs()
    facade = ProfileFacade(tmp_path, fs=fs)
    facade.write(ProfileTarget.PROFILE, "hello")
    er = facade.edit(ProfileTarget.PROFILE, "missing", "x")
    assert er.ok is False
    assert "oldText not found" in er.error


def test_facade_dedup_skips_snapshot_on_noop_write(tmp_path: Path):
    fs = FakeFs()
    facade = ProfileFacade(tmp_path, fs=fs)
    facade.write(ProfileTarget.PROFILE, "same")
    wr = facade.write(ProfileTarget.PROFILE, "same")
    assert wr.snapshot is not None
    assert wr.snapshot.ok is False
    assert wr.snapshot.skipped_reason == "dedup-previous"
    history = facade.history(ProfileTarget.PROFILE)
    assert history.total == 1  # only the first write produced a snapshot


def test_facace_restore_restores_previous_content(tmp_path: Path):
    fs = FakeFs()
    facade = ProfileFacade(tmp_path, fs=fs)
    facade.write(ProfileTarget.PROFILE, "v1")
    facade.write(ProfileTarget.PROFILE, "v2")
    facade.write(ProfileTarget.PROFILE, "v3")
    history = facade.history(ProfileTarget.PROFILE)
    # Newest first: v3, v2, v1. Pick the v1 snapshot (last in list).
    v1_ts = history.snapshots[-1].ts
    rr = facade.restore(ProfileTarget.PROFILE, v1_ts)
    assert rr.ok is True
    assert rr.applied_filename.endswith(".md")
    assert facade.read(ProfileTarget.PROFILE).content == "v1"


def test_facade_restore_snapshots_current_before_applying(tmp_path: Path):
    fs = FakeFs()
    facade = ProfileFacade(tmp_path, fs=fs)
    facade.write(ProfileTarget.PROFILE, "v1")
    facade.write(ProfileTarget.PROFILE, "v2")
    history = facade.history(ProfileTarget.PROFILE)
    v1_ts = history.snapshots[-1].ts
    rr = facade.restore(ProfileTarget.PROFILE, v1_ts)
    assert rr.pre_restore_snapshot is not None
    assert rr.pre_restore_snapshot.ok is True
    # The pre-restore snapshot captured v2 (current before restore).
    target_dir = versions_dir(tmp_path, ProfileTarget.PROFILE)
    pre_path = target_dir / rr.pre_restore_snapshot.filename
    assert fs.read(pre_path) == "v2"


def test_facade_restore_unknown_version_returns_error(tmp_path: Path):
    fs = FakeFs()
    facade = ProfileFacade(tmp_path, fs=fs)
    facade.write(ProfileTarget.PROFILE, "v1")
    rr = facade.restore(ProfileTarget.PROFILE, "2099-99-99T000000000Z")
    assert rr.ok is False
    assert "matches prefix" in rr.error or "matches" in rr.error


def test_facade_history_limit_caps_rows_but_total_is_unfiltered(tmp_path: Path):
    fs = FakeFs()
    facade = ProfileFacade(tmp_path, fs=fs)
    for i in range(5):
        facade.write(ProfileTarget.PROFILE, f"v{i}", note=f"n{i}")
    capped = facade.history(ProfileTarget.PROFILE, limit=2)
    assert len(capped.snapshots) == 2
    assert capped.total == 5


def test_facace_read_life_story_round_trip(tmp_path: Path):
    fs = FakeFs()
    facade = ProfileFacade(tmp_path, fs=fs)
    wr = facade.write(ProfileTarget.LIFE_STORY, "Once upon a time\n")
    assert wr.ok is True
    rd = facade.read(ProfileTarget.LIFE_STORY)
    assert rd.content == "Once upon a time"
