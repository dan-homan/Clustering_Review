"""The "Hide uncertain motions" gate (`_MotionFit.significant`).

A motion is kept when it is either a >=3sigma detection OR a *confident*
non-detection (`speed + 3*speed_err < SLOW_SPEED_MAS_YR`). The second clause
exists because a stationary cluster has a low SNR by construction; it must NOT
be a bare `speed < 0.05` cut, which admits marginal detections such as
0.042 +/- 0.019 mas/yr (2.2sigma) and left the checkbox with nothing to remove
on slow sources (the 2022+171u report, 2026-09-18).
"""

from __future__ import annotations

import numpy as np

from mojave_review.plots.summary import (
    SLOW_SPEED_MAS_YR, _Slice, _motion_fit,
)


def _slice(speed: float, scatter: float, *, n: int = 9, cid: int = 1,
           robust: bool = True) -> _Slice:
    """A cluster moving along +x at ``speed`` mas/yr with Gaussian scatter.

    ``scatter`` (mas) sets the slope uncertainty: bigger scatter over the same
    time baseline -> a less significant motion.
    """
    t = np.arange(n, dtype=float) + 2000.0
    rng = np.random.default_rng(12345)
    x = speed * (t - t[0]) + rng.normal(0.0, scatter, n)
    y = rng.normal(0.0, scatter, n)
    nan = np.full(n, np.nan)
    return _Slice(
        cid=cid, robust=robust, time=t,
        ep_name=np.array([f"{int(v)}_01_01" for v in t]),
        xpos=x, ypos=y, dist=np.hypot(x, y), pa=nan, size=nan, flux=nan,
        pflux=nan, evpa=nan, tb_obs=nan,
        use_in_fit=np.ones(n, dtype=bool), selected=np.zeros(n, dtype=bool),
        sig_dx=nan, sig_dy=nan, sig_dist=nan, sig_pa=nan,
    )


def test_fast_well_constrained_motion_is_significant():
    mf = _motion_fit(_slice(0.5, 0.02))
    assert mf is not None
    assert mf.speed / mf.speed_err >= 3.0
    assert mf.significant


def test_confident_non_detection_is_significant():
    # Essentially stationary and tightly constrained: low SNR, but the motion
    # is confidently below the slow-speed threshold, so it is kept.
    mf = _motion_fit(_slice(0.0, 0.005))
    assert mf is not None
    assert mf.speed / mf.speed_err < 3.0
    assert mf.speed + 3.0 * mf.speed_err < SLOW_SPEED_MAS_YR
    assert mf.significant


def test_marginal_slow_detection_is_not_significant():
    # The 2022+171u case (its cluster 1 fits 0.042 +/- 0.019 mas/yr): slow in
    # its best-fit value, but the 3-sigma band reaches past the slow-speed
    # threshold, so it is an uncertain motion. cluster_code.py's
    # `speed < 0.05 and sqrt(var_vx+var_vy) < 0.05` kept it.
    mf = _motion_fit(_slice(0.03, 0.15))
    assert mf is not None
    assert mf.speed < SLOW_SPEED_MAS_YR          # the old cut would pass it
    assert mf.speed_err < SLOW_SPEED_MAS_YR      # ... on both halves
    assert mf.speed / mf.speed_err < 3.0
    assert not mf.significant


def test_fast_but_unconstrained_motion_is_not_significant():
    mf = _motion_fit(_slice(0.3, 1.5))
    assert mf is not None
    assert mf.speed / mf.speed_err < 3.0
    assert not mf.significant


def test_non_robust_and_short_series_get_no_fit():
    assert _motion_fit(_slice(0.5, 0.02, robust=False)) is None
    assert _motion_fit(_slice(0.5, 0.02, n=4)) is None
    assert _motion_fit(_slice(0.5, 0.02, cid=-1)) is None
    assert _motion_fit(_slice(0.5, 0.02, cid=1000)) is None
