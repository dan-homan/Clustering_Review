"""Dash layout for the review tool."""

from __future__ import annotations

from pathlib import Path

from dash import dcc, html

from ..data.loader import list_sources
from .recommendations_panel import build_recommendations_panel


def build_layout(results_dir: Path, reviewer: str, admin: bool = False) -> html.Div:
    sources = list_sources(results_dir)
    source_options = [{"label": s.label, "value": str(s.folder)} for s in sources]
    initial = source_options[0]["value"] if source_options else None

    header = html.Div(
        [
            html.H2("MOJAVE Cluster Review", style={"margin": "0"}),
            html.Div(
                [
                    html.Span(f"Reviewer: {reviewer}",
                              style={"marginRight": "1.5em", "color": "#555"}),
                    html.Span("Source:", style={"marginRight": "0.5em"}),
                    dcc.Dropdown(
                        id="source-picker",
                        options=source_options,
                        value=initial,
                        clearable=False,
                        style={"minWidth": "320px", "display": "inline-block"},
                    ),
                    html.Span("Model:", style={"margin": "0 0.5em 0 1.5em"}),
                    dcc.Dropdown(
                        id="model-picker",
                        clearable=False,
                        style={"minWidth": "180px", "display": "inline-block"},
                    ),
                    # Reload the on-disk CSV / NPZ for the current source.
                    # ``load_bundle`` already invalidates its cache when
                    # the file mtimes change, so the explicit button is
                    # really a belt-and-braces — useful when the reviewer
                    # just ran ``mojave-apply`` (or otherwise edited
                    # Results/) and wants to be *sure* the app is showing
                    # fresh data, without leaving them guessing whether
                    # the auto-detect caught it.
                    html.Button(
                        "↻ Reload",
                        id="reload-bundles",
                        n_clicks=0,
                        title="Re-read CSV + NPZ from disk for all "
                              "sources / models (auto-detected on file "
                              "change too)",
                        style={"marginLeft": "0.5em", "padding": "0.2em 0.6em",
                               "fontSize": "0.85em"},
                    ),
                    html.Span("View:", style={"margin": "0 0.5em 0 1.5em"}),
                    dcc.RadioItems(
                        id="view-picker",
                        options=[{"label": v, "value": v}
                                 for v in ("Position", "XY Position", "Flux",
                                           "Polarization", "Kinematics")],
                        value="Position",
                        inline=True,
                        inputStyle={"marginRight": "0.25em", "marginLeft": "0.5em"},
                    ),
                    # "Visualize recommendations": when ON + model=current, the
                    # summary + overlay are rendered with the user's pending
                    # edits applied to the underlying CSV. Auto-managed when
                    # model is a backup or a "Rec: <slug>" entry (disabled,
                    # implicit for the latter).
                    dcc.Checklist(
                        id="visualize-checkbox",
                        options=[{"label": " Visualize recommendations",
                                  "value": "yes"}],
                        # Default ON: reviewers almost always want to see their
                        # in-progress edits applied. No-op when there are no
                        # recommendations yet.
                        value=["yes"],
                        inputStyle={"marginRight": "0.3em"},
                        style={"marginLeft": "1.5em",
                               "fontSize": "0.9em",
                               "color": "#444"},
                    ),
                    # The overlay panel synthesizes the Stokes I image from
                    # the epoch's clean components convolved with the
                    # restoring beam by default — no NRAO fetch, no on-disk
                    # cache, ~1 ms per epoch, and the result matches the
                    # restored CLEAN FITS to within a fraction of a percent
                    # at the contour levels that matter for review. Tick
                    # this checkbox to fall back to the real CLEAN FITS
                    # image (which carries the residual noise sea synthesis
                    # cannot reproduce).
                    dcc.Checklist(
                        id="use-fits-checkbox",
                        options=[{"label": " Use FITS images",
                                  "value": "yes"}],
                        value=[],
                        inputStyle={"marginRight": "0.3em"},
                        style={"marginLeft": "1em",
                               "fontSize": "0.9em",
                               "color": "#444"},
                    ),
                    # Replace the single-epoch contour background with the
                    # epoch-averaged "stacked" image (all epochs' clean
                    # components / N, convolved with the median beam). The
                    # per-epoch cluster overlay still tracks the slider.
                    # Overrides "Use FITS images" when both are ticked.
                    dcc.Checklist(
                        id="stack-image-checkbox",
                        options=[{"label": " Stacked image",
                                  "value": "yes"}],
                        value=[],
                        inputStyle={"marginRight": "0.3em"},
                        style={"marginLeft": "1em",
                               "fontSize": "0.9em",
                               "color": "#444"},
                    ),
                ],
                style={"display": "flex", "alignItems": "center", "marginTop": "0.5em"},
            ),
        ],
        style={"padding": "0.75em 1em", "borderBottom": "1px solid #ddd",
               "background": "#fafafa"},
    )

    vector_scale_row = html.Div(
        [
            html.Span("Vector scale (Kinematics):",
                      style={"marginRight": "0.75em", "color": "#555"}),
            html.Div(
                dcc.Slider(
                    id="vector-scale",
                    min=0.2, max=5.0, step=0.1, value=1.0,
                    marks={0.5: "½×", 1: "1×", 2: "2×", 3: "3×", 5: "5×"},
                    tooltip={"placement": "bottom", "always_visible": False},
                    updatemode="mouseup",
                ),
                style={"flex": "1 1 0", "minWidth": "200px"},
            ),
        ],
        id="vector-scale-row",
        style={"display": "flex", "alignItems": "center",
               "padding": "0.25em 1em", "fontSize": "0.9em"},
    )

    summary_panel = html.Div(
        [
            html.Div(
                [
                    html.H4("Summary plots", style={"margin": "0.25em 0"}),
                    # Hide the non-robust (slategray) clusters from both the
                    # plots and the legend. Unassigned (-1) / synthetic
                    # (>=1000) clusters are unaffected.
                    dcc.Checklist(
                        id="hide-non-robust-checkbox",
                        options=[{"label": " Hide non-robust clusters",
                                  "value": "yes"}],
                        value=[],
                        inputStyle={"marginRight": "0.3em"},
                        style={"marginLeft": "1.5em", "fontSize": "0.85em",
                               "color": "#444"},
                    ),
                ],
                style={"display": "flex", "alignItems": "center"},
            ),
            vector_scale_row,
            dcc.Loading(
                dcc.Graph(
                    id="summary-graph",
                    style={"height": "720px"},
                    responsive=True,
                    # Strip the box-select and lasso-select tools from the
                    # modebar. Selection is click-only on purpose: those
                    # two modes behave quite differently (e.g. don't toggle
                    # on a repeat click, can't be partially undone) and
                    # reviewers reported getting stranded with an
                    # accidental box-selection they couldn't reverse.
                    # Clicking individual points (with the click-toggle
                    # callback in ui/callbacks.py) is the supported flow.
                    config={
                        "modeBarButtonsToRemove": ["select2d", "lasso2d"],
                        "displaylogo": False,
                    },
                ),
                type="default",
            ),
        ],
        id="summary-panel",
        style={"flex": "1 1 0", "padding": "0.5em", "minWidth": "0"},
    )

    epoch_controls = html.Div(
        [
            html.Button("◀", id="epoch-prev", n_clicks=0,
                        style={"width": "2.2em", "marginRight": "0.5em"}),
            html.Button("▶", id="epoch-next", n_clicks=0,
                        style={"width": "2.2em", "marginRight": "0.5em"}),
            # Escape hatch when Plotly's SVG layer ends up stale (rare but
            # reported in marathon review sessions): clicking this forces
            # the overlay figure to redraw with a fresh uirevision key, so
            # any stale axis-state or hidden-trace state is discarded.
            # Also useful as a "back to the full source view" shortcut
            # after the reviewer has zoomed in to inspect one cluster.
            html.Button("Reset view", id="overlay-reset", n_clicks=0,
                        title="Force the overlay panel to redraw and "
                              "reset to the default zoom",
                        style={"marginRight": "1em", "padding": "0.2em 0.6em",
                               "fontSize": "0.85em"}),
            html.Div(
                dcc.Slider(
                    id="epoch-slider", min=0, max=0, step=1, value=0,
                    marks={}, included=False,
                    tooltip={"placement": "bottom", "always_visible": False},
                    updatemode="mouseup",
                ),
                style={"flex": "1 1 0", "minWidth": "200px"},
            ),
            html.Span(id="epoch-label",
                      style={"marginLeft": "1em", "minWidth": "9em",
                             "color": "#555", "fontFamily": "ui-monospace, monospace"}),
        ],
        style={"display": "flex", "alignItems": "center",
               "padding": "0.25em 0.5em"},
    )

    overlay_panel = html.Div(
        [
            html.H4("Epoch overlay", style={"margin": "0.25em 0"}),
            epoch_controls,
            dcc.Loading(
                dcc.Graph(
                    id="overlay-graph",
                    style={"height": "720px"},
                    responsive=True,
                ),
                type="default",
            ),
        ],
        id="overlay-panel",
        style={"flex": "1 1 0", "padding": "0.5em", "minWidth": "0"},
    )

    # Vertical drag handle between the two panels — wired up by
    # assets/resizable.js. Initial flex is 1/1, drag updates to fixed-px.
    split_handle = html.Div(id="split-handle", title="Drag to resize panels")

    body = html.Div(
        [summary_panel, split_handle, overlay_panel],
        style={"display": "flex", "padding": "0.5em"},
    )

    return html.Div(
        [
            dcc.Store(id="reviewer-store", data=reviewer),
            dcc.Store(id="beam-params"),
            # Increments whenever the user clicks "Reset view"; folds into
            # the overlay's uirevision key so a click forces a complete
            # redraw + axis reset.
            dcc.Store(id="overlay-reset-counter", data=0),
            # Increments whenever the user clicks "↻ Reload"; participates
            # as an Input on every callback that reads load_bundle, so a
            # click forces a re-render with freshly-read data. Pairs with
            # the mtime-based invalidation in data/loader.py.
            dcc.Store(id="reload-counter", data=0),
            # Selection of summary-graph points: a list of {"cid", "epoch"}
            # dicts. Updated by the click-toggle callback only — box-select
            # and lasso are disabled by stripping select2d/lasso2d from
            # the summary plot's modebar (see above). Drives both the
            # gold-diamond highlight in the summary figure and the
            # selection-driven actions in the Edits tab.
            dcc.Store(id="selection-store", data=[]),
            header,
            body,
            build_recommendations_panel(admin=admin),
        ],
        style={"fontFamily": "system-ui, sans-serif"},
    )
