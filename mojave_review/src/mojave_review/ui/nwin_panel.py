"""Admin-only Window-N review panel (the --editN replacement).

Collapsible <details> block, rendered only with --admin. Lets the model
builder scrub the pipeline's per-window cluster fits (cluster_fits/*.npz),
compare candidate N values per window against the BIC* suggestion and the
current model, record per-window N choices (autosaved to
<recs>/<source>/nwin_edits/nwin_choices.json), and generate the
find_clusters.py --N_win_file rerun command.

All component ids are prefixed ``nwin-``; the callbacks live in
ui/nwin_callbacks.py and are registered only when admin.
"""

from __future__ import annotations

from dash import dcc, html

_BTN = {"width": "2.2em", "marginRight": "0.5em"}
_SLIDER_WRAP = {"flex": "1 1 0", "minWidth": "200px"}
_ROW = {"display": "flex", "alignItems": "center", "padding": "0.15em 0"}
_LABEL = {"marginRight": "0.5em", "color": "#555", "fontSize": "0.85em",
          "minWidth": "5.5em"}


def build_nwin_panel() -> html.Details:
    controls = html.Div(
        [
            # Window selector
            html.Div(
                [
                    html.Span("Window:", style=_LABEL),
                    html.Button("◀", id="nwin-win-prev", n_clicks=0, style=_BTN),
                    html.Button("▶", id="nwin-win-next", n_clicks=0, style=_BTN),
                    html.Div(
                        dcc.Slider(
                            id="nwin-window-slider", min=0, max=0, step=1,
                            value=0, marks={}, included=False,
                            tooltip={"placement": "bottom",
                                     "always_visible": False},
                            updatemode="mouseup",
                        ),
                        style=_SLIDER_WRAP,
                    ),
                    html.Span(id="nwin-window-label",
                              style={"marginLeft": "1em", "minWidth": "16em",
                                     "color": "#555",
                                     "fontFamily": "ui-monospace, monospace",
                                     "fontSize": "0.85em"}),
                ],
                style=_ROW,
            ),
            # N selector
            html.Div(
                [
                    html.Span("Clusters N:", style=_LABEL),
                    html.Button("−", id="nwin-n-down", n_clicks=0, style=_BTN),
                    html.Button("+", id="nwin-n-up", n_clicks=0, style=_BTN),
                    html.Div(
                        dcc.Slider(
                            id="nwin-n-slider", min=1, max=16, step=1,
                            value=1, marks={}, included=False,
                            tooltip={"placement": "bottom",
                                     "always_visible": False},
                            updatemode="mouseup",
                        ),
                        style=_SLIDER_WRAP,
                    ),
                    html.Span(id="nwin-status",
                              style={"marginLeft": "1em", "minWidth": "16em",
                                     "color": "#555", "fontSize": "0.85em"}),
                ],
                style=_ROW,
            ),
            # Epoch-within-window selector
            html.Div(
                [
                    html.Span("Epoch:", style=_LABEL),
                    html.Button("◀", id="nwin-epoch-prev", n_clicks=0, style=_BTN),
                    html.Button("▶", id="nwin-epoch-next", n_clicks=0, style=_BTN),
                    html.Div(
                        dcc.Slider(
                            id="nwin-epoch-slider", min=0, max=0, step=1,
                            value=0, marks={}, included=False,
                            tooltip={"placement": "bottom",
                                     "always_visible": False},
                            updatemode="mouseup",
                        ),
                        style=_SLIDER_WRAP,
                    ),
                    html.Span(id="nwin-epoch-label",
                              style={"marginLeft": "1em", "minWidth": "12em",
                                     "color": "#555",
                                     "fontFamily": "ui-monospace, monospace",
                                     "fontSize": "0.85em"}),
                ],
                style=_ROW,
            ),
            # Record / clear actions
            html.Div(
                [
                    html.Button(
                        "Record N for this window",
                        id="nwin-record-btn", n_clicks=0,
                        title="Save the displayed N as this window's choice "
                              "(autosaves to nwin_edits/nwin_choices.json)",
                        style={"padding": "0.3em 0.9em", "fontSize": "0.85em",
                               "background": "#1f77b4", "color": "white",
                               "border": "none", "borderRadius": "4px",
                               "cursor": "pointer"},
                    ),
                    dcc.Input(
                        id="nwin-comment", type="text", value="",
                        placeholder="optional comment for this choice",
                        debounce=True,
                        style={"marginLeft": "0.75em", "flex": "1 1 0",
                               "maxWidth": "28em", "fontSize": "0.85em",
                               "padding": "0.25em 0.5em"},
                    ),
                    html.Button(
                        "Clear choice", id="nwin-clear-btn", n_clicks=0,
                        title="Remove the recorded choice for this window",
                        style={"marginLeft": "0.75em", "padding": "0.3em 0.9em",
                               "fontSize": "0.85em"},
                    ),
                    html.Button(
                        "Clear all", id="nwin-clear-all-btn", n_clicks=0,
                        title="Remove every recorded choice for this source "
                              "(deletes nwin_choices.json)",
                        style={"marginLeft": "0.5em", "padding": "0.3em 0.9em",
                               "fontSize": "0.85em", "color": "#a33"},
                    ),
                    html.Button(
                        "Generate rerun command",
                        id="nwin-cmd-btn", n_clicks=0,
                        title="find_clusters.py command applying the recorded "
                              "choices via --N_win_file (run it in your "
                              "production working directory)",
                        style={"marginLeft": "1.5em", "padding": "0.3em 0.9em",
                               "fontSize": "0.85em", "background": "#d68a00",
                               "color": "white", "border": "none",
                               "borderRadius": "4px", "cursor": "pointer"},
                    ),
                ],
                style={**_ROW, "padding": "0.4em 0"},
            ),
            # Recorded choices + generated command
            html.Div(id="nwin-choices-list",
                     style={"fontSize": "0.85em", "color": "#444",
                            "padding": "0.1em 0 0.3em"}),
            html.Div(
                [
                    dcc.Clipboard(
                        target_id="nwin-cmd-text",
                        title="Copy command",
                        style={"marginRight": "0.5em", "cursor": "pointer"},
                    ),
                    dcc.Textarea(
                        id="nwin-cmd-text", value="", readOnly=True,
                        style={"width": "100%", "minHeight": "3.2em",
                               "fontFamily": "ui-monospace, monospace",
                               "fontSize": "0.8em"},
                    ),
                ],
                id="nwin-cmd-row",
                style={"display": "none"},
            ),
        ],
        style={"padding": "0.2em 1.25em 0.4em"},
    )

    graphs = html.Div(
        [
            html.Div(
                dcc.Loading(
                    dcc.Graph(
                        id="nwin-bic-graph",
                        style={"height": "560px"},
                        responsive=True,
                        config={"displaylogo": False},
                    ),
                    type="default",
                ),
                style={"flex": "1 1 0", "minWidth": "0"},
            ),
            html.Div(
                dcc.Loading(
                    dcc.Graph(
                        id="nwin-overlay-graph",
                        style={"height": "560px"},
                        responsive=True,
                        config={"displaylogo": False},
                    ),
                    type="default",
                ),
                style={"flex": "1.2 1 0", "minWidth": "0"},
            ),
        ],
        style={"display": "flex", "padding": "0 1.25em 0.75em"},
    )

    return html.Details(
        [
            html.Summary(
                "🔢  Window-N review (admin — needs local cluster_fits/)",
                style={"cursor": "pointer", "padding": "0.4em 1em",
                       "fontWeight": 600, "color": "#333",
                       "userSelect": "none"},
            ),
            # Hint shown when the source has no cluster_fits on disk (e.g. a
            # server deploy — the sync excludes them) — body hidden then.
            html.Div(id="nwin-hint",
                     style={"display": "none"}),
            html.Div(
                [controls, graphs],
                id="nwin-body",
                style={"display": "none"},
            ),
            dcc.Store(id="nwin-meta", data=None),
            dcc.Store(id="nwin-choices-store", data={}),
            dcc.Store(id="nwin-beam-params", data=None),
        ],
        id="nwin-details",
        open=False,
        style={"borderBottom": "1px solid #ddd", "background": "#f8faf7"},
    )
