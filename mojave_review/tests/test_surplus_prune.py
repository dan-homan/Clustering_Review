"""plan_surplus_prune: give back assignments a source no longer needs.

The add side of ``auto_balance`` has always respected the review target
(``slots = review_target - len(committed)``), so a surplus never comes from
over-assigning on purpose — it comes from assignments handed out *before*
reviews landed, which nothing cleaned up. On the real sample that left 40 of
87 open sources over target with 44 recoverable queue entries.

Rules pinned here (reviewer decisions 2026-11-09):
  * submitted reviews are untouchable;
  * only PENDING assignments are removable — never one with a started draft;
  * within a source, the heaviest-loaded reviewer donates first.
"""

from __future__ import annotations

import math

from mojave_review.data.assignments import (
    AssignmentRecord, AssignmentStore, apply_removals, plan_surplus_prune,
)


def _store(assignments: dict[str, list[str]]) -> AssignmentStore:
    return AssignmentStore(assignments={
        rv: [AssignmentRecord(source=s, assigned_at="") for s in srcs]
        for rv, srcs in assignments.items()
    })


def _plan(assignments, *, submitted=None, drafts=(), weights=None, target=2):
    """``drafts`` is a set of (reviewer, source) pairs that have a draft."""
    store = _store(assignments)
    open_sources = {s for srcs in assignments.values() for s in srcs}
    open_sources |= set((submitted or {}).keys())
    draft_set = set(drafts)

    def status(reviewer: str, source: str) -> str:
        if reviewer in (submitted or {}).get(source, set()):
            return "submitted"
        return "in_progress" if (reviewer, source) in draft_set else "pending"

    return store, plan_surplus_prune(
        store,
        open_sources=open_sources,
        submitted_by={k: set(v) for k, v in (submitted or {}).items()},
        status=status,
        weight_by_source=weights or {s: 1.0 for s in open_sources},
        review_target=target,
    )


# ---------------------------------------------------------------------------
# Core arithmetic
# ---------------------------------------------------------------------------


def test_at_target_is_a_noop():
    _s, plan = _plan({"a": ["S1"], "b": ["S1"]})
    assert plan.removals == []
    assert plan.residual == []


def test_under_target_is_a_noop():
    _s, plan = _plan({"a": ["S1"]})
    assert plan.removals == []


def test_third_pending_assignment_is_given_back():
    _s, plan = _plan({"a": ["S1"], "b": ["S1"], "c": ["S1"]})
    assert len(plan.removals) == 1
    assert plan.removals[0][1] == "S1"


def test_submitted_reviews_count_toward_the_target():
    # One submitted + two pending = 3 committed, so one pending goes.
    _s, plan = _plan({"b": ["S1"], "c": ["S1"]},
                     submitted={"S1": {"a"}})
    assert len(plan.removals) == 1
    assert plan.removals[0][0] in ("b", "c")


def test_submitted_reviewer_is_never_removed():
    # Two submitted + one pending: the pending one is pure surplus.
    _s, plan = _plan({"a": ["S1"], "b": ["S1"], "c": ["S1"]},
                     submitted={"S1": {"a", "b"}})
    assert plan.removals == [("c", "S1")]


def test_two_surplus_slots_both_go():
    _s, plan = _plan({"a": ["S1"], "b": ["S1"], "c": ["S1"], "d": ["S1"]})
    assert len(plan.removals) == 2


def test_custom_target_is_honoured():
    _s, plan = _plan({"a": ["S1"], "b": ["S1"], "c": ["S1"]}, target=3)
    assert plan.removals == []


# ---------------------------------------------------------------------------
# Drafts are protected
# ---------------------------------------------------------------------------


def test_pending_is_dropped_before_a_draft():
    """The drafting reviewer is deliberately the one the donor rule would
    otherwise pick — ``a`` is both alphabetically first AND the heaviest — so
    this fails if draft protection is dropped, rather than passing by luck of
    name ordering."""
    weights = {"S1": 10.0, "HEAVY": 100.0}
    _s, plan = _plan({"a": ["S1", "HEAVY"], "b": ["S1"], "c": ["S1"]},
                     drafts=[("a", "S1")], weights=weights)
    assert len(plan.removals) == 1
    assert plan.removals[0][0] != "a", "a has a draft and must be left alone"
    assert plan.removals[0] == ("b", "S1")   # heaviest of the two pending


def test_a_draft_is_never_dropped_even_when_it_is_the_only_surplus():
    """Three drafts, nothing pending: the source stays over target rather
    than orphaning someone's work-in-progress."""
    _s, plan = _plan({"a": ["S1"], "b": ["S1"], "c": ["S1"]},
                     drafts=[("a", "S1"), ("b", "S1"), ("c", "S1")])
    assert plan.removals == []
    assert plan.residual == [("S1", 0, 1)]


# ---------------------------------------------------------------------------
# Residual reporting
# ---------------------------------------------------------------------------


def test_three_submitted_is_reported_not_removed():
    """Over target by submissions alone — no queue edit can fix it, and the
    preview must say so rather than look like it mis-counted."""
    _s, plan = _plan({}, submitted={"S1": {"a", "b", "c"}})
    assert plan.removals == []
    assert plan.residual == [("S1", 3, 1)]


def test_three_submitted_plus_a_pending_still_gives_the_pending_back():
    """The pending review is pointless work even though the source stays
    over target — take it, and report what's left. (Real case: 1504+377u.)"""
    _s, plan = _plan({"d": ["S1"]}, submitted={"S1": {"a", "b", "c"}})
    assert plan.removals == [("d", "S1")]
    assert plan.residual == [("S1", 3, 1)]


# ---------------------------------------------------------------------------
# Who donates
# ---------------------------------------------------------------------------


def test_heaviest_loaded_reviewer_donates_first():
    # a holds three open sources, b one -> a is heavier, so a gives up S1.
    _s, plan = _plan({"a": ["S1", "X", "Y"], "b": ["S1"], "c": ["S1"]})
    assert ("a", "S1") in plan.removals


def test_load_counts_weight_not_record_count():
    """b holds fewer sources than a but far heavier ones, so b donates."""
    weights = {"S1": 1.0, "X": 1.0, "Y": 1.0, "BIG": 100.0}
    _s, plan = _plan({"a": ["S1", "X", "Y"], "b": ["S1", "BIG"], "c": ["S1"]},
                     weights=weights)
    assert ("b", "S1") in plan.removals


def test_submitted_work_does_not_make_a_reviewer_look_busy():
    """a's other sources are all submitted (done), so a is NOT the heaviest
    and should keep the pending slot; b, with outstanding work, donates."""
    _s, plan = _plan(
        {"a": ["S1", "X", "Y"], "b": ["S1", "Z"], "c": ["S1"]},
        submitted={"X": {"a"}, "Y": {"a"}},
    )
    assert ("b", "S1") in plan.removals
    assert ("a", "S1") not in plan.removals


def test_ties_break_on_name_for_reproducibility():
    """All three reviewers have identical load, so only the name tie-break
    decides. Pin the actual value — a dict-ordering-dependent answer would
    make successive previews disagree with each other."""
    _s, plan = _plan({"a": ["S1"], "b": ["S1"], "c": ["S1"]})
    assert plan.removals == [("a", "S1")]
    # And it does not depend on the insertion order of the store.
    _s2, plan2 = _plan({"c": ["S1"], "b": ["S1"], "a": ["S1"]})
    assert plan2.removals == [("a", "S1")]


def test_load_is_decremented_as_removals_are_planned():
    """Two surplus sources. ``a`` starts heaviest by a small margin, so it
    donates first — and because its load is decremented immediately, the
    SECOND donation comes from ``b`` instead of stripping ``a`` twice.

    The margin has to be smaller than one source's weight for the ordering to
    flip at all: a reviewer who stays heaviest even after donating *should*
    donate again, which is what makes this worth pinning precisely."""
    weights = {"S1": 10.0, "S2": 10.0, "P": 5.0}
    _s, plan = _plan(
        {"a": ["S1", "S2", "P"], "b": ["S1", "S2"], "c": ["S1", "S2"]},
        weights=weights,          # loads: a 25, b 20, c 20
    )
    donors = sorted(rv for rv, _src in plan.removals)
    assert len(plan.removals) == 2
    assert donors == ["a", "b"], donors        # not ["a", "a"]


# ---------------------------------------------------------------------------
# Scope + integration with apply_removals
# ---------------------------------------------------------------------------


def test_only_in_scope_sources_are_touched():
    """A finalized/stage-2 source isn't in ``open_sources``, so even an
    over-subscribed one is left to the other prune pass."""
    store = _store({"a": ["OPEN", "CLOSED"], "b": ["OPEN", "CLOSED"],
                    "c": ["OPEN", "CLOSED"]})
    plan = plan_surplus_prune(
        store,
        open_sources={"OPEN"},
        submitted_by={},
        status=lambda rv, s: "pending",
        weight_by_source={"OPEN": 1.0, "CLOSED": 1.0},
    )
    assert all(src == "OPEN" for _rv, src in plan.removals)


def test_apply_then_replan_is_idempotent():
    store, plan = _plan({"a": ["S1"], "b": ["S1"], "c": ["S1"], "d": ["S1"]})
    assert apply_removals(store, plan.removals) == 2
    survivors = {rv for rv, recs in store.assignments.items()
                 if any(r.source == "S1" for r in recs)}
    assert len(survivors) == 2
    # Re-planning against the mutated store finds nothing more to do.
    plan2 = plan_surplus_prune(
        store, open_sources={"S1"}, submitted_by={},
        status=lambda rv, s: "pending",
        weight_by_source={"S1": 1.0})
    assert plan2.removals == []


def test_never_drops_below_target():
    """The invariant that matters: whatever the mix, the committed count
    after applying is never less than the target."""
    cases = [
        ({"a": ["S1"], "b": ["S1"], "c": ["S1"]}, None, ()),
        ({"a": ["S1"], "b": ["S1"], "c": ["S1"], "d": ["S1"]}, None, ()),
        ({"b": ["S1"], "c": ["S1"]}, {"S1": {"a"}}, ()),
        ({"c": ["S1"], "d": ["S1"]}, {"S1": {"a", "b"}}, ()),
        ({"a": ["S1"], "b": ["S1"], "c": ["S1"]}, None, [("a", "S1")]),
    ]
    for assignments, submitted, drafts in cases:
        store, plan = _plan(assignments, submitted=submitted, drafts=drafts)
        apply_removals(store, plan.removals)
        still = sum(1 for rv, recs in store.assignments.items()
                    if any(r.source == "S1" for r in recs))
        assert still + len((submitted or {}).get("S1", set())) >= 2, (
            assignments, submitted, drafts, plan.removals)


# ---------------------------------------------------------------------------
# Dashboard wiring
# ---------------------------------------------------------------------------


def test_checkbox_is_an_input_on_the_auto_balance_preview(tmp_path):
    """The trim is opt-in via a checkbox, and toggling it must re-run the
    preview in place — so it has to be an Input, not State."""
    import dash
    from mojave_review.ui.dashboard_callbacks import (
        register_dashboard_callbacks as register_db)

    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    (tmp_path / "Results").mkdir()
    register_db(
        app,
        results_dir=tmp_path / "Results",
        recommendations_dir=tmp_path / "recommendations",
        tokens_path=None,
        reviewer="Test Admin",
    )
    spec = next(cb for key, cb in app.callback_map.items()
                if "dashboard-ab-modal.style" in key)
    inputs = {f"{i['id']}.{i['property']}" for i in spec["inputs"]}
    assert "dashboard-ab-trim-surplus.value" in inputs
