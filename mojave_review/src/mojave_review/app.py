"""Dash application factory."""

from __future__ import annotations

from pathlib import Path

from dash import Dash

from .ui.layout import build_layout
from .ui.callbacks import register_callbacks


def create_app(
    results_dir: Path,
    recommendations_dir: Path,
    cache_dir: Path,
    reviewer: str,
) -> Dash:
    app = Dash(__name__, title="MOJAVE Cluster Review", suppress_callback_exceptions=True)
    app.layout = build_layout(results_dir=results_dir, reviewer=reviewer)
    register_callbacks(
        app,
        results_dir=results_dir,
        recommendations_dir=recommendations_dir,
        cache_dir=cache_dir,
        reviewer=reviewer,
    )
    return app
