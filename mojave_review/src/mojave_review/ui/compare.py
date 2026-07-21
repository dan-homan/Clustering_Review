"""XVIII-vs-clustering comparison page.

A standalone read-only page (``/compare``) with two side-by-side panels,
each behaving like the main page's right-hand pane: a view selector
(Position / Position Angle / Flux / Kinematics / Epoch overlay) plus a
per-epoch overlay whose epoch stepper is independent of the other side.
The left panel shows the old MOJAVE Paper XVIII Gaussian fits; the right
panel shows the current clustering fits. Polarization is dropped on both
sides (XVIII has none), per the shared-views decision.

Only sources present in BOTH the XVIII table and the current Results, and
whose clustering is finalized (Stage 3 done), are offered.
"""

from __future__ import annotations

from pathlib import Path

from dash import dcc, html

from ..data.fits_cache import split_source_band
from ..data.loader import list_sources
from ..data.xviii import xviii_sources
from ..recommendations import store
from .urls import rel

# View strings shared by both panels (order = dropdown order). "overlay" is
# the per-epoch FITS/CC overlay; the rest are build_summary_figure views.
_MODE_OPTIONS = [
    {"label": "Epoch overlay", "value": "overlay"},
    {"label": "Position", "value": "Position"},
    {"label": "Position Angle", "value": "Position Angle"},
    {"label": "Flux", "value": "Flux"},
    {"label": "Kinematics", "value": "Kinematics"},
]

# Panel id prefixes. XVIII on the left, clustering on the right.
XVIII = "cmp-x"
CLUST = "cmp-c"


def compare_source_options(
    results_dir: Path, recommendations_dir: Path | None,
    xviii_path: str | None = None,
) -> list[dict]:
    """Source dropdown options: XVIII ∩ current Results ∩ finalized clustering.

    Value = source folder path; label = source name; ``search`` = source
    name for type-to-filter."""
    xv = xviii_sources(xviii_path)
    out: list[dict] = []
    for s in list_sources(results_dir):
        no_band, _ = split_source_band(s.source)
        if no_band not in xv:
            continue
        if recommendations_dir is not None and \
                store.source_phase(recommendations_dir, s.source) != "final":
            continue
        out.append({"label": s.source, "value": str(s.folder),
                    "search": s.source})
    out.sort(key=lambda o: o["search"])
    return out


def _panel(prefix: str, title: str, subtitle: str, accent: str) -> html.Div:
    """One comparison panel (mode selector + overlay controls + graphs).

    Epoch stepping is NOT here — it lives in a shared bar above both panels so
    the two sides always show the same epoch."""
    return html.Div(
        [
            html.Div(
                [
                    html.Span(title, style={"fontWeight": "bold",
                                            "color": accent,
                                            "marginRight": "0.6em"}),
                    html.Span(subtitle, style={"color": "#888",
                                               "fontSize": "0.85em"}),
                ],
                style={"padding": "0.25em 0.5em"},
            ),
            html.Div(
                [
                    dcc.Dropdown(
                        id=f"{prefix}-mode",
                        options=_MODE_OPTIONS,
                        value="overlay",
                        clearable=False,
                        style={"minWidth": "180px", "flex": "1 1 0"},
                    ),
                    dcc.Checklist(
                        id=f"{prefix}-use-fits",
                        options=[{"label": " Use FITS image", "value": "fits"}],
                        value=[],
                        style={"fontSize": "0.85em", "marginLeft": "0.6em"},
                    ),
                    html.Button("Reset view", id=f"{prefix}-reset", n_clicks=0,
                                title="Redraw and reset the overlay zoom",
                                style={"marginLeft": "0.6em",
                                       "padding": "0.2em 0.6em",
                                       "fontSize": "0.85em"}),
                ],
                style={"display": "flex", "alignItems": "center",
                       "margin": "0.25em 0.5em"},
            ),
            # Vector-scale slider (Kinematics only; hidden otherwise).
            html.Div(
                [
                    html.Span("Vector scale:",
                              style={"marginRight": "0.5em", "fontSize": "0.9em"}),
                    html.Div(
                        dcc.Slider(
                            id=f"{prefix}-vector-scale", min=0.2, max=5.0,
                            step=0.1, value=1.0, marks=None,
                            tooltip={"placement": "bottom",
                                     "always_visible": False},
                            updatemode="mouseup",
                        ),
                        style={"flex": "1 1 0", "minWidth": "120px"},
                    ),
                ],
                id=f"{prefix}-vector-scale-row",
                style={"display": "none", "alignItems": "center",
                       "padding": "0.25em 0.5em"},
            ),
            # Overlay mode: overlay graph (epoch stepper is shared, above).
            html.Div(
                [
                    dcc.Loading(
                        dcc.Graph(id=f"{prefix}-overlay-graph",
                                  style={"height": "640px"}, responsive=True),
                        type="default",
                    ),
                ],
                id=f"{prefix}-overlay-container",
            ),
            # Summary mode: single graph (hidden by default).
            html.Div(
                dcc.Loading(
                    dcc.Graph(
                        id=f"{prefix}-summary-graph",
                        style={"height": "640px"}, responsive=True,
                        config={"modeBarButtonsToRemove": ["select2d",
                                                           "lasso2d"],
                                "displaylogo": False},
                    ),
                    type="default",
                ),
                id=f"{prefix}-summary-container",
                style={"display": "none"},
            ),
            # Per-panel stores.
            dcc.Store(id=f"{prefix}-beam-params"),
            dcc.Store(id=f"{prefix}-reset-counter", data=0),
        ],
        id=f"{prefix}-panel",
        style={"flex": "1 1 0", "padding": "0.5em", "minWidth": "0"},
    )


def build_compare_page(
    results_dir: Path, recommendations_dir: Path,
    reviewer: str, admin: bool = False,
    xviii_path: str | None = None,
) -> html.Div:
    source_options = compare_source_options(results_dir, recommendations_dir,
                                            xviii_path)
    initial = source_options[0]["value"] if source_options else None

    header = html.Div(
        [
            html.Div(
                [
                    html.H2("XVIII vs. Clustering",
                            style={"margin": "0", "display": "inline-block"}),
                    html.A("← Back to review", href=rel("/"),
                           style={"marginLeft": "1.5em", "color": "#1f77b4",
                                  "textDecoration": "none", "fontSize": "0.9em",
                                  "verticalAlign": "middle"}),
                ],
                style={"display": "flex", "alignItems": "baseline"},
            ),
            html.Div(
                [
                    html.Span("Source:", style={"marginRight": "0.5em"}),
                    dcc.Dropdown(
                        id="cmp-source-picker",
                        options=source_options,
                        value=initial,
                        clearable=False,
                        style={"minWidth": "260px", "display": "inline-block"},
                    ),
                ],
                style={"display": "flex", "alignItems": "center"},
            ),
        ],
        style={"display": "flex", "justifyContent": "space-between",
               "alignItems": "center", "padding": "0.5em 1em",
               "borderBottom": "1px solid #ddd", "gap": "1em",
               "flexWrap": "wrap"},
    )

    # Shared epoch stepper — one master epoch axis (union of both sides), so
    # both panels always show the same epoch. A side that lacks the selected
    # epoch renders a blank map.
    epoch_bar = html.Div(
        [
            html.Span("Epoch:", style={"marginRight": "0.6em",
                                       "fontWeight": "bold"}),
            html.Button("◀", id="cmp-epoch-prev", n_clicks=0,
                        title="Previous epoch", style={"marginRight": "0.3em"}),
            html.Button("▶", id="cmp-epoch-next", n_clicks=0,
                        title="Next epoch", style={"marginRight": "0.8em"}),
            html.Div(
                dcc.Slider(
                    id="cmp-epoch-slider", min=0, max=0, step=1, value=0,
                    marks={}, included=False,
                    tooltip={"placement": "bottom", "always_visible": False},
                    updatemode="mouseup",
                ),
                style={"flex": "1 1 0", "minWidth": "220px"},
            ),
            html.Span(id="cmp-epoch-label",
                      style={"marginLeft": "1em", "minWidth": "11em",
                             "color": "#555",
                             "fontFamily": "ui-monospace, monospace"}),
        ],
        style={"display": "flex", "alignItems": "center",
               "padding": "0.4em 1em", "borderBottom": "1px solid #eee"},
    )

    body = html.Div(
        [
            _panel(XVIII, "MOJAVE XVIII", "Gaussian fits (Lister et al.)",
                   "#8B0000"),
            html.Div(className="split-handle", style={"width": "2px",
                                                      "background": "#ddd"}),
            _panel(CLUST, "Clustering", "current fits", "#1f4e79"),
        ],
        style={"display": "flex", "padding": "0.5em"},
    )

    return html.Div(
        [
            dcc.Store(id="cmp-reviewer-store", data=reviewer),
            # Decimal year of the shared selected epoch — drives the vertical
            # epoch marker on each panel's summary (epoch-axis) views.
            dcc.Store(id="cmp-active-epoch", data=None),
            dcc.Store(id="cmp-x-epoch-line-dummy", data=None),
            dcc.Store(id="cmp-c-epoch-line-dummy", data=None),
            html.Div(
                f"No finalized sources are present in both the XVIII table "
                f"and the current results."
                if not source_options else "",
                style={"padding": "0.5em 1em", "color": "#a00"}
                if not source_options else {"display": "none"},
            ),
            header,
            epoch_bar,
            body,
        ]
    )
