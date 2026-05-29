"""Apply a :class:`Recommendation` to a cluster DataFrame.

This is the **single source of truth** for the apply logic — used by:

* The web UI "Visualize recommendations" checkbox (in-memory transform).
* The `mojave-apply` CLI (which also captures the produced ``history``
  lines into ``history.txt``).

So the on-disk effect of pressing apply and the on-screen effect of
ticking visualize are guaranteed to match.

Application order (matters):

1. **Edits** in the order they appear in ``rec.edits``. For each edit we
   track which clusterIDs had their population change (set in
   ``affected``), so the eligibility pass at the end can reconsider them.
   For ``change_clusterID``:
     - 999-overlap rule (matches ``cluster_code.update_clusterIDs``):
       if another row at the same epoch already has the target ID, that
       other row is moved to 999.
     - If the new ID is 0 (the core), ``core_x`` / ``core_y`` for every
       row at that epoch are reset to the new core row's ``avg_x`` /
       ``avg_y`` — the npz interprets positions via simple
       ``avg - core`` subtraction.
2. ``cluster_feedback`` derived ``set_robust`` (the reviewer's explicit
   choices in the Robustness tab), unless ``no_robustness_changes`` is set.
3. **Auto-eligibility** on the affected clusters: a cluster that's no
   longer eligible (< 5 ``use_in_fit=True`` rows) is forced to
   ``robust=False``; one that's newly eligible *and* has no explicit
   ``cluster_feedback`` entry is auto-promoted to ``robust=True``. Cluster
   0 is excluded (the core is always robust by definition).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import Recommendation


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PARK_ID = 999             # collision target for the 999-overlap rule
EPOCH_ATOL = 1e-4         # tolerance for epoch-float equality
ELIGIBILITY_MIN_FIT = 5   # number of use_in_fit=True rows required


# ---------------------------------------------------------------------------
# Helpers (exported for callers that also need them — cli/apply.py uses
# `epoch_mask` for its notebook summary line construction)
# ---------------------------------------------------------------------------


def epoch_mask(df: pd.DataFrame, epoch: float) -> np.ndarray:
    """Boolean mask for rows where df['epoch'] ≈ epoch (within EPOCH_ATOL).

    NB: we deliberately avoid ``np.isclose`` here. ``np.isclose(a, b,
    atol=X)`` does NOT mean "tolerance = X" — the *effective* tolerance
    is ``atol + rtol*|b|`` and the default ``rtol=1e-5`` makes the bound
    ``1e-4 + 1e-5*2016 ≈ 0.02 yr`` at year 2016, which is wider than the
    4–7 day spacing between MOJAVE epochs (0415+379's
    2016_11_06/12/18 trio collides). A plain absolute compare is the
    safe form: identical semantics to ``np.isclose(..., atol=ATOL,
    rtol=0)`` but without the trap-door default."""
    return np.abs(df["epoch"].astype(float).to_numpy() - float(epoch)) <= EPOCH_ATOL


def _update_core_at_epoch(
    df: pd.DataFrame, em: np.ndarray, new_core_x: float, new_core_y: float,
) -> None:
    """Set ``core_x`` / ``core_y`` for *every* row at this epoch. Those
    columns power the simple-vector-subtraction positions-relative-to-core
    convention used downstream."""
    df.loc[em, "core_x"] = new_core_x
    df.loc[em, "core_y"] = new_core_y


# ---------------------------------------------------------------------------
# Edit applicators
# ---------------------------------------------------------------------------


def _apply_change_clusterID_single(
    df: pd.DataFrame, epoch: float, from_id: int, to_id: int,
    history: list[str], affected: set[int],
) -> None:
    em = epoch_mask(df, epoch)
    target_mask = em & (df["clusterID"] == from_id)
    if not target_mask.any():
        return
    target_idx = int(df.index[target_mask][0])
    if to_id != PARK_ID:
        overlap = em & (df["clusterID"] == to_id) & (df.index != target_idx)
        for idx in df.index[overlap]:
            old = int(df.at[idx, "clusterID"])
            df.at[idx, "clusterID"] = PARK_ID
            history.append(
                f"# Re-ID cluster {old} in epoch {epoch} from {old} to "
                f"{PARK_ID} due to overlap with new ID {to_id}"
            )
            affected.update({old, PARK_ID})
    df.at[target_idx, "clusterID"] = to_id
    history.append(f"# Re-ID cluster {from_id} in epoch {epoch} from {from_id} to {to_id}")
    affected.update({from_id, to_id})
    if to_id == 0:
        _update_core_at_epoch(df, em,
                              float(df.at[target_idx, "avg_x"]),
                              float(df.at[target_idx, "avg_y"]))
        history.append(
            f"# Recomputed core positions in epoch {epoch} relative to new coreID = 0"
        )


def _apply_change_clusterID_all_epochs(
    df: pd.DataFrame, from_id: int, to_id: int,
    history: list[str], affected: set[int],
) -> None:
    affected_epochs = sorted(df.loc[df["clusterID"] == from_id, "epoch"].unique())
    if not affected_epochs:
        return
    for ep in affected_epochs:
        em = epoch_mask(df, ep)
        target_mask = em & (df["clusterID"] == from_id)
        target_idx = int(df.index[target_mask][0])
        if to_id != PARK_ID:
            overlap = em & (df["clusterID"] == to_id) & (df.index != target_idx)
            for idx in df.index[overlap]:
                old = int(df.at[idx, "clusterID"])
                df.at[idx, "clusterID"] = PARK_ID
                history.append(
                    f"# Re-ID cluster {old} in epoch {ep} from {old} to "
                    f"{PARK_ID} due to overlap with new ID {to_id}"
                )
                affected.update({old, PARK_ID})
        df.at[target_idx, "clusterID"] = to_id
        if to_id == 0:
            _update_core_at_epoch(df, em,
                                  float(df.at[target_idx, "avg_x"]),
                                  float(df.at[target_idx, "avg_y"]))
    affected.update({from_id, to_id})
    history.append(f"# Re-ID cluster {from_id} all epochs from {from_id} to {to_id}")
    if to_id == 0:
        history.append("#   --> Recomputed positions relative to the core in each epoch")


def _apply_set_use_in_fit_single(
    df: pd.DataFrame, epoch: float, cluster_id: int, value: bool,
    history: list[str], affected: set[int],
) -> None:
    em = epoch_mask(df, epoch)
    mask = em & (df["clusterID"] == cluster_id)
    if not mask.any():
        return
    df.loc[mask, "use_in_fit"] = bool(value)
    history.append(f"# Set cluster {cluster_id} in epoch {epoch} use_in_fit={value}")
    affected.add(cluster_id)


def _apply_set_use_in_fit_epoch(
    df: pd.DataFrame, epoch: float, value: bool,
    history: list[str], affected: set[int],
) -> None:
    em = epoch_mask(df, epoch)
    if not em.any():
        return
    df.loc[em, "use_in_fit"] = bool(value)
    history.append(f"# Set all clusters in epoch {epoch} use_in_fit={value}")
    affected.update(df.loc[em, "clusterID"].astype(int).tolist())


def _apply_set_robust(
    df: pd.DataFrame, cluster_id: int, value: bool, history: list[str],
) -> None:
    mask = df["clusterID"] == cluster_id
    if not mask.any():
        return
    current = bool(df.loc[mask, "robust"].iloc[0])
    if current == bool(value):
        return
    df.loc[mask, "robust"] = bool(value)
    history.append(f"# Set cluster {cluster_id} as robust={bool(value)}")


# ---------------------------------------------------------------------------
# Auto-eligibility pass
# ---------------------------------------------------------------------------


def _apply_auto_eligibility(
    df: pd.DataFrame, affected: set[int],
    explicit_cf_cids: set[int],
    min_fit: int = ELIGIBILITY_MIN_FIT,
) -> list[str]:
    """For each cluster whose population changed, recompute the
    ``robust`` flag against eligibility:

    * ineligible (< min_fit use_in_fit=True rows) → forced
      ``robust=False`` (no exceptions; ineligibility is hard).
    * eligible *and* not explicitly set via cluster_feedback → auto-
      promoted to ``robust=True`` if currently False.

    Cluster 0 (the core) is excluded — it's always robust by definition.
    The park ID 999 and the unassigned −1 are also excluded.
    """
    history: list[str] = []
    skip = {-1, 0, PARK_ID}
    for cid in sorted(affected):
        if int(cid) in skip:
            continue
        mask = df["clusterID"] == int(cid)
        if not mask.any():
            continue                # cluster has no rows left (fully renamed away)
        n_fit = int(df.loc[mask, "use_in_fit"].astype(bool).sum())
        currently_robust = bool(df.loc[mask, "robust"].iloc[0])
        eligible = n_fit >= min_fit
        if not eligible:
            if currently_robust:
                df.loc[mask, "robust"] = False
                history.append(
                    f"# Auto: cluster {cid} demoted to non-robust "
                    f"({n_fit} use_in_fit rows < {min_fit})"
                )
        else:
            if not currently_robust and int(cid) not in explicit_cf_cids:
                df.loc[mask, "robust"] = True
                history.append(
                    f"# Auto: cluster {cid} promoted to robust "
                    f"(newly eligible with {n_fit} use_in_fit rows)"
                )
    return history


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_recommendation_with_history(
    cluster_df: pd.DataFrame, rec: Recommendation,
) -> tuple[pd.DataFrame, list[str]]:
    """Apply ``rec`` to a copy of ``cluster_df`` and return both the
    modified DataFrame and the produced history lines (one per edit /
    auto change, in the format ``cluster_code.update_clusterIDs`` writes
    so ``history.txt`` stays readable)."""
    df = cluster_df.copy()
    df["robust"] = df["robust"].astype(bool)
    df["use_in_fit"] = df["use_in_fit"].astype(bool)
    df["clusterID"] = df["clusterID"].astype(int)

    history: list[str] = []
    affected: set[int] = set()

    # 1) Edits in order
    for edit in rec.edits:
        op, scope = edit.op, edit.scope
        if op == "change_clusterID":
            if scope == "single" and edit.epoch is not None \
                    and edit.from_id is not None and edit.to_id is not None:
                _apply_change_clusterID_single(
                    df, float(edit.epoch), int(edit.from_id), int(edit.to_id),
                    history, affected,
                )
            elif scope == "all_epochs" and edit.from_id is not None and edit.to_id is not None:
                _apply_change_clusterID_all_epochs(
                    df, int(edit.from_id), int(edit.to_id), history, affected,
                )
        elif op == "set_use_in_fit":
            if scope == "single" and edit.epoch is not None \
                    and edit.clusterID is not None and edit.value is not None:
                _apply_set_use_in_fit_single(
                    df, float(edit.epoch), int(edit.clusterID), bool(edit.value),
                    history, affected,
                )
            elif scope == "epoch" and edit.epoch is not None and edit.value is not None:
                _apply_set_use_in_fit_epoch(
                    df, float(edit.epoch), bool(edit.value), history, affected,
                )
        elif op == "set_robust" and scope == "cluster" \
                and edit.clusterID is not None and edit.value is not None:
            _apply_set_robust(df, int(edit.clusterID), bool(edit.value), history)

    # 2) cluster_feedback overrides (unless the reviewer signed off on no
    #    robustness changes).
    explicit_cf_cids: set[int] = set()
    if not rec.no_robustness_changes:
        for cid_str, cf in rec.cluster_feedback.items():
            if cf.recommended_robust is None:
                continue
            try:
                cid = int(cid_str)
            except (TypeError, ValueError):
                continue
            explicit_cf_cids.add(cid)
            _apply_set_robust(df, cid, bool(cf.recommended_robust), history)

    # 3) Auto-eligibility constraint on the clusters this round touched.
    history.extend(_apply_auto_eligibility(df, affected, explicit_cf_cids))

    return df, history


def apply_recommendation(cluster_df: pd.DataFrame, rec: Recommendation) -> pd.DataFrame:
    """Back-compat wrapper for callers that don't care about the history
    log (the web UI's visualize path). Always prefer the with-history
    variant for new code so future tweaks land everywhere."""
    df, _ = apply_recommendation_with_history(cluster_df, rec)
    return df
