"""Unit test for plots.uncertainty against a hand-computed example.

Run directly (`python3 tests/test_uncertainty.py` from the package root with
`src` on the path) or via pytest.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from mojave_review.plots.uncertainty import (
    _weighted_mean_se, compute_position_uncertainties,
)


def _make_inputs():
    # One epoch. Core (cid 0) and one cluster (cid 1), 3 Stokes-I CCs each,
    # equal fluxes (so weights are uniform and the math is easy to verify).
    #   core   x = [ 0.0, 0.1, -0.1], y = [0,0,0]
    #   clu 1  x = [ 5.0, 5.2,  4.8], y = [0.1, -0.1, 0.0]
    ev = 2000.0
    cc_dtype = np.dtype([("epoch", "f8"), ("x", "f8"), ("y", "f8"),
                         ("stokes", "U1"), ("flux", "f8")])
    cc = np.array([
        (ev,  0.0,  0.0, "i", 1.0),
        (ev,  0.1,  0.0, "i", 1.0),
        (ev, -0.1,  0.0, "i", 1.0),
        (ev,  5.0,  0.1, "i", 1.0),
        (ev,  5.2, -0.1, "i", 1.0),
        (ev,  4.8,  0.0, "i", 1.0),
    ], dtype=cc_dtype)
    cc_labels = np.array([0, 0, 0, 1, 1, 1], dtype=np.int32)
    df = pd.DataFrame([
        dict(epoch=ev, clusterID=0, origID=0, avg_x=0.0, avg_y=0.0,
             core_x=0.0, core_y=0.0),
        dict(epoch=ev, clusterID=1, origID=1, avg_x=5.0, avg_y=0.0,
             core_x=0.0, core_y=0.0),
    ])
    return df, cc, cc_labels


def test_weighted_mean_se_basic():
    # x=[5,5.2,4.8], uniform weights: SE^2 = sum(x-xbar)^2 / ((N-1)*sumw)
    se = _weighted_mean_se(np.array([5.0, 5.2, 4.8]), np.ones(3))
    assert abs(se - np.sqrt(0.08 / (2 * 3))) < 1e-12
    # N<2 -> NaN
    assert np.isnan(_weighted_mean_se(np.array([1.0]), np.array([1.0])))
    # zero total weight -> NaN
    assert np.isnan(_weighted_mean_se(np.array([1.0, 2.0]), np.zeros(2)))


def test_cluster1_uncertainties():
    df, cc, cc_labels = _make_inputs()
    unc = compute_position_uncertainties(df, cc, cc_labels)
    sig_dx, sig_dy, sig_dist, sig_pa = unc[(2000.0, 1)]

    # Hand-computed (see docs/uncertainty_estimates.md):
    se_clu_x = np.sqrt(0.08 / 6)       # 0.115470
    se_core_x = np.sqrt(0.02 / 6)      # 0.057735
    se_clu_y = np.sqrt(0.02 / 6)       # 0.057735 (core y SE = 0)
    exp_dx = np.hypot(se_clu_x, se_core_x)   # 0.129099
    exp_dy = se_clu_y                         # 0.057735
    dx, dy, d = 5.0, 0.0, 5.0
    exp_dist = np.sqrt(dx**2 * exp_dx**2 + dy**2 * exp_dy**2) / d   # 0.129099
    exp_pa = np.sqrt(dy**2 * exp_dx**2 + dx**2 * exp_dy**2) / d**2 * 180/np.pi

    assert abs(sig_dx - exp_dx) < 1e-9, (sig_dx, exp_dx)
    assert abs(sig_dy - exp_dy) < 1e-9, (sig_dy, exp_dy)
    assert abs(sig_dist - exp_dist) < 1e-9, (sig_dist, exp_dist)
    assert abs(sig_pa - exp_pa) < 1e-9, (sig_pa, exp_pa)


def test_core_row_has_nan_distance():
    # cluster 0 sits at the core (d=0) -> dist/pa undefined.
    df, cc, cc_labels = _make_inputs()
    unc = compute_position_uncertainties(df, cc, cc_labels)
    _, _, sig_dist0, sig_pa0 = unc[(2000.0, 0)]
    assert np.isnan(sig_dist0) and np.isnan(sig_pa0)


if __name__ == "__main__":
    test_weighted_mean_se_basic()
    test_cluster1_uncertainties()
    test_core_row_has_nan_distance()
    print("PASS: all uncertainty unit checks")
