"""Dash callbacks wiring the UI to the data + plot functions."""

from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, no_update

from ..data.fits_cache import split_source_band
from ..data.loader import _SOURCE_DIR_RE, SourceRef, list_models, load_bundle
from ..plots.overlay import overlay_figure_for_epoch
from ..plots.summary import build_summary_figure


def register_callbacks(
    app: Dash,
    *,
    results_dir: Path,
    recommendations_dir: Path,
    cache_dir: Path,
    reviewer: str,
) -> None:

    # ---- model picker (depends on source) --------------------------------
    @app.callback(
        Output("model-picker", "options"),
        Output("model-picker", "value"),
        Input("source-picker", "value"),
    )
    def _populate_models(source_folder: str | None):
        if not source_folder:
            return [], None
        src = _source_from_folder(source_folder)
        if src is None:
            return [], None
        models = list_models(src)
        opts = [{"label": mf.label, "value": mf.key} for mf in models]
        return opts, (opts[0]["value"] if opts else None)

    # ---- summary figure --------------------------------------------------
    @app.callback(
        Output("summary-graph", "figure"),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
        Input("view-picker", "value"),
        Input("vector-scale", "value"),
    )
    def _refresh_summary(source_folder, model_key, view, vector_scale):
        if not source_folder or not model_key:
            return go.Figure()
        bundle = load_bundle(source_folder, model_key)
        return build_summary_figure(
            bundle.cluster_df, view=view,
            vector_scale_factor=vector_scale or 1.0,
        )

    # ---- vector-scale visibility -----------------------------------------
    @app.callback(
        Output("vector-scale-row", "style"),
        Input("view-picker", "value"),
    )
    def _toggle_scale_row(view):
        base = {"alignItems": "center", "padding": "0.25em 1em", "fontSize": "0.9em"}
        return {**base, "display": "flex" if view == "Kinematics" else "none"}

    # ---- epoch slider population (depends on source + model) -------------
    @app.callback(
        Output("epoch-slider", "min"),
        Output("epoch-slider", "max"),
        Output("epoch-slider", "marks"),
        Output("epoch-slider", "value"),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
        State("epoch-slider", "value"),
    )
    def _populate_epoch_slider(source_folder, model_key, current_val):
        if not source_folder or not model_key:
            return 0, 0, {}, 0
        bundle = load_bundle(source_folder, model_key)
        if bundle.plotdata is None:
            return 0, 0, {}, 0
        n = len(bundle.plotdata.epoch_info)
        if n == 0:
            return 0, 0, {}, 0
        # one mark every ~6 epochs, labeled with the integer year
        step = max(1, n // 6)
        marks = {
            int(i): f"{bundle.plotdata.epoch_info[i]['epoch_val']:.0f}"
            for i in range(0, n, step)
        }
        marks[n - 1] = f"{bundle.plotdata.epoch_info[n - 1]['epoch_val']:.0f}"
        # preserve current value if still valid; otherwise reset to 0
        new_val = current_val if current_val is not None and 0 <= current_val < n else 0
        return 0, n - 1, marks, new_val

    # ---- ◀ / ▶ buttons step the slider -----------------------------------
    @app.callback(
        Output("epoch-slider", "value", allow_duplicate=True),
        Input("epoch-prev", "n_clicks"),
        Input("epoch-next", "n_clicks"),
        State("epoch-slider", "value"),
        State("epoch-slider", "min"),
        State("epoch-slider", "max"),
        prevent_initial_call=True,
    )
    def _step_epoch(_prev_n, _next_n, value, lo, hi):
        if value is None or lo is None or hi is None:
            return no_update
        trig = ctx.triggered_id
        if trig == "epoch-prev":
            return max(int(lo), int(value) - 1)
        if trig == "epoch-next":
            return min(int(hi), int(value) + 1)
        return no_update

    # ---- epoch label readout ---------------------------------------------
    @app.callback(
        Output("epoch-label", "children"),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
        Input("epoch-slider", "value"),
    )
    def _epoch_label(source_folder, model_key, epoch_int):
        if not source_folder or not model_key or epoch_int is None:
            return ""
        bundle = load_bundle(source_folder, model_key)
        if bundle.plotdata is None or epoch_int >= len(bundle.plotdata.epoch_info):
            return ""
        info = bundle.plotdata.epoch_info[int(epoch_int)]
        return f"{info['epoch_name']}  ·  {info['epoch_val']:.4f}"

    # ---- overlay figure --------------------------------------------------
    @app.callback(
        Output("overlay-graph", "figure"),
        Output("beam-params", "data"),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
        Input("epoch-slider", "value"),
    )
    def _refresh_overlay(source_folder, model_key, epoch_int):
        if not source_folder or not model_key or epoch_int is None:
            return go.Figure(), None
        src = _source_from_folder(source_folder)
        if src is None:
            return go.Figure(), None
        bundle = load_bundle(source_folder, model_key)
        source_no_band, band = split_source_band(src.source)
        return overlay_figure_for_epoch(
            bundle, int(epoch_int), cache_dir,
            source_no_band=source_no_band, band=band,
        )

    # ---- clientside: reposition beam ellipse on zoom/pan -----------------
    # Runs in the browser. Uses Plotly.restyle directly on the graph div so
    # Dash never replaces the figure object — that's important because a
    # figure-output replacement here was clobbering uirevision-driven zoom
    # persistence on epoch change. Returning no_update keeps the dummy
    # output untouched.
    app.clientside_callback(
        """
        function(relayoutData, beamParams) {
            if (!beamParams || !relayoutData) {
                return window.dash_clientside.no_update;
            }
            var wrapper = document.getElementById('overlay-graph');
            if (!wrapper) return window.dash_clientside.no_update;
            var gd = wrapper.querySelector('.js-plotly-plot');
            if (!gd || !window.Plotly) return window.dash_clientside.no_update;

            var bmaj = beamParams.bmaj, bmin = beamParams.bmin, bpa = beamParams.bpa;
            var idx = beamParams.beam_idx;

            // Pick the current viewport. relayoutData may carry an explicit
            // range from a zoom/pan, or {xaxis.autorange: true} on reset.
            var xRange, yRange;
            if (relayoutData['xaxis.autorange'] !== undefined
                || relayoutData['autosize'] !== undefined) {
                xRange = beamParams.x_extent;
                yRange = beamParams.y_extent;
            } else if (relayoutData['xaxis.range[0]'] !== undefined
                       && relayoutData['yaxis.range[0]'] !== undefined) {
                xRange = [relayoutData['xaxis.range[0]'],
                          relayoutData['xaxis.range[1]']];
                yRange = [relayoutData['yaxis.range[0]'],
                          relayoutData['yaxis.range[1]']];
            } else {
                return window.dash_clientside.no_update;
            }

            var xLo = Math.min(xRange[0], xRange[1]);
            var xHi = Math.max(xRange[0], xRange[1]);
            var yLo = Math.min(yRange[0], yRange[1]);
            var yHi = Math.max(yRange[0], yRange[1]);
            var xSpan = xHi - xLo;
            var ySpan = yHi - yLo;

            if (xSpan < 5 * bmaj || ySpan < 5 * bmaj) {
                window.Plotly.restyle(gd, {visible: false}, [idx]);
                return window.dash_clientside.no_update;
            }

            // Place at high-x (visually LEFT, since +x is reversed) and
            // low-y corner of the current viewport, with a small inset.
            var bx = xHi - 0.08 * xSpan;
            var by = yLo + 0.08 * ySpan;
            var n = 60;
            var ex = new Array(n), ey = new Array(n);
            var cosPa = Math.cos(bpa * Math.PI / 180);
            var sinPa = Math.sin(bpa * Math.PI / 180);
            for (var i = 0; i < n; i++) {
                var t = 2 * Math.PI * i / (n - 1);
                var xr = (bmin / 2) * Math.cos(t);
                var yr = (bmaj / 2) * Math.sin(t);
                ex[i] = bx + xr * cosPa - yr * sinPa;
                ey[i] = by + xr * sinPa + yr * cosPa;
            }
            // Plotly.restyle update values must be wrapped in arrays
            // (one element per trace being updated).
            window.Plotly.restyle(gd,
                {x: [ex], y: [ey], visible: true}, [idx]);
            return window.dash_clientside.no_update;
        }
        """,
        Output("beam-params", "data", allow_duplicate=True),
        Input("overlay-graph", "relayoutData"),
        State("beam-params", "data"),
        prevent_initial_call=True,
    )


def _source_from_folder(folder_str: str) -> SourceRef | None:
    folder = Path(folder_str)
    m = _SOURCE_DIR_RE.match(folder.name)
    if not m:
        return None
    return SourceRef(
        source=m.group("source"),
        epoch_min=float(m.group("emin")),
        epoch_max=float(m.group("emax")),
        folder=folder,
    )
