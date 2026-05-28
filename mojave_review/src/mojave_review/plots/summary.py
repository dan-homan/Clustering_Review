"""Plotly port of cluster_code.make_summary_plots.

Renders a top/bottom pair of per-cluster summary plots for one of four views:

    "Position"      -> distance vs epoch        |  PA vs epoch
    "Flux"          -> log10(I flux) vs epoch   |  log10(Tb obs) vs epoch
    "Polarization"  -> log10(P flux) vs epoch   |  EVPA vs epoch
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

VIEWS = ("Position", "Flux", "Polarization", "Kinematics")


def _cluster_style(cid: int, robust: bool) -> tuple[str, str, bool]:
    if cid >= 1000 or cid < 0:
        return "black", "cross-thin", False
    if not robust:
        idx = cid % len(_CL_MARKERS)
        return "cyan", _MPL_TO_PLOTLY_SYMBOL.get(_CL_MARKERS[idx], "circle"), _CL_FILL[idx] == "full"
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

        out.append(_Slice(
            cid=int(cid), robust=robust, time=time,
            xpos=xpos, ypos=ypos, dist=dist, pa=pa, size=size,
            flux=flux, pflux=pflux, evpa=evpa, tb_obs=tb_obs,
            use_in_fit=use_in_fit, selected=selected,
        ))
    return out


# ---------------------------------------------------------------------------
# Trace helpers
# ---------------------------------------------------------------------------


def _marker_style(color: str, symbol: str, filled: bool) -> dict:
    if filled:
        return {"color": color, "symbol": symbol, "size": 8,
                "line": {"width": 1, "color": color}}
    open_symbol = symbol if symbol.endswith("-open") else f"{symbol}-open"
    return {"color": color, "symbol": open_symbol, "size": 8,
            "line": {"width": 1.5, "color": color}}


def _customdata(s: _Slice) -> np.ndarray:
    return np.stack([np.full_like(s.time, s.cid, dtype=float), s.time], axis=-1)


def _add_cluster_traces(
    fig: go.Figure, s: _Slice, row: int, col: int, ydata: np.ndarray,
    show_legend: bool, show_fit: np.ndarray | None = None,
    ylabel_for_hover: str = "y",
) -> None:
    color, symbol, filled = _cluster_style(s.cid, s.robust)
    marker = _marker_style(color, symbol, filled)

    fig.add_trace(
        go.Scattergl(
            x=s.time, y=ydata,
            mode="lines+markers",
            line={"color": color, "width": 1, "dash": "dot"},
            marker=marker,
            name=str(s.cid) if s.robust and s.cid >= 0 else None,
            legendgroup=f"cid_{s.cid}",
            showlegend=show_legend and s.robust and 0 <= s.cid < 1000,
            customdata=_customdata(s),
            hovertemplate=(
                f"cluster %{{customdata[0]:.0f}}<br>"
                f"epoch %{{customdata[1]:.4f}}<br>"
                f"{ylabel_for_hover} %{{y:.4g}}<extra></extra>"
            ),
        ),
        row=row, col=col,
    )

    excl = ~s.use_in_fit
    if np.any(excl):
        fig.add_trace(
            go.Scattergl(
                x=s.time[excl], y=ydata[excl],
                mode="markers",
                marker={"color": "black", "symbol": "line-ne",
                        "size": 12, "line": {"width": 2, "color": "black"}},
                showlegend=False, legendgroup=f"cid_{s.cid}", hoverinfo="skip",
            ),
            row=row, col=col,
        )

    if np.any(s.selected):
        fig.add_trace(
            go.Scattergl(
                x=s.time[s.selected], y=ydata[s.selected],
                mode="markers",
                marker={"color": "rgba(0,0,0,0)", "symbol": "diamond-open",
                        "size": 14, "line": {"width": 2, "color": "gold"}},
                showlegend=False, legendgroup=f"cid_{s.cid}", hoverinfo="skip",
            ),
            row=row, col=col,
        )

    if show_fit is not None:
        fig.add_trace(
            go.Scattergl(
                x=s.time, y=show_fit, mode="lines",
                line={"color": color, "width": 1, "dash": "dot"},
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
) -> go.Figure:
    """Build a 2-row (top/bottom) summary figure for one of the four views.

    `vector_scale_factor` multiplies the auto-computed arrow length in the
    Kinematics view. 1.0 = default; <1 shrinks, >1 enlarges. Ignored by other
    views.
    """
    if view not in VIEWS:
        view = "Position"

    titles = {
        "Position":     ("Distance from origin [mas]", "Position angle [deg]"),
        "Flux":         ("log10(I flux density) [Jy]", "log10(Tb obs) [K]"),
        "Polarization": ("log10(Polarized flux) [Jy]", "EVPA [deg]"),
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
                    ylabel_for_hover="dist",
                )
            if s.cid > 0:
                _add_cluster_traces(
                    fig, s, row=2, col=1, ydata=s.pa,
                    show_legend=False, ylabel_for_hover="PA",
                )
        fig.update_xaxes(title_text="Epoch", row=1, col=1)
        fig.update_xaxes(title_text="Epoch", row=2, col=1)
        fig.update_yaxes(title_text="Distance from origin [mas]", row=1, col=1)
        fig.update_yaxes(title_text="PA [deg]", row=2, col=1)

    elif view == "Flux":
        for s in slices:
            with np.errstate(divide="ignore", invalid="ignore"):
                logflux = np.log10(s.flux)
                logtb = np.log10(s.tb_obs)
            _add_cluster_traces(
                fig, s, row=1, col=1, ydata=logflux,
                show_legend=True, ylabel_for_hover="log10(I)",
            )
            if s.cid >= 0:
                _add_cluster_traces(
                    fig, s, row=2, col=1, ydata=logtb,
                    show_legend=False, ylabel_for_hover="log10(Tb)",
                )
        fig.update_xaxes(title_text="Epoch", row=1, col=1)
        fig.update_xaxes(title_text="Epoch", row=2, col=1)
        fig.update_yaxes(title_text="log10(I flux) [Jy]", row=1, col=1)
        fig.update_yaxes(title_text="log10(Tb obs) [K]", row=2, col=1)

    elif view == "Polarization":
        for s in slices:
            if s.cid < 0:
                continue
            with np.errstate(divide="ignore", invalid="ignore"):
                logp = np.log10(s.pflux)
            _add_cluster_traces(
                fig, s, row=1, col=1, ydata=logp,
                show_legend=True, ylabel_for_hover="log10(P)",
            )
            _add_cluster_traces(
                fig, s, row=2, col=1, ydata=s.evpa,
                show_legend=False, ylabel_for_hover="EVPA",
            )
        fig.update_xaxes(title_text="Epoch", row=1, col=1)
        fig.update_xaxes(title_text="Epoch", row=2, col=1)
        fig.update_yaxes(title_text="log10(P flux) [Jy]", row=1, col=1)
        fig.update_yaxes(title_text="EVPA [deg]", row=2, col=1)

    elif view == "Kinematics":
        _draw_kinematics(fig, slices, motion_fits,
                         vector_scale_factor=vector_scale_factor)
        fig.update_xaxes(title_text="Distance from origin [mas]", row=1, col=1)
        fig.update_yaxes(title_text="Apparent speed [mas/yr]", row=1, col=1)
        # +x to the left (astro convention) — explicit range below reverses it.
        # scaleanchor enforces equal data-per-pixel on both axes.
        fig.update_xaxes(title_text="X [mas]", row=2, col=1,
                         constrain="domain")
        fig.update_yaxes(title_text="Y [mas]", row=2, col=1,
                         scaleanchor="x2", scaleratio=1.0,
                         constrain="domain")

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

    # Pad axes to fit arrow tips, then set x reversed (+x left).
    arrow_pad_x = float(np.abs(vx).max() * arrow_scale)
    arrow_pad_y = float(np.abs(vy).max() * arrow_scale)
    pad = 0.15
    xlo = float(all_xs.min()) - pad * xspan - arrow_pad_x
    xhi = float(all_xs.max()) + pad * xspan + arrow_pad_x
    ylo = float(all_ys.min()) - pad * yspan - arrow_pad_y
    yhi = float(all_ys.max()) + pad * yspan + arrow_pad_y
    fig.update_xaxes(range=[xhi, xlo], row=2, col=1)  # reversed
    fig.update_yaxes(range=[ylo, yhi], row=2, col=1)
