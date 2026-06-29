"""data/assignments: schema round-trip, derived helpers, atomic write."""

from __future__ import annotations

import datetime as dt
import json

from mojave_review.data.assignments import (
    AssignmentRecord, AssignmentStore, STALE_DAYS, all_assigned_sources,
    assignment_status, assignments_for, assignments_path, is_stale,
    load_store, needs_for, reviewer_load, save_store,
)
from mojave_review.recommendations.schema import Recommendation
from mojave_review.recommendations.store import (
    save_recommendation, save_submitted,
)


# ---------------------------------------------------------------------------
# Schema / I-O
# ---------------------------------------------------------------------------


def test_load_store_returns_empty_when_file_missing(tmp_path):
    store = load_store(tmp_path)
    assert store.version == 1
    assert store.assignments == {}
    assert store.deadline is None


def test_save_load_round_trip(tmp_path):
    store = AssignmentStore(
        deadline="2026-07-15",
        assignments={
            "alice": [AssignmentRecord(
                source="0003-066u", assigned_at="2026-06-29T12:00:00+00:00",
                target_date="2026-07-05", assigned_by="homand")],
            "bob": [AssignmentRecord(
                source="0415+379u", assigned_at="2026-06-29T12:00:00+00:00")],
        },
    )
    p = save_store(tmp_path, store)
    assert p == assignments_path(tmp_path)
    assert p.is_file()
    assert store.updated_at                          # touched on save

    loaded = load_store(tmp_path)
    assert loaded.deadline == "2026-07-15"
    assert loaded.default_review_target == 2
    alice = loaded.assignments["alice"]
    assert len(alice) == 1
    assert alice[0].source == "0003-066u"
    assert alice[0].target_date == "2026-07-05"
    assert alice[0].assigned_by == "homand"


def test_save_is_atomic_no_partfile_left(tmp_path):
    save_store(tmp_path, AssignmentStore())
    leftovers = list((tmp_path / "_admin").glob("*.part*"))
    assert leftovers == []


def test_save_writes_under_admin_subdir(tmp_path):
    save_store(tmp_path, AssignmentStore())
    expected = tmp_path / "_admin" / "assignments.json"
    assert expected.is_file()
    # Schema fields are preserved verbatim.
    with expected.open() as f:
        on_disk = json.load(f)
    assert on_disk["version"] == 1
    assert on_disk["default_review_target"] == 2


# ---------------------------------------------------------------------------
# Derived helpers
# ---------------------------------------------------------------------------


def _submit(recs_dir, source: str, reviewer: str) -> None:
    save_submitted(
        recs_dir,
        Recommendation(source=source, model="current", reviewer=reviewer,
                       source_comment="ok"),
    )


def _draft(recs_dir, source: str, reviewer: str, *, empty: bool) -> None:
    rec = Recommendation(source=source, model="current", reviewer=reviewer)
    if not empty:
        rec.source_comment = "in progress"
    save_recommendation(recs_dir, rec)


def test_needs_for_clamps_to_target(tmp_path):
    # No submissions yet ⇒ needs full target.
    assert needs_for(tmp_path, "0003-066u") == 2
    _submit(tmp_path, "0003-066u", "alice")
    assert needs_for(tmp_path, "0003-066u") == 1
    _submit(tmp_path, "0003-066u", "bob")
    assert needs_for(tmp_path, "0003-066u") == 0
    # A third submission must NOT go negative.
    _submit(tmp_path, "0003-066u", "chris")
    assert needs_for(tmp_path, "0003-066u") == 0


def test_needs_for_respects_explicit_target(tmp_path):
    _submit(tmp_path, "0003-066u", "alice")
    assert needs_for(tmp_path, "0003-066u", review_target=3) == 2


def test_assignment_status_submitted_wins(tmp_path):
    # Submitted + draft both present ⇒ "submitted" beats the draft.
    _draft(tmp_path, "0003-066u", "alice", empty=False)
    _submit(tmp_path, "0003-066u", "alice")
    assert assignment_status(tmp_path, "0003-066u", "alice") == "submitted"


def test_assignment_status_empty_draft_is_pending(tmp_path):
    _draft(tmp_path, "0003-066u", "alice", empty=True)
    assert assignment_status(tmp_path, "0003-066u", "alice") == "pending"


def test_assignment_status_nonempty_draft_is_in_progress(tmp_path):
    _draft(tmp_path, "0003-066u", "alice", empty=False)
    assert assignment_status(tmp_path, "0003-066u", "alice") == "in_progress"


# ---------------------------------------------------------------------------
# Stale flag (Phase 4 will consume this; covered now to lock the contract)
# ---------------------------------------------------------------------------


def test_is_stale_requires_target_date():
    no_target = AssignmentRecord(
        source="0003-066u", assigned_at="2026-01-01T00:00:00+00:00")
    assert is_stale(no_target, today=dt.date(2099, 1, 1)) is False


def test_is_stale_at_boundary():
    rec = AssignmentRecord(
        source="0003-066u", assigned_at="2026-06-01T00:00:00+00:00",
        target_date="2026-06-10")
    boundary = dt.date(2026, 6, 10) + dt.timedelta(days=STALE_DAYS)
    assert is_stale(rec, today=boundary) is False           # equal → not stale
    assert is_stale(rec, today=boundary + dt.timedelta(days=1)) is True


# ---------------------------------------------------------------------------
# Small accessors
# ---------------------------------------------------------------------------


def test_assignments_for_returns_empty_when_unknown_reviewer():
    store = AssignmentStore()
    assert assignments_for(store, "ghost") == []


def test_reviewer_load_and_all_assigned_sources():
    store = AssignmentStore(assignments={
        "alice": [
            AssignmentRecord(source="A", assigned_at=""),
            AssignmentRecord(source="B", assigned_at=""),
        ],
        "bob": [AssignmentRecord(source="C", assigned_at="")],
    })
    assert reviewer_load(store, "alice") == 2
    assert reviewer_load(store, "ghost") == 0
    assert all_assigned_sources(store) == {"A", "B", "C"}
