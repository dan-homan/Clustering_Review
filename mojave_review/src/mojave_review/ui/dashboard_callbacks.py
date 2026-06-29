"""Dashboard admin callbacks: auto-balance + reassign-queue.

Only registered when the app starts in ``--admin`` mode. Each apply
writes ``assignments.json`` and then navigates to ``/dashboard`` —
the route callback in ``app.py`` re-renders the page against the
fresh on-disk state. Same pattern the existing review-page uses for
"write + refresh" actions.
"""

from __future__ import annotations

from pathlib import Path

from dash import ALL, Input, Output, State, html, no_update

from ..auth.runtime import current_reviewer
from ..auth.tokens import load_store as load_token_store
from ..recommendations.store import reviewer_slug, source_phase
from ..data.assignments import (
    active_reviewers, apply_additions, auto_balance,
    credit_prior_submissions, load_store, reassign_queue,
    save_store, set_paused, set_source_target_date,
    sources_in_range, submitted_by_map,
)
from ..data.difficulty import score_all
from ..data.loader import list_sources
from .dashboard import known_reviewers, _source_progress_rows


def _name_for_slug(
    tokens_path, recommendations_dir, fallback_reviewer,
) -> dict:
    """Map every known slug → full reviewer name. Used by credit-prior
    so submission files (keyed by slug) get recorded under the same
    name the rest of the app uses."""
    out: dict[str, str] = {}
    if fallback_reviewer:
        out[reviewer_slug(fallback_reviewer)] = fallback_reviewer
    if tokens_path is not None and tokens_path.is_file():
        try:
            ts = load_token_store(tokens_path)
            for u in ts:
                out[reviewer_slug(u.name)] = u.name
        except Exception:
            pass
    # Fall-through entries (slug-only) come from known_reviewers itself,
    # which already inserts the slug as the name when no upstream
    # identity is known. So we don't need to scan submissions here.
    return out


def register_dashboard_callbacks(
    app,
    *,
    results_dir: Path,
    recommendations_dir: Path,
    tokens_path: Path | None,
    reviewer: str,
) -> None:
    """Register the dashboard callbacks: source-progress filter +
    admin-only auto-balance / reassign-queue / team-management /
    target-dates.

    Reviewer / source roster are re-read on every callback invocation
    so the live state of the team file + recommendations tree is
    always used — no stale closures.
    """

    # -------------------------------------------------------------------
    # Source-progress filter (everyone sees this)
    # -------------------------------------------------------------------

    @app.callback(
        Output("dashboard-sources", "data"),
        Input("dashboard-src-filter", "value"),
        prevent_initial_call=False,
    )
    def _src_filter(value):
        store = load_store(recommendations_dir)
        return _source_progress_rows(
            results_dir=results_dir,
            recommendations_dir=recommendations_dir,
            store=store,
            filter_value=value or "in_progress",
        )

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

        all_sources = list_sources(results_dir)
        # Pre-credit pass first: every source the team has ever
        # submitted (including those folded into Stage 3 / archived to
        # considered/) counts as completed work. This must include
        # finalized sources, so use the full source list — not the
        # phase-open subset.
        store = load_store(recommendations_dir)
        nfs = _name_for_slug(
            tokens_path, recommendations_dir,
            current_reviewer(reviewer))
        n_credited = credit_prior_submissions(
            store,
            recommendations_dir=recommendations_dir,
            sources=[s.source for s in all_sources],
            name_for_slug=nfs,
        )

        # Auto-balance candidates: only sources that are open for
        # reviewer recommendations (Stage 2 done). Stage 1/2-in-progress
        # sources aren't ready; finalized sources already had their
        # full review cycle.
        open_sources = [
            s for s in all_sources
            if source_phase(recommendations_dir, s.source) == "open"
        ]
        scored = score_all(open_sources)

        known = known_reviewers(
            tokens_path, recommendations_dir, reviewer)
        reviewers = active_reviewers(store, known)

        current_map = {
            r: [rec.source for rec in records]
            for r, records in store.assignments.items()
        }
        sub_map = submitted_by_map(
            recommendations_dir, [s.source for s in open_sources])
        additions = auto_balance(
            scored_sources=scored,
            reviewers=reviewers,
            current_assignments=current_map,
            submitted_by=sub_map,
        )

        total_new = sum(len(v) for v in additions.values())
        header_lines = [
            html.Div(
                f"Open sources considered: {len(open_sources)} of "
                f"{len(all_sources)} total (only \"Stage 2 done\" "
                f"sources are eligible).",
                style={"color": "#555", "fontSize": "0.85em"},
            ),
            html.Div(
                f"Active reviewers: {len(reviewers)} of {len(known)} "
                f"({len(known) - len(reviewers)} paused).",
                style={"color": "#555", "fontSize": "0.85em",
                       "marginBottom": "0.3em"},
            ),
            html.Div(
                f"Credited {n_credited} prior submission(s) as "
                f"completed assignments — these will be saved when "
                f"you Apply.",
                style={"color": "#1a7" if n_credited else "#888",
                       "fontSize": "0.85em", "marginBottom": "0.5em"},
            ),
        ]

        if total_new == 0:
            body_inner = html.Div(
                "No new assignments needed — every open source already "
                "has enough committed reviewers.",
                style={"color": "#666", "padding": "0.4em"},
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
            body_inner = html.Div([
                html.Div(f"{total_new} new assignment(s) across "
                         f"{sum(1 for v in additions.values() if v)} "
                         f"reviewer(s):",
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
        body = html.Div(header_lines + [body_inner])
        # Apply needs both the credited-store state (so we don't
        # re-credit on apply — that's now a State input) and the
        # additions to merge. Bundle them.
        preview_store = {
            "additions": additions,
            "credited_store": store.to_dict(),
            "n_credited": n_credited,
        }
        return {"display": "block",
                "position": "fixed", "top": 0, "left": 0,
                "right": 0, "bottom": 0,
                "background": "rgba(0,0,0,0.35)", "zIndex": 1000}, body, preview_store

    @app.callback(
        Output("url", "href", allow_duplicate=True),
        Output("dashboard-admin-status", "children",
               allow_duplicate=True),
        Input("dashboard-ab-apply", "n_clicks"),
        State("dashboard-ab-preview-store", "data"),
        prevent_initial_call=True,
    )
    def _ab_apply(_n, preview):
        if not preview or preview.get("additions") is None:
            return no_update, "no preview to apply"
        # Re-load from disk and re-credit so the apply is correct even
        # if another admin tab made changes between preview and apply.
        # (We could just commit the preview's "credited_store" verbatim
        # but a fresh credit pass is idempotent and avoids stale-state
        # surprises.)
        store = load_store(recommendations_dir)
        all_sources = list_sources(results_dir)
        nfs = _name_for_slug(
            tokens_path, recommendations_dir,
            current_reviewer(reviewer))
        n_credited = credit_prior_submissions(
            store,
            recommendations_dir=recommendations_dir,
            sources=[s.source for s in all_sources],
            name_for_slug=nfs,
        )
        n_added = apply_additions(
            store, preview["additions"],
            assigned_by=current_reviewer(reviewer))
        save_store(recommendations_dir, store)
        parts = []
        if n_credited:
            parts.append(f"credited {n_credited}")
        parts.append(f"added {n_added}")
        return "/dashboard", "auto-balance: " + ", ".join(parts)

    # -------------------------------------------------------------------
    # Target dates: bulk-by-range + per-source save
    # -------------------------------------------------------------------

    @app.callback(
        Output("dashboard-td-modal", "style"),
        Input("dashboard-td-btn", "n_clicks"),
        Input("dashboard-td-close", "n_clicks"),
        Input("dashboard-td-cancel", "n_clicks"),
        prevent_initial_call=True,
    )
    def _td_open_or_close(open_n, close_n, cancel_n):
        from dash import ctx
        if ctx.triggered_id == "dashboard-td-btn":
            return {"display": "block",
                    "position": "fixed", "top": 0, "left": 0,
                    "right": 0, "bottom": 0,
                    "background": "rgba(0,0,0,0.35)", "zIndex": 1000}
        return {"display": "none"}

    @app.callback(
        Output({"type": "dashboard-td-input", "source": ALL}, "value"),
        Input("dashboard-td-apply-range", "n_clicks"),
        State("dashboard-td-from", "value"),
        State("dashboard-td-to", "value"),
        State("dashboard-td-bulk-date", "value"),
        State({"type": "dashboard-td-input", "source": ALL}, "id"),
        State({"type": "dashboard-td-input", "source": ALL}, "value"),
        prevent_initial_call=True,
    )
    def _td_apply_range(_n, from_src, to_src, bulk_date,
                        ids, current_vals):
        # No bulk date set ⇒ leave everything alone.
        if not bulk_date:
            return current_vals
        # Resolve the source set: empty From/To means "from the
        # beginning / to the end" of the displayed list, matching what
        # the placeholder strings suggest.
        all_srcs = sorted({i["source"] for i in ids
                           if isinstance(i, dict)})
        lo = from_src or (all_srcs[0] if all_srcs else "")
        hi = to_src or (all_srcs[-1] if all_srcs else "")
        in_range = set(sources_in_range(all_srcs, lo, hi))
        return [
            bulk_date if (
                isinstance(i, dict) and i["source"] in in_range
            ) else cur
            for i, cur in zip(ids, current_vals)
        ]

    @app.callback(
        Output("url", "href", allow_duplicate=True),
        Output("dashboard-admin-status", "children",
               allow_duplicate=True),
        Input("dashboard-td-save", "n_clicks"),
        State({"type": "dashboard-td-input", "source": ALL}, "id"),
        State({"type": "dashboard-td-input", "source": ALL}, "value"),
        prevent_initial_call=True,
    )
    def _td_save(_n, ids, values):
        store = load_store(recommendations_dir)
        n_changed = 0
        for ident, val in zip(ids, values):
            # Match the same pattern-matching convention as the rest of
            # the callbacks — see _tm_save's note.
            src = ident["source"] if isinstance(ident, dict) else None
            if not src:
                continue
            new = (val or "").strip() or None
            cur = store.source_target_dates.get(src)
            if new != cur:
                set_source_target_date(store, src, new)
                n_changed += 1
        save_store(recommendations_dir, store)
        return "/dashboard", f"target dates: {n_changed} updated"

    # -------------------------------------------------------------------
    # Manage team — pause / activate individual reviewers
    # -------------------------------------------------------------------

    @app.callback(
        Output("dashboard-tm-modal", "style"),
        Input("dashboard-team-btn", "n_clicks"),
        Input("dashboard-tm-close", "n_clicks"),
        Input("dashboard-tm-cancel", "n_clicks"),
        prevent_initial_call=True,
    )
    def _tm_open_or_close(open_n, close_n, cancel_n):
        from dash import ctx
        if ctx.triggered_id == "dashboard-team-btn":
            return {"display": "block",
                    "position": "fixed", "top": 0, "left": 0,
                    "right": 0, "bottom": 0,
                    "background": "rgba(0,0,0,0.35)", "zIndex": 1000}
        return {"display": "none"}

    @app.callback(
        Output("url", "href", allow_duplicate=True),
        Output("dashboard-admin-status", "children",
               allow_duplicate=True),
        Input("dashboard-tm-save", "n_clicks"),
        State({"type": "dashboard-tm-status", "reviewer": ALL}, "value"),
        State({"type": "dashboard-tm-status", "reviewer": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _tm_save(_n, statuses, ids):
        # Same i["..."] dict-indexing convention as the aggregation
        # callback in ui/callbacks.py — real browser dispatch hands
        # pattern-matching IDs through as dicts; test_client doesn't,
        # but that's a test-harness limitation we don't try to paper
        # over here.
        store = load_store(recommendations_dir)
        previous_paused = set(store.paused_reviewers)
        for status, ident in zip(statuses, ids):
            set_paused(store, ident["reviewer"], status == "paused")
        save_store(recommendations_dir, store)
        new_paused = set(store.paused_reviewers)
        delta = (
            f"+{len(new_paused - previous_paused)} paused / "
            f"-{len(previous_paused - new_paused)} resumed"
        )
        return "/dashboard", f"team updated: {delta}"

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
