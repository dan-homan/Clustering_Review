"""Per-epoch FITS overlay figure: contour image + cluster overlay.

Coordinates are in milliarcseconds (mas) relative to the *fitted core*
position for that epoch (so the core sits at (0, 0)). The x-axis is
reversed so positive x is to the LEFT (astronomical convention), and the
two axes share data-per-pixel scale.

A reasonable Plotly substitute for matplotlib's `contour()` is harder to
get than it sounds — `go.Contour` has no `levels=[...]` arg, only
start/end/size. We work around that by transforming z into log2-units of a
chosen contour base (`cbase = 3.5 * inoise`), then asking for unit-step
contours.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from ..data.fits_cache import FitsRef, fetch_fits, open_fits
from ._extent import compute_source_extent
from .summary import _cluster_style  # private helper, but shared inside package


ImageSource = Literal["fits", "synthesize"]


# CSS color name -> RGB triplet, for building rgba() fill colors. Covers
# every color _cluster_style() can return.
_NAMED_RGB = {
    "blue": (0, 0, 255),
    "green": (0, 128, 0),
    "red": (255, 0, 0),
    "magenta": (255, 0, 255),
    "goldenrod": (218, 165, 32),
    "gray": (128, 128, 128),
    "cyan": (0, 255, 255),
    "slategray": (112, 128, 144),
    "black": (0, 0, 0),
}


def _rgba(named_color: str, alpha: float) -> str:
    r, g, b = _NAMED_RGB.get(named_color, (128, 128, 128))
    return f"rgba({r},{g},{b},{alpha})"


# 3-sigma inclusion diameter, expressed as a multiple of FWHM:
#   sigma = FWHM / (2 sqrt(2 ln 2)) = FWHM / 2.3548
#   3-sigma diameter = 6 sigma = 6 * FWHM / 2.3548 = 2.548 * FWHM
SIGMA3_OVER_FWHM = 2.548


# Tight tolerance for matching the floating-point ``epoch`` column to
# ``epoch_info["epoch_val"]``. The default ``np.isclose`` (rtol=1e-5,
# atol=1e-8) is too generous around year 2016 (tolerance ≈ 0.02 yr ≈ 7
# days) and silently merges epochs spaced 4–7 days apart — e.g. 0415+379's
# 2016_11_06 / 2016_11_12 / 2016_11_18 cluster all matched each other.
# ``EPOCH_MATCH_ATOL`` is ~52 minutes — well above any CSV ⇄ NPZ float
# round-trip noise (which is typically 0 in the present datasets), well
# below the shortest legitimate inter-epoch spacing (~4 days). Use
# ``epoch_match_mask`` everywhere instead of ``np.isclose`` for this.
EPOCH_MATCH_ATOL = 1e-4


def epoch_match_mask(epoch_array: np.ndarray, epoch_val: float) -> np.ndarray:
    """Boolean mask selecting rows whose float epoch == ``epoch_val`` to
    within ``EPOCH_MATCH_ATOL``."""
    return np.abs(np.asarray(epoch_array, dtype=float) - float(epoch_val)) <= EPOCH_MATCH_ATOL


@dataclass
class EpochAxes:
    """Convenience bundle for one FITS image after coord conversion."""
    image: np.ndarray         # 2D, Stokes I
    x_mas: np.ndarray         # 1D, per-column mas position (descending if CDELT1<0)
    y_mas: np.ndarray         # 1D, per-row mas position
    pix_to_mas: float         # |CDELT1| in mas
    crpix1: float
    crpix2: float


def _load_fits_image(path: Path, core_x: float, core_y: float) -> EpochAxes:
    """Open the FITS, squeeze to 2D, compute mas coords relative to the core."""
    with open_fits(path) as hdul:
        data = np.asarray(hdul[0].data)
        hdr = hdul[0].header
    # MOJAVE FITS are typically (1, 1, NAXIS2, NAXIS1) — strip leading axes.
    while data.ndim > 2:
        data = data[0]
    crpix1 = float(hdr["CRPIX1"])
    crpix2 = float(hdr["CRPIX2"])
    cdelt1_mas = float(hdr["CDELT1"]) * 3_600_000.0   # deg/pix -> mas/pix (signed)
    cdelt2_mas = float(hdr["CDELT2"]) * 3_600_000.0
    n_y, n_x = data.shape
    # FITS pixel (1..N), Python index (0..N-1); reference pixel sits at CRPIX.
    x_mas = (np.arange(n_x) + 1 - crpix1) * cdelt1_mas - core_x
    y_mas = (np.arange(n_y) + 1 - crpix2) * cdelt2_mas - core_y
    return EpochAxes(
        image=data, x_mas=x_mas, y_mas=y_mas,
        pix_to_mas=abs(cdelt1_mas), crpix1=crpix1, crpix2=crpix2,
    )


def _ellipse_xy(cx: float, cy: float, major: float, minor: float,
                pa_deg: float, n: int = 60) -> tuple[np.ndarray, np.ndarray]:
    """Return (x, y) arrays tracing a FWHM-sized ellipse at (cx, cy).

    ``pa_deg`` is the astronomical position angle: 0° puts the major axis
    along +y (north) and positive PA rotates **counter-clockwise from
    north** in the displayed plot (north through east). Because the
    overlay panel reverses the x-axis (+x to the left = east in the sky
    convention), display-CCW corresponds to mathematical-CW in the
    underlying data coordinates — hence the sign flip on the sin terms
    compared with a textbook math-CCW rotation.
    """
    theta = np.linspace(0, 2 * np.pi, n)
    a = major / 2.0
    b = minor / 2.0
    cos_pa = np.cos(np.deg2rad(pa_deg))
    sin_pa = np.sin(np.deg2rad(pa_deg))
    xr = b * np.cos(theta)        # minor along x
    yr = a * np.sin(theta)        # major along y
    # math-CW in data coords <=> display-CCW with reversed x-axis
    x = cx + xr * cos_pa + yr * sin_pa
    y = cy - xr * sin_pa + yr * cos_pa
    return x, y


def build_overlay_figure(
    *,
    epoch_axes: EpochAxes,
    cluster_df: pd.DataFrame,
    cc_data: np.ndarray,
    cc_labels: np.ndarray,
    epoch_val: float,
    epoch_name: str,
    inoise: float,
    bmaj: float,
    bmin: float,
    bpa: float,
    cbase_factor: float = 3.5,
    n_levels: int = 10,
    show_3sigma: bool = False,
    image_source_label: str = "",
    uirevision: str = "overlay",
) -> go.Figure:
    """Build the FITS-overlay figure for one epoch.

    ``epoch_axes`` is the pre-built Stokes I image + mas-coord axes. The
    caller picks how it was produced (loaded from a CLEAN FITS via
    ``_load_fits_image`` or synthesized from clean components via
    ``plots.synthesize_fits.synthesize_stokes_i``) — this function just
    contours it.

    ``image_source_label`` is appended to the figure title when non-empty
    so the reviewer can tell at a glance whether they're looking at a
    real FITS or a synthesized image.
    """
    # Per-epoch slice of the cluster table. Use a tight tolerance — the
    # default np.isclose merges neighbouring epochs spaced a few days apart
    # (see EPOCH_MATCH_ATOL docstring).
    epoch_mask = epoch_match_mask(cluster_df["epoch"].to_numpy(), epoch_val)
    sub = cluster_df.loc[epoch_mask].copy()
    fit_mask = sub["clusterID"] >= 0
    fitted = sub.loc[fit_mask]

    core_x = float(fitted["core_x"].iloc[0]) if len(fitted) else 0.0
    core_y = float(fitted["core_y"].iloc[0]) if len(fitted) else 0.0

    ax = epoch_axes
    cbase = max(cbase_factor * float(inoise), 1e-9)

    # The CLEAN image is already convolved with the restoring beam; do NOT
    # apply additional smoothing here — it would blur real structure.
    log_z = np.log2(np.maximum(ax.image, cbase) / cbase)

    fig = go.Figure()
    fig.add_trace(
        go.Contour(
            z=log_z, x=ax.x_mas, y=ax.y_mas,
            contours=dict(coloring="lines", start=0, end=n_levels, size=1,
                          showlabels=False),
            line=dict(width=1, color="#444", smoothing=1.0),
            colorscale=[[0, "#444"], [1, "#444"]],
            showscale=False, hoverinfo="skip",
        )
    )

    # Clean components for this epoch.
    cc_mask = epoch_match_mask(cc_data["epoch"], epoch_val)
    cc_x = cc_data["x"][cc_mask].astype(float) - core_x
    cc_y = cc_data["y"][cc_mask].astype(float) - core_y

    if cc_labels is None:
        # Backup whose underlying fit doesn't match current's NPZ — the
        # cc → cluster mapping isn't trustworthy. Render CCs as a single
        # neutral-grey trace so the reviewer can still see where the
        # bright clean components sit without being misled by colour.
        # The cluster ellipses + labels (from this backup's own CSV)
        # still tell the cluster story.
        if int(cc_mask.sum()) > 0:
            fig.add_trace(
                go.Scattergl(
                    x=cc_x, y=cc_y, mode="markers",
                    marker=dict(color="#888", size=3, opacity=0.5,
                                line=dict(width=0)),
                    name="clean components",
                    showlegend=False,
                    hovertemplate=("CC<br>x %{x:.3f} mas"
                                   "<br>y %{y:.3f} mas<extra></extra>"),
                )
            )
    else:
        # Map original CC labels -> current clusterIDs via
        # fitted['origID'] -> fitted['clusterID'], then colour by cluster.
        cc_lbl = cc_labels[cc_mask]
        if len(fitted):
            orig_to_cluster = dict(zip(
                fitted["origID"].to_numpy(dtype=int),
                fitted["clusterID"].to_numpy(dtype=int),
            ))
        else:
            orig_to_cluster = {}
        robust_lookup = {
            int(row["clusterID"]): bool(row["robust"])
            for _, row in fitted.iterrows()
        }
        for lbl in np.unique(cc_lbl):
            in_lbl = cc_lbl == lbl
            if not np.any(in_lbl):
                continue
            cid = orig_to_cluster.get(int(lbl), int(lbl))
            robust = robust_lookup.get(cid, True)
            color, _, _ = _cluster_style(cid, robust)
            fig.add_trace(
                go.Scattergl(
                    x=cc_x[in_lbl], y=cc_y[in_lbl],
                    mode="markers",
                    marker=dict(color=color, size=4, opacity=0.6,
                                line=dict(width=0)),
                    name=f"cluster {cid}" if cid >= 0 else "unassigned",
                    showlegend=False,
                    hovertemplate=(f"cluster {cid}<br>"
                                   "x %{x:.3f} mas<br>"
                                   "y %{y:.3f} mas<extra></extra>"),
                )
            )

    # Cluster centers + FWHM ellipses from the fit table.
    if len(fitted):
        cx_arr = (fitted["avg_x"] - fitted["core_x"]).to_numpy(dtype=float)
        cy_arr = (fitted["avg_y"] - fitted["core_y"]).to_numpy(dtype=float)
        fmaj = fitted["fwhm_maj"].to_numpy(dtype=float)
        fmin = fitted["fwhm_min"].to_numpy(dtype=float)
        cpa = fitted["cpa"].to_numpy(dtype=float)
        ids = fitted["clusterID"].astype(int).to_numpy()
        robs = fitted["robust"].astype(bool).to_numpy()
        for x, y, maj, minor, pa, cid, rob in zip(cx_arr, cy_arr, fmaj, fmin, cpa, ids, robs):
            color, _, _ = _cluster_style(int(cid), bool(rob))
            if np.isfinite(maj) and np.isfinite(minor) and maj > 0 and minor > 0:
                pa_use = float(pa) if np.isfinite(pa) else 0.0
                # Draw the 3-sigma inclusion outline FIRST (larger, lighter)
                # so the FWHM ellipse overlays it cleanly — Plotly composites
                # later traces on top, so this ordering preserves the darker
                # FWHM core inside the lighter halo. Controlled by the
                # header "Show 3σ outlines" checkbox; default off.
                if show_3sigma:
                    ex3, ey3 = _ellipse_xy(x, y,
                                           SIGMA3_OVER_FWHM * float(maj),
                                           SIGMA3_OVER_FWHM * float(minor),
                                           pa_use)
                    fig.add_trace(
                        go.Scatter(
                            x=ex3, y=ey3, mode="lines",
                            line=dict(color=color, width=1, dash="dot"),
                            fill="toself", fillcolor=_rgba(color, 0.04),
                            showlegend=False,
                            hovertemplate=(f"cluster {cid} 3σ inclusion<br>"
                                           f"maj {SIGMA3_OVER_FWHM*maj:.3f} mas<br>"
                                           f"min {SIGMA3_OVER_FWHM*minor:.3f} mas<extra></extra>"),
                        )
                    )
                # FWHM ellipse — solid outline + heavier fill, drawn last so
                # it sits on top of the 3-sigma halo.
                ex, ey = _ellipse_xy(x, y, float(maj), float(minor), pa_use)
                fig.add_trace(
                    go.Scatter(
                        x=ex, y=ey, mode="lines",
                        line=dict(color=color, width=1),
                        fill="toself", fillcolor=_rgba(color, 0.15),
                        showlegend=False,
                        hovertemplate=(f"cluster {cid} FWHM<br>"
                                       f"maj {maj:.3f} mas<br>"
                                       f"min {minor:.3f} mas<br>"
                                       f"pa {pa:.1f}°<extra></extra>"),
                    )
                )
            # Non-core clusters: black number at center. Core (clusterID == 0)
            # is already marked with a black X by the dedicated trace below.
            if int(cid) != 0:
                fig.add_trace(
                    go.Scatter(
                        x=[x], y=[y],
                        mode="text",
                        text=[str(int(cid))],
                        textfont=dict(size=18, color="black",
                                      family="ui-monospace, monospace"),
                        showlegend=False,
                        hovertemplate=(f"cluster {cid}<br>"
                                       "center (%{x:.3f}, %{y:.3f}) mas<extra></extra>"),
                    )
                )
                # (Center marker disabled by request — keep code for easy revert.)
                # fig.add_trace(
                #     go.Scatter(
                #         x=[x], y=[y], mode="markers",
                #         marker=dict(color="black", symbol="x", size=14,
                #                     line=dict(width=2, color="black")),
                #         showlegend=False, hoverinfo="skip",
                #     )
                # )

    # Black 'x' at the core (0,0)
    fig.add_trace(
        go.Scatter(
            x=[0.0], y=[0.0], mode="markers",
            marker=dict(color="black", symbol="x-thin", size=14,
                        line=dict(width=2, color="black")),
            name="core", showlegend=False,
            hovertemplate="core (0, 0)<extra></extra>",
        )
    )

    # Initial zoom box from the cluster footprint (mirrors the formula in
    # cluster_code.show_clusters: positions ± 2*sizeMaj ± 1.5*<bmaj>, then 5%
    # extra padding). Falls back to the full FITS extent if there are no
    # fitted clusters in the source.
    x_lo_fits = float(np.min(ax.x_mas))
    x_hi_fits = float(np.max(ax.x_mas))
    y_lo_fits = float(np.min(ax.y_mas))
    y_hi_fits = float(np.max(ax.y_mas))
    extent = compute_source_extent(cluster_df)
    if extent is not None:
        (x_lo_zoom, x_hi_zoom), (y_lo_zoom, y_hi_zoom) = extent
    else:
        x_lo_zoom, x_hi_zoom = x_lo_fits, x_hi_fits
        y_lo_zoom, y_hi_zoom = y_lo_fits, y_hi_fits

    # Beam ellipse in the lower-LEFT corner of the initial view (+x reversed,
    # so high-x = visually left). The clientside zoom callback repositions
    # the beam on later zoom/pan using beam_params.x_extent / y_extent below.
    x_span_zoom = x_hi_zoom - x_lo_zoom
    y_span_zoom = y_hi_zoom - y_lo_zoom
    bx = x_hi_zoom - 0.08 * x_span_zoom
    by = y_lo_zoom + 0.08 * y_span_zoom
    bex, bey = _ellipse_xy(bx, by, float(bmaj), float(bmin), float(bpa))
    fig.add_trace(
        go.Scatter(
            x=bex, y=bey, mode="lines",
            line=dict(color="#1f77b4", width=1.5),
            fill="toself", fillcolor="rgba(31,119,180,0.15)",
            name="beam", showlegend=False,
            hovertemplate=(f"beam<br>bmaj {bmaj:.3f} mas<br>"
                           f"bmin {bmin:.3f} mas<br>bpa {bpa:.1f}°<extra></extra>"),
        )
    )

    # Axes: +x reversed (astro convention) + equal mas/pixel scale (so a
    # square in data is a square on screen) AND free-form drag-zoom. The
    # combination is `scaleanchor` + `constrain="domain"` on both axes:
    # Plotly preserves the dragged data range exactly and shrinks the
    # drawable panel area to honor the equal scale (whitespace appears on
    # the sides or top/bottom under non-square zooms — accepted).
    # ``uirevision`` (passed in by the caller) preserves the user's manual
    # zoom across epoch changes within a (source, model) session. Scoping
    # it to (source, model) — instead of a hard-coded constant — flushes
    # any stale Plotly axis state when the reviewer swaps sources or
    # models, which is what we want and which also serves as a defensive
    # release valve against rare cases where Plotly's SVG layer goes
    # stale after many hours of epoch-switching.
    fig.update_xaxes(
        title_text="X [mas]",
        range=[x_hi_zoom, x_lo_zoom],     # reversed: +x to the left
        constrain="domain",
    )
    fig.update_yaxes(
        title_text="Y [mas]",
        range=[y_lo_zoom, y_hi_zoom],
        scaleanchor="x", scaleratio=1.0,
        constrain="domain",
    )
    fig.update_layout(
        template="plotly_white",
        height=720,
        margin=dict(l=60, r=20, t=40, b=50),
        title=dict(text=(f"{epoch_name} ({epoch_val:.4f})  ·  "
                         f"cbase = {1000*cbase:.2f} mJy/beam"
                         + (f"  ·  {image_source_label}"
                            if image_source_label else "")
                         + ("  ·  CC↔cluster mapping unavailable"
                            if cc_labels is None else "")),
                   font=dict(size=12), x=0.5, xanchor="center"),
        dragmode="zoom",
        uirevision=uirevision,
    )
    return fig


# ---------------------------------------------------------------------------
# Convenience wrapper that knows about the data bundle + cache directory
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _stacked_axes_for_bundle(folder: str, model_key: str, csv_sha: str):
    """Cached stacked Stokes-I image for a (source, model) bundle.

    Keyed on the CSV content hash (``csv_sha``) so it invalidates when the
    CSV changes (e.g. after ``mojave-apply``). The stacked image is
    independent of the selected epoch, so caching keeps epoch-scrubbing
    snappy. Returns ``(EpochAxes, (bmaj, bmin, bpa))`` (median beam) or
    ``None`` when the bundle has no plotdata to stack.
    """
    from ..data.loader import load_bundle
    from .synthesize_fits import synthesize_stacked_stokes_i
    b = load_bundle(folder, model_key)
    if b.plotdata is None:
        return None
    return synthesize_stacked_stokes_i(
        cluster_df=b.cluster_df,
        cc_data=b.plotdata.cc_data,
        epoch_info=b.plotdata.epoch_info,
    )


def overlay_figure_for_epoch(
    bundle,
    epoch_int: int,
    cache_dir: Path,
    source_no_band: str,
    band: str,
    fits_data_dir: Path | None = None,
    show_3sigma: bool = False,
    image_source: ImageSource = "synthesize",
    stacked: bool = False,
    uirevision: str = "overlay",
) -> tuple[go.Figure, dict | None]:
    """Higher-level wrapper: prepares the Stokes I image (either by
    synthesizing it from clean components or by fetching the CLEAN FITS),
    then delegates to ``build_overlay_figure``.

    Returns ``(figure, beam_params)``. ``beam_params`` is ``None`` when the
    figure is the placeholder (backup model, out-of-range epoch, fetch error).
    Otherwise it carries everything the clientside zoom callback needs to
    keep the beam visible at the viewport corner.

    ``image_source``:
      - ``"synthesize"`` (default): build the Stokes I image from the
        epoch's clean components convolved with the restoring beam. Fast,
        offline, matches FITS within a fraction of a percent at the
        contour levels that matter for review.
      - ``"fits"``: fetch the CLEAN FITS image from the local data dir /
        on-disk cache / NRAO archive. Include this when residual-noise
        structure matters.

    ``stacked``: when True, the contour background is the epoch-averaged
    stacked image (all epochs' clean components, divided by the epoch count,
    convolved with the median beam) instead of the single-epoch image, and
    the drawn beam is the median beam. Overrides ``image_source``. The
    per-epoch cluster overlay (CC scatter, ellipses, labels) still follows
    ``epoch_int`` so the reviewer can scrub epochs against the stable
    averaged background.
    """
    if bundle.plotdata is None:
        return _empty_overlay(
            "Epoch overlay only available for the current model "
            "(backups have no .plotdata.npz)."
        ), None
    pd_ = bundle.plotdata
    if epoch_int < 0 or epoch_int >= len(pd_.epoch_info):
        return _empty_overlay(f"Epoch index {epoch_int} out of range."), None
    info = pd_.epoch_info[epoch_int]
    epoch_name = str(info["epoch_name"])
    epoch_val = float(info["epoch_val"])

    # Core position for this epoch — needed by both branches so the image
    # axes can be expressed relative to the core (0, 0). The mask uses the
    # tight tolerance to avoid pulling in neighbouring epochs' rows.
    epoch_mask = epoch_match_mask(bundle.cluster_df["epoch"].to_numpy(), epoch_val)
    fitted = bundle.cluster_df.loc[epoch_mask & (bundle.cluster_df["clusterID"] >= 0)]
    core_x = float(fitted["core_x"].iloc[0]) if len(fitted) else 0.0
    core_y = float(fitted["core_y"].iloc[0]) if len(fitted) else 0.0

    # Synthesis is the default — fast, offline, matches the FITS image at
    # contour-relevant levels to within a fraction of a percent. The FITS
    # path is the explicit override (header checkbox), and gets tagged in
    # the title so the reviewer always knows when they're looking at the
    # CLEAN restored image (residual noise sea included) vs the synthesized
    # one (clean components convolved with the restoring beam).
    # Beam + noise default to this epoch's values; the stacked branch swaps
    # in the median beam and median noise across all epochs.
    beam_bmaj = float(info["bmaj"])
    beam_bmin = float(info["bmin"])
    beam_bpa = float(info["bpa"])
    inoise_use = float(info["inoise"])

    if stacked:
        # Stacked image overrides image_source — it is always built from the
        # clean components (convolved with the median beam), never FITS.
        result = _stacked_axes_for_bundle(
            str(bundle.source.folder), bundle.model.key, bundle.csv_sha)
        if result is None:
            return _empty_overlay(
                "Stacked image needs plotdata, which this model lacks."
            ), None
        epoch_axes, (beam_bmaj, beam_bmin, beam_bpa) = result
        # Median noise across epochs sets the contour base for the average.
        inoise_use = float(np.median(pd_.epoch_info["inoise"]))
        image_source_label = (f"Stacked image · {len(pd_.epoch_info)} epochs "
                              "· median beam")
    elif image_source == "fits":
        ref = FitsRef(
            source_no_band=source_no_band,
            band=str(info["band"]) or band,
            epoch_name=epoch_name,
            stokes="i",
        )
        try:
            fits_path = fetch_fits(ref, cache_dir, fits_data_dir=fits_data_dir)
        except Exception as e:
            return _empty_overlay(f"Could not fetch FITS:\n{e}"), None
        epoch_axes = _load_fits_image(fits_path, core_x=core_x, core_y=core_y)
        image_source_label = "FITS image"
    else:
        # Lazy import to avoid pulling scipy at module load time.
        from .synthesize_fits import synthesize_stokes_i
        epoch_axes = synthesize_stokes_i(
            cluster_df=bundle.cluster_df,
            cc_data=pd_.cc_data,
            epoch_val=epoch_val,
            core_x=core_x, core_y=core_y,
            pix_to_mas=float(info["pix_to_mas"]),
            bmaj=float(info["bmaj"]),
            bmin=float(info["bmin"]),
            bpa=float(info["bpa"]),
        )
        image_source_label = ""

    fig = build_overlay_figure(
        epoch_axes=epoch_axes,
        cluster_df=bundle.cluster_df,
        cc_data=pd_.cc_data,
        cc_labels=pd_.cc_labels,
        epoch_val=epoch_val,
        epoch_name=epoch_name,
        inoise=inoise_use,
        bmaj=beam_bmaj,
        bmin=beam_bmin,
        bpa=beam_bpa,
        show_3sigma=show_3sigma,
        image_source_label=image_source_label,
        uirevision=uirevision,
    )

    # Locate the beam trace by name. x_extent / y_extent are the initial
    # zoom box (cluster footprint) so a double-click reset still places the
    # beam in the right corner.
    beam_idx = next((i for i, t in enumerate(fig.data)
                     if getattr(t, "name", None) == "beam"), None)
    if beam_idx is None or not fig.data:
        return fig, None
    extent = compute_source_extent(bundle.cluster_df)
    if extent is not None:
        (x_lo_e, x_hi_e), (y_lo_e, y_hi_e) = extent
    else:
        contour = fig.data[0]
        x_arr = np.asarray(contour.x, dtype=float)
        y_arr = np.asarray(contour.y, dtype=float)
        x_lo_e, x_hi_e = float(x_arr.min()), float(x_arr.max())
        y_lo_e, y_hi_e = float(y_arr.min()), float(y_arr.max())
    beam_params = {
        "bmaj": beam_bmaj,
        "bmin": beam_bmin,
        "bpa": beam_bpa,
        "beam_idx": int(beam_idx),
        "x_extent": [x_lo_e, x_hi_e],
        "y_extent": [y_lo_e, y_hi_e],
    }
    return fig, beam_params


def _empty_overlay(message: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        template="plotly_white",
        height=720,
        margin=dict(l=20, r=20, t=20, b=20),
        annotations=[dict(text=message, x=0.5, y=0.5,
                          xref="paper", yref="paper",
                          showarrow=False, font=dict(size=14, color="#666"),
                          align="center")],
    )
    return fig
