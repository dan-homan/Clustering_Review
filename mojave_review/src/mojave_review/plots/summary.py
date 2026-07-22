"""Plotly port of cluster_code.make_summary_plots.

Renders per-cluster summary plots for one of five views (top/bottom pair,
except "Position Angle" which is a single panel):

    "Position"       -> distance vs epoch        |  XY centroid track (mas)
    "Position Angle" -> PA vs epoch              (single panel)
    "Flux"           -> I flux vs epoch          |  Tb obs vs epoch  (log y-axis)
    "Polarization"   -> P flux vs epoch (log y)  |  EVPA vs epoch
    "Kinematics"     -> speed vs distance        |  X/Y velocity vectors

The numerical behavior mirrors cluster_code.py's make_summary_plots:
PA / EVPA de-wrapping, the shift_pa flag, the Tb formula, per-cluster
position polyfit (used for the predicted distance line + the Kinematics view).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ---------------------------------------------------------------------------
# Style tables — ported from cluster_code.py (cl_colors / cl_markers / cl_fill)
# ---------------------------------------------------------------------------

_MPL_COLOR = {
    "b": "blue", "g": "green", "r": "red", "m": "magenta",
    "y": "goldenrod", "gray": "darkorange", "c": "cyan", "k": "black",
}
_CL_COLORS = ["b", "g", "r", "m", "y", "gray"]

_MPL_TO_PLOTLY_SYMBOL = {
    "x": "x", "o": "circle", "s": "square", "p": "pentagon",
    "*": "star", "^": "triangle-up", "v": "triangle-down",
    "X": "x-thin", "D": "diamond", "P": "cross", "1": "y-up",
    "+": "cross-thin",
}
_CL_MARKERS = ["x", "o", "s", "o", "s", "p", "*", "^", "v",
               "*", "^", "v", "X", "D", "P", "D", "1"]
_CL_FILL = ["none", "full", "none", "none", "full", "none", "full", "none", "full",
            "none", "full", "none", "full", "none", "full", "full", "none"]

_FREQ_GHZ = 15.4  # U-band

VIEWS = ("Position", "Position Angle", "Flux", "Polarization", "Kinematics")


def _cluster_style(cid: int, robust: bool) -> tuple[str, str, bool]:
    if cid >= 1000 or cid < 0:
        return "black", "cross-thin", False
    if not robust:
        idx = cid % len(_CL_MARKERS)
        return "slategray", _MPL_TO_PLOTLY_SYMBOL.get(_CL_MARKERS[idx], "circle"), _CL_FILL[idx] == "full"
    color = _MPL_COLOR[_CL_COLORS[cid % len(_CL_COLORS)]]
    idx = cid % len(_CL_MARKERS)
    return color, _MPL_TO_PLOTLY_SYMBOL.get(_CL_MARKERS[idx], "circle"), _CL_FILL[idx] == "full"


# ---------------------------------------------------------------------------
# Per-cluster slice
# ---------------------------------------------------------------------------


@dataclass
class _Slice:
    cid: int
    robust: bool
    time: np.ndarray
    ep_name: np.ndarray    # epoch labels in YYYY_MM_DD form (for hover)
    xpos: np.ndarray       # avg_x - core_x
    ypos: np.ndarray       # avg_y - core_y
    dist: np.ndarray
    pa: np.ndarray
    size: np.ndarray
    flux: np.ndarray
    pflux: np.ndarray
    evpa: np.ndarray
    tb_obs: np.ndarray
    use_in_fit: np.ndarray
    selected: np.ndarray
    # 1-sigma position uncertainties (NaN where unavailable); see
    # plots/uncertainty.py + docs/uncertainty_estimates.md.
    sig_dx: np.ndarray
    sig_dy: np.ndarray
    sig_dist: np.ndarray
    sig_pa: np.ndarray


def _dewrap(arr: np.ndarray, period: float, jump: float) -> np.ndarray:
    a = arr.copy()
    for j in range(1, len(a)):
        if a[j] - a[j - 1] > jump:
            a[j] -= period
        elif a[j] - a[j - 1] < -jump:
            a[j] += period
    return a


def _shift_pa_flag(df: pd.DataFrame) -> bool:
    non_core = df["clusterID"] > 0
    x_c = (df.loc[non_core, "avg_x"] - df.loc[non_core, "core_x"]).to_numpy()
    y_c = (df.loc[non_core, "avg_y"] - df.loc[non_core, "core_y"]).to_numpy()
    if len(x_c) == 0:
        return False
    pa_c = (180.0 / np.pi) * np.arctan2(x_c, y_c)
    return bool(np.nanmedian(np.abs(pa_c)) > 120)


def _build_slices(df: pd.DataFrame, z: float, shift_pa: bool,
                  flux_threshold: float) -> list[_Slice]:
    out: list[_Slice] = []
    for cid in np.unique(df["clusterID"].to_numpy()):
        m = df["clusterID"] == cid
        sub = df.loc[m].sort_values("epoch")
        time = sub["epoch"].to_numpy(dtype=float)
        ep_name = (sub["ep_name"].astype(str).to_numpy()
                   if "ep_name" in sub.columns
                   else np.array([f"{t:.4f}" for t in time]))
        xpos = (sub["avg_x"] - sub["core_x"]).to_numpy(dtype=float)
        ypos = (sub["avg_y"] - sub["core_y"]).to_numpy(dtype=float)
        dist = np.sqrt(xpos**2 + ypos**2)

        pa = (180.0 / np.pi) * np.arctan2(xpos, ypos)
        pa = _dewrap(pa, period=360.0, jump=300.0)
        if shift_pa:
            pa = pa + (pa < -60.0) * 360.0

        fwhm_maj = sub["fwhm_maj"].to_numpy(dtype=float)
        fwhm_min = sub["fwhm_min"].to_numpy(dtype=float)
        size = np.sqrt(fwhm_maj * fwhm_min)
        size = np.where(size < 0.1, 0.1, size)

        flux = sub["iflux"].to_numpy(dtype=float)
        if not np.any(flux > 0) or np.nanmedian(flux) < flux_threshold:
            continue

        pflux = sub["pflux"].to_numpy(dtype=float) if "pflux" in sub else np.full_like(flux, np.nan)
        evpa = sub["evpa"].to_numpy(dtype=float) if "evpa" in sub else np.full_like(flux, np.nan)
        evpa = _dewrap(evpa, period=180.0, jump=150.0)

        tb_obs = 1.22e12 * flux * (1.0 + z) / (_FREQ_GHZ**2 * size**2)

        robust = bool(sub["robust"].iloc[0]) if len(sub) else False
        use_in_fit = sub["use_in_fit"].to_numpy(dtype=bool)
        selected = (sub["select"].to_numpy(dtype=bool)
                    if "select" in sub.columns else np.zeros_like(use_in_fit, dtype=bool))

        def _col(name: str) -> np.ndarray:
            return (sub[name].to_numpy(dtype=float)
                    if name in sub.columns else np.full(len(sub), np.nan))

        out.append(_Slice(
            cid=int(cid), robust=robust, time=time, ep_name=ep_name,
            xpos=xpos, ypos=ypos, dist=dist, pa=pa, size=size,
            flux=flux, pflux=pflux, evpa=evpa, tb_obs=tb_obs,
            use_in_fit=use_in_fit, selected=selected,
            sig_dx=_col("sig_dx"), sig_dy=_col("sig_dy"),
            sig_dist=_col("sig_dist"), sig_pa=_col("sig_pa"),
        ))
    return out


# ---------------------------------------------------------------------------
# Trace helpers
# ---------------------------------------------------------------------------


def _set_log_yaxis(fig: go.Figure, row: int, col: int, title: str) -> None:
    """Make a y-axis log-scaled with 10^x exponent-style tick labels.

    Used for the flux / Tb / polarized-flux panels: we plot the RAW values
    (Jy, K) and let the axis do the log scaling, so ticks read 10^8, 10^9, …
    instead of the old log10() numbers on a linear axis. One tick per decade
    (dtick=1 in log units), rendered via exponentformat="power".
    """
    fig.update_yaxes(title_text=title, type="log", dtick=1,
                     exponentformat="power", showexponent="all",
                     row=row, col=col)


def _marker_style(color: str, symbol: str, filled: bool) -> dict:
    if filled:
        return {"color": color, "symbol": symbol, "size": 8,
                "line": {"width": 1, "color": color}}
    open_symbol = symbol if symbol.endswith("-open") else f"{symbol}-open"
    return {"color": color, "symbol": open_symbol, "size": 8,
            "line": {"width": 1.5, "color": color}}


def _customdata(s: _Slice) -> list[list]:
    # Return PLAIN Python lists, not a numpy array. plotly.py 6 base64-encodes
    # numpy arrays as typed arrays by default; per-point ``customdata`` then
    # arrives in the browser as a Float64Array and Dash relays it into
    # ``clickData`` as an OBJECT ({"0": cid, "1": epoch}) rather than a list.
    # The click-selection callback (ui/callbacks._toggle_on_click) indexes
    # customdata as cd[0]/cd[1], which silently fails on that object — so
    # clicking a point stopped selecting it. Plain lists serialize as JSON
    # arrays and read back correctly. (cid first, then decimal epoch, then the
    # YYYY_MM_DD epoch name for display only — the click callback reads cd[0]
    # and cd[1], so the decimal epoch MUST stay at index 1.)
    return [[int(s.cid), float(t), str(n)] for t, n in zip(s.time, s.ep_name)]


def _err_dict(arr: np.ndarray | None, color: str) -> dict | None:
    """Build a Plotly error-bar spec from a 1-sigma array, or None.

    Non-finite entries become JSON null (no bar for that point). Returns None
    when there's nothing to draw, so the trace carries no error_x/error_y.
    Values are plain Python lists (not numpy) — same plotly-6 typed-array
    hygiene as customdata, though error arrays aren't read back server-side.
    """
    if arr is None:
        return None
    vals = [float(v) if (v is not None and np.isfinite(v)) else None
            for v in np.asarray(arr, dtype=float)]
    if all(v is None for v in vals):
        return None
    return dict(type="data", array=vals, visible=True,
                thickness=1, width=0, color=color)


def _add_cluster_traces(
    fig: go.Figure, s: _Slice, row: int, col: int, ydata: np.ndarray,
    show_legend: bool, show_fit: np.ndarray | None = None,
    ylabel_for_hover: str = "y", error_y_arr: np.ndarray | None = None,
    yunit: str = "", hover_extra: str = "",
) -> None:
    color, symbol, filled = _cluster_style(s.cid, s.robust)
    marker = _marker_style(color, symbol, filled)

    # Which clusters get a legend entry. Robust and non-robust clusters both
    # appear (so they can be toggled/isolated by legend click), and the
    # unassigned cluster (-1, the black "+") is included too. Only synthetic
    # clusters (>=1000) stay out of the legend.
    in_legend = s.cid == -1 or 0 <= s.cid < 1000

    fig.add_trace(
        go.Scatter(
            x=s.time, y=ydata,
            mode="lines+markers",
            line={"color": color, "width": 1, "dash": "dot"},
            marker=marker,
            error_y=_err_dict(error_y_arr, color),
            name=str(s.cid) if in_legend else None,
            legendgroup=f"cid_{s.cid}",
            showlegend=show_legend and in_legend,
            customdata=_customdata(s),
            hovertemplate=(
                f"cluster %{{customdata[0]:.0f}}<br>"
                f"epoch %{{customdata[2]}}<br>"
                f"{ylabel_for_hover} %{{y:.4g}}{yunit}{hover_extra}"
                "<extra></extra>"
            ),
        ),
        row=row, col=col,
    )

    excl = ~s.use_in_fit
    if np.any(excl):
        fig.add_trace(
            go.Scatter(
                x=s.time[excl], y=ydata[excl],
                mode="markers",
                marker={"color": "black", "symbol": "line-ne",
                        "size": 12, "line": {"width": 2, "color": "black"}},
                showlegend=False, legendgroup=f"cid_{s.cid}", hoverinfo="skip",
            ),
            row=row, col=col,
        )

    if np.any(s.selected):
        # SVG scatter so the open-symbol outline renders cleanly.
        # NB: for "*-open" symbols, `marker.color` IS the outline color
        # (not the fill — there is no fill). The earlier
        # `marker.color="rgba(0,0,0,0)"` made the ring invisible.
        fig.add_trace(
            go.Scatter(
                x=s.time[s.selected], y=ydata[s.selected],
                mode="markers",
                marker={
                    "color": "gold",
                    "symbol": "circle-open",
                    "size": 22,
                    "line": {"width": 3, "color": "gold"},
                },
                showlegend=False, legendgroup=f"cid_{s.cid}", hoverinfo="skip",
            ),
            row=row, col=col,
        )

    if show_fit is not None:
        # Motion fit: SOLID (vs the thin dotted epoch-to-epoch connector line
        # above) so it reads as the model/trend; same width and cluster colour,
        # so solid-vs-dotted alone carries the distinction.
        fig.add_trace(
            go.Scatter(
                x=s.time, y=show_fit, mode="lines",
                line={"color": color, "width": 1, "dash": "solid"},
                showlegend=False, legendgroup=f"cid_{s.cid}", hoverinfo="skip",
            ),
            row=row, col=col,
        )


def _add_xy_traces(fig: go.Figure, s: _Slice, row: int, col: int,
                   show_legend: bool) -> None:
    """One cluster's centroid track in (x, y) mas relative to the core, with
    1-sigma x/y error bars (the XY panel — the bottom of the Position view).
    Mirrors _add_cluster_traces' legend / selection / use-in-fit conventions
    but plots xpos vs ypos."""
    color, symbol, filled = _cluster_style(s.cid, s.robust)
    marker = _marker_style(color, symbol, filled)
    in_legend = s.cid == -1 or 0 <= s.cid < 1000

    fig.add_trace(
        go.Scatter(
            x=s.xpos, y=s.ypos,
            mode="lines+markers",
            line={"color": color, "width": 1, "dash": "dot"},
            marker=marker,
            error_x=_err_dict(s.sig_dx, color),
            error_y=_err_dict(s.sig_dy, color),
            name=str(s.cid) if in_legend else None,
            legendgroup=f"cid_{s.cid}",
            showlegend=show_legend and in_legend,
            customdata=_customdata(s),
            hovertemplate=(
                "cluster %{customdata[0]:.0f}<br>"
                "epoch %{customdata[2]}<br>"
                "x %{x:.3f} mas<br>y %{y:.3f} mas<extra></extra>"
            ),
        ),
        row=row, col=col,
    )

    excl = ~s.use_in_fit
    if np.any(excl):
        fig.add_trace(
            go.Scatter(
                x=s.xpos[excl], y=s.ypos[excl], mode="markers",
                marker={"color": "black", "symbol": "line-ne",
                        "size": 12, "line": {"width": 2, "color": "black"}},
                showlegend=False, legendgroup=f"cid_{s.cid}", hoverinfo="skip",
            ),
            row=row, col=col,
        )

    if np.any(s.selected):
        fig.add_trace(
            go.Scatter(
                x=s.xpos[s.selected], y=s.ypos[s.selected], mode="markers",
                marker={"color": "gold", "symbol": "circle-open",
                        "size": 22, "line": {"width": 3, "color": "gold"}},
                showlegend=False, legendgroup=f"cid_{s.cid}", hoverinfo="skip",
            ),
            row=row, col=col,
        )


def _draw_xy(fig: go.Figure, slices: list[_Slice], row: int,
             show_legend: bool) -> None:
    """Draw the XY centroid-track panel (per-cluster (x, y) mas vs core) on
    subplot ``row``: +x reversed, a black × at the core, and a 10%-padded range.
    The bottom panel of the Position view. The equal mas/pixel scale is NOT
    enforced here via ``scaleanchor`` (which would lock drag-zoom to the panel
    aspect). Instead a clientside script (``assets/equal_aspect.js``) keeps
    equal units by letterboxing — it narrows the panel's ``xaxis<row>.domain``
    to match the current ranges after every draw/zoom, so circles stay round
    while the reviewer can still draw an arbitrary-shape zoom box. ``row``
    selects the axis pair (1 -> x/y, 2 -> x2/y2)."""
    all_x: list[float] = [0.0]
    all_y: list[float] = [0.0]
    for s in slices:
        if s.cid < 1:                       # skip unassigned (-1) + the core
            continue
        _add_xy_traces(fig, s, row=row, col=1, show_legend=show_legend)
        all_x.extend(float(v) for v in s.xpos if np.isfinite(v))
        all_y.extend(float(v) for v in s.ypos if np.isfinite(v))
    fig.add_trace(
        go.Scatter(
            x=[0.0], y=[0.0], mode="markers",
            marker={"color": "black", "symbol": "x-thin", "size": 14,
                    "line": {"width": 2, "color": "black"}},
            name="core", showlegend=False,
            hovertemplate="core (0, 0)<extra></extra>",
        ),
        row=row, col=1,
    )
    ax = np.asarray(all_x, dtype=float)
    ay = np.asarray(all_y, dtype=float)
    x_span = float(ax.max() - ax.min()) or 1.0
    y_span = float(ay.max() - ay.min()) or 1.0
    # No scaleanchor/constrain: equal units come from the letterbox script
    # (see docstring). The explicit reversed x-range + padded y-range set the
    # initial framing; the script then equalizes px/mas by shrinking a domain.
    fig.update_xaxes(title_text="X [mas]",
                     range=[float(ax.max()) + 0.1 * x_span,
                            float(ax.min()) - 0.1 * x_span],   # reversed
                     row=row, col=1)
    fig.update_yaxes(title_text="Y [mas]",
                     range=[float(ay.min()) - 0.1 * y_span,
                            float(ay.max()) + 0.1 * y_span],
                     row=row, col=1)


# ---------------------------------------------------------------------------
# Motion fits
# ---------------------------------------------------------------------------


@dataclass
class _MotionFit:
    cid: int
    px: np.ndarray            # polynomial coefficients for x(t) — px[0] = slope (speed_x)
    py: np.ndarray
    pred_dist: np.ndarray     # predicted distance, evaluated at s.time
    median_dist: float
    median_x: float
    median_y: float
    speed: float              # sqrt(speed_x^2 + speed_y^2), mas/yr
    speed_err: float          # 1-sigma uncertainty on speed (mas/yr)
    significant: bool         # passes the >=3sigma (or slow-and-tight) gate


def _motion_fit(s: _Slice) -> _MotionFit | None:
    """Linear (x,y)-vs-time fit for a robust cluster with >=5 use-in-fit points.

    Returns a fit for EVERY qualifying robust cluster regardless of how
    significant the motion is; ``significant`` records whether it clears the
    original >=3sigma (or slow-and-tightly-constrained) gate. Callers decide
    whether to show all fits or only the significant ones (the "Show only
    3-sigma motions" checkbox)."""
    if s.cid < 0 or s.cid >= 1000 or not s.robust:
        return None
    valid = (~np.isnan(s.xpos)) & (~np.isnan(s.ypos)) & s.use_in_fit
    if valid.sum() <= 4:
        return None
    try:
        px, corrx = np.polyfit(s.time[valid], s.xpos[valid], deg=1, cov=True)
        py, corry = np.polyfit(s.time[valid], s.ypos[valid], deg=1, cov=True)
    except (np.linalg.LinAlgError, ValueError):
        return None
    speed = float(np.sqrt(px[0]**2 + py[0]**2))
    var_vx, var_vy = float(corrx[0, 0]), float(corry[0, 0])
    snr_den = np.sqrt(px[0]**2 * var_vx + py[0]**2 * var_vy)
    sig = (px[0]**2 + py[0]**2) / snr_den if snr_den > 0 else 0.0
    slow = speed < 0.05 and np.sqrt(var_vx + var_vy) < 0.05
    significant = bool(sig >= 3.0 or slow)
    # 1-sigma on speed = |grad sqrt(vx^2+vy^2)| propagated through the two
    # independent slope variances. Degenerates gracefully when speed -> 0.
    if speed > 0:
        speed_err = float(np.sqrt(px[0]**2 * var_vx + py[0]**2 * var_vy) / speed)
    else:
        speed_err = float(np.sqrt(var_vx + var_vy))
    pred_x = px[0] * s.time + px[1]
    pred_y = py[0] * s.time + py[1]
    pred_dist = np.sqrt(pred_x**2 + pred_y**2)
    return _MotionFit(
        cid=s.cid, px=px, py=py, pred_dist=pred_dist,
        median_dist=float(np.median(pred_dist)),
        median_x=float(np.median(pred_x)),
        median_y=float(np.median(pred_y)),
        speed=speed, speed_err=speed_err, significant=significant,
    )


def _beta_str(speed: float, speed_err: float, z: float) -> str:
    """β_app (apparent speed in units of c) with propagated 1-sigma error, as a
    hover string, or "" when z is unknown. ``beta_app`` is linear in the angular
    speed, so the error simply scales: β_err = beta_app(speed_err)."""
    from ..data.source_params import beta_app as _beta_app
    b = _beta_app(speed, z)
    if b is None:
        return ""
    b_err = _beta_app(speed_err, z) or 0.0
    return f"β_app = {b:.2f} ± {b_err:.2f} c (z = {z:g})"


def _motion_hover_extra(mf: "_MotionFit | None", z: float) -> str:
    """Hover suffix describing a cluster's fitted proper motion: fitted speed in
    mas/yr (1-sigma error) and, when z is known, apparent speed in c (also with
    a 1-sigma error). Returns "" when there is no fit. Prefixed with ``<br>`` so
    it can be appended straight into a hovertemplate."""
    if mf is None:
        return ""
    lines = [f"fitted speed {mf.speed:.3f} ± {mf.speed_err:.3f} mas/yr"]
    beta = _beta_str(mf.speed, mf.speed_err, z)
    if beta:
        lines.append(beta)
    return "<br>" + "<br>".join(lines)


# ---------------------------------------------------------------------------
# Public figure builder
# ---------------------------------------------------------------------------


def build_summary_figure(
    cluster_df: pd.DataFrame,
    view: str = "Position",
    z: float = 0.0,
    flux_threshold: float = 0.0,
    vector_scale_factor: float = 1.0,
    hide_non_robust: bool = False,
    only_3sigma: bool = False,
    source_label: str = "",
) -> go.Figure:
    """Build a 2-row (top/bottom) summary figure for one of the four views.

    `source_label`, when set, is drawn as a small badge in the top-right
    corner (paper coords) instead of as a subplot/figure title. Subplot
    titles were removed (they duplicated the axis titles); the only one
    that carried extra meaning — the Kinematics velocity-vector panel — is
    re-added as a single targeted annotation below.

    `vector_scale_factor` multiplies the auto-computed arrow length in the
    Kinematics view. The auto scale draws the *median* speed (floored at
    0.05 mas/yr) at ~1/5th of the panel span. 1.0 = default; <1 shrinks,
    >1 enlarges. Ignored by other views.

    `hide_non_robust` drops the non-robust (slategray) clusters from both the
    plots and the legend. The unassigned cluster (-1) is treated as non-robust
    here and is hidden too; synthetic (>=1000) clusters are NOT affected.

    `only_3sigma` restores the original behaviour of only drawing projected
    motion (the Position fit line + the Kinematics points/vectors) for clusters
    whose motion clears the >=3sigma (or slow-and-tight) gate. Default False:
    projected motion is shown for ALL robust clusters.
    """
    if view not in VIEWS:
        view = "Position"

    # Position Angle is a single plot; the other four views are top/bottom pairs.
    is_single = view == "Position Angle"
    if is_single:
        fig = make_subplots(rows=1, cols=1)
    else:
        # With a known z the Tb formula's (1+z) factor puts it in the host
        # galaxy frame; with z=0 (unknown) it's the observed value. Used only
        # for the y-axis title now that subplot titles are gone.
        z_known = z is not None and z > 0
        tb_label = "Tb host-frame [K]" if z_known else "Tb obs [K]"
        # No subplot_titles: they duplicated the axis titles. Frees the
        # inter-panel space for the resizable divider and the source badge.
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=False,
            vertical_spacing=0.10,
        )

    if cluster_df.empty:
        fig.update_layout(
            template="plotly_white",
            annotations=[dict(text="No data", x=0.5, y=0.5,
                              xref="paper", yref="paper", showarrow=False)],
        )
        return fig

    shift_pa = _shift_pa_flag(cluster_df)
    slices = _build_slices(cluster_df, z=z, shift_pa=shift_pa, flux_threshold=flux_threshold)

    if hide_non_robust:
        # "Non-robust" = the slategray clusters (robust False, normal cid),
        # plus the unassigned cluster (-1), which is treated as non-robust
        # for this purpose. Robust clusters and synthetic (>=1000) are kept.
        def _is_non_robust(s: _Slice) -> bool:
            return s.cid == -1 or (not s.robust and 0 <= s.cid < 1000)
        slices = [s for s in slices if not _is_non_robust(s)]

    # Per-cluster motion fits — used by Position (overlay line) and Kinematics
    motion_fits = {s.cid: _motion_fit(s) for s in slices}

    if view == "Position":
        # Top: distance vs epoch (+ motion-fit overlay). Bottom: the XY
        # centroid track (equal-aspect spatial plot; kept equal via the
        # equal_aspect.js letterbox, flagged below with layout.meta).
        for s in slices:
            mf = motion_fits.get(s.cid)
            show_fit = (mf.pred_dist
                        if mf is not None and (not only_3sigma or mf.significant)
                        else None)
            if s.cid >= 0:
                _add_cluster_traces(
                    fig, s, row=1, col=1, ydata=s.dist,
                    show_legend=True,
                    show_fit=show_fit,
                    ylabel_for_hover="dist", error_y_arr=s.sig_dist,
                    hover_extra=_motion_hover_extra(mf, z),
                )
        fig.update_xaxes(title_text="Epoch", row=1, col=1)
        fig.update_yaxes(title_text="Distance from origin [mas]", row=1, col=1)
        # Legend comes from the distance traces (row 1); XY (row 2) reuses the
        # same legendgroups, so show_legend=False avoids duplicate entries.
        _draw_xy(fig, slices, row=2, show_legend=False)
        # Tell the letterbox script that THIS figure's bottom panel is the
        # equal-aspect XY track (Kinematics' bottom shares the "X/Y [mas]" axis
        # titles but keeps scaleanchor, so titles can't disambiguate).
        fig.update_layout(meta="xy-bottom")

    elif view == "Position Angle":
        # PA vs epoch, on its own panel.
        for s in slices:
            if s.cid > 0:
                _add_cluster_traces(
                    fig, s, row=1, col=1, ydata=s.pa,
                    show_legend=True, ylabel_for_hover="PA",
                    error_y_arr=s.sig_pa,
                )
        fig.update_xaxes(title_text="Epoch", row=1, col=1)
        fig.update_yaxes(title_text="PA [deg]", row=1, col=1)

    elif view == "Flux":
        # Plot raw I flux / Tb on log-scaled axes (10^x ticks); hover shows
        # the real values with units.
        for s in slices:
            _add_cluster_traces(
                fig, s, row=1, col=1, ydata=s.flux,
                show_legend=True, ylabel_for_hover="I", yunit=" Jy",
            )
            if s.cid >= 0:
                _add_cluster_traces(
                    fig, s, row=2, col=1, ydata=s.tb_obs,
                    show_legend=False, ylabel_for_hover="Tb", yunit=" K",
                )
        fig.update_xaxes(title_text="Epoch", row=1, col=1)
        fig.update_xaxes(title_text="Epoch", row=2, col=1)
        _set_log_yaxis(fig, row=1, col=1, title="I flux density [Jy]")
        _set_log_yaxis(fig, row=2, col=1, title=tb_label)

    elif view == "Polarization":
        # P flux on a log-scaled axis (10^x ticks); EVPA stays linear.
        for s in slices:
            if s.cid < 0:
                continue
            _add_cluster_traces(
                fig, s, row=1, col=1, ydata=s.pflux,
                show_legend=True, ylabel_for_hover="P", yunit=" Jy",
            )
            _add_cluster_traces(
                fig, s, row=2, col=1, ydata=s.evpa,
                show_legend=False, ylabel_for_hover="EVPA", yunit=" deg",
            )
        fig.update_xaxes(title_text="Epoch", row=1, col=1)
        fig.update_xaxes(title_text="Epoch", row=2, col=1)
        _set_log_yaxis(fig, row=1, col=1, title="Polarized flux [Jy]")
        fig.update_yaxes(title_text="EVPA [deg]", row=2, col=1)

    elif view == "Kinematics":
        _draw_kinematics(fig, slices, motion_fits,
                         vector_scale_factor=vector_scale_factor,
                         only_3sigma=only_3sigma, z=z)
        # Always show the (0 distance, 0 speed) corner so the reader can see
        # where each feature sits relative to the core and to zero motion.
        # Distances and speeds are non-negative, so rangemode="tozero" anchors
        # both axes at 0 while still auto-fitting the data maxima.
        fig.update_xaxes(title_text="Distance from origin [mas]",
                         rangemode="tozero", row=1, col=1)
        fig.update_yaxes(title_text="Apparent speed [mas/yr]",
                         rangemode="tozero", row=1, col=1)
        # +x to the left (astro convention). We DON'T set an explicit range
        # here: letting Plotly autorange (reversed for x) reproduces exactly
        # what the toolbar "home"/reset button gives — a snug, equal-aspect
        # fit to the markers (cluster tails + core). scaleanchor +
        # constrain="domain" keep mas/pixel equal while still allowing a
        # free-form drag-zoom (panel shrinks instead of expanding the data
        # range to match aspect). autorange="reversed" is what flips +x left
        # without an explicit range to fight it.
        fig.update_xaxes(title_text="X [mas]", row=2, col=1,
                         autorange="reversed", constrain="domain")
        fig.update_yaxes(title_text="Y [mas]", row=2, col=1,
                         scaleanchor="x2", scaleratio=1.0,
                         constrain="domain")

    fig.update_layout(
        template="plotly_white",
        height=720,
        margin={"l": 60, "r": 20, "t": 36, "b": 50},
        legend={"title": "Cluster", "tracegroupgap": 4},
        dragmode="zoom",
    )

    # Per-panel source badge, top-left of EACH subplot, anchored to the axis
    # DOMAIN (not paper) so it (a) sits just above the actual plotted box —
    # correct even where constrain="domain" shrinks it — and (b) tracks the
    # panel when the divider resizes it. The Kinematics bottom panel is the
    # velocity-vector plot, which used to carry a subtitle; that label is
    # folded into its badge instead of a separate annotation.
    if source_label:
        def _badge(text, xref, yref):
            fig.add_annotation(
                text=f"<b>{text}</b>", xref=xref, yref=yref,
                x=0.0, y=1.0, xanchor="left", yanchor="bottom", showarrow=False,
                font={"size": 14, "color": "#333"},
            )
        _badge(source_label, "x domain", "y domain")          # top panel
        if not is_single:
            bottom = (f"{source_label},  X/Y Vector Plot"
                      if view == "Kinematics" else source_label)
            _badge(bottom, "x2 domain", "y2 domain")          # bottom panel
    return fig


# ---------------------------------------------------------------------------
# Kinematics view (speed vs distance + arrow-headed vectors)
# ---------------------------------------------------------------------------


def _draw_kinematics(
    fig: go.Figure,
    slices: list[_Slice],
    motion_fits: dict[int, "_MotionFit | None"],
    vector_scale_factor: float = 1.0,
    only_3sigma: bool = False,
    z: float = 0.0,
) -> None:
    fits = [mf for mf in motion_fits.values()
            if mf is not None and mf.cid > 0
            and (not only_3sigma or mf.significant)]
    if not fits:
        return

    # beta_app (apparent speed in units of c, with 1-sigma error) for the hovers.
    def _beta_line(mf: "_MotionFit") -> str:
        b = _beta_str(mf.speed, mf.speed_err, z)
        return f"{b}<br>" if b else ""

    # speed vs distance scatter, with 1-sigma speed error bars
    for mf in fits:
        color, symbol, filled = _cluster_style(mf.cid, True)
        marker = _marker_style(color, symbol, filled)
        fig.add_trace(
            go.Scatter(
                x=[mf.median_dist], y=[mf.speed],
                mode="markers", marker=marker,
                error_y=_err_dict(np.array([mf.speed_err]), color),
                name=str(mf.cid), legendgroup=f"cid_{mf.cid}",
                showlegend=True,
                hovertemplate=(f"cluster {mf.cid}<br>"
                               "median dist %{x:.2f} mas<br>"
                               f"speed {mf.speed:.3f} ± {mf.speed_err:.3f} "
                               "mas/yr<br>"
                               f"{_beta_line(mf)}<extra></extra>"),
            ),
            row=1, col=1,
        )

    # Vector plot: include (0,0) in the auto-extent so the core is always shown.
    xs = np.array([mf.median_x for mf in fits])
    ys = np.array([mf.median_y for mf in fits])
    vx = np.array([mf.px[0] for mf in fits])
    vy = np.array([mf.py[0] for mf in fits])
    all_xs = np.append(xs, 0.0)
    all_ys = np.append(ys, 0.0)
    xspan = float(np.ptp(all_xs)) or 1.0
    yspan = float(np.ptp(all_ys)) or 1.0
    span = max(xspan, yspan)
    # Auto-fit on the MEDIAN speed: draw the typical arrow at ~1/5th of the
    # panel span, so most vectors are legible regardless of one fast outlier.
    # Floor the reference speed at 0.05 mas/yr so a near-stationary source
    # doesn't blow the arrows up to absurd lengths. User multiplier on top.
    speeds = np.sqrt(vx**2 + vy**2)
    median_speed = float(np.median(speeds)) if speeds.size else 0.0
    ref_speed = max(median_speed, 0.05)
    arrow_scale = 0.2 * span / ref_speed * float(vector_scale_factor)

    # Core marker at (0,0)
    fig.add_trace(
        go.Scatter(
            x=[0.0], y=[0.0], mode="markers",
            marker={"color": "black", "symbol": "x-thin", "size": 14,
                    "line": {"width": 2, "color": "black"}},
            name="core", showlegend=False,
            hovertemplate="core (0, 0)<extra></extra>",
        ),
        row=2, col=1,
    )

    for mf, x0, y0, sx, sy in zip(fits, xs, ys, vx, vy):
        color, symbol, filled = _cluster_style(mf.cid, True)
        marker = _marker_style(color, symbol, filled)
        x1 = x0 + sx * arrow_scale
        y1 = y0 + sy * arrow_scale
        fig.add_trace(
            go.Scatter(
                x=[x0], y=[y0], mode="markers", marker=marker,
                showlegend=False, legendgroup=f"cid_{mf.cid}",
                hovertemplate=(f"cluster {mf.cid}<br>"
                               "tail (%{x:.2f}, %{y:.2f}) mas<br>"
                               f"vx {sx:.3f} mas/yr<br>vy {sy:.3f} mas/yr<br>"
                               f"{_beta_line(mf)}<extra></extra>"),
            ),
            row=2, col=1,
        )
        # arrowhead annotation (xref/yref point at the row-2 subplot axes)
        fig.add_annotation(
            x=x1, y=y1, ax=x0, ay=y0,
            xref="x2", yref="y2", axref="x2", ayref="y2",
            showarrow=True, arrowhead=2, arrowsize=1.2, arrowwidth=2,
            arrowcolor=color, standoff=0,
        )

    # No explicit range: the view branch leaves x autorange="reversed" and y
    # scaleanchor'd, so the initial view is Plotly's autorange fit to the
    # markers (cluster tails + core) — identical to the toolbar "home" reset.
