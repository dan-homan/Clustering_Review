"""Per-epoch overlay for the XVIII Gaussian fits, on the shared clean-image.

The comparison page shows the SAME Stokes-I background on both sides — built
from our npz clean components (or the real FITS) — and overlays the two
feature models on top: the current clustering on the right, the old MOJAVE
XVIII Gaussian fits on the left. This module renders the XVIII side.

It reuses ``plots.overlay.build_overlay_figure`` with ``cc_labels=None`` so
the clean components render as faint neutral-grey context dots (they belong
to the clustering, not to XVIII features) while the FWHM ellipses + labels
come from the XVIII cluster DataFrame (built by ``data.xviii``, already in
the clustering core frame). The clustering side just uses
``overlay.overlay_figure_for_epoch`` unchanged.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import plotly.graph_objects as go

from ..data.fits_cache import FitsRef, fetch_fits
from ._extent import compute_source_extent
from .overlay import (
    ImageSource,
    _empty_overlay,
    _load_fits_image,
    build_overlay_figure,
    epoch_match_mask,
)


def build_xviii_overlay(
    bundle,
    xviii_df,
    epoch_val: float,
    cache_dir: Path,
    source_no_band: str,
    band: str,
    *,
    fits_data_dir: Path | None = None,
    image_source: ImageSource = "synthesize",
    uirevision: str = "xviii-overlay",
    source_label: str = "",
    extent: tuple | None = None,
    cbase_factor: float = 3.0,
) -> tuple[go.Figure, dict | None]:
    """Render the XVIII overlay for one epoch (matched to the nearest MOJAVE
    observation in ``bundle``'s npz for the background image)."""
    if bundle.plotdata is None:
        return _empty_overlay("XVIII overlay needs the current model's npz."), None
    if xviii_df is None or xviii_df.empty:
        return _empty_overlay("No XVIII Gaussian fits for this source."), None

    pd_ = bundle.plotdata
    ep_vals = np.asarray(pd_.epoch_info["epoch_val"], dtype=float)
    j = int(np.argmin(np.abs(ep_vals - epoch_val)))
    info = pd_.epoch_info[j]
    ev = float(info["epoch_val"])
    epoch_name = str(info["epoch_name"])

    if not (epoch_match_mask(xviii_df["epoch"].to_numpy(), ev)
            & (xviii_df["clusterID"] >= 0)).any():
        return _empty_overlay(f"No XVIII features at {epoch_name}."), None

    # Re-register onto the clustering map. ``xviii_df`` carries absolute
    # map positions in ``avg_x``/``avg_y`` with ``core_x``/``core_y`` = the
    # XVIII core (its summary frame). The shared background image is centered
    # on the CLUSTERING core, so override the core reference with the fitted
    # clustering core for this epoch: ``avg − core`` then places the Gaussians
    # at their true offset from the clustering core (preserving the XVIII-core
    # vs clustering-core registration difference; MRT Note 3).
    cdf = bundle.cluster_df
    cfit = cdf.loc[epoch_match_mask(cdf["epoch"].to_numpy(), ev)
                   & (cdf["clusterID"] >= 0)]
    core_x = float(cfit["core_x"].iloc[0]) if len(cfit) else 0.0
    core_y = float(cfit["core_y"].iloc[0]) if len(cfit) else 0.0
    xviii_df = xviii_df.copy()
    xviii_df["core_x"] = core_x
    xviii_df["core_y"] = core_y

    beam_bmaj = float(info["bmaj"])
    beam_bmin = float(info["bmin"])
    beam_bpa = float(info["bpa"])
    inoise_use = float(info["inoise"])

    if image_source == "fits":
        ref = FitsRef(
            source_no_band=source_no_band,
            band=str(info["band"]) or band,
            epoch_name=epoch_name,
            stokes="i",
        )
        try:
            fits_path = fetch_fits(ref, cache_dir, fits_data_dir=fits_data_dir)
        except Exception as e:  # noqa: BLE001 — surface the fetch error to the UI
            return _empty_overlay(f"Could not fetch FITS:\n{e}"), None
        epoch_axes = _load_fits_image(fits_path, core_x=core_x, core_y=core_y)
        image_source_label = "FITS Image"
    else:
        from .synthesize_fits import synthesize_stokes_i
        epoch_axes = synthesize_stokes_i(
            cluster_df=xviii_df,
            cc_data=pd_.cc_data,
            epoch_val=ev,
            core_x=core_x, core_y=core_y,
            pix_to_mas=float(info["pix_to_mas"]),
            bmaj=beam_bmaj, bmin=beam_bmin, bpa=beam_bpa,
        )
        image_source_label = "Clean Component Convolution"

    fig = build_overlay_figure(
        epoch_axes=epoch_axes,
        cluster_df=xviii_df,
        cc_data=pd_.cc_data,
        cc_labels=None,          # grey context dots; ellipses come from XVIII
        epoch_val=ev,
        epoch_name=epoch_name,
        inoise=inoise_use,
        bmaj=beam_bmaj,
        bmin=beam_bmin,
        bpa=beam_bpa,
        image_source_label=image_source_label,
        source_label=source_label,
        uirevision=uirevision,
        extent_override=extent,
        cbase_factor=cbase_factor,
    )

    beam_idx = next((i for i, t in enumerate(fig.data)
                     if getattr(t, "name", None) == "beam"), None)
    if beam_idx is None or not fig.data:
        return fig, None
    extent = extent or compute_source_extent(xviii_df)
    if extent is not None:
        (x_lo_e, x_hi_e), (y_lo_e, y_hi_e) = extent
    else:
        contour = fig.data[0]
        x_arr = np.asarray(contour.x, dtype=float)
        y_arr = np.asarray(contour.y, dtype=float)
        x_lo_e, x_hi_e = float(np.nanmin(x_arr)), float(np.nanmax(x_arr))
        y_lo_e, y_hi_e = float(np.nanmin(y_arr)), float(np.nanmax(y_arr))
    beam_params = {
        "bmaj": beam_bmaj, "bmin": beam_bmin, "bpa": beam_bpa,
        "beam_idx": int(beam_idx),
        "x_extent": [x_lo_e, x_hi_e], "y_extent": [y_lo_e, y_hi_e],
    }
    return fig, beam_params
