"""Apply a Recommendation to a cluster DataFrame.

Used by the "Visualize recommendations" checkbox (own in-progress recs) and
by the model dropdown when a reviewer picks ``Rec: <other-reviewer>`` to
see what the data would look like under that reviewer's suggestions.

The function is pure: it copies the input DataFrame and never mutates it.
Application order is fixed:

1. ``set_robust`` (derived from ``cluster_feedback.recommended_robust``
   unless ``no_robustness_changes=True``).
2. ``edits`` in the order they appear in the recommendation. Later edits
   see the effects of earlier ones (e.g. after ``change_clusterID
   from=3 to=4 all_epochs``, a later ``set_use_in_fit`` against cluster
   4 includes the just-renumbered rows).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .schema import Recommendation


def apply_recommendation(cluster_df: pd.DataFrame, rec: Recommendation) -> pd.DataFrame:
    """Return a copy of cluster_df with the recommendation's edits applied."""
    df = cluster_df.copy()
    # Coerce the typed columns we'll be mutating into known dtypes so the
    # in-place assignments below don't trip Pandas' upcasting machinery.
    if "robust" in df.columns:
        df["robust"] = df["robust"].astype(bool)
    if "use_in_fit" in df.columns:
        df["use_in_fit"] = df["use_in_fit"].astype(bool)
    if "clusterID" in df.columns:
        df["clusterID"] = df["clusterID"].astype(int)

    # --- 1) set_robust (derived from cluster_feedback) --------------------
    if not rec.no_robustness_changes:
        for cid_str, cf in rec.cluster_feedback.items():
            if cf.recommended_robust is None:
                continue
            try:
                cid = int(cid_str)
            except (TypeError, ValueError):
                continue
            mask = df["clusterID"] == cid
            df.loc[mask, "robust"] = bool(cf.recommended_robust)

    # --- 2) edits in order ------------------------------------------------
    for edit in rec.edits:
        op = edit.op
        scope = edit.scope
        if op == "change_clusterID":
            to_id = edit.to_id
            if to_id is None:
                continue
            if scope == "all_epochs" and edit.from_id is not None:
                mask = df["clusterID"] == int(edit.from_id)
                df.loc[mask, "clusterID"] = int(to_id)
            elif scope == "single" and edit.epoch is not None and edit.from_id is not None:
                mask = (
                    (df["clusterID"] == int(edit.from_id))
                    & np.isclose(df["epoch"].astype(float),
                                 float(edit.epoch), atol=1e-4)
                )
                df.loc[mask, "clusterID"] = int(to_id)
        elif op == "set_use_in_fit":
            value = edit.value
            if value is None:
                continue
            if scope == "epoch" and edit.epoch is not None:
                mask = np.isclose(df["epoch"].astype(float),
                                  float(edit.epoch), atol=1e-4)
                df.loc[mask, "use_in_fit"] = bool(value)
            elif scope == "single" and edit.epoch is not None and edit.clusterID is not None:
                mask = (
                    (df["clusterID"] == int(edit.clusterID))
                    & np.isclose(df["epoch"].astype(float),
                                 float(edit.epoch), atol=1e-4)
                )
                df.loc[mask, "use_in_fit"] = bool(value)
        elif op == "set_robust":
            # Stored set_robust edits (rare; usually derived from
            # cluster_feedback). Apply for completeness.
            if scope == "cluster" and edit.clusterID is not None and edit.value is not None:
                mask = df["clusterID"] == int(edit.clusterID)
                df.loc[mask, "robust"] = bool(edit.value)
    return df
