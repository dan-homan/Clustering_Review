"""Admin-only callbacks for the recommendations panel.

Currently: the "Generate baseline apply command (Stage 2)" button and its
modal (the button itself lives in the Stage-2 admin block in ui/layout.py).

Only registered when ``mojave-review`` was started with ``--admin``;
the corresponding layout chunks are also only emitted in that case
(see ``ui/recommendations_panel.build_recommendations_panel``).
"""

from __future__ import annotations

import shlex
from pathlib import Path

from dash import Dash, Input, Output, State, no_update

from ..auth.runtime import current_reviewer
from ..data.loader import _SOURCE_DIR_RE
from ..recommendations.store import (
    is_submitted, submission_path, rec_path, source_phase)


def _source_name_from_folder(folder_str: str | None) -> str | None:
    if not folder_str:
        return None
    m = _SOURCE_DIR_RE.match(Path(folder_str).name)
    return m.group("source") if m else None


def _build_apply_command(
    *, results_dir: Path, source_folder: str, recommendation_path: Path,
) -> str:
    """Format as one flag/value pair per line. Quote each argument so paths
    with spaces / shell metacharacters survive a copy-paste."""
    lines = [
        "mojave-apply",
        f"--results-dir {shlex.quote(str(results_dir))}",
        f"--source {shlex.quote(Path(source_folder).name)}",
        f"--recommendation {shlex.quote(str(recommendation_path))}",
    ]
    return " \\\n    ".join(lines)


def register_admin(
    app: Dash, *,
    results_dir: Path,
    recommendations_dir: Path,
    reviewer: str,
) -> None:

    # ---- "Generate baseline apply command (Stage 2)" click handler -------
    # Prefers the submitted/<slug>.json (the reviewer's signed-off
    # snapshot). Falls back to current/<slug>.json if no submission yet,
    # so admins can still apply an in-progress draft locally.
    @app.callback(
        Output("apply-cmd-modal", "style"),
        Output("apply-cmd-text", "value"),
        Output("apply-cmd-hint", "children"),
        Input("generate-apply-cmd-btn", "n_clicks"),
        State("source-picker", "value"),
        State("model-picker", "value"),
        prevent_initial_call=True,
    )
    def _do_generate(_n, source_folder, model_key):
        if not source_folder or model_key != "current":
            return no_update, no_update, no_update
        source_name = _source_name_from_folder(source_folder)
        if source_name is None:
            return no_update, no_update, no_update

        cur_reviewer = current_reviewer(reviewer)
        if is_submitted(recommendations_dir, source_name, cur_reviewer):
            target = submission_path(recommendations_dir, source_name, cur_reviewer)
            hint = (f"Targeting the SUBMITTED recommendation "
                    f"({target.parent.name}/{target.name}).")
        else:
            target = rec_path(recommendations_dir, source_name, "current", cur_reviewer)
            hint = (f"No submission found yet — falling back to your "
                    f"in-progress current recommendation "
                    f"({target.parent.name}/{target.name}). "
                    f"Click \"Submit Recommendation\" first to freeze it.")

        cmd = _build_apply_command(
            results_dir=results_dir,
            source_folder=source_folder,
            recommendation_path=target,
        )

        overlay_style = {
            "display": "block", "position": "fixed",
            "top": "0", "left": "0", "right": "0", "bottom": "0",
            "background": "rgba(0,0,0,0.4)", "zIndex": 1000,
            "overflow": "auto",
        }
        return overlay_style, cmd, hint

    # ---- Close the apply-command modal -----------------------------------
    @app.callback(
        Output("apply-cmd-modal", "style", allow_duplicate=True),
        Input("close-apply-cmd-modal", "n_clicks"),
        Input("close-apply-cmd-modal-2", "n_clicks"),
        prevent_initial_call=True,
    )
    def _close_apply_cmd_modal(_a, _b):
        return {"display": "none"}

    # ---- Stage-gate the baseline-apply (Stage 2) button -----------------
    # Hidden on non-current models (read-only views) AND once the source is
    # past Stage 2 (phase open/final): from Stage-2-done onward the Stage-3
    # aggregated apply is the right path, so we hide the Stage-2 baseline
    # apply to avoid confusing the two. Pairs with the Stage-3 panel's own
    # visibility toggle (_toggle_agg_panel).
    @app.callback(
        Output("generate-apply-cmd-btn", "style"),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
        Input("reload-counter", "data"),
    )
    def _toggle_btn_visibility(source_folder, model_key, _reload_counter):
        base = {"padding": "0.3em 0.9em", "fontSize": "0.85em",
                "background": "#d68a00", "color": "white",
                "border": "none", "borderRadius": "4px", "cursor": "pointer"}
        source_name = _source_name_from_folder(source_folder)
        if not source_folder or model_key != "current" or source_name is None:
            return {**base, "display": "none"}
        if source_phase(recommendations_dir, source_name) in ("open", "final"):
            return {**base, "display": "none"}
        return base

    # ---- Clientside "Copy command" inside the apply-command modal -------
    app.clientside_callback(
        """
        function(n_clicks, text) {
            if (!n_clicks) return window.dash_clientside.no_update;
            if (!text) return window.dash_clientside.no_update;
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).catch(() => {});
            } else {
                const ta = document.createElement("textarea");
                ta.value = text;
                ta.style.position = "fixed";
                ta.style.opacity = "0";
                document.body.appendChild(ta);
                ta.select();
                try { document.execCommand("copy"); } catch (_) {}
                document.body.removeChild(ta);
            }
            setTimeout(() => {
                const btn = document.getElementById("copy-apply-cmd");
                if (btn) btn.textContent = "Copy command";
            }, 1500);
            return "Copied!";
        }
        """,
        Output("copy-apply-cmd", "children"),
        Input("copy-apply-cmd", "n_clicks"),
        State("apply-cmd-text", "value"),
        prevent_initial_call=True,
    )
