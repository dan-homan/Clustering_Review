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


def _beam_kernel(bmaj: float, bmin: float, bpa: float, pix_to_mas: float) -> np.ndarray:
    """Peak-normalized restoring-beam Gaussian kernel, rotated to the
    astronomical PA convention used by ``_ellipse_xy``: positive PA rotates
    the major axis display-CCW (north through east) — equivalently data-CW
    once you account for the reversed x display.

    The CLEAN restoring beam is *peak*-normalized — that's what gives the
    "Jy/beam" intensity unit. A clean component of flux F at pixel p shows up
    in the restored image as F × beam(0) = F at pixel p, so the kernel peak
    must be 1 (the bare ``exp(...)`` already satisfies that — do NOT divide
    by sum, which would area-normalize and shrink the image by ~beam_area).
    """
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
    return np.exp(-0.5 * ((xr / sigma_min_pix) ** 2
                          + (yr / sigma_maj_pix) ** 2))


def _render_image(
    cc_x: np.ndarray, cc_y: np.ndarray, flux: np.ndarray,
    *, cluster_df, pix_to_mas: float, bmaj: float, bmin: float, bpa: float,
    flux_scale: float = 1.0,
) -> EpochAxes:
    """Grid core-relative CCs, optionally rescale, and convolve with the beam.

    ``cc_x`` / ``cc_y`` / ``flux`` are already core-relative (one entry per
    clean component). ``flux_scale`` multiplies the gridded flux before
    convolution — used by the stacked builder to divide by the epoch count.

    Returns an ``EpochAxes`` whose ``image`` axes are core-relative —
    exactly what ``_load_fits_image`` returns after its core-shift — so
    ``build_overlay_figure`` can consume it unchanged. ``x_mas`` is
    ascending; the overlay's ``update_xaxes`` reverses the displayed range.
    """
    # Field of view: union of cluster footprint (so the initial zoom box is
    # fully inside the image) and the CC bounding box, plus a fixed padding
    # margin. The cluster footprint is core-relative already.
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

    if flux_scale != 1.0:
        img *= flux_scale

    img = fftconvolve(img, _beam_kernel(bmaj, bmin, bpa, pix_to_mas), mode="same")

    return EpochAxes(
        image=img,
        x_mas=x_mas,
        y_mas=y_mas,
        pix_to_mas=float(pix_to_mas),
        crpix1=0.0,
        crpix2=0.0,
    )


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
    return _render_image(
        cc_x, cc_y, flux, cluster_df=cluster_df, pix_to_mas=pix_to_mas,
        bmaj=bmaj, bmin=bmin, bpa=bpa,
    )


def synthesize_stacked_stokes_i(
    *,
    cluster_df,
    cc_data: np.ndarray,
    epoch_info: np.ndarray,
) -> tuple[EpochAxes, tuple[float, float, float]]:
    """Stack every epoch's Stokes-I clean components into one average image.

    Each epoch's CCs are shifted to that epoch's fitted core (so all epochs
    share the core-at-(0,0) frame), accumulated onto a single common grid,
    divided by the epoch count (a per-epoch *average*), then convolved once
    with the **median beam** across epochs. Because convolution is linear,
    this equals the average of the per-epoch restored images had they all
    been restored with the median beam.

    The common pixel scale is the median ``pix_to_mas`` across epochs.

    Returns ``(epoch_axes, (bmaj, bmin, bpa))`` where the beam tuple is the
    median beam — the caller uses it for both the contour image (already
    baked in) and the drawn beam ellipse.
    """
    n_epochs = len(epoch_info)
    # Median beam + pixel scale across epochs. Beam PAs for a single source
    # are tightly clustered across epochs, so a plain median is adequate here
    # (no angle-wrap handling needed for review-grade stacking).
    bmaj = float(np.median(epoch_info["bmaj"]))
    bmin = float(np.median(epoch_info["bmin"]))
    bpa = float(np.median(epoch_info["bpa"]))
    pix_to_mas = float(np.median(epoch_info["pix_to_mas"]))

    stokes_mask = np.char.lower(cc_data["stokes"].astype(str)) == "i"
    epochs_x = cluster_df["epoch"].to_numpy()
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    fs: list[np.ndarray] = []
    for info in epoch_info:
        ev = float(info["epoch_val"])
        fitted = cluster_df.loc[epoch_match_mask(epochs_x, ev)
                                & (cluster_df["clusterID"] >= 0)]
        core_x = float(fitted["core_x"].iloc[0]) if len(fitted) else 0.0
        core_y = float(fitted["core_y"].iloc[0]) if len(fitted) else 0.0
        sub = cc_data[epoch_match_mask(cc_data["epoch"], ev) & stokes_mask]
        xs.append(sub["x"].astype(float) - core_x)
        ys.append(sub["y"].astype(float) - core_y)
        fs.append(sub["flux"].astype(float))

    cc_x = np.concatenate(xs) if xs else np.array([], dtype=float)
    cc_y = np.concatenate(ys) if ys else np.array([], dtype=float)
    flux = np.concatenate(fs) if fs else np.array([], dtype=float)

    axes = _render_image(
        cc_x, cc_y, flux, cluster_df=cluster_df, pix_to_mas=pix_to_mas,
        bmaj=bmaj, bmin=bmin, bpa=bpa,
        flux_scale=1.0 / max(n_epochs, 1),
    )
    return axes, (bmaj, bmin, bpa)
