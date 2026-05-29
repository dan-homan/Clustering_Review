"""Dash callbacks wiring the UI to the data + plot functions."""

from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, no_update

from ..data.fits_cache import split_source_band
from ..data.loader import _SOURCE_DIR_RE, SourceRef, list_models, load_bundle
from ..plots.overlay import overlay_figure_for_epoch
from ..plots.summary import build_summary_figure
from ..recommendations.apply import apply_recommendation
from ..recommendations.store import (
    list_other_reviewer_slugs, load_recommendation_by_slug, reviewer_slug,
)
from . import recommendations_callbacks
from .recommendations_callbacks import build_rec_from_ui_state


def register_callbacks(
    app: Dash,
    *,
    results_dir: Path,
    recommendations_dir: Path,
    cache_dir: Path,
    reviewer: str,
    admin: bool = False,
    fits_data_dir: Path | None = None,
) -> None:
    # Recommendations tab — load + autosave behavior.
    recommendations_callbacks.register(
        app,
        results_dir=results_dir,
        recommendations_dir=recommendations_dir,
        reviewer=reviewer,
        admin=admin,
    )

    # Local helpers that resolve a (source, model) pair into a possibly
    # rec-applied DataFrame, closing over recommendations_dir + reviewer.
    def _effective_model_for_load(model_key: str) -> str:
        return "current" if (model_key or "").startswith("rec:") else model_key

    def _resolve_df_for_plot(
        source_folder: str, source_name: str, model_key: str,
        *, visualize_val, cluster_rows, edits, no_changes_val,
    ):
        eff_key = _effective_model_for_load(model_key)
        bundle = load_bundle(source_folder, eff_key)
        df = bundle.cluster_df.copy()
        if (model_key or "").startswith("rec:"):
            slug = model_key[4:]
            rec = load_recommendation_by_slug(
                recommendations_dir, source_name, "current", slug,
            )
            if rec is not None:
                df = apply_recommendation(df, rec)
        elif model_key == "current" and visualize_val:
            own_rec = build_rec_from_ui_state(
                source=source_name, model="current", reviewer=reviewer,
                source_comment=None,
                no_robustness_changes=bool(no_changes_val),
                cluster_rows=cluster_rows, epoch_rows=None,
                edits=edits,
            )
            df = apply_recommendation(df, own_rec)
        return df

    # ---- model picker (depends on source) --------------------------------
    # Options include:
    #   - "current"                        — the live model
    #   - "backup_NNN"                     — saved backup runs
    #   - "rec:<slug>"                     — other reviewers' pending recommendations
    #                                         applied on top of "current"
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
        # Other reviewers' rec files at <recs>/<source>/current/<slug>.json,
        # excluding the current user's own slug.
        own_slug = reviewer_slug(reviewer)
        for slug in list_other_reviewer_slugs(recommendations_dir, src.source, own_slug):
            opts.append({"label": f"Rec: {slug}", "value": f"rec:{slug}"})
        return opts, (opts[0]["value"] if opts else None)

    # ---- summary figure --------------------------------------------------
    @app.callback(
        Output("summary-graph", "figure"),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
        Input("view-picker", "value"),
        Input("vector-scale", "value"),
        Input("selection-store", "data"),
        Input("visualize-checkbox", "value"),
        Input("cluster-feedback-table", "data"),
        Input("edits-store", "data"),
        Input("no-changes-checkbox", "value"),
    )
    def _refresh_summary(source_folder, model_key, view, vector_scale,
                         selection, visualize_val, cluster_rows, edits,
                         no_changes_val):
        if not source_folder or not model_key:
            return go.Figure()
        src = _source_from_folder(source_folder)
        if src is None:
            return go.Figure()
        df = _resolve_df_for_plot(
            source_folder, src.source, model_key,
            visualize_val=visualize_val, cluster_rows=cluster_rows,
            edits=edits, no_changes_val=no_changes_val,
        )
        # Apply the user's current selection to the dataframe so the existing
        # gold open-diamond overlay highlights the chosen points across views.
        df["select"] = False
        if selection:
            sel_keys = {(int(s["cid"]), round(float(s["epoch"]), 4))
                        for s in selection
                        if s.get("cid") is not None and s.get("epoch") is not None}
            if sel_keys:
                cids = df["clusterID"].astype(int).to_numpy()
                eps = df["epoch"].round(4).to_numpy()
                mask = [(int(c), float(e)) in sel_keys for c, e in zip(cids, eps)]
                df.loc[mask, "select"] = True
        return build_summary_figure(
            df, view=view,
            vector_scale_factor=vector_scale or 1.0,
        )

    # ---- selection store: click toggles, box/lasso replaces --------------
    # Both events fire only on Position / Flux / Polarization views. The
    # summary graph carries customdata=[cid, epoch] on every cluster point,
    # so identifying selected points is just reading that field.
    #
    # We must reset clickData to None after each handled click — Dash only
    # fires the callback when the Input *value* changes, and re-clicking
    # the same point would otherwise yield an identical clickData dict and
    # the toggle would silently fail to deselect on the second click.
    @app.callback(
        Output("selection-store", "data", allow_duplicate=True),
        Output("summary-graph", "clickData", allow_duplicate=True),
        Input("summary-graph", "clickData"),
        State("selection-store", "data"),
        State("view-picker", "value"),
        prevent_initial_call=True,
    )
    def _toggle_on_click(click_data, current, view):
        if view == "Kinematics" or not click_data:
            return no_update, no_update
        pts = click_data.get("points") or []
        out = list(current or [])
        existing = {(int(s["cid"]), round(float(s["epoch"]), 4))
                    for s in out
                    if s.get("cid") is not None and s.get("epoch") is not None}
        changed = False
        for p in pts:
            cd = p.get("customdata")
            if not cd or len(cd) < 2:
                continue
            try:
                cid = int(cd[0])
                epoch = round(float(cd[1]), 4)
            except (TypeError, ValueError):
                continue
            key = (cid, epoch)
            if key in existing:
                out = [s for s in out
                       if not (int(s["cid"]) == cid
                               and round(float(s["epoch"]), 4) == epoch)]
                existing.discard(key)
            else:
                out.append({"cid": cid, "epoch": epoch})
                existing.add(key)
            changed = True
        # Reset clickData so a repeat click on the same point fires again.
        return (out if changed else no_update), None

    @app.callback(
        Output("selection-store", "data", allow_duplicate=True),
        Input("summary-graph", "selectedData"),
        State("view-picker", "value"),
        prevent_initial_call=True,
    )
    def _replace_on_box(selected_data, view):
        if view == "Kinematics":
            return no_update
        # Plotly sends `null` when the user clears via the modebar — wipe.
        if selected_data is None:
            return []
        pts = selected_data.get("points") or []
        out: list[dict] = []
        seen: set[tuple[int, float]] = set()
        for p in pts:
            cd = p.get("customdata")
            if not cd or len(cd) < 2:
                continue
            try:
                cid = int(cd[0])
                epoch = round(float(cd[1]), 4)
            except (TypeError, ValueError):
                continue
            key = (cid, epoch)
            if key in seen:
                continue
            seen.add(key)
            out.append({"cid": cid, "epoch": epoch})
        return out

    # ---- visualize-checkbox state managed by the current model -----------
    # model=current  -> user-controllable, default off
    # model=backup_* -> disabled, off  (no recs apply to a backup)
    # model=rec:<>   -> disabled, on   (visualization is the whole point)
    @app.callback(
        Output("visualize-checkbox", "options"),
        Output("visualize-checkbox", "value", allow_duplicate=True),
        Input("model-picker", "value"),
        prevent_initial_call=True,
    )
    def _manage_visualize_checkbox(model_key):
        if not model_key:
            return [{"label": " Visualize recommendations",
                     "value": "yes", "disabled": True}], []
        if model_key == "current":
            return [{"label": " Visualize recommendations",
                     "value": "yes", "disabled": False}], no_update
        if model_key.startswith("rec:"):
            return [{"label": " Visualize recommendations",
                     "value": "yes", "disabled": True}], ["yes"]
        # backup_NNN or unknown
        return [{"label": " Visualize recommendations",
                 "value": "yes", "disabled": True}], []

    # ---- clear selection when source or model changes --------------------
    @app.callback(
        Output("selection-store", "data", allow_duplicate=True),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
        prevent_initial_call=True,
    )
    def _clear_selection_on_swap(*_):
        return []

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
        Input("visualize-checkbox", "value"),
        Input("cluster-feedback-table", "data"),
        Input("edits-store", "data"),
        Input("no-changes-checkbox", "value"),
        Input("show-3sigma-checkbox", "value"),
    )
    def _refresh_overlay(source_folder, model_key, epoch_int,
                         visualize_val, cluster_rows, edits, no_changes_val,
                         show_3sigma_val):
        if not source_folder or not model_key or epoch_int is None:
            return go.Figure(), None
        src = _source_from_folder(source_folder)
        if src is None:
            return go.Figure(), None
        # The overlay needs the npz (cc_data / cc_labels / epoch_info), which
        # only the "current" bundle has. For rec:<slug>, fall back to current.
        eff_key = _effective_model_for_load(model_key)
        bundle = load_bundle(source_folder, eff_key)
        source_no_band, band = split_source_band(src.source)
        # If recommendations are being visualised, swap in the modified
        # cluster_df before rendering. The npz fields stay untouched.
        applied_df = _resolve_df_for_plot(
            source_folder, src.source, model_key,
            visualize_val=visualize_val, cluster_rows=cluster_rows,
            edits=edits, no_changes_val=no_changes_val,
        )
        if not applied_df.equals(bundle.cluster_df):
            # Construct a shallow shim bundle with the patched df. The
            # SourceBundle dataclass is mutable; cheap to copy fields.
            from dataclasses import replace as _replace
            bundle = _replace(bundle, cluster_df=applied_df)
        return overlay_figure_for_epoch(
            bundle, int(epoch_int), cache_dir,
            source_no_band=source_no_band, band=band,
            fits_data_dir=fits_data_dir,
            show_3sigma=bool(show_3sigma_val),
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
            // Astronomical PA: 0 = major along +y (north), positive rotates
            // CCW from north in the displayed plot. The overlay axis has
            // +x reversed, so display-CCW is data-CW — sin signs flipped
            // accordingly. Keep in lock-step with plots/overlay._ellipse_xy.
            for (var i = 0; i < n; i++) {
                var t = 2 * Math.PI * i / (n - 1);
                var xr = (bmin / 2) * Math.cos(t);
                var yr = (bmaj / 2) * Math.sin(t);
                ex[i] = bx + xr * cosPa + yr * sinPa;
                ey[i] = by - xr * sinPa + yr * cosPa;
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


