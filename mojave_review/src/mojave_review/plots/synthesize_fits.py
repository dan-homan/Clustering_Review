"""Synthesize a Stokes I image from clean components.

The MOJAVE FITS image for an epoch is a CLEAN model convolved with the
restoring beam plus a residual noise sea. Everything except the noise sea
is recoverable from ``plotdata.npz`` — the clean components carry the
delta-function model, and each epoch's bmaj/bmin/bpa give the restoring
beam.

When the user picks "Synthesize image from CCs" in the header, this module
takes the place of ``_load_fits_image`` in ``overlay.py``. The result is
an ``EpochAxes`` with core-relative axes (so the rest of the overlay code
path doesn't change at all).

Trade-offs vs the FITS path:
  + works fully offline; no NRAO fetch, no disk cache
  + roughly matches contour shape at the levels that matter for cluster
    review (clean-component centroids + beam convolution dominate)
  + same compute cost on every render (~50–100 ms per epoch on a laptop)
  - no residual noise sea, so contours look a touch cleaner than the FITS
  - relies on the CC stokes='i' subset being complete for that epoch
"""

from __future__ import annotations

import numpy as np
from scipy.signal import fftconvolve

from ._extent import compute_source_extent
from .overlay import EpochAxes, epoch_match_mask


# Field-of-view padding (mas) added on every side of the union of the
# cluster footprint and the per-epoch CC bounding box. Keeps the contour
# image from clipping at the edges of where the user might zoom.
_FOV_PAD_MAS = 8.0

# Minimum image dimensions (pixels). Anything below this is dominated by
# the beam kernel size and the FFT convolution gets numerically noisy.
_MIN_PIX = 64


def synthesize_stokes_i(
    *,
    cluster_df,
    cc_data: np.ndarray,
    epoch_val: float,
    core_x: float,
    core_y: float,
    pix_to_mas: float,
    bmaj: float,
    bmin: float,
    bpa: float,
) -> EpochAxes:
    """Place this epoch's Stokes-I CCs on a pixel grid and convolve with the
    restoring beam.

    Returns an ``EpochAxes`` whose ``image`` axes are already
    core-relative — exactly what ``_load_fits_image`` returns after its
    core-shift — so ``build_overlay_figure`` can consume it unchanged.

    Coordinates: ``x_mas`` is ascending. The overlay's ``update_xaxes``
    reverses the displayed range, so positive x still appears on the left
    in the panel. The kernel rotation matches ``_ellipse_xy`` 's
    astronomical PA convention.
    """
    # Per-epoch Stokes I clean components (mas, absolute → core-relative).
    # Use the tight epoch tolerance to avoid pulling CCs from neighbouring
    # epochs spaced a few days apart (see overlay.EPOCH_MATCH_ATOL).
    epoch_mask = epoch_match_mask(cc_data["epoch"], epoch_val)
    stokes_mask = np.char.lower(cc_data["stokes"].astype(str)) == "i"
    sub = cc_data[epoch_mask & stokes_mask]
    cc_x = sub["x"].astype(float) - core_x
    cc_y = sub["y"].astype(float) - core_y
    flux = sub["flux"].astype(float)

    # Field of view: union of cluster footprint (so the initial zoom box is
    # fully inside the image) and the per-epoch CC bounding box, plus a fixed
    # padding margin on each side. The cluster footprint is core-relative
    # already (compute_source_extent works in the same frame).
    extent = compute_source_extent(cluster_df)
    if extent is not None:
        (x_lo, x_hi), (y_lo, y_hi) = extent
    else:
        x_lo, x_hi = -25.0, 25.0
        y_lo, y_hi = -25.0, 25.0
    if len(cc_x):
        x_lo = min(x_lo, float(cc_x.min()))
        x_hi = max(x_hi, float(cc_x.max()))
        y_lo = min(y_lo, float(cc_y.min()))
        y_hi = max(y_hi, float(cc_y.max()))
    x_lo -= _FOV_PAD_MAS
    x_hi += _FOV_PAD_MAS
    y_lo -= _FOV_PAD_MAS
    y_hi += _FOV_PAD_MAS

    n_x = max(_MIN_PIX, int(np.ceil((x_hi - x_lo) / pix_to_mas)))
    n_y = max(_MIN_PIX, int(np.ceil((y_hi - y_lo) / pix_to_mas)))
    # Snap the FOV so the pixel grid is exactly pix_to_mas per cell.
    x_lo = x_hi - (n_x - 1) * pix_to_mas
    y_hi = y_lo + (n_y - 1) * pix_to_mas

    x_mas = np.linspace(x_lo, x_hi, n_x)   # ascending
    y_mas = np.linspace(y_lo, y_hi, n_y)   # ascending

    # Drop CCs onto their nearest pixel using vectorised np.add.at, which
    # accumulates correctly when multiple components share a pixel.
    img = np.zeros((n_y, n_x), dtype=np.float64)
    if len(cc_x):
        ix = np.rint((cc_x - x_lo) / pix_to_mas).astype(int)
        iy = np.rint((cc_y - y_lo) / pix_to_mas).astype(int)
        inside = (ix >= 0) & (ix < n_x) & (iy >= 0) & (iy < n_y)
        np.add.at(img, (iy[inside], ix[inside]), flux[inside])

    # Restoring-beam kernel. Centered Gaussian, rotated to match the
    # astronomical PA convention used by _ellipse_xy: positive PA rotates
    # the major axis display-CCW (north through east) — equivalently
    # data-CW once you account for the reversed x display.
    sigma_maj_pix = max(bmaj / 2.3548 / pix_to_mas, 1e-6)
    sigma_min_pix = max(bmin / 2.3548 / pix_to_mas, 1e-6)
    k_half = int(np.ceil(3.5 * max(sigma_maj_pix, sigma_min_pix)))
    yy, xx = np.mgrid[-k_half : k_half + 1, -k_half : k_half + 1]
    cos_pa = np.cos(np.deg2rad(bpa))
    sin_pa = np.sin(np.deg2rad(bpa))
    # Inverse of the (xr, yr) -> (x, y) rotation in _ellipse_xy:
    #   xr =  x*cos_pa - y*sin_pa     (minor-axis direction)
    #   yr =  x*sin_pa + y*cos_pa     (major-axis direction)
    xr = xx * cos_pa - yy * sin_pa
    yr = xx * sin_pa + yy * cos_pa
    # The CLEAN restoring beam is *peak*-normalized — that's what gives the
    # "Jy/beam" intensity unit. A clean component of flux F at pixel p shows
    # up in the restored image as F × beam(0) = F at pixel p. So we want the
    # kernel peak = 1 (the bare exp(...) already satisfies that — do NOT
    # divide by sum, which would area-normalize and shrink the image by a
    # factor of ~beam_area_in_pix).
    kernel = np.exp(-0.5 * ((xr / sigma_min_pix) ** 2
                            + (yr / sigma_maj_pix) ** 2))

    img = fftconvolve(img, kernel, mode="same")

    return EpochAxes(
        image=img,
        x_mas=x_mas,
        y_mas=y_mas,
        pix_to_mas=float(pix_to_mas),
        crpix1=0.0,
        crpix2=0.0,
    )
