"""Positional uncertainty estimates for cluster centroids.

See ``docs/uncertainty_estimates.md`` for the full derivation and the choices
made. Summary: per (epoch, cluster) we estimate the 1-sigma uncertainty of the
centroid position **relative to the core**, from the source's Stokes-I clean
components (flux-weighted), using the unbiased weighted standard-error-of-the-
mean. The four results (sig_dx, sig_dy, sig_dist, sig_pa) are attached to the
cluster table and drawn as 1-sigma error bars on the position plots.

These are derived here, not read from the CSV. If the production pipeline later
writes them to the CSV, prefer those columns and fall back to this module.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .overlay import epoch_match_mask  # tight float-epoch matcher (atol ~1e-4)


__all__ = ["compute_position_uncertainties", "attach_position_uncertainties"]

_UNC_COLS = ("sig_dx", "sig_dy", "sig_dist", "sig_pa")


def _weighted_mean_se(vals: np.ndarray, weights: np.ndarray) -> float:
    """1-sigma standard error of the flux-weighted mean of ``vals``.

    SE^2 = sum_i w_i (v_i - vbar)^2 / [ (N-1) * sum_i w_i ]

    which is the unbiased weighted variance divided by N (see the docs).
    Returns ``np.nan`` when N < 2 or sum(w) <= 0 — no spread estimate.
    """
    v = np.asarray(vals, dtype=float)
    w = np.asarray(weights, dtype=float)
    n = v.size
    sw = float(w.sum())
    if n < 2 or not np.isfinite(sw) or sw <= 0:
        return np.nan
    vbar = float(np.sum(w * v) / sw)
    num = float(np.sum(w * (v - vbar) ** 2))
    se2 = num / ((n - 1) * sw)
    return float(np.sqrt(se2)) if se2 > 0 else 0.0


def compute_position_uncertainties(
    cluster_df: pd.DataFrame, cc_data, cc_labels,
) -> dict[tuple[float, int], tuple[float, float, float, float]]:
    """Map ``(epoch_rounded_4dp, clusterID) -> (sig_dx, sig_dy, sig_dist, sig_pa_deg)``.

    ``cc_data`` / ``cc_labels`` are the ``plotdata.npz`` arrays. CC→cluster
    membership uses ``cc_labels -> origID -> clusterID`` from ``cluster_df``'s
    fitted rows, so it follows the current (possibly rec-applied) model.
    """
    out: dict[tuple[float, int], tuple[float, float, float, float]] = {}
    if cc_data is None or cc_labels is None or cluster_df.empty:
        return out

    stokes_i = np.char.lower(cc_data["stokes"].astype(str)) == "i"
    cc_epoch = cc_data["epoch"].astype(float)
    cc_x = cc_data["x"].astype(float)
    cc_y = cc_data["y"].astype(float)
    cc_f = cc_data["flux"].astype(float)
    df_epochs = cluster_df["epoch"].to_numpy(dtype=float)

    for ev in np.unique(df_epochs):
        fit = cluster_df.loc[epoch_match_mask(df_epochs, ev)
                             & (cluster_df["clusterID"] >= 0)]
        if fit.empty:
            continue
        orig_to_cluster = dict(zip(fit["origID"].astype(int),
                                   fit["clusterID"].astype(int)))
        m = epoch_match_mask(cc_epoch, ev) & stokes_i
        if not m.any():
            continue
        lbls = cc_labels[m]
        cids = np.array([orig_to_cluster.get(int(l), int(l)) for l in lbls])
        x, y, f = cc_x[m], cc_y[m], cc_f[m]

        # Per-cluster standard error of the centroid in x and y.
        se_x: dict[int, float] = {}
        se_y: dict[int, float] = {}
        for cid in np.unique(cids):
            sel = cids == cid
            se_x[int(cid)] = _weighted_mean_se(x[sel], f[sel])
            se_y[int(cid)] = _weighted_mean_se(y[sel], f[sel])

        core_sx = se_x.get(0, np.nan)
        core_sy = se_y.get(0, np.nan)

        for _, row in fit.iterrows():
            cid = int(row["clusterID"])
            # Combine cluster + core centroid SE in quadrature (independent).
            # np.hypot propagates NaN, so an undefined core SE -> NaN bars.
            sig_dx = float(np.hypot(se_x.get(cid, np.nan), core_sx))
            sig_dy = float(np.hypot(se_y.get(cid, np.nan), core_sy))

            dx = float(row["avg_x"]) - float(row["core_x"])
            dy = float(row["avg_y"]) - float(row["core_y"])
            d2 = dx * dx + dy * dy
            d = np.sqrt(d2)
            if d > 0 and np.isfinite(sig_dx) and np.isfinite(sig_dy):
                sig_dist = float(np.sqrt(dx * dx * sig_dx * sig_dx
                                         + dy * dy * sig_dy * sig_dy) / d)
                sig_pa = float(np.sqrt(dy * dy * sig_dx * sig_dx
                                       + dx * dx * sig_dy * sig_dy) / d2
                               * (180.0 / np.pi))
            else:
                sig_dist = np.nan
                sig_pa = np.nan
            out[(round(float(ev), 4), cid)] = (sig_dx, sig_dy, sig_dist, sig_pa)

    return out


def attach_position_uncertainties(
    cluster_df: pd.DataFrame, cc_data, cc_labels,
) -> pd.DataFrame:
    """Return a copy of ``cluster_df`` with ``sig_dx/sig_dy/sig_dist/sig_pa``
    columns added (NaN where an estimate isn't possible). Never raises on bad
    input — uncertainties are optional decoration on the plots."""
    df = cluster_df.copy()
    if df.empty:
        for c in _UNC_COLS:
            df[c] = np.array([], dtype=float)
        return df
    try:
        unc = compute_position_uncertainties(df, cc_data, cc_labels)
    except Exception:
        unc = {}
    nan4 = (np.nan, np.nan, np.nan, np.nan)
    rows = [
        unc.get((round(float(e), 4), int(c)), nan4)
        for e, c in zip(df["epoch"].to_numpy(dtype=float),
                        df["clusterID"].to_numpy(dtype=int))
    ]
    arr = np.asarray(rows, dtype=float).reshape(-1, 4)
    for i, c in enumerate(_UNC_COLS):
        df[c] = arr[:, i]
    return df
