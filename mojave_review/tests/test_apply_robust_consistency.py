"""Applying a recommendation must leave every clusterID with ONE robust flag
across all its epochs (no per-epoch inconsistency in the resulting table/CSV)."""

from __future__ import annotations

import pandas as pd

from mojave_review.recommendations.apply import apply_recommendation_with_history
from mojave_review.recommendations.schema import (
    ClusterFeedback, Edit, Recommendation,
)


def _df(robust_by_epoch):
    # cluster 0 (core) + cluster 1 with a per-epoch robust list (>=5 epochs so
    # it stays eligible and isn't auto-demoted).
    rows = [dict(epoch=1990.0 + i, clusterID=0, origID=0, robust=True,
                 use_in_fit=True) for i in range(6)]
    for i, rob in enumerate(robust_by_epoch):
        rows.append(dict(epoch=2000.0 + i, clusterID=1, origID=1,
                         robust=rob, use_in_fit=True))
    return pd.DataFrame(rows)


def _uniform(df, cid):
    return df.loc[df["clusterID"] == cid, "robust"].nunique() == 1


def test_inconsistent_input_normalized_by_empty_rec():
    # Pre-existing per-epoch inconsistency, no edits -> normalized to the
    # earliest-epoch value (True here).
    df = _df([True, True, False, True, True, True])
    rec = Recommendation(source="x", model="current", reviewer="t")
    out, hist = apply_recommendation_with_history(df, rec)
    assert _uniform(out, 1)
    assert bool(out.loc[out["clusterID"] == 1, "robust"].iloc[0]) is True
    assert any("Normalized cluster 1" in h for h in hist)


def test_set_robust_repairs_inconsistency_to_first_epoch_value():
    # Cluster 1 inconsistent; explicit set_robust=True (== first-epoch value)
    # must still write EVERY epoch, not early-return on iloc[0].
    df = _df([True, False, False, True, True, True])
    rec = Recommendation(source="x", model="current", reviewer="t",
                         cluster_feedback={"1": ClusterFeedback(True, "")})
    out, _ = apply_recommendation_with_history(df, rec)
    assert _uniform(out, 1)
    assert bool(out.loc[out["clusterID"] == 1, "robust"].iloc[0]) is True


def test_set_robust_false_applies_to_all_epochs():
    df = _df([True, True, True, True, True, True])
    rec = Recommendation(source="x", model="current", reviewer="t",
                         cluster_feedback={"1": ClusterFeedback(False, "")})
    out, _ = apply_recommendation_with_history(df, rec)
    assert _uniform(out, 1)
    assert bool(out.loc[out["clusterID"] == 1, "robust"].iloc[0]) is False


def test_consistent_input_unchanged():
    df = _df([True, True, True, True, True, True])
    rec = Recommendation(source="x", model="current", reviewer="t")
    out, hist = apply_recommendation_with_history(df, rec)
    assert out.equals(df)          # no-op preserved for consistent data
    assert hist == []
