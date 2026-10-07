"""``robust=True`` on a cluster too thin to be eligible is a data defect.

The eligibility rule is: a cluster needs >= 5 ``use_in_fit=True`` epochs to be
robust. Three sample sources carried a robust flag on a cluster that never met
it (``0306+102u`` cl 9, ``1127-145u`` cl 9, ``0415+379u`` cl 999) — undetected
because the consistency audit only compared a cluster's flag against *itself*,
and ``apply_recommendation`` only re-checks clusters an edit touched.
"""

from __future__ import annotations

import pandas as pd

from mojave_review.recommendations.apply import (
    PARK_ID, apply_recommendation_with_history, robust_ineligible,
    _normalize_robust_eligibility,
)
from mojave_review.recommendations.schema import Edit, Recommendation


def _rows(cid, n_epochs, robust, use_in_fit=True, start=2000.0):
    return [dict(epoch=start + i, clusterID=cid, origID=cid, robust=robust,
                 use_in_fit=use_in_fit) for i in range(n_epochs)]


def _df(*groups):
    return pd.DataFrame([r for g in groups for r in g])


# --- detection -------------------------------------------------------------

def test_detects_thin_robust_cluster():
    df = _df(_rows(0, 6, True), _rows(1, 6, True), _rows(9, 3, True))
    assert robust_ineligible(df) == {9: 3}


def test_counts_use_in_fit_not_epochs():
    # 8 epochs, only 4 of them fitted -> ineligible.
    df = _df(_rows(0, 8, True), _rows(3, 4, True) + _rows(3, 4, True, False,
                                                          start=2010.0))
    assert robust_ineligible(df) == {3: 4}


def test_park_id_in_scope():
    # 999 is < 1000, so it renders as an ordinary coloured cluster.
    df = _df(_rows(0, 6, True), _rows(PARK_ID, 1, True, False))
    assert robust_ineligible(df) == {PARK_ID: 0}


def test_clean_model_reports_nothing():
    df = _df(_rows(0, 6, True), _rows(1, 6, True), _rows(2, 3, False))
    assert robust_ineligible(df) == {}


def test_core_unassigned_and_synthetic_out_of_scope():
    # core (0) is robust by definition even with one fitted epoch; -1 and
    # >= 1000 draw as black '+' and are never fitted.
    df = _df(_rows(0, 1, True), _rows(-1, 3, True, False),
             _rows(1001, 2, True, False))
    assert robust_ineligible(df) == {}


# --- repair ----------------------------------------------------------------

def test_normalize_demotes_and_logs():
    df = _df(_rows(0, 6, True), _rows(9, 3, True))
    hist = _normalize_robust_eligibility(df)
    assert not df.loc[df["clusterID"] == 9, "robust"].any()
    assert df.loc[df["clusterID"] == 0, "robust"].all()      # core untouched
    assert any("Normalized cluster 9" in h and "3 use_in_fit rows" in h
               for h in hist)
    # idempotent
    assert _normalize_robust_eligibility(df) == []


def test_normalize_notes_the_park_bucket():
    df = _df(_rows(0, 6, True), _rows(PARK_ID, 1, True, False))
    hist = _normalize_robust_eligibility(df)
    assert any("(park)" in h for h in hist)


# --- apply-side -----------------------------------------------------------

def test_parked_row_does_not_inherit_a_robust_flag():
    # Renumbering 1 -> 2 at an epoch where 2 already exists parks the old
    # cluster-2 row on 999; it used to arrive carrying cluster 2's robust=True.
    df = _df(_rows(0, 6, True), _rows(1, 6, True), _rows(2, 6, True))
    rec = Recommendation(
        source="x", model="current", reviewer="t",
        edits=[Edit(op="change_clusterID", scope="single", epoch=2000.0,
                    from_id=1, to_id=2)],
    )
    out, hist = apply_recommendation_with_history(df, rec)
    parked = out.loc[out["clusterID"] == PARK_ID]
    assert len(parked) == 1
    assert not parked["robust"].any()
    assert any(f"cluster {PARK_ID} (park)" in h for h in hist)


def test_apply_leaves_untouched_thin_cluster_alone():
    # Applying a recommendation must not silently rewrite a cluster no edit
    # touched (sample-wide repair is mojave-review-audit-robust's job), and the
    # no-op fast path must survive.
    df = _df(_rows(0, 6, True), _rows(9, 3, True))
    rec = Recommendation(source="x", model="current", reviewer="t")
    out, hist = apply_recommendation_with_history(df, rec)
    assert out.equals(df)
    assert hist == []
