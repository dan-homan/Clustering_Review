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
                    html.Span("View:", style={"margin": "0 0.5em 0 1.5em"}),
                    dcc.RadioItems(
                        id="view-picker",
                        options=[{"label": v, "value": v}
                                 for v in ("Position", "Flux", "Polarization", "Kinematics")],
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
                        value=[],
                        inputStyle={"marginRight": "0.3em"},
                        style={"marginLeft": "1.5em",
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
            html.H4("Summary plots", style={"margin": "0.25em 0"}),
            vector_scale_row,
            dcc.Loading(
                dcc.Graph(
                    id="summary-graph",
                    style={"height": "720px"},
                    responsive=True,
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
                        style={"width": "2.2em", "marginRight": "1em"}),
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
            # Selection of summary-graph points: a list of {"cid", "epoch"}
            # dicts. Updated by click / box-select callbacks. Drives both the
            # gold-diamond highlight in the summary figure and the
            # selection-driven actions in the Edits tab.
            dcc.Store(id="selection-store", data=[]),
            header,
            body,
            build_recommendations_panel(admin=admin),
        ],
        style={"fontFamily": "system-ui, sans-serif"},
    )
