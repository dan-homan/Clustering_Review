"""Unit tests for recommendations/aggregate.py (Stage-3 aggregation logic)."""

from __future__ import annotations

import pandas as pd

from mojave_review.recommendations.aggregate import (
    AggregationView, build_aggregation_view, compose_aggregated,
    default_final_robust, describe_edit, edit_key,
)
from mojave_review.recommendations.apply import apply_recommendation
from mojave_review.recommendations.schema import (
    ClusterFeedback, Edit, Recommendation,
)


def _current_df():
    # clusters 0(core),1,2,3 ; robust flags as a starting model
    rows = []
    for cid, robust in [(0, True), (1, True), (2, True), (3, True)]:
        for ep in (2000.0, 2001.0, 2002.0, 2003.0, 2004.0):
            rows.append(dict(clusterID=cid, epoch=ep, robust=robust,
                             use_in_fit=True))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# default robustness vote
# ---------------------------------------------------------------------------

def test_default_majority_plus_current():
    # current=Robust(True); two reviewers say non-robust -> votes T,F,F -> False
    assert default_final_robust([False, False], True) is False
    # current=True; one says False -> tie 1-1 -> current True
    assert default_final_robust([False], True) is True
    # current=False; two say robust -> votes F,T,T -> True
    assert default_final_robust([True, True], False) is True
    # current=True; one robust one non -> T,T,F -> True
    assert default_final_robust([True, False], True) is True


# ---------------------------------------------------------------------------
# edit identity / description
# ---------------------------------------------------------------------------

def test_edit_key_collapses_identical_ignores_comment():
    a = Edit(op="change_clusterID", scope="all_epochs", from_id=3, to_id=4,
             comment="reviewer A wording")
    b = Edit(op="change_clusterID", scope="all_epochs", from_id=3, to_id=4,
             comment="reviewer B different wording")
    assert edit_key(a) == edit_key(b)
    c = Edit(op="change_clusterID", scope="all_epochs", from_id=3, to_id=5)
    assert edit_key(a) != edit_key(c)


def test_describe_edit_readable():
    assert "Re-ID 3 → 4" in describe_edit(
        Edit(op="change_clusterID", scope="all_epochs", from_id=3, to_id=4))
    assert "use_in_fit=False" in describe_edit(
        Edit(op="set_use_in_fit", scope="epoch", epoch=2003.0, value=False))


# ---------------------------------------------------------------------------
# build the view
# ---------------------------------------------------------------------------

def _recs():
    a = Recommendation(
        source="x", model="current", reviewer="alice",
        updated_at="2026-06-01T10:00:00+00:00",
        cluster_feedback={"2": ClusterFeedback(False, "merges with 1"),
                          "3": ClusterFeedback(False, "")},
        edits=[Edit(op="change_clusterID", scope="all_epochs", from_id=3,
                    to_id=4, comment="A"),
               Edit(op="set_use_in_fit", scope="epoch", epoch=2003.0,
                    value=False)],
        source_comment="looks ok overall",
    )
    b = Recommendation(
        source="x", model="current", reviewer="bob",
        updated_at="2026-06-02T11:00:00+00:00",
        cluster_feedback={"2": ClusterFeedback(False, "")},
        edits=[Edit(op="change_clusterID", scope="all_epochs", from_id=3,
                    to_id=4, comment="B")],   # same edit as alice -> collapses
    )
    return [a, b]


def test_build_view_robustness_votes_and_default():
    view = build_aggregation_view("x", _recs(), _current_df())
    assert view.reviewers == ["alice", "bob"]
    rows = {r.cid: r for r in view.robustness_rows}
    # cluster 2: alice+bob both non-robust; current robust -> votes T,F,F -> False
    assert rows[2].votes == {"alice": False, "bob": False}
    assert rows[2].default_final is False
    # cluster 3: only alice non-robust; current robust -> tie -> current True
    assert rows[3].votes == {"alice": False}
    assert rows[3].default_final is True


def test_build_view_edits_dedupe_and_order():
    view = build_aggregation_view("x", _recs(), _current_df())
    # two unique edits: the re-ID (proposed by both) and the use_in_fit (alice)
    assert len(view.edit_rows) == 2
    reid = view.edit_rows[0]
    assert reid.op == "change_clusterID"       # change_clusterID ordered first
    assert sorted(reid.proposers) == ["alice", "bob"]
    assert view.edit_rows[1].op == "set_use_in_fit"


def test_build_view_comments_collected():
    view = build_aggregation_view("x", _recs(), _current_df())
    by = {c.reviewer: c for c in view.comments}
    assert by["alice"].source_comment == "looks ok overall"
    assert (2, "merges with 1") in by["alice"].cluster_comments
    assert "bob" not in by  # bob left no comments -> omitted


# ---------------------------------------------------------------------------
# compose decisions -> Recommendation, and apply it
# ---------------------------------------------------------------------------

def test_compose_and_apply():
    view = build_aggregation_view("x", _recs(), _current_df())
    payload = view.store_payload()
    reid_key = view.edit_rows[0].key
    rec = compose_aggregated(
        "x", "admin",
        robustness_finals={2: False, 3: True},   # accept 2->non-robust; keep 3
        robustness_reasons={2: "consensus"},
        accepted_edit_keys=[reid_key],            # accept the re-ID only
        edit_reasons={reid_key: "both agreed"},
        store_payload=payload,
    )
    assert rec.cluster_feedback["2"].recommended_robust is False
    assert rec.cluster_feedback["2"].comment == "consensus"
    assert len(rec.edits) == 1 and rec.edits[0].to_id == 4
    assert rec.edits[0].comment == "both agreed"

    out = apply_recommendation(_current_df(), rec)
    # cluster 3 renamed to 4 everywhere
    assert (out["clusterID"] == 3).sum() == 0
    assert (out["clusterID"] == 4).sum() == 5
    # cluster 2 now non-robust
    assert bool(out.loc[out["clusterID"] == 2, "robust"].iloc[0]) is False


def test_compose_empty_when_nothing_decided():
    view = build_aggregation_view("x", _recs(), _current_df())
    rec = compose_aggregated(
        "x", "admin", robustness_finals={}, robustness_reasons={},
        accepted_edit_keys=[], edit_reasons={}, store_payload=view.store_payload(),
    )
    assert rec.is_empty()


def test_view_empty_no_submissions():
    view = build_aggregation_view("x", [], _current_df())
    assert view.is_empty()
    assert view.robustness_rows == [] and view.edit_rows == []


# ---------------------------------------------------------------------------
# Stage-3 ledger entry (suggested-vs-applied record)
# ---------------------------------------------------------------------------

def test_stage3_ledger_entry_records_accepted_and_rejected():
    from mojave_review.recommendations.aggregate import stage3_ledger_entry
    view = build_aggregation_view("x", _recs(), _current_df())
    reid_key = view.edit_rows[0].key
    uif_key = view.edit_rows[1].key
    md = stage3_ledger_entry(
        view,
        finals={2: False, 3: True},          # cl2 changed; cl3 kept (dissent)
        rob_reasons={2: "consensus"},
        accepted_keys=[reid_key],            # re-ID accepted; use_in_fit rejected
        edit_reasons={reid_key: "both agreed", uif_key: "looks fine"},
        applied_by="homand", date="2026-06-05", backup_ref="backup_007",
    )
    assert md.startswith("### 2026-06-05 — Stage 3 reconciliation "
                         "(run 1, applied by homand) — backup_007")
    assert "Considered: alice" in md and "bob" in md
    # cl2 changed to Non-robust, supported, with reason
    assert "cl 2 → Non-robust ✓ (changed from Robust)" in md
    assert "consensus" in md
    # cl3 kept Robust, alice's dissent recorded as ✗
    assert "cl 3 → Robust — (kept)" in md
    assert "alice suggested Non-robust ✗" in md
    # edits: re-ID accepted ✓, use_in_fit not applied ✗
    assert "✓ accepted" in md and "both agreed" in md
    assert "✗ not applied" in md


def test_archive_considered_submissions(tmp_path=None):
    import tempfile
    from pathlib import Path
    from mojave_review.recommendations.store import (
        save_submitted, archive_considered_submissions, submission_path,
    )
    d = Path(tempfile.mkdtemp())
    rd = d / "recommendations"
    for who in ("alice", "bob"):
        save_submitted(rd, Recommendation(source="0003+380u", model="current",
                                          reviewer=who, source_comment="x"))
    moved = archive_considered_submissions(
        rd, "0003+380u", ["alice", "bob"], date="2026-06-05", execute=True)
    assert len(moved) == 2
    # gone from submitted/, present under considered/<date>/
    assert not submission_path(rd, "0003+380u", "alice").is_file()
    assert (rd / "0003+380u" / "considered" / "2026-06-05" / "alice.json").is_file()
    # dry-run leaves files in place
    save_submitted(rd, Recommendation(source="0003+380u", model="current",
                                      reviewer="carol"))
    dry = archive_considered_submissions(
        rd, "0003+380u", ["carol"], date="2026-06-06", execute=False)
    assert len(dry) == 1 and submission_path(rd, "0003+380u", "carol").is_file()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS: aggregate logic")


def test_stage3_ledger_entry_run_index():
    from mojave_review.recommendations.aggregate import stage3_ledger_entry
    view = build_aggregation_view("x", _recs(), _current_df())
    md = stage3_ledger_entry(
        view, finals={}, rob_reasons={}, accepted_keys=[], edit_reasons={},
        applied_by="homand", date="2026-06-12", backup_ref="backup_009",
        run_index=3,
    )
    assert md.startswith("### 2026-06-12 — Stage 3 reconciliation (run 3, "
                         "applied by homand) — backup_009")
