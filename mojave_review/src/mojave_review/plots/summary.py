"""Plotly port of cluster_code.make_summary_plots.

Renders a top/bottom pair of per-cluster summary plots for one of four views:

    "Position"      -> distance vs epoch        |  PA vs epoch
    "Flux"          -> I flux vs epoch          |  Tb obs vs epoch  (log y-axis)
    "Polarization"  -> P flux vs epoch (log y)  |  EVPA vs epoch
    "Kinematics"    -> speed vs distance        |  X/Y velocity vectors

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
    "y": "goldenrod", "gray": "gray", "c": "cyan", "k": "black",
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

VIEWS = ("Position", "XY Position", "Flux", "Polarization", "Kinematics")


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
    yunit: str = "",
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
                f"{ylabel_for_hover} %{{y:.4g}}{yunit}<extra></extra>"
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
        fig.add_trace(
            go.Scatter(
                x=s.time, y=show_fit, mode="lines",
                line={"color": color, "width": 1, "dash": "dot"},
                showlegend=False, legendgroup=f"cid_{s.cid}", hoverinfo="skip",
            ),
            row=row, col=col,
        )


def _add_xy_traces(fig: go.Figure, s: _Slice, row: int, col: int,
                   show_legend: bool) -> None:
    """XY Position view: one cluster's centroid track in (x, y) mas relative
    to the core, with 1-sigma x/y error bars. Mirrors _add_cluster_traces'
    legend / selection / use-in-fit conventions but plots xpos vs ypos."""
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


def _motion_fit(s: _Slice) -> _MotionFit | None:
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
    snr_num = px[0]**2 + py[0]**2
    snr_den = np.sqrt(px[0]**2 * corrx[0, 0] + py[0]**2 * corry[0, 0])
    sig = snr_num / snr_den if snr_den > 0 else 0.0
    slow = (np.sqrt(px[0]**2 + py[0]**2) < 0.05
            and np.sqrt(corrx[0, 0] + corry[0, 0]) < 0.05)
    if sig < 3.0 and not slow:
        return None
    pred_x = px[0] * s.time + px[1]
    pred_y = py[0] * s.time + py[1]
    pred_dist = np.sqrt(pred_x**2 + pred_y**2)
    return _MotionFit(
        cid=s.cid, px=px, py=py, pred_dist=pred_dist,
        median_dist=float(np.median(pred_dist)),
        median_x=float(np.median(pred_x)),
        median_y=float(np.median(pred_y)),
        speed=float(np.sqrt(px[0]**2 + py[0]**2)),
    )


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
) -> go.Figure:
    """Build a 2-row (top/bottom) summary figure for one of the four views.

    `vector_scale_factor` multiplies the auto-computed arrow length in the
    Kinematics view. 1.0 = default; <1 shrinks, >1 enlarges. Ignored by other
    views.

    `hide_non_robust` drops the non-robust (slategray) clusters from both the
    plots and the legend. The unassigned cluster (-1) is treated as non-robust
    here and is hidden too; synthetic (>=1000) clusters are NOT affected.
    """
    if view not in VIEWS:
        view = "Position"

    # XY Position is a single plot; the other four views are top/bottom pairs.
    is_xy = view == "XY Position"
    if is_xy:
        fig = make_subplots(
            rows=1, cols=1,
            subplot_titles=("XY position relative to core [mas]",),
        )
    else:
        titles = {
            "Position":     ("Distance from origin [mas]", "Position angle [deg]"),
            "Flux":         ("I flux density [Jy]", "Tb obs [K]"),
            "Polarization": ("Polarized flux [Jy]", "EVPA [deg]"),
            "Kinematics":   ("Apparent speed vs distance", "X/Y velocity vectors"),
        }[view]
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=False,
            subplot_titles=titles,
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
        for s in slices:
            mf = motion_fits.get(s.cid)
            if s.cid >= 0:
                _add_cluster_traces(
                    fig, s, row=1, col=1, ydata=s.dist,
                    show_legend=True,
                    show_fit=mf.pred_dist if mf is not None else None,
                    ylabel_for_hover="dist", error_y_arr=s.sig_dist,
                )
            if s.cid > 0:
                _add_cluster_traces(
                    fig, s, row=2, col=1, ydata=s.pa,
                    show_legend=False, ylabel_for_hover="PA",
                    error_y_arr=s.sig_pa,
                )
        fig.update_xaxes(title_text="Epoch", row=1, col=1)
        fig.update_xaxes(title_text="Epoch", row=2, col=1)
        fig.update_yaxes(title_text="Distance from origin [mas]", row=1, col=1)
        fig.update_yaxes(title_text="PA [deg]", row=2, col=1)

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
        _set_log_yaxis(fig, row=2, col=1, title="Tb obs [K]")

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
        from ._extent import compute_source_extent
        _draw_kinematics(fig, slices, motion_fits,
                         vector_scale_factor=vector_scale_factor,
                         extent=compute_source_extent(cluster_df))
        fig.update_xaxes(title_text="Distance from origin [mas]", row=1, col=1)
        fig.update_yaxes(title_text="Apparent speed [mas/yr]", row=1, col=1)
        # +x to the left (astro convention). scaleanchor + constrain="domain"
        # keeps mas/pixel equal on both axes while still allowing the user
        # to drag a free-form zoom rectangle (panel shrinks instead of
        # expanding the data range to match aspect).
        fig.update_xaxes(title_text="X [mas]", row=2, col=1,
                         constrain="domain")
        fig.update_yaxes(title_text="Y [mas]", row=2, col=1,
                         scaleanchor="x2", scaleratio=1.0,
                         constrain="domain")

    elif view == "XY Position":
        # Per-cluster centroid track in (x, y) mas relative to the core, with
        # 1-sigma x/y error bars. Skip unassigned (-1, NaN positions) and the
        # core (cid 0, the origin — marked by the × below).
        all_x: list[float] = [0.0]
        all_y: list[float] = [0.0]
        for s in slices:
            if s.cid < 1:
                continue
            _add_xy_traces(fig, s, row=1, col=1, show_legend=True)
            all_x.extend(float(v) for v in s.xpos if np.isfinite(v))
            all_y.extend(float(v) for v in s.ypos if np.isfinite(v))
        # Black × at the core (0, 0).
        fig.add_trace(
            go.Scatter(
                x=[0.0], y=[0.0], mode="markers",
                marker={"color": "black", "symbol": "x-thin", "size": 14,
                        "line": {"width": 2, "color": "black"}},
                name="core", showlegend=False,
                hovertemplate="core (0, 0)<extra></extra>",
            ),
            row=1, col=1,
        )
        ax = np.asarray(all_x, dtype=float)
        ay = np.asarray(all_y, dtype=float)
        x_span = float(ax.max() - ax.min()) or 1.0
        y_span = float(ay.max() - ay.min()) or 1.0
        x_lo = float(ax.min()) - 0.1 * x_span
        x_hi = float(ax.max()) + 0.1 * x_span
        y_lo = float(ay.min()) - 0.1 * y_span
        y_hi = float(ay.max()) + 0.1 * y_span
        # +x to the left (astro convention) + equal mas/pixel scale, matching
        # the overlay panel.
        fig.update_xaxes(title_text="X [mas]", range=[x_hi, x_lo],
                         constrain="domain", row=1, col=1)
        fig.update_yaxes(title_text="Y [mas]", range=[y_lo, y_hi],
                         scaleanchor="x", scaleratio=1.0,
                         constrain="domain", row=1, col=1)

    fig.update_layout(
        template="plotly_white",
        height=720,
        margin={"l": 60, "r": 20, "t": 50, "b": 50},
        legend={"title": "Cluster", "tracegroupgap": 4},
        dragmode="zoom",
    )
    return fig


# ---------------------------------------------------------------------------
# Kinematics view (speed vs distance + arrow-headed vectors)
# ---------------------------------------------------------------------------


def _draw_kinematics(
    fig: go.Figure,
    slices: list[_Slice],
    motion_fits: dict[int, "_MotionFit | None"],
    vector_scale_factor: float = 1.0,
    extent: tuple[tuple[float, float], tuple[float, float]] | None = None,
) -> None:
    fits = [mf for mf in motion_fits.values() if mf is not None and mf.cid > 0]
    if not fits:
        return

    # speed vs distance scatter
    for mf in fits:
        color, symbol, filled = _cluster_style(mf.cid, True)
        marker = _marker_style(color, symbol, filled)
        fig.add_trace(
            go.Scatter(
                x=[mf.median_dist], y=[mf.speed],
                mode="markers", marker=marker,
                name=str(mf.cid), legendgroup=f"cid_{mf.cid}",
                showlegend=True,
                hovertemplate=(f"cluster {mf.cid}<br>"
                               "median dist %{x:.2f} mas<br>"
                               "speed %{y:.3f} mas/yr<extra></extra>"),
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
    max_v = float(np.max(np.sqrt(vx**2 + vy**2))) or 1.0
    # Auto-fit: longest arrow ~25% of the panel span; user multiplier on top.
    arrow_scale = 0.25 * span / max_v * float(vector_scale_factor)

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
                               f"vx {sx:.3f} mas/yr<br>vy {sy:.3f} mas/yr<extra></extra>"),
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

    # Base extent: prefer the data-driven cluster box (matches the overlay
    # panel's initial zoom); fall back to the fit-cluster positions. Then
    # always expand by arrow-tip length so vectors don't run off-panel.
    arrow_pad_x = float(np.abs(vx).max() * arrow_scale)
    arrow_pad_y = float(np.abs(vy).max() * arrow_scale)
    if extent is not None:
        (xlo, xhi), (ylo, yhi) = extent
    else:
        pad = 0.15
        xlo = float(all_xs.min()) - pad * xspan
        xhi = float(all_xs.max()) + pad * xspan
        ylo = float(all_ys.min()) - pad * yspan
        yhi = float(all_ys.max()) + pad * yspan
    xlo -= arrow_pad_x
    xhi += arrow_pad_x
    ylo -= arrow_pad_y
    yhi += arrow_pad_y
    fig.update_xaxes(range=[xhi, xlo], row=2, col=1)  # reversed
    fig.update_yaxes(range=[ylo, yhi], row=2, col=1)
