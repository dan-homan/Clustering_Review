"""data/assignments: schema round-trip, derived helpers, atomic write."""

from __future__ import annotations

import datetime as dt
import json

from mojave_review.data.assignments import (
    AssignmentRecord, AssignmentStore, STALE_DAYS, all_assigned_sources,
    apply_additions, assignment_status, assignments_for, assignments_path,
    auto_balance, is_stale, load_store, needs_for,
    reassign_queue, remove_assignment, reviewer_load, save_store,
    submitted_by_map,
)
from mojave_review.data.difficulty import SourceDifficulty
from mojave_review.recommendations.schema import Recommendation
from mojave_review.recommendations.store import (
    save_recommendation, save_submitted,
)


def _sd(name: str, score: float) -> SourceDifficulty:
    """Tiny factory for difficulty stubs in load-balance tests."""
    import math
    return SourceDifficulty(
        source=name, folder=f"{name}_1994.00-2026.00",
        n_epochs=int(score / 10) or 1,
        mean_features=10.0,
        score=score,
        balance_weight=math.sqrt(score),
        stars=1, outlier=False,
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


# ---------------------------------------------------------------------------
# auto_balance — LPT load balancer
# ---------------------------------------------------------------------------


def test_auto_balance_distributes_in_lpt_order():
    # Four sources, three reviewers, no prior assignments. Sources sorted
    # by balance_weight desc: A(score=400, bw=20), B(225, 15), C(100, 10),
    # D(25, 5). Target=2, so each source gets 2 slots. With six reviewers
    # exactly we'd be tight; with three, each reviewer ends up with
    # multiple sources, picked least-loaded-first.
    srcs = [_sd("A", 400.0), _sd("B", 225.0), _sd("C", 100.0), _sd("D", 25.0)]
    additions = auto_balance(
        scored_sources=srcs,
        reviewers=["alice", "bob", "chris"],
        current_assignments={},
        submitted_by={},
    )
    # Each source got 2 assignments.
    total = sum(len(v) for v in additions.values())
    assert total == 8
    # The heaviest source A must go to two reviewers (one each).
    assigned_to_A = [r for r, srcs in additions.items() if "A" in srcs]
    assert len(assigned_to_A) == 2
    # No reviewer is given the same source twice.
    for r, srcs in additions.items():
        assert len(set(srcs)) == len(srcs)


def test_auto_balance_respects_need_cap():
    # Source X already has 2 submissions ⇒ 0 new slots.
    srcs = [_sd("X", 100.0), _sd("Y", 50.0)]
    additions = auto_balance(
        scored_sources=srcs,
        reviewers=["alice", "bob", "chris"],
        current_assignments={},
        submitted_by={"X": {"someone", "anotherone"}},
    )
    assert all("X" not in srcs for srcs in additions.values())
    # Y still gets its 2 slots.
    assigned_to_Y = [r for r, srcs in additions.items() if "Y" in srcs]
    assert len(assigned_to_Y) == 2


def test_auto_balance_skips_already_submitted_reviewer():
    # Alice already submitted X; auto-balance must not pick her for X.
    srcs = [_sd("X", 100.0)]
    additions = auto_balance(
        scored_sources=srcs,
        reviewers=["alice", "bob", "chris"],
        current_assignments={},
        submitted_by={"X": {"alice"}},
    )
    # X needs 1 more reviewer (target 2, 1 submitted); must NOT be alice.
    chosen = [r for r, srcs in additions.items() if "X" in srcs]
    assert chosen and "alice" not in chosen


def test_auto_balance_skips_already_assigned():
    # Bob is already assigned X; auto-balance picks somebody else.
    srcs = [_sd("X", 100.0)]
    additions = auto_balance(
        scored_sources=srcs,
        reviewers=["alice", "bob", "chris"],
        current_assignments={"bob": ["X"]},
        submitted_by={},
    )
    chosen = [r for r, srcs in additions.items() if "X" in srcs]
    assert chosen and "bob" not in chosen


def test_auto_balance_idempotent_when_fully_committed():
    # Source has both target slots already covered by current assignments
    # AND nothing new ⇒ no additions.
    srcs = [_sd("X", 100.0)]
    additions = auto_balance(
        scored_sources=srcs,
        reviewers=["alice", "bob", "chris"],
        current_assignments={"alice": ["X"], "bob": ["X"]},
        submitted_by={},
    )
    assert all(srcs == [] for srcs in additions.values())


def test_auto_balance_load_uses_balance_weight_not_raw():
    # Two sources: one monster (score 2300, bw≈48), one tiny (score 25, bw=5).
    # With raw-score balancing the monster would skew everything to one
    # reviewer; with sqrt the two reviewers' totals are within sqrt-2x.
    srcs = [_sd("monster", 2300.0), _sd("tiny", 25.0)]
    additions = auto_balance(
        scored_sources=srcs,
        reviewers=["alice", "bob"],
        current_assignments={},
        submitted_by={},
        review_target=1,
    )
    assert "monster" in additions["alice"] or "monster" in additions["bob"]
    # tiny must go to the OTHER reviewer (LPT after monster).
    if "monster" in additions["alice"]:
        assert additions["bob"] == ["tiny"]
    else:
        assert additions["alice"] == ["tiny"]


# ---------------------------------------------------------------------------
# apply_additions, remove_assignment, reassign_queue
# ---------------------------------------------------------------------------


def test_apply_additions_writes_records():
    store = AssignmentStore()
    n = apply_additions(
        store, {"alice": ["A", "B"], "bob": ["C"]},
        assigned_by="homand",
    )
    assert n == 3
    assert {r.source for r in store.assignments["alice"]} == {"A", "B"}
    assert store.assignments["alice"][0].assigned_by == "homand"
    assert store.assignments["alice"][0].assigned_at  # set to now


def test_apply_additions_idempotent_on_existing_sources():
    store = AssignmentStore(assignments={
        "alice": [AssignmentRecord(source="A", assigned_at="t0")],
    })
    n = apply_additions(store, {"alice": ["A", "B"]})
    assert n == 1
    assert [r.source for r in store.assignments["alice"]] == ["A", "B"]


def test_remove_assignment_returns_bool_and_keeps_reviewer():
    store = AssignmentStore(assignments={
        "alice": [
            AssignmentRecord(source="A", assigned_at=""),
            AssignmentRecord(source="B", assigned_at=""),
        ],
    })
    assert remove_assignment(store, "alice", "A") is True
    assert [r.source for r in store.assignments["alice"]] == ["B"]
    # No-op on absent source / unknown reviewer.
    assert remove_assignment(store, "alice", "ghost") is False
    assert remove_assignment(store, "nobody", "A") is False
    # Reviewer entry stays so they remain visible on the team table.
    assert "alice" in store.assignments


def test_reassign_queue_moves_and_skips_conflicts():
    store = AssignmentStore(assignments={
        "alice": [
            AssignmentRecord(source="A", assigned_at="t0"),
            AssignmentRecord(source="B", assigned_at="t0"),
            AssignmentRecord(source="C", assigned_at="t0"),
        ],
        "bob": [AssignmentRecord(source="B", assigned_at="t0")],
    })
    moved, skipped = reassign_queue(
        store, from_reviewer="alice", to_reviewer="bob",
        submitted_by={"C": {"bob"}},
    )
    # B is already on bob's queue ⇒ skipped. C is already submitted by
    # bob ⇒ skipped. Only A moves.
    assert moved == ["A"]
    assert set(skipped) == {"B", "C"}
    bob_sources = [r.source for r in store.assignments["bob"]]
    assert "A" in bob_sources
    # Alice keeps the skipped ones.
    alice_remaining = [r.source for r in store.assignments["alice"]]
    assert set(alice_remaining) == {"B", "C"}


def test_reassign_queue_empty_source():
    store = AssignmentStore()
    moved, skipped = reassign_queue(
        store, from_reviewer="alice", to_reviewer="bob")
    assert moved == [] and skipped == []


def test_submitted_by_map_from_disk(tmp_path):
    save_submitted(tmp_path, Recommendation(
        source="0003-066u", model="current", reviewer="alice",
        source_comment="ok"))
    save_submitted(tmp_path, Recommendation(
        source="0003-066u", model="current", reviewer="Bob Smith",
        source_comment="ok"))
    m = submitted_by_map(tmp_path, ["0003-066u", "0415+379u"])
    assert m["0003-066u"] == {"alice", "bob_smith"}
    assert m["0415+379u"] == set()
