"""Dash layout for the review tool."""

from __future__ import annotations

from pathlib import Path

from dash import dcc, html

from ..data.loader import list_sources


def build_layout(results_dir: Path, reviewer: str) -> html.Div:
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
                dcc.Graph(id="summary-graph", style={"height": "720px"}),
                type="default",
            ),
        ],
        style={"flex": "1 1 0", "padding": "0.5em", "minWidth": "0"},
    )

    overlay_panel = html.Div(
        [
            html.H4("Epoch overlay", style={"margin": "0.25em 0"}),
            html.Div("Coming next: FITS + cluster markers per epoch.",
                     style={"color": "#888", "fontStyle": "italic"}),
        ],
        style={"flex": "1 1 0", "padding": "0.5em", "minWidth": "0"},
    )

    body = html.Div(
        [summary_panel, overlay_panel],
        style={"display": "flex", "gap": "0.5em", "padding": "0.5em"},
    )

    return html.Div(
        [
            dcc.Store(id="reviewer-store", data=reviewer),
            header,
            body,
        ],
        style={"fontFamily": "system-ui, sans-serif"},
    )
