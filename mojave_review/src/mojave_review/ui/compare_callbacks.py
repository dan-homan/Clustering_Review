"""Callbacks for the XVIII-vs-clustering comparison page (``/compare``).

The two panels (``cmp-x`` = XVIII, ``cmp-c`` = clustering) share ONE epoch
axis (a shared stepper above both), so the same epoch is shown on both sides;
a side that lacks the selected epoch renders a blank map. Both overlays use a
shared XY extent so they frame identically. Each panel's summary (epoch-axis)
views get a vertical marker at the shared epoch.

Each panel keeps its own mode selector, FITS toggle, zoom-reset, and
vector-scale. Registered unconditionally; inert unless the compare page is
mounted (suppress_callback_exceptions is on).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import plotly.graph_objects as go
from dash import Input, Output, State, ctx, no_update

from ..data.fits_cache import split_source_band
from ..data.loader import load_bundle
from ..data.source_params import (find_source_params, load_redshifts,
                                  redshift_for)
from ..data.xviii import build_xviii_cluster_df, xviii_epoch_options
from ..plots._extent import compute_source_extent
from ..plots.compare_overlay import build_xviii_overlay
from ..plots.overlay import overlay_figure_for_epoch
from ..plots.summary import (
    build_summary_figure,
    kinematics_vector_stats,
    shared_vector_scale_abs,
)
from .compare import CLUST, XVIII
from .callbacks import _source_from_folder

_EV_TOL = 1e-3   # decimal-year tolerance for matching a side's epoch list

# Beam-repositioning clientside callback, parameterized by graph id (mirrors
# the main overlay's beam callback: Plotly.restyle on zoom/pan, no round-trip).
_BEAM_JS = """
function(relayoutData, beamParams) {
    if (!beamParams || !relayoutData) return window.dash_clientside.no_update;
    var wrapper = document.getElementById('%(gid)s');
    if (!wrapper) return window.dash_clientside.no_update;
    var gd = wrapper.querySelector('.js-plotly-plot');
    if (!gd || !window.Plotly) return window.dash_clientside.no_update;
    var bmaj = beamParams.bmaj, bmin = beamParams.bmin, bpa = beamParams.bpa;
    var idx = beamParams.beam_idx;
    var xRange, yRange;
    if (relayoutData['xaxis.autorange'] !== undefined
        || relayoutData['autosize'] !== undefined) {
        xRange = beamParams.x_extent; yRange = beamParams.y_extent;
    } else if (relayoutData['xaxis.range[0]'] !== undefined
               && relayoutData['yaxis.range[0]'] !== undefined) {
        xRange = [relayoutData['xaxis.range[0]'], relayoutData['xaxis.range[1]']];
        yRange = [relayoutData['yaxis.range[0]'], relayoutData['yaxis.range[1]']];
    } else { return window.dash_clientside.no_update; }
    var xLo = Math.min(xRange[0], xRange[1]), xHi = Math.max(xRange[0], xRange[1]);
    var yLo = Math.min(yRange[0], yRange[1]), yHi = Math.max(yRange[0], yRange[1]);
    var xSpan = xHi - xLo, ySpan = yHi - yLo;
    if (xSpan < 5 * bmaj || ySpan < 5 * bmaj) {
        window.Plotly.restyle(gd, {visible: false}, [idx]);
        return window.dash_clientside.no_update;
    }
    var bx = xHi - 0.08 * xSpan, by = yLo + 0.08 * ySpan;
    var n = 60, ex = new Array(n), ey = new Array(n);
    var cosPa = Math.cos(bpa * Math.PI / 180), sinPa = Math.sin(bpa * Math.PI / 180);
    for (var i = 0; i < n; i++) {
        var t = 2 * Math.PI * i / (n - 1);
        var xr = (bmin / 2) * Math.cos(t), yr = (bmaj / 2) * Math.sin(t);
        ex[i] = bx + xr * cosPa + yr * sinPa;
        ey[i] = by - xr * sinPa + yr * cosPa;
    }
    window.Plotly.restyle(gd, {x: [ex], y: [ey], visible: true}, [idx]);
    return window.dash_clientside.no_update;
}
"""

# Vertical "active epoch" marker on a panel's summary graph, parameterized by
# graph id (mirrors the main page's marker). Draws a line on epoch-axis
# subplots at the shared epoch; clears on non-epoch views / overlay mode.
_MARKER_JS = """
function(epoch, mode, _figure) {
    var wrapper = document.getElementById('%(gid)s');
    if (!wrapper) return window.dash_clientside.no_update;
    var gd = wrapper.querySelector('.js-plotly-plot');
    if (!gd || !window.Plotly) return window.dash_clientside.no_update;
    var epochAxes = {'Position': ['x'], 'Position Angle': ['x'],
                     'Flux': ['x', 'x2']};
    var axes = epochAxes[mode];
    if (!axes || epoch === null || epoch === undefined) {
        window.Plotly.relayout(gd, {shapes: []});
        return window.dash_clientside.no_update;
    }
    var lineStyle = {color: 'rgba(90,90,90,0.65)', width: 1.5};
    var shapes = axes.map(function(ax) {
        var ysuf = ax === 'x' ? 'y' : ax.replace('x', 'y');
        return {type: 'line', xref: ax, yref: ysuf + ' domain',
                x0: epoch, x1: epoch, y0: 0, y1: 1,
                line: lineStyle, layer: 'below'};
    });
    window.Plotly.relayout(gd, {shapes: shapes});
    return window.dash_clientside.no_update;
}
"""


# Axis-lock: when "Lock display areas" is on, mirror one panel's zoom/pan onto
# the other so both always frame the same plotting area. Parameterized by the
# SOURCE and TARGET graph ids.
#
# We do NOT read ranges out of relayoutData: the overlay's equal-aspect
# letterbox (equal_aspect.js) fires its own domain-only Plotly.relayout right
# after the user's zoom, and Dash's relayoutData prop keeps only the LATEST
# event — so the domain event clobbers the range event and a payload-based
# filter sees nothing. Instead, on ANY genuine relayout we read the source
# graph's CURRENT axis ranges from _fullLayout (which the letterbox never
# touches) and copy them to the target. Domains are left alone so each panel
# keeps its own letterbox. A global guard + a near-equal check break the echo
# loop (the target's own relayout, and the letterbox's follow-up domain event).
_SYNC_JS = """
function(relayoutData, lockVal) {
    var ns = window.dash_clientside;
    if (!lockVal || lockVal.length === 0) return ns.no_update;
    if (!relayoutData || window.__cmpAxisSync || !window.Plotly)
        return ns.no_update;
    var sw = document.getElementById('%(source)s');
    var tw = document.getElementById('%(target)s');
    if (!sw || !tw) return ns.no_update;
    var sgd = sw.querySelector('.js-plotly-plot');
    var tgd = tw.querySelector('.js-plotly-plot');
    if (!sgd || !tgd || !sgd._fullLayout || !tgd._fullLayout)
        return ns.no_update;
    var sfl = sgd._fullLayout, tfl = tgd._fullLayout, patch = {}, changed = false;
    Object.keys(sfl).forEach(function (k) {
        if ((k.indexOf('xaxis') === 0 || k.indexOf('yaxis') === 0)
            && sfl[k] && sfl[k].range) {
            var nw = sfl[k].range;
            patch[k + '.range'] = nw.slice();
            var cur = tfl[k] && tfl[k].range;
            if (!cur || Math.abs(cur[0] - nw[0]) > 1e-6
                || Math.abs(cur[1] - nw[1]) > 1e-6) changed = true;
        }
    });
    if (!changed || Object.keys(patch).length === 0) return ns.no_update;
    window.__cmpAxisSync = true;
    setTimeout(function () { window.__cmpAxisSync = false; }, 400);
    var p = window.Plotly.relayout(tgd, patch);
    if (p && p.finally) p.finally(function () { window.__cmpAxisSync = false; });
    return ns.no_update;
}
"""

# On toggling the lock ON, immediately copy the LEFT (XVIII) panels' current
# ranges to the RIGHT (clustering) panels so they start out identical.
_SYNC_ENABLE_JS = """
function(lockVal) {
    var ns = window.dash_clientside;
    if (!lockVal || lockVal.length === 0 || !window.Plotly) return ns.no_update;
    var pairs = [['cmp-x-overlay-graph', 'cmp-c-overlay-graph'],
                 ['cmp-x-summary-graph', 'cmp-c-summary-graph']];
    window.__cmpAxisSync = true;
    setTimeout(function () { window.__cmpAxisSync = false; }, 500);
    pairs.forEach(function (pr) {
        var sw = document.getElementById(pr[0]);
        var tw = document.getElementById(pr[1]);
        if (!sw || !tw) return;
        var sgd = sw.querySelector('.js-plotly-plot');
        var tgd = tw.querySelector('.js-plotly-plot');
        if (!sgd || !tgd || !sgd._fullLayout) return;
        var fl = sgd._fullLayout, patch = {};
        Object.keys(fl).forEach(function (k) {
            if ((k.indexOf('xaxis') === 0 || k.indexOf('yaxis') === 0)
                && fl[k] && fl[k].range) patch[k + '.range'] = fl[k].range.slice();
        });
        if (Object.keys(patch).length) window.Plotly.relayout(tgd, patch);
    });
    return ns.no_update;
}
"""


@dataclass
class _Side:
    src: object
    bundle: object
    summary_df: object
    epochs: list          # [(epoch_val, ep_name)]
    xviii_df: object | None


def register_compare_callbacks(
    app,
    *,
    results_dir: Path,
    recommendations_dir: Path,
    cache_dir: Path,
    reviewer: str,
    admin: bool = False,
    fits_data_dir: Path | None = None,
    xviii_path: str | None = None,
) -> None:
    _redshift_map = load_redshifts(find_source_params(results_dir))

    def _resolve(kind: str, source_folder: str) -> _Side | None:
        if not source_folder:
            return None
        src = _source_from_folder(source_folder)
        if src is None:
            return None
        bundle = load_bundle(source_folder, "current")
        no_band, band = split_source_band(src.source)
        if kind == "xviii":
            xdf = build_xviii_cluster_df(no_band, band, bundle, path=xviii_path)
            return _Side(src, bundle, xdf, xviii_epoch_options(xdf), xdf)
        epochs = []
        if bundle.plotdata is not None:
            for row in bundle.plotdata.epoch_info:
                epochs.append((float(row["epoch_val"]), str(row["epoch_name"])))
        return _Side(src, bundle, bundle.cluster_df, epochs, None)

    def _master_epochs(source_folder: str) -> list[tuple[float, str]]:
        """Union of both sides' epochs (clustering name wins), sorted."""
        merged: dict[float, tuple[float, str]] = {}
        for kind in ("clust", "xviii"):   # clustering first → its names win
            side = _resolve(kind, source_folder)
            if side is None:
                continue
            for ev, name in side.epochs:
                key = round(ev, 4)
                merged.setdefault(key, (ev, name))
        return [merged[k] for k in sorted(merged)]

    def _shared_extent(source_folder: str):
        """Union of both sides' cluster footprints → identical framing."""
        exts = []
        for kind in ("clust", "xviii"):
            side = _resolve(kind, source_folder)
            if side is None or side.summary_df is None or side.summary_df.empty:
                continue
            e = compute_source_extent(side.summary_df)
            if e is not None:
                exts.append(e)
        if not exts:
            return None
        x_lo = min(e[0][0] for e in exts)
        x_hi = max(e[0][1] for e in exts)
        y_lo = min(e[1][0] for e in exts)
        y_hi = max(e[1][1] for e in exts)
        return ((x_lo, x_hi), (y_lo, y_hi))

    def _blank(msg: str) -> go.Figure:
        fig = go.Figure()
        fig.update_layout(template="plotly_white", height=640,
                          margin=dict(l=40, r=20, t=40, b=40),
                          xaxis={"visible": False}, yaxis={"visible": False},
                          annotations=[dict(text=msg, showarrow=False,
                                            xref="paper", yref="paper",
                                            x=0.5, y=0.5,
                                            font=dict(color="#999", size=14))])
        return fig

    # ---- shared epoch slider population -------------------------------
    @app.callback(
        Output("cmp-epoch-slider", "min"),
        Output("cmp-epoch-slider", "max"),
        Output("cmp-epoch-slider", "marks"),
        Output("cmp-epoch-slider", "value"),
        Input("cmp-source-picker", "value"),
        State("cmp-epoch-slider", "value"),
    )
    def _populate_slider(source_folder, current_val):
        epochs = _master_epochs(source_folder)
        if not epochs:
            return 0, 0, {}, 0
        n = len(epochs)
        step = max(1, n // 8)
        marks = {i: f"{epochs[i][0]:.0f}" for i in range(0, n, step)}
        marks[n - 1] = f"{epochs[n - 1][0]:.0f}"
        new_val = current_val if current_val is not None and 0 <= current_val < n else 0
        return 0, n - 1, marks, new_val

    # ---- shared ◀ / ▶ stepping ----------------------------------------
    @app.callback(
        Output("cmp-epoch-slider", "value", allow_duplicate=True),
        Input("cmp-epoch-prev", "n_clicks"),
        Input("cmp-epoch-next", "n_clicks"),
        State("cmp-epoch-slider", "value"),
        State("cmp-epoch-slider", "min"),
        State("cmp-epoch-slider", "max"),
        prevent_initial_call=True,
    )
    def _step(_p, _n, value, lo, hi):
        if value is None or lo is None or hi is None:
            return no_update
        trig = ctx.triggered_id
        if trig == "cmp-epoch-prev":
            return max(int(lo), int(value) - 1)
        if trig == "cmp-epoch-next":
            return min(int(hi), int(value) + 1)
        return no_update

    # ---- shared epoch label + active-epoch store ----------------------
    @app.callback(
        Output("cmp-epoch-label", "children"),
        Output("cmp-active-epoch", "data"),
        Input("cmp-source-picker", "value"),
        Input("cmp-epoch-slider", "value"),
    )
    def _label(source_folder, epoch_int):
        epochs = _master_epochs(source_folder)
        if not epochs or epoch_int is None:
            return "", None
        i = int(epoch_int)
        if not (0 <= i < len(epochs)):
            return "", None
        ev, ename = epochs[i]
        return f"{ename}  ·  {ev:.4f}", ev

    def _register_panel(prefix: str, kind: str) -> None:
        # --- mode toggle: overlay vs summary container ------------------
        @app.callback(
            Output(f"{prefix}-overlay-container", "style"),
            Output(f"{prefix}-summary-container", "style"),
            Input(f"{prefix}-mode", "value"),
        )
        def _toggle_mode(mode):
            if mode == "overlay":
                return {"display": "block"}, {"display": "none"}
            return {"display": "none"}, {"display": "block"}

        # --- vector-scale row visibility (Kinematics only) --------------
        @app.callback(
            Output(f"{prefix}-vector-scale-row", "style"),
            Input(f"{prefix}-mode", "value"),
        )
        def _toggle_scale(mode):
            base = {"alignItems": "center", "padding": "0.25em 0.5em"}
            base["display"] = "flex" if mode == "Kinematics" else "none"
            return base

        # --- summary figure --------------------------------------------
        @app.callback(
            Output(f"{prefix}-summary-graph", "figure"),
            Input("cmp-source-picker", "value"),
            Input(f"{prefix}-mode", "value"),
            Input(f"{prefix}-vector-scale", "value"),
        )
        def _summary(source_folder, mode, vector_scale):
            if mode == "overlay":
                return no_update
            side = _resolve(kind, source_folder)
            if side is None or side.summary_df is None or side.summary_df.empty:
                return _blank("No data for this view.")
            z = redshift_for(_redshift_map, side.src.source) or 0.0
            # Kinematics: use ONE absolute arrow scale computed over BOTH sides'
            # fits, so a vector of a given length means the same speed on the
            # XVIII and clustering panels (their independent auto-scales used to
            # differ — confusing). None for other views / when neither side has
            # a motion fit (then each panel auto-scales as before).
            vscale_abs = None
            if mode == "Kinematics":
                stats = []
                for k in ("clust", "xviii"):
                    other = _resolve(k, source_folder)
                    if other is not None and other.summary_df is not None \
                            and not other.summary_df.empty:
                        stats.append(kinematics_vector_stats(other.summary_df))
                vscale_abs = shared_vector_scale_abs(stats)
            fig = build_summary_figure(
                side.summary_df, view=mode, z=z,
                vector_scale_factor=vector_scale or 1.0,
                source_label=side.src.source,
                vector_scale_abs=vscale_abs,
            )
            fig.update_layout(uirevision=f"{prefix}:{source_folder}:{mode}")
            return fig

        # --- reset-view counter ----------------------------------------
        @app.callback(
            Output(f"{prefix}-reset-counter", "data"),
            Input(f"{prefix}-reset", "n_clicks"),
            State(f"{prefix}-reset-counter", "data"),
            prevent_initial_call=True,
        )
        def _bump_reset(_n, cur):
            return int(cur or 0) + 1

        # --- overlay figure (shared epoch + shared extent) -------------
        @app.callback(
            Output(f"{prefix}-overlay-graph", "figure"),
            Output(f"{prefix}-beam-params", "data"),
            Input("cmp-source-picker", "value"),
            Input("cmp-epoch-slider", "value"),
            Input("cmp-use-fits", "value"),
            Input(f"{prefix}-reset-counter", "data"),
        )
        def _overlay(source_folder, epoch_int, use_fits_val, reset_counter):
            epochs = _master_epochs(source_folder)
            side = _resolve(kind, source_folder)
            if side is None or not epochs or epoch_int is None:
                return _blank("Select a source."), None
            i = int(epoch_int)
            if not (0 <= i < len(epochs)):
                return _blank(""), None
            ev = epochs[i][0]
            image_source = "fits" if use_fits_val else "synthesize"
            no_band, band = split_source_band(side.src.source)
            uirev = f"{prefix}:{source_folder}:{reset_counter or 0}"
            extent = _shared_extent(source_folder)
            if kind == "xviii":
                # build_xviii_overlay returns a blank map when this epoch has
                # no XVIII features (i.e. clustering-only epoch).
                return build_xviii_overlay(
                    side.bundle, side.xviii_df, ev, cache_dir,
                    no_band, band, fits_data_dir=fits_data_dir,
                    image_source=image_source, uirevision=uirev,
                    source_label=side.src.source, extent=extent,
                )
            # clustering: map shared epoch -> epoch_info index; blank if absent.
            j = next((k for k, (e, _) in enumerate(side.epochs)
                      if abs(e - ev) <= _EV_TOL), None)
            if j is None:
                return _blank("No clustering data for this epoch."), None
            return overlay_figure_for_epoch(
                side.bundle, j, cache_dir,
                source_no_band=no_band, band=band,
                fits_data_dir=fits_data_dir,
                image_source=image_source, uirevision=uirev,
                source_label=side.src.source, extent=extent,
            )

        # --- beam repositioning (clientside) ---------------------------
        app.clientside_callback(
            _BEAM_JS % {"gid": f"{prefix}-overlay-graph"},
            Output(f"{prefix}-beam-params", "data", allow_duplicate=True),
            Input(f"{prefix}-overlay-graph", "relayoutData"),
            State(f"{prefix}-beam-params", "data"),
            prevent_initial_call=True,
        )

        # --- vertical epoch marker on the summary graph (clientside) ---
        app.clientside_callback(
            _MARKER_JS % {"gid": f"{prefix}-summary-graph"},
            Output(f"{prefix}-epoch-line-dummy", "data"),
            Input("cmp-active-epoch", "data"),
            Input(f"{prefix}-mode", "value"),
            Input(f"{prefix}-summary-graph", "figure"),
            prevent_initial_call=True,
        )

    _register_panel(XVIII, "xviii")
    _register_panel(CLUST, "clust")

    # ---- axis lock: mirror zoom/pan between the two panels ------------
    for src_gid, tgt_gid, dummy in (
        ("cmp-x-overlay-graph", "cmp-c-overlay-graph", "cmp-sync-x-ov"),
        ("cmp-c-overlay-graph", "cmp-x-overlay-graph", "cmp-sync-c-ov"),
        ("cmp-x-summary-graph", "cmp-c-summary-graph", "cmp-sync-x-sum"),
        ("cmp-c-summary-graph", "cmp-x-summary-graph", "cmp-sync-c-sum"),
    ):
        app.clientside_callback(
            _SYNC_JS % {"source": src_gid, "target": tgt_gid},
            Output(dummy, "data"),
            Input(src_gid, "relayoutData"),
            State("cmp-lock-axes", "value"),
            prevent_initial_call=True,
        )

    app.clientside_callback(
        _SYNC_ENABLE_JS,
        Output("cmp-sync-enable", "data"),
        Input("cmp-lock-axes", "value"),
        prevent_initial_call=True,
    )
