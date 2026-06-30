"""The Robustness tab uses inline Robust/Non-robust radio rows (not a DataTable
dropdown). Verify the row builder, the bridge round-trip, and the
preload-to-current semantics (an opinion is recorded only when the pick differs
from the cluster's current status)."""

from __future__ import annotations

from mojave_review.ui.recommendations_panel import build_cluster_rows
from mojave_review.ui.recommendations_callbacks import (
    _cluster_rows_from_components,
    build_rec_from_ui_state,
)


def _radio_and_comment(children):
    # children[0] is the header; each data row is [label, radio, textarea].
    radios = [row.children[1] for row in children[1:]]
    comments = [row.children[2] for row in children[1:]]
    return radios, comments


def _store_from_rows(children):
    """Reconstruct the store the bridge/submit build from the rendered rows."""
    radios, comments = _radio_and_comment(children)
    return _cluster_rows_from_components(
        [r.value for r in radios], [r.id for r in radios],
        [c.value for c in comments], [c.id for c in comments],
    )


def test_build_cluster_rows_radio_preloaded_to_current():
    rows = build_cluster_rows([
        {"clusterID": 0, "current_robust": "Robust",
         "recommended_robust": "robust", "comment": ""},
        {"clusterID": 3, "current_robust": "Robust",
         "recommended_robust": "robust", "comment": ""},
        {"clusterID": 5, "current_robust": "Non-robust",
         "recommended_robust": "non-robust", "comment": ""},
    ])
    radios, _ = _radio_and_comment(rows)
    # Radio preloaded to the current status; id carries clusterID + current.
    assert radios[0].id == {"type": "robust-radio", "cid": 0, "cur": "robust"}
    assert radios[0].value == "robust"
    assert radios[2].value == "non-robust"
    # Core (cluster 0) radio is disabled, others are not.
    assert all(o["disabled"] for o in radios[0].options)
    assert not any(o["disabled"] for o in radios[1].options)


def test_unchanged_picks_record_no_opinion():
    # Every radio left at its preloaded current value → no cluster_feedback.
    rows = build_cluster_rows([
        {"clusterID": 3, "current_robust": "Robust",
         "recommended_robust": "robust", "comment": ""},
        {"clusterID": 5, "current_robust": "Non-robust",
         "recommended_robust": "non-robust", "comment": ""},
    ])
    store = _store_from_rows(rows)
    rec = build_rec_from_ui_state(
        source="x", model="current", reviewer="r", source_comment="",
        no_robustness_changes=False, cluster_rows=store, epoch_rows=[], edits=[])
    assert rec.cluster_feedback == {}
    assert rec.is_empty()


def test_flipped_pick_records_opinion_comment_only_records_none():
    rows = build_cluster_rows([
        # flipped robust -> non-robust = an opinion
        {"clusterID": 4, "current_robust": "Robust",
         "recommended_robust": "non-robust", "comment": "merges with 3"},
        # unchanged but commented = recorded with recommended_robust=None
        {"clusterID": 6, "current_robust": "Robust",
         "recommended_robust": "robust", "comment": "looks fine"},
    ])
    store = _store_from_rows(rows)
    rec = build_rec_from_ui_state(
        source="x", model="current", reviewer="r", source_comment="",
        no_robustness_changes=False, cluster_rows=store, epoch_rows=[], edits=[])
    assert rec.cluster_feedback["4"].recommended_robust is False
    assert rec.cluster_feedback["4"].comment == "merges with 3"
    assert rec.cluster_feedback["6"].recommended_robust is None
    assert rec.cluster_feedback["6"].comment == "looks fine"
