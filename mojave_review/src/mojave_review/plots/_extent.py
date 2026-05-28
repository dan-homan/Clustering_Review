"""Helpers for computing an initial zoom box from cluster data.

Mirrors the extent computation in ``cluster_code.show_clusters``:

    xmin = min(avg_x - core_x - size_factor*sizeMaj) - beam_factor*median(bmaj)
    xmax = max(avg_x - core_x + size_factor*sizeMaj) + beam_factor*median(bmaj)
    (same in y)
    then expanded by `padding` on each side.

We use this both for the overlay panel's initial view and for the Kinematics
summary's X/Y vector subplot so each source opens framed on its actual jet
footprint rather than the full FITS or full-arrow extent.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

Extent = tuple[tuple[float, float], tuple[float, float]]


def compute_source_extent(
    cluster_df: pd.DataFrame,
    *,
    padding: float = 0.05,
    beam_factor: float = 1.5,
    size_factor: float = 2.0,
) -> Extent | None:
    """Return ``((x_lo, x_hi), (y_lo, y_hi))`` or ``None`` if no fitted clusters."""
    fitted = cluster_df[cluster_df["clusterID"] >= 0]
    if len(fitted) == 0:
        return None
    xpos = (fitted["avg_x"] - fitted["core_x"]).to_numpy(dtype=float)
    ypos = (fitted["avg_y"] - fitted["core_y"]).to_numpy(dtype=float)
    size = fitted["sizeMaj"].fillna(0).to_numpy(dtype=float)
    if "bmaj" in cluster_df.columns:
        median_beam = float(np.nanmedian(cluster_df["bmaj"].to_numpy(dtype=float)))
    else:
        median_beam = 0.0
    if not np.isfinite(median_beam):
        median_beam = 0.0

    xmin = float(np.nanmin(xpos - size_factor * size)) - beam_factor * median_beam
    xmax = float(np.nanmax(xpos + size_factor * size)) + beam_factor * median_beam
    ymin = float(np.nanmin(ypos - size_factor * size)) - beam_factor * median_beam
    ymax = float(np.nanmax(ypos + size_factor * size)) + beam_factor * median_beam

    xspan = xmax - xmin
    yspan = ymax - ymin
    if xspan <= 0 or yspan <= 0:
        return None

    return (
        (xmin - padding * xspan, xmax + padding * xspan),
        (ymin - padding * yspan, ymax + padding * yspan),
    )
