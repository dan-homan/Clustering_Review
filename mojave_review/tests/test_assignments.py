"""data/assignments: schema round-trip, derived helpers, atomic write."""

from __future__ import annotations

import datetime as dt
import json

from mojave_review.data.assignments import (
    AssignmentRecord, AssignmentStore, SCHEMA_VERSION, STALE_DAYS,
    active_reviewers, all_assigned_sources, all_submitting_reviewers,
    apply_additions, assignment_status, assignments_for, assignments_path,
    auto_balance, credit_prior_submissions, get_source_target_date,
    is_paused, is_stale, load_store, migrate_per_record_targets_to_source,
    needs_for, reassign_queue, remove_assignment, reviewer_load, save_store,
    set_paused, set_source_target_date, set_source_target_dates_bulk,
    sources_in_range, submitted_by_map,
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
    assert store.version == SCHEMA_VERSION
    assert store.assignments == {}
    assert store.deadline is None
    assert store.paused_reviewers == []


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
    assert on_disk["version"] == SCHEMA_VERSION
    assert on_disk["default_review_target"] == 2
    assert on_disk["paused_reviewers"] == []


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
    # v3: target dates live on the store keyed by source.
    store = AssignmentStore()
    assert is_stale(store, "0003-066u",
                    today=dt.date(2099, 1, 1)) is False


def test_is_stale_at_boundary():
    store = AssignmentStore(source_target_dates={"0003-066u": "2026-06-10"})
    boundary = dt.date(2026, 6, 10) + dt.timedelta(days=STALE_DAYS)
    assert is_stale(store, "0003-066u", today=boundary) is False
    assert is_stale(store, "0003-066u",
                    today=boundary + dt.timedelta(days=1)) is True
    # Source without a target ⇒ never stale.
    assert is_stale(store, "0415+379u",
                    today=dt.date(2099, 1, 1)) is False


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


# ---------------------------------------------------------------------------
# Schema v1 → v2 compat
# ---------------------------------------------------------------------------


def test_load_store_v1_compat(tmp_path):
    # Hand-write a v1 store (no paused_reviewers field) and verify load.
    p = tmp_path / "_admin"
    p.mkdir()
    (p / "assignments.json").write_text(json.dumps({
        "version": 1, "updated_at": "t0", "deadline": None,
        "default_review_target": 2,
        "assignments": {
            "alice": [{"source": "A", "assigned_at": "t0"}],
        },
    }))
    store = load_store(tmp_path)
    # Field defaults applied; assignments preserved.
    assert store.paused_reviewers == []
    assert [r.source for r in store.assignments["alice"]] == ["A"]


# ---------------------------------------------------------------------------
# Team-pause
# ---------------------------------------------------------------------------


def test_set_paused_is_idempotent():
    store = AssignmentStore()
    set_paused(store, "alice", True)
    set_paused(store, "alice", True)
    assert store.paused_reviewers == ["alice"]
    assert is_paused(store, "alice") is True
    set_paused(store, "alice", False)
    assert store.paused_reviewers == []
    assert is_paused(store, "alice") is False
    # Resuming a non-paused reviewer is a no-op.
    set_paused(store, "bob", False)
    assert store.paused_reviewers == []


def test_active_reviewers_filters_paused_and_preserves_order():
    store = AssignmentStore(paused_reviewers=["bob"])
    assert active_reviewers(store, ["alice", "bob", "chris"]) \
        == ["alice", "chris"]


def test_auto_balance_with_paused_excluded_externally():
    # auto_balance itself doesn't know about pausing — the caller is
    # expected to pre-filter via active_reviewers. This double-checks
    # that filtering at the call site works as a one-liner.
    srcs = [_sd("X", 100.0)]
    store = AssignmentStore(paused_reviewers=["bob"])
    additions = auto_balance(
        scored_sources=srcs,
        reviewers=active_reviewers(store, ["alice", "bob", "chris"]),
        current_assignments={}, submitted_by={},
    )
    # Bob must not receive X.
    assert "X" not in additions.get("bob", [])
    chosen = [r for r, srcs in additions.items() if "X" in srcs]
    assert set(chosen) <= {"alice", "chris"}


# ---------------------------------------------------------------------------
# Submissions discovery — including considered/ (post Stage-3 apply)
# ---------------------------------------------------------------------------


def test_all_submitting_reviewers_includes_considered(tmp_path):
    # One source has a live submission + a considered (Stage-3-applied)
    # one from a different reviewer.
    save_submitted(tmp_path, Recommendation(
        source="X", model="current", reviewer="alice",
        source_comment="x"))
    cdir = tmp_path / "X" / "considered" / "2026-06-15"
    cdir.mkdir(parents=True)
    (cdir / "bob.json").write_text("{}")
    m = all_submitting_reviewers(tmp_path, ["X"])
    assert m["X"] == {"alice", "bob"}


def test_credit_prior_submissions_idempotent(tmp_path):
    save_submitted(tmp_path, Recommendation(
        source="A", model="current", reviewer="alice",
        source_comment="x"))
    save_submitted(tmp_path, Recommendation(
        source="A", model="current", reviewer="bob",
        source_comment="x"))
    save_submitted(tmp_path, Recommendation(
        source="B", model="current", reviewer="alice",
        source_comment="x"))
    store = AssignmentStore()
    # First credit pass: 3 records.
    n = credit_prior_submissions(
        store,
        recommendations_dir=tmp_path,
        sources=["A", "B"],
        name_for_slug={"alice": "alice", "bob": "bob"},
    )
    assert n == 3
    assert {r.source for r in store.assignments["alice"]} == {"A", "B"}
    assert {r.source for r in store.assignments["bob"]} == {"A"}
    # Second pass: must be a no-op (no duplicates).
    assert credit_prior_submissions(
        store,
        recommendations_dir=tmp_path,
        sources=["A", "B"],
        name_for_slug={"alice": "alice", "bob": "bob"},
    ) == 0


def test_load_store_v2_compat(tmp_path):
    # v2 (no source_target_dates) loads cleanly with empty map.
    p = tmp_path / "_admin"
    p.mkdir()
    (p / "assignments.json").write_text(json.dumps({
        "version": 2, "updated_at": "t0", "deadline": None,
        "default_review_target": 2,
        "assignments": {
            "alice": [{"source": "A", "assigned_at": "t0",
                       "target_date": "2026-07-05"}],
        },
        "paused_reviewers": [],
    }))
    store = load_store(tmp_path)
    assert store.source_target_dates == {}
    # The per-record target survives load (we keep the field) but is
    # NOT promoted automatically; callers can run the explicit
    # migration helper if they want.
    assert store.assignments["alice"][0].target_date == "2026-07-05"


# ---------------------------------------------------------------------------
# Source-level target dates (v3 helpers)
# ---------------------------------------------------------------------------


def test_set_get_clear_source_target_date():
    store = AssignmentStore()
    set_source_target_date(store, "A", "2026-07-05")
    assert get_source_target_date(store, "A") == "2026-07-05"
    # Setting to None clears.
    set_source_target_date(store, "A", None)
    assert get_source_target_date(store, "A") is None
    # Empty string also clears.
    set_source_target_date(store, "A", "2026-07-05")
    set_source_target_date(store, "A", "")
    assert get_source_target_date(store, "A") is None


def test_set_source_target_date_validates_format():
    import pytest
    store = AssignmentStore()
    with pytest.raises(ValueError):
        set_source_target_date(store, "A", "not-a-date")
    assert get_source_target_date(store, "A") is None


def test_bulk_set_returns_changed_count():
    store = AssignmentStore(source_target_dates={"A": "2026-07-05"})
    # A unchanged, B + C newly set ⇒ 2 changes.
    n = set_source_target_dates_bulk(
        store, ["A", "B", "C"], "2026-07-05")
    assert n == 2
    assert store.source_target_dates == {
        "A": "2026-07-05", "B": "2026-07-05", "C": "2026-07-05"}
    # Setting all to None clears them all (3 changes).
    n = set_source_target_dates_bulk(
        store, ["A", "B", "C"], None)
    assert n == 3
    assert store.source_target_dates == {}


def test_sources_in_range_lexicographic_with_swap():
    all_sources = ["0003-066u", "0003+380u", "0415+379u", "0851+202u",
                   "1226+023u", "2200+420u"]
    # Inclusive range. Note the lexicographic order — '-' < '+' < digits,
    # so 0003-066u sorts BEFORE 0003+380u in ASCII; the test just
    # verifies the inclusive-range mechanic, not MOJAVE's source order.
    assert sources_in_range(all_sources, "0415+379u", "1226+023u") == [
        "0415+379u", "0851+202u", "1226+023u"]
    # Bounds reversed ⇒ silent swap.
    assert sources_in_range(all_sources, "2200+420u", "0851+202u") == [
        "0851+202u", "1226+023u", "2200+420u"]


def test_migrate_per_record_targets_idempotent():
    store = AssignmentStore(assignments={
        "alice": [AssignmentRecord(source="A", assigned_at="t0",
                                   target_date="2026-07-05")],
        "bob": [AssignmentRecord(source="A", assigned_at="t0")],
    })
    # First migration: A picks up its target date from alice's record.
    assert migrate_per_record_targets_to_source(store) == 1
    assert store.source_target_dates == {"A": "2026-07-05"}
    # Idempotent — second call doesn't double-promote.
    assert migrate_per_record_targets_to_source(store) == 0
    # An explicit source-level entry wins over a stray per-record one.
    store.source_target_dates["A"] = "2026-08-01"
    store.assignments["alice"][0].target_date = "2026-07-05"
    assert migrate_per_record_targets_to_source(store) == 0
    assert store.source_target_dates["A"] == "2026-08-01"


def test_credit_prior_submissions_translates_slug_to_name(tmp_path):
    # "Bob Smith" slugifies to "bob_smith"; credit must record under the
    # full name (matching what auto_balance / dashboard use as the key).
    save_submitted(tmp_path, Recommendation(
        source="A", model="current", reviewer="Bob Smith",
        source_comment="x"))
    store = AssignmentStore()
    credit_prior_submissions(
        store,
        recommendations_dir=tmp_path,
        sources=["A"],
        name_for_slug={"bob_smith": "Bob Smith"},
    )
    assert "Bob Smith" in store.assignments
    # Slug-only fallback when the lookup is missing the slug: record
    # goes under the slug rather than being silently dropped.
    store2 = AssignmentStore()
    credit_prior_submissions(
        store2,
        recommendations_dir=tmp_path,
        sources=["A"],
        name_for_slug={},
    )
    assert "bob_smith" in store2.assignments
