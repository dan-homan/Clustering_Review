"""Overlay colouring uses ONE robust flag per cluster, not the per-epoch flag.

Regression for the "cluster colour flickers across epochs" bug: ``robust`` is a
per-cluster property, but the CSV can carry a flag that varies across a
cluster's epochs (e.g. cluster 3 in 0003-066 is flagged non-robust at three
epochs only). The summary plot collapses it to one value per cluster; the
overlay must agree, or the CC scatter + FWHM ellipse flip between coloured
(robust) and slategray (non-robust) as you scrub epochs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from mojave_review.plots.overlay import (
    build_overlay_figure, robust_by_cluster, EpochAxes,
)

_SLATEGRAY = "#708090"   # non-robust styling (see plots.summary._cluster_style)


_CC_DTYPE = np.dtype([
    ("epoch", "f8"), ("x", "f8"), ("y", "f8"), ("stokes", "U1"),
    ("flux", "f8"), ("sizex", "f8"), ("sizey", "f8"),
    ("group", "f8"), ("clusterID", "i4"),
])


def _cluster_df():
    # Cluster 3 (origID 2): robust=True at its first epoch (2000), but the CSV
    # flags it non-robust at 2001. Per-cluster rule -> robust (the 2000 value).
    rows = []
    for ep, rob3 in [(2000.0, True), (2001.0, False)]:
        rows.append(dict(clusterID=0, origID=0, epoch=ep, avg_x=0.0, avg_y=0.0,
                         core_x=0.0, core_y=0.0, fwhm_maj=0.4, fwhm_min=0.4,
                         cpa=0.0, robust=True, sizeMaj=0.4, bmaj=0.5))
        rows.append(dict(clusterID=3, origID=2, epoch=ep, avg_x=1.0, avg_y=0.0,
                         core_x=0.0, core_y=0.0, fwhm_maj=0.4, fwhm_min=0.4,
                         cpa=0.0, robust=rob3, sizeMaj=0.4, bmaj=0.5))
    return pd.DataFrame(rows)


def _cc_at(epoch):
    cc = np.array([(epoch, 1.0, 0.0, "i", 1.0, 0.1, 0.1, 0.0, 2)],
                  dtype=_CC_DTYPE)
    return cc, cc["clusterID"].copy()


def _epoch_axes():
    return EpochAxes(image=np.zeros((2, 2)), x_mas=np.array([2.0, -2.0]),
                     y_mas=np.array([-2.0, 2.0]), pix_to_mas=0.1,
                     crpix1=1.0, crpix2=1.0)


def test_robust_by_cluster_collapses_per_epoch_flag():
    m = robust_by_cluster(_cluster_df())
    assert m[3] is True          # first-epoch (2000) value wins
    assert m[0] is True


def test_overlay_uses_consistent_robust_at_nonrobust_epoch():
    # Render epoch 2001, where cluster 3's per-epoch flag is False. With the
    # fix the CC + ellipse are still drawn robust (magenta), matching 2000.
    cc_data, cc_labels = _cc_at(2001.0)
    fig = build_overlay_figure(
        epoch_axes=_epoch_axes(), cluster_df=_cluster_df(),
        cc_data=cc_data, cc_labels=cc_labels,
        epoch_val=2001.0, epoch_name="2001_01_01",
        inoise=1e-3, bmaj=0.5, bmin=0.5, bpa=0.0,
    )
    cc_trace = next(t for t in fig.data if t.name == "cluster 3")
    assert cc_trace.marker.color == "magenta"          # not slategray
    # FWHM ellipse for cluster 3 is also robust-coloured.
    ell = next(t for t in fig.data
               if "cluster 3 FWHM" in (t.hovertemplate or ""))
    assert ell.line.color == "magenta"
    assert _SLATEGRAY not in (cc_trace.marker.color, ell.line.color)
