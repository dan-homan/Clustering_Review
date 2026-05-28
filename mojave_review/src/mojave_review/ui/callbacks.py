"""Dash callbacks wiring the UI to the data + plot functions."""

from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
from dash import Dash, Input, Output, no_update

from ..data.loader import list_models, load_bundle
from ..plots.summary import build_summary_figure


def register_callbacks(
    app: Dash,
    *,
    results_dir: Path,
    recommendations_dir: Path,
    cache_dir: Path,
    reviewer: str,
) -> None:
    # When the source changes, refresh the model-picker options.
    @app.callback(
        Output("model-picker", "options"),
        Output("model-picker", "value"),
        Input("source-picker", "value"),
    )
    def _populate_models(source_folder: str | None):
        if not source_folder:
            return [], None
        from ..data.loader import _SOURCE_DIR_RE, SourceRef  # local import to avoid cycle
        folder = Path(source_folder)
        m = _SOURCE_DIR_RE.match(folder.name)
        if not m:
            return [], None
        src = SourceRef(
            source=m.group("source"),
            epoch_min=float(m.group("emin")),
            epoch_max=float(m.group("emax")),
            folder=folder,
        )
        models = list_models(src)
        opts = [{"label": mf.label, "value": mf.key} for mf in models]
        return opts, (opts[0]["value"] if opts else None)

    # Rebuild the summary figure whenever source / model / view / scale changes.
    @app.callback(
        Output("summary-graph", "figure"),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
        Input("view-picker", "value"),
        Input("vector-scale", "value"),
    )
    def _refresh_summary(
        source_folder: str | None,
        model_key: str | None,
        view: str,
        vector_scale: float,
    ):
        if not source_folder or not model_key:
            return go.Figure()
        bundle = load_bundle(source_folder, model_key)
        return build_summary_figure(
            bundle.cluster_df,
            view=view,
            vector_scale_factor=vector_scale or 1.0,
        )

    # Hide the vector-scale slider when not on the Kinematics view.
    @app.callback(
        Output("vector-scale-row", "style"),
        Input("view-picker", "value"),
    )
    def _toggle_scale_row(view: str):
        base = {"alignItems": "center", "padding": "0.25em 1em", "fontSize": "0.9em"}
        if view == "Kinematics":
            return {**base, "display": "flex"}
        return {**base, "display": "none"}
