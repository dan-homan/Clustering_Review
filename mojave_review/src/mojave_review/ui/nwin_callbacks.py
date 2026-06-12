"""Callbacks for the admin Window-N review panel (the --editN replacement).

Registered only when --admin. The panel's data flow:

    source change ──▶ _nwin_load_source: WindowMeta + choices from disk
    window slider ──▶ _nwin_window_changed: N + epoch sliders re-seeded
    N / epoch     ──▶ _nwin_overlay: (window, N) bundle → overlay figure
    record/clear  ──▶ _nwin_edit_choices: choices store + autosave to
                      <recs>/<source>/nwin_edits/nwin_choices.json
    cmd button    ──▶ _nwin_command: find_clusters --N_win_file rerun string

Figure-building helpers are module-level (not closures) so they can be
unit-tested without a Dash app.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, html, no_update

from ..data.fits_cache import split_source_band
from ..data.loader import load_bundle
from ..data.window_fits import (
    bic_table, build_rerun_command, build_window_meta,
    list_window_fits, load_nwin_choices, load_window_fit, nwin_choices_path,
    save_nwin_choices, window_bundle,
)
from ..plots.overlay import overlay_figure_for_epoch
from .callbacks import _source_from_folder

_GRAPH_HEIGHT = 560


# ---------------------------------------------------------------------------
# Figure builders (unit-testable, no Dash)
# ---------------------------------------------------------------------------


def build_bic_figure(meta: dict, win_idx: int, chosen_n: int | None,
                     choices: dict[str, dict]) -> go.Figure:
    """Two-row figure: BIC* vs N for the selected window (top) and the
    N-per-window strip chart across all windows (bottom)."""
    from plotly.subplots import make_subplots

    labels = meta["labels"]
    ref_epochs = meta["ref_epochs"]
    win_idx = int(np.clip(win_idx, 0, len(labels) - 1))
    label = labels[win_idx]

    fig = make_subplots(
        rows=2, cols=1, vertical_spacing=0.14,
        subplot_titles=(f"BIC* vs N — window {label}",
                        "N per window (click a point to jump)"),
    )

    # --- top: BIC* curve for the selected window -------------------------
    refs = {r.label: r for r in list_window_fits(Path(meta["folder"]),
                                                 meta["source"])}
    ref = refs.get(label)
    tab = (bic_table(ref.csv_path, meta["complex_factor"])
           if ref is not None else None)
    if tab is not None and len(tab):
        fig.add_trace(
            go.Scatter(x=tab["Nclusters"], y=tab["bicstar"],
                       mode="lines+markers", line=dict(color="#1f77b4"),
                       name="BIC*", showlegend=False,
                       hovertemplate="N=%{x}<br>BIC* %{y:.1f}<extra></extra>"),
            row=1, col=1,
        )
        # Mark the displayed / chosen N on the curve.
        if chosen_n is not None and (tab["Nclusters"] == chosen_n).any():
            y = float(tab.loc[tab["Nclusters"] == chosen_n, "bicstar"].iloc[0])
            fig.add_trace(
                go.Scatter(x=[chosen_n], y=[y], mode="markers",
                           marker=dict(color="#d62728", size=12, symbol="circle-open",
                                       line=dict(width=2)),
                           name="displayed N", showlegend=False,
                           hovertemplate=f"displayed N={chosen_n}<extra></extra>"),
                row=1, col=1,
            )
    else:
        fig.add_annotation(text="No BIC* diagnostics for this window",
                           xref="x domain", yref="y domain", x=0.5, y=0.5,
                           showarrow=False, font=dict(color="#888"), row=1, col=1)

    # --- bottom: strip chart across windows ------------------------------
    idx = list(range(len(labels)))
    cur = [meta["cur_N"][i] for i in idx]
    bic = [meta["bic_N"][i] for i in idx]
    fig.add_trace(
        go.Scatter(x=ref_epochs, y=cur, mode="lines+markers",
                   line=dict(color="#888", width=1),
                   marker=dict(color="#888", size=6),
                   name="current model", customdata=idx,
                   hovertemplate=("window %{customdata}<br>ref %{x:.2f}"
                                  "<br>current N=%{y}<extra></extra>")),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(x=ref_epochs, y=bic, mode="markers",
                   marker=dict(color="#1f77b4", size=7, symbol="diamond-open",
                               line=dict(width=1.5)),
                   name="BIC* suggestion", customdata=idx,
                   hovertemplate=("window %{customdata}<br>ref %{x:.2f}"
                                  "<br>BIC* N=%{y}<extra></extra>")),
        row=2, col=1,
    )
    chosen_x = [ref_epochs[i] for i in idx if labels[i] in choices]
    chosen_y = [choices[labels[i]]["N"] for i in idx if labels[i] in choices]
    chosen_i = [i for i in idx if labels[i] in choices]
    if chosen_x:
        fig.add_trace(
            go.Scatter(x=chosen_x, y=chosen_y, mode="markers",
                       marker=dict(color="#d62728", size=9),
                       name="recorded choice", customdata=chosen_i,
                       hovertemplate=("window %{customdata}<br>ref %{x:.2f}"
                                      "<br>chosen N=%{y}<extra></extra>")),
            row=2, col=1,
        )
    # Selected-window marker (vertical line on the strip chart).
    fig.add_shape(type="line", xref="x2", yref="y2 domain",
                  x0=ref_epochs[win_idx], x1=ref_epochs[win_idx], y0=0, y1=1,
                  line=dict(color="rgba(90,90,90,0.5)", width=1.5),
                  layer="below")

    fig.update_xaxes(title_text="N clusters", row=1, col=1)
    fig.update_yaxes(title_text="BIC*", row=1, col=1)
    fig.update_xaxes(title_text="reference epoch", row=2, col=1)
    fig.update_yaxes(title_text="N", row=2, col=1)
    fig.update_layout(
        template="plotly_white", height=_GRAPH_HEIGHT,
        margin=dict(l=55, r=15, t=40, b=45),
        legend=dict(orientation="h", yanchor="bottom", y=-0.22,
                    font=dict(size=10)),
        # Keep zoom while choices update, reset on source/window change.
        uirevision=f"nwin-bic:{meta['folder']}:{win_idx}",
    )
    return fig


def build_window_overlay(meta: dict, win_idx: int, n: int, epoch_idx: int,
                         cache_dir: Path,
                         fits_data_dir: Path | None = None,
                         uirevision: str = "nwin-overlay"):
    """(figure, beam_params) for one (window, N, epoch). Clamps all three
    indices so transient mid-update callback states can't error."""
    src = _source_from_folder(meta["folder"])
    refs = list_window_fits(Path(meta["folder"]), meta["source"])
    if src is None or not refs:
        return _empty(), None
    win_idx = int(np.clip(win_idx, 0, len(refs) - 1))
    ref = refs[win_idx]
    wf = load_window_fit(ref.npz_path)
    n = int(np.clip(n, int(wf.clusters.min()), int(wf.clusters.max())))
    epoch_idx = int(np.clip(epoch_idx, 0, len(wf.ep_info) - 1))
    bundle = window_bundle(src, ref, n)
    source_no_band, band = split_source_band(src.source)
    fig, beam = overlay_figure_for_epoch(
        bundle, epoch_idx, cache_dir,
        source_no_band=source_no_band, band=band,
        fits_data_dir=fits_data_dir,
        image_source="synthesize",
        uirevision=uirevision,
    )
    fig.update_layout(
        height=_GRAPH_HEIGHT,
        title=dict(text=(f"window {ref.label} · N={n} · "
                         + (fig.layout.title.text or ""))),
    )
    return fig, beam


def _empty() -> go.Figure:
    fig = go.Figure()
    fig.update_layout(template="plotly_white", height=_GRAPH_HEIGHT,
                      margin=dict(l=20, r=20, t=20, b=20))
    return fig


def _choice_seed_n(meta: dict, win_idx: int, choices: dict[str, dict]) -> int:
    """The N the sliders seed to when a window is selected: recorded choice
    > current model > BIC* suggestion > minN."""
    label = meta["labels"][win_idx]
    if label in choices:
        return int(choices[label]["N"])
    for cand in (meta["cur_N"][win_idx], meta["bic_N"][win_idx]):
        if cand is not None:
            return int(cand)
    return int(meta["minN"])


def _status_text(meta: dict, win_idx: int, n: int,
                 choices: dict[str, dict]) -> str:
    label = meta["labels"][win_idx]
    cur = meta["cur_N"][win_idx]
    bic = meta["bic_N"][win_idx]
    parts = [f"current model N={cur if cur is not None else '?'}",
             f"BIC* suggests {bic if bic is not None else '?'}"]
    if label in choices:
        c = choices[label]
        parts.append(f"recorded choice N={c['N']}"
                     + (f" ({c['comment']})" if c["comment"] else ""))
    else:
        parts.append("no recorded choice")
    return " · ".join(parts)


def _choices_children(meta: dict, choices: dict[str, dict]) -> list:
    if not choices:
        return [html.Span("No recorded choices for this source.",
                          style={"color": "#888"})]
    items = []
    label_set = set(meta["labels"]) if meta else set()
    for label in sorted(choices):
        c = choices[label]
        stale = label not in label_set
        cur = None
        if meta and label in label_set:
            cur = meta["cur_N"][meta["labels"].index(label)]
        txt = f"{label}: N={c['N']}"
        if cur is not None:
            txt += f" (model has {cur})"
        if c["comment"]:
            txt += f" — {c['comment']}"
        if stale:
            txt += "  ⚠ matches no current window"
        items.append(html.Li(txt, style={"color": "#a33" if stale else "#444"}))
    return [html.Span(f"{len(choices)} recorded choice"
                      f"{'' if len(choices) == 1 else 's'} "
                      f"(autosaved to nwin_edits/nwin_choices.json):"),
            html.Ul(items, style={"margin": "0.2em 0 0 1.2em"})]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(
    app: Dash,
    *,
    results_dir: Path,
    recommendations_dir: Path,
    cache_dir: Path,
    fits_data_dir: Path | None = None,
) -> None:

    # ---- source change: build meta + load choices from disk --------------
    @app.callback(
        Output("nwin-meta", "data"),
        Output("nwin-choices-store", "data"),
        Output("nwin-window-slider", "min"),
        Output("nwin-window-slider", "max"),
        Output("nwin-window-slider", "marks"),
        Output("nwin-window-slider", "value"),
        Output("nwin-hint", "children"),
        Output("nwin-hint", "style"),
        Output("nwin-body", "style"),
        Output("nwin-cmd-text", "value"),
        Output("nwin-cmd-row", "style"),
        Input("source-picker", "value"),
        Input("reload-counter", "data"),
    )
    def _nwin_load_source(source_folder, _reload_counter):
        hint_style = {"display": "block", "padding": "0.3em 1.25em 0.8em",
                      "fontSize": "0.85em", "color": "#777"}
        hidden = {"display": "none"}
        empty = (None, {}, 0, 0, {}, 0)
        src = _source_from_folder(source_folder) if source_folder else None
        if src is None:
            return (*empty, "No source selected.", hint_style, hidden,
                    "", hidden)
        try:
            current_df = load_bundle(source_folder, "current").cluster_df
        except Exception:
            current_df = None
        meta = build_window_meta(src, current_df)
        if meta is None:
            return (*empty,
                    "No cluster_fits/ found for this source. Window-N review "
                    "needs the pipeline's local per-window fit files "
                    "(cluster_fits/*.npz — excluded from the server sync).",
                    hint_style, hidden, "", hidden)
        n_win = len(meta.labels)
        step = max(1, n_win // 8)
        marks = {i: f"{meta.ref_epochs[i]:.0f}" for i in range(0, n_win, step)}
        marks[n_win - 1] = f"{meta.ref_epochs[n_win - 1]:.0f}"
        choices = load_nwin_choices(
            nwin_choices_path(recommendations_dir, src.source))
        return (meta.to_store(), choices, 0, n_win - 1, marks, 0,
                "", hidden, {"display": "block"}, "", hidden)

    # ---- ◀ / ▶ window step buttons ----------------------------------------
    @app.callback(
        Output("nwin-window-slider", "value", allow_duplicate=True),
        Input("nwin-win-prev", "n_clicks"),
        Input("nwin-win-next", "n_clicks"),
        State("nwin-window-slider", "value"),
        State("nwin-window-slider", "min"),
        State("nwin-window-slider", "max"),
        prevent_initial_call=True,
    )
    def _nwin_step_window(_p, _n, value, lo, hi):
        if value is None or lo is None or hi is None:
            return no_update
        if ctx.triggered_id == "nwin-win-prev":
            return max(int(lo), int(value) - 1)
        return min(int(hi), int(value) + 1)

    # ---- click a strip-chart point to jump to that window -----------------
    @app.callback(
        Output("nwin-window-slider", "value", allow_duplicate=True),
        Output("nwin-bic-graph", "clickData"),
        Input("nwin-bic-graph", "clickData"),
        prevent_initial_call=True,
    )
    def _nwin_jump_on_click(click_data):
        if not click_data:
            return no_update, no_update
        for p in click_data.get("points") or []:
            cd = p.get("customdata")
            if cd is None:
                continue
            try:
                # Reset clickData so re-clicking the same point fires again
                # (same Dash quirk as the summary-graph click toggle).
                return int(cd), None
            except (TypeError, ValueError):
                continue
        return no_update, None

    # ---- window changed: seed N + epoch sliders ---------------------------
    @app.callback(
        Output("nwin-n-slider", "min"),
        Output("nwin-n-slider", "max"),
        Output("nwin-n-slider", "marks"),
        Output("nwin-n-slider", "value"),
        Output("nwin-epoch-slider", "min"),
        Output("nwin-epoch-slider", "max"),
        Output("nwin-epoch-slider", "marks"),
        Output("nwin-epoch-slider", "value"),
        Output("nwin-window-label", "children"),
        Input("nwin-window-slider", "value"),
        Input("nwin-meta", "data"),
        State("nwin-choices-store", "data"),
    )
    def _nwin_window_changed(win_idx, meta, choices):
        if not meta or win_idx is None:
            return 1, 16, {}, 1, 0, 0, {}, 0, ""
        win_idx = int(np.clip(win_idx, 0, len(meta["labels"]) - 1))
        label = meta["labels"][win_idx]
        refs = list_window_fits(Path(meta["folder"]), meta["source"])
        wf = load_window_fit(refs[win_idx].npz_path)
        n_lo, n_hi = int(wf.clusters.min()), int(wf.clusters.max())
        n_marks = {v: str(v) for v in range(n_lo, n_hi + 1,
                                            max(1, (n_hi - n_lo) // 8 or 1))}
        n_val = int(np.clip(_choice_seed_n(meta, win_idx, choices or {}),
                            n_lo, n_hi))
        eps = wf.ep_info["epoch_val"]
        n_ep = len(eps)
        ep_marks = {i: f"{float(eps[i]):.2f}" for i in range(n_ep)}
        # Default to the window's reference (median) epoch — what the old
        # matplotlib editor displayed per window.
        ref_ep = wf.ref_epoch
        ep_val = int(np.argmin(np.abs(np.asarray(eps, dtype=float) - ref_ep)))
        win_label = (f"{win_idx + 1}/{len(meta['labels'])} · {label} · "
                     f"ref {ref_ep:.4f}")
        return (n_lo, n_hi, n_marks, n_val,
                0, n_ep - 1, ep_marks, ep_val, win_label)

    # ---- − / + N step buttons ---------------------------------------------
    @app.callback(
        Output("nwin-n-slider", "value", allow_duplicate=True),
        Input("nwin-n-down", "n_clicks"),
        Input("nwin-n-up", "n_clicks"),
        State("nwin-n-slider", "value"),
        State("nwin-n-slider", "min"),
        State("nwin-n-slider", "max"),
        prevent_initial_call=True,
    )
    def _nwin_step_n(_d, _u, value, lo, hi):
        if value is None or lo is None or hi is None:
            return no_update
        if ctx.triggered_id == "nwin-n-down":
            return max(int(lo), int(value) - 1)
        return min(int(hi), int(value) + 1)

    # ---- ◀ / ▶ epoch step buttons ------------------------------------------
    @app.callback(
        Output("nwin-epoch-slider", "value", allow_duplicate=True),
        Input("nwin-epoch-prev", "n_clicks"),
        Input("nwin-epoch-next", "n_clicks"),
        State("nwin-epoch-slider", "value"),
        State("nwin-epoch-slider", "min"),
        State("nwin-epoch-slider", "max"),
        prevent_initial_call=True,
    )
    def _nwin_step_epoch(_p, _n, value, lo, hi):
        if value is None or lo is None or hi is None:
            return no_update
        if ctx.triggered_id == "nwin-epoch-prev":
            return max(int(lo), int(value) - 1)
        return min(int(hi), int(value) + 1)

    # ---- overlay figure -----------------------------------------------------
    # Window slider is an Input (not just the derived N/epoch sliders) so the
    # overlay can't be left showing a stale window; build_window_overlay
    # clamps, so a transient (new window, old N/epoch) render is safe.
    @app.callback(
        Output("nwin-overlay-graph", "figure"),
        Output("nwin-beam-params", "data"),
        Output("nwin-epoch-label", "children"),
        Input("nwin-window-slider", "value"),
        Input("nwin-n-slider", "value"),
        Input("nwin-epoch-slider", "value"),
        State("nwin-meta", "data"),
    )
    def _nwin_overlay(win_idx, n, epoch_idx, meta):
        if not meta or win_idx is None or n is None or epoch_idx is None:
            return _empty(), None, ""
        fig, beam = build_window_overlay(
            meta, int(win_idx), int(n), int(epoch_idx), cache_dir,
            fits_data_dir=fits_data_dir,
            # Persist the admin's zoom across window / N / epoch scrubbing —
            # the old matplotlib editor kept fixed limits the same way.
            uirevision=f"nwin-overlay:{meta['folder']}",
        )
        refs = list_window_fits(Path(meta["folder"]), meta["source"])
        wi = int(np.clip(int(win_idx), 0, len(refs) - 1))
        wf = load_window_fit(refs[wi].npz_path)
        ei = int(np.clip(int(epoch_idx), 0, len(wf.ep_info) - 1))
        info = wf.ep_info[ei]
        lbl = f"{info['epoch_name']}  ·  {float(info['epoch_val']):.4f}"
        return fig, beam, lbl

    # ---- BIC* + strip chart --------------------------------------------------
    @app.callback(
        Output("nwin-bic-graph", "figure"),
        Input("nwin-window-slider", "value"),
        Input("nwin-n-slider", "value"),
        Input("nwin-choices-store", "data"),
        State("nwin-meta", "data"),
    )
    def _nwin_bic(win_idx, n, choices, meta):
        if not meta or win_idx is None:
            return _empty()
        return build_bic_figure(meta, int(win_idx),
                                int(n) if n is not None else None,
                                choices or {})

    # ---- status line ----------------------------------------------------------
    @app.callback(
        Output("nwin-status", "children"),
        Input("nwin-window-slider", "value"),
        Input("nwin-n-slider", "value"),
        Input("nwin-choices-store", "data"),
        State("nwin-meta", "data"),
    )
    def _nwin_status(win_idx, n, choices, meta):
        if not meta or win_idx is None:
            return ""
        wi = int(np.clip(int(win_idx), 0, len(meta["labels"]) - 1))
        return _status_text(meta, wi, int(n or 0), choices or {})

    # ---- record / clear / clear-all — autosaves to disk -----------------------
    @app.callback(
        Output("nwin-choices-store", "data", allow_duplicate=True),
        Output("nwin-comment", "value"),
        Input("nwin-record-btn", "n_clicks"),
        Input("nwin-clear-btn", "n_clicks"),
        Input("nwin-clear-all-btn", "n_clicks"),
        State("nwin-window-slider", "value"),
        State("nwin-n-slider", "value"),
        State("nwin-comment", "value"),
        State("nwin-choices-store", "data"),
        State("nwin-meta", "data"),
        prevent_initial_call=True,
    )
    def _nwin_edit_choices(_r, _c, _ca, win_idx, n, comment, choices, meta):
        if not meta or win_idx is None:
            return no_update, no_update
        wi = int(np.clip(int(win_idx), 0, len(meta["labels"]) - 1))
        label = meta["labels"][wi]
        out = dict(choices or {})
        trig = ctx.triggered_id
        if trig == "nwin-record-btn":
            if n is None:
                return no_update, no_update
            out[label] = {"N": int(n), "comment": (comment or "").strip()}
        elif trig == "nwin-clear-btn":
            out.pop(label, None)
        elif trig == "nwin-clear-all-btn":
            out = {}
        try:
            csv_sha = load_bundle(meta["folder"], "current").csv_sha
        except Exception:
            csv_sha = None
        save_nwin_choices(
            nwin_choices_path(recommendations_dir, meta["source"]),
            meta["source"], out, model_sha=csv_sha)
        return out, ""

    # ---- recorded-choices list --------------------------------------------------
    @app.callback(
        Output("nwin-choices-list", "children"),
        Input("nwin-choices-store", "data"),
        Input("nwin-meta", "data"),
    )
    def _nwin_choices_list(choices, meta):
        return _choices_children(meta or {}, choices or {})

    # ---- generate the rerun command ----------------------------------------------
    @app.callback(
        Output("nwin-cmd-text", "value", allow_duplicate=True),
        Output("nwin-cmd-row", "style"),
        Input("nwin-cmd-btn", "n_clicks"),
        State("nwin-meta", "data"),
        State("nwin-choices-store", "data"),
        prevent_initial_call=True,
    )
    def _nwin_command(_n, meta, choices):
        shown = {"display": "flex", "alignItems": "flex-start",
                 "padding": "0.2em 0 0.5em"}
        if not meta:
            return "No source loaded.", shown
        if not choices:
            return ("No recorded choices yet — record at least one window's "
                    "N first."), shown
        path = nwin_choices_path(recommendations_dir, meta["source"])
        cmd = build_rerun_command(Path(meta["folder"]), path)
        if cmd is None:
            return (f"run_string.txt not found in {meta['folder']} — compose "
                    f"the find_clusters.py command manually and add: "
                    f"--N_win_file {path.resolve()}"), shown
        return (f"# run from your production working directory "
                f"(where find_clusters.py was originally run):\n{cmd}"), shown

    # ---- clientside: reposition the beam ellipse on zoom/pan ----------------------
    # Same restyle-and-no_update discipline as the main overlay's beam
    # callback (see ui/callbacks.py) — only the component ids differ.
    app.clientside_callback(
        """
        function(relayoutData, beamParams) {
            if (!beamParams || !relayoutData) {
                return window.dash_clientside.no_update;
            }
            var wrapper = document.getElementById('nwin-overlay-graph');
            if (!wrapper) return window.dash_clientside.no_update;
            var gd = wrapper.querySelector('.js-plotly-plot');
            if (!gd || !window.Plotly) return window.dash_clientside.no_update;

            var bmaj = beamParams.bmaj, bmin = beamParams.bmin, bpa = beamParams.bpa;
            var idx = beamParams.beam_idx;

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
                ex[i] = bx + xr * cosPa + yr * sinPa;
                ey[i] = by - xr * sinPa + yr * cosPa;
            }
            window.Plotly.restyle(gd,
                {x: [ex], y: [ey], visible: true}, [idx]);
            return window.dash_clientside.no_update;
        }
        """,
        Output("nwin-beam-params", "data", allow_duplicate=True),
        Input("nwin-overlay-graph", "relayoutData"),
        State("nwin-beam-params", "data"),
        prevent_initial_call=True,
    )
