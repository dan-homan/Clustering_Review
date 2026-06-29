"""Dashboard admin callbacks: auto-balance + reassign-queue.

Only registered when the app starts in ``--admin`` mode. Each apply
writes ``assignments.json`` and then navigates to ``/dashboard`` —
the route callback in ``app.py`` re-renders the page against the
fresh on-disk state. Same pattern the existing review-page uses for
"write + refresh" actions.
"""

from __future__ import annotations

from pathlib import Path

from dash import Input, Output, State, html, no_update

from ..auth.runtime import current_reviewer
from ..recommendations.store import reviewer_slug
from ..data.assignments import (
    apply_additions, auto_balance, load_store, reassign_queue,
    save_store, submitted_by_map,
)
from ..data.difficulty import score_all
from ..data.loader import list_sources
from .dashboard import known_reviewers


def register_dashboard_callbacks(
    app,
    *,
    results_dir: Path,
    recommendations_dir: Path,
    tokens_path: Path | None,
    reviewer: str,
) -> None:
    """Register the admin-only auto-balance and reassign-queue callbacks.

    Reviewer / source roster are re-read on every callback invocation
    so the live state of the team file + recommendations tree is
    always used — no stale closures.
    """

    # -------------------------------------------------------------------
    # Auto-balance: preview-then-apply
    # -------------------------------------------------------------------

    @app.callback(
        Output("dashboard-ab-modal", "style"),
        Output("dashboard-ab-preview-body", "children"),
        Output("dashboard-ab-preview-store", "data"),
        Input("dashboard-auto-balance-btn", "n_clicks"),
        Input("dashboard-ab-close", "n_clicks"),
        Input("dashboard-ab-cancel", "n_clicks"),
        prevent_initial_call=True,
    )
    def _ab_open_or_close(open_n, close_n, cancel_n):
        from dash import ctx
        if not ctx.triggered_id or ctx.triggered_id != "dashboard-auto-balance-btn":
            return {"display": "none"}, no_update, None

        sources = list_sources(results_dir)
        scored = score_all(sources)
        reviewers = known_reviewers(
            tokens_path, recommendations_dir, reviewer)
        store = load_store(recommendations_dir)
        current_map = {
            r: [rec.source for rec in records]
            for r, records in store.assignments.items()
        }
        sub_map = submitted_by_map(
            recommendations_dir, [s.source for s in sources])
        additions = auto_balance(
            scored_sources=scored,
            reviewers=reviewers,
            current_assignments=current_map,
            submitted_by=sub_map,
        )

        total = sum(len(v) for v in additions.values())
        if total == 0:
            body = html.Div(
                "Nothing to do — every source already has enough "
                "reviewers (assigned or submitted).",
                style={"color": "#666", "padding": "0.5em"},
            )
        else:
            rows = []
            for r in sorted(reviewers):
                srcs = additions.get(r, [])
                rows.append(html.Tr([
                    html.Td(r, style={"padding": "4px 8px",
                                      "fontWeight": 600}),
                    html.Td(str(len(srcs)),
                            style={"padding": "4px 8px",
                                   "textAlign": "right",
                                   "color": "#1a7" if srcs else "#aaa"}),
                    html.Td(
                        ", ".join(srcs) if srcs else "—",
                        style={"padding": "4px 8px", "color": "#555",
                               "fontSize": "0.85em"},
                    ),
                ]))
            body = html.Div([
                html.Div(f"{total} new assignments across "
                         f"{sum(1 for v in additions.values() if v)} "
                         f"reviewer(s).",
                         style={"marginBottom": "0.4em",
                                "fontWeight": 600}),
                html.Table(
                    [html.Thead(html.Tr([
                        html.Th("Reviewer", style={"padding": "4px 8px",
                                                  "textAlign": "left"}),
                        html.Th("# new", style={"padding": "4px 8px",
                                                "textAlign": "right"}),
                        html.Th("Sources", style={"padding": "4px 8px",
                                                  "textAlign": "left"}),
                    ]))] + [html.Tbody(rows)],
                    style={"width": "100%",
                           "borderCollapse": "collapse",
                           "fontFamily": "system-ui, sans-serif"},
                ),
            ])
        return {"display": "block",
                "position": "fixed", "top": 0, "left": 0,
                "right": 0, "bottom": 0,
                "background": "rgba(0,0,0,0.35)", "zIndex": 1000}, body, additions

    @app.callback(
        Output("url", "href", allow_duplicate=True),
        Output("dashboard-admin-status", "children",
               allow_duplicate=True),
        Input("dashboard-ab-apply", "n_clicks"),
        State("dashboard-ab-preview-store", "data"),
        prevent_initial_call=True,
    )
    def _ab_apply(_n, additions):
        if not additions:
            return no_update, "no preview to apply"
        store = load_store(recommendations_dir)
        n = apply_additions(
            store, additions, assigned_by=current_reviewer(reviewer))
        save_store(recommendations_dir, store)
        return "/dashboard", f"auto-balance: added {n} assignments"

    # -------------------------------------------------------------------
    # Reassign queue
    # -------------------------------------------------------------------

    @app.callback(
        Output("dashboard-rq-modal", "style"),
        Input("dashboard-reassign-btn", "n_clicks"),
        Input("dashboard-rq-close", "n_clicks"),
        Input("dashboard-rq-cancel", "n_clicks"),
        prevent_initial_call=True,
    )
    def _rq_open_or_close(open_n, close_n, cancel_n):
        from dash import ctx
        if ctx.triggered_id == "dashboard-reassign-btn":
            return {"display": "block",
                    "position": "fixed", "top": 0, "left": 0,
                    "right": 0, "bottom": 0,
                    "background": "rgba(0,0,0,0.35)", "zIndex": 1000}
        return {"display": "none"}

    @app.callback(
        Output("url", "href", allow_duplicate=True),
        Output("dashboard-admin-status", "children",
               allow_duplicate=True),
        Input("dashboard-rq-apply", "n_clicks"),
        State("dashboard-rq-from", "value"),
        State("dashboard-rq-to", "value"),
        prevent_initial_call=True,
    )
    def _rq_apply(_n, from_r, to_r):
        if not from_r or not to_r:
            return no_update, "pick both From and To"
        if from_r == to_r:
            return no_update, "From and To must differ"
        # Build the slug-keyed submitted_by_map and translate against the
        # reviewer NAME the picker hands back. The store keys on names
        # too (same identity reviewers see in tokens.yaml), so we need
        # to convert the target NAME to its slug before checking the
        # "already submitted by target" guard.
        sources = [s.source for s in list_sources(results_dir)]
        sub_map_by_slug = submitted_by_map(recommendations_dir, sources)
        to_slug = reviewer_slug(to_r)
        sub_map_for_target = {
            s: {to_r} if to_slug in slugs else set()
            for s, slugs in sub_map_by_slug.items()
        }
        store = load_store(recommendations_dir)
        moved, skipped = reassign_queue(
            store,
            from_reviewer=from_r, to_reviewer=to_r,
            submitted_by=sub_map_for_target,
        )
        save_store(recommendations_dir, store)
        msg = f"reassign {from_r}→{to_r}: moved {len(moved)}"
        if skipped:
            msg += f", skipped {len(skipped)}"
        return "/dashboard", msg
