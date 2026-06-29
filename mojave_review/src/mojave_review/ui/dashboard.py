"""Read-only assignment & progress dashboard.

A separate page at ``/dashboard``, linked from the review page header.
Phase 2 is intentionally read-only — admin edit controls land in
Phase 3. The page renders three tables from data already on disk:

* **My queue** — the current reviewer's assignments, with per-source
  status (pending / in_progress / submitted) and target date.
* **The team** — every known reviewer, with assigned / submitted /
  in-progress / stale counts.
* **All submitted reviews** — every source that has any submission,
  with reviewer slugs (a click-through link to the review page's
  ``Rec: <slug>`` view is a Phase 2 follow-up — for now the slugs are
  shown as plain text so the page is still informational).

Page state does not persist across navigation in either direction. The
header link uses ``target="_blank"`` so reviewers can keep the dashboard
open in its own tab while they work in the main review tab.
"""

from __future__ import annotations

from pathlib import Path

from dash import dash_table, dcc, html

from ..auth.tokens import load_store as load_token_store
from ..data.loader import list_sources
from ..recommendations.store import (
    count_submissions, is_submitted, list_other_reviewer_slugs,
    reviewer_slug,
)
from ..data.assignments import (
    AssignmentStore, assignment_status, is_stale, load_store, needs_for,
)


# ---------------------------------------------------------------------------
# Reviewer roster
# ---------------------------------------------------------------------------


def known_reviewers(
    tokens_path: Path | None, recommendations_dir: Path,
    fallback_reviewer: str,
) -> list[str]:
    """The team list, taken from the union of three signals so the
    dashboard never lies about who's on the project:

    * tokens.yaml (authoritative when present)
    * any reviewer who has submitted *anything* under ``recommendations/``
    * the fallback (single-user) reviewer name

    Returns alphabetically-sorted full reviewer names (NOT slugs) so the
    dashboard tables read as a roster, not a filename listing.
    """
    names: set[str] = {fallback_reviewer} if fallback_reviewer else set()
    if tokens_path is not None and Path(tokens_path).is_file():
        try:
            ts = load_token_store(Path(tokens_path))
            names.update(u.name for u in ts)
        except Exception:
            pass

    # Slugs found on disk under <recs>/<src>/submitted/<slug>.json — if a
    # tokens.yaml is in use these will already match a User.name (via
    # reviewer_slug); without tokens.yaml the slugs ARE the only identity
    # we have, so we expose them as names.
    if recommendations_dir.is_dir():
        on_disk_slugs: set[str] = set()
        for sub_dir in recommendations_dir.glob("*/submitted"):
            for f in sub_dir.glob("*.json"):
                if f.stem:
                    on_disk_slugs.add(f.stem)
        # Drop slugs that are already covered by a known reviewer's slug.
        covered = {reviewer_slug(n) for n in names}
        names.update(s for s in on_disk_slugs if s not in covered)

    return sorted(names)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


_CARD_STYLE = {
    "border": "1px solid #e0e0e0", "borderRadius": "6px",
    "background": "#fff", "padding": "0.75em 1em",
    "marginBottom": "1em",
}
_TABLE_STYLE = {
    "fontFamily": "system-ui, sans-serif", "fontSize": "0.88em",
}
_CELL_STYLE = {
    "padding": "6px 10px", "textAlign": "left",
}


def _status_chip(text: str, color: str) -> dict:
    return {
        "if": {"filter_query": f'{{status}} = "{text}"', "column_id": "status"},
        "color": color, "fontWeight": 600,
    }


def _my_queue_table(
    store: AssignmentStore, recommendations_dir: Path, reviewer: str,
) -> html.Div:
    records = store.assignments.get(reviewer, [])
    if not records:
        msg = ("You have no assignments yet."
               if reviewer else "No reviewer identity in this session.")
        return html.Div(
            [html.H3("My queue", style={"margin": "0 0 0.5em"}),
             html.Div(msg, style={"color": "#777"})],
            style=_CARD_STYLE,
        )
    rows = []
    for rec in sorted(records, key=lambda r: (
            r.target_date or "9999-12-31", r.source)):
        status = assignment_status(
            recommendations_dir, rec.source, reviewer)
        rows.append({
            "source": rec.source,
            "target_date": rec.target_date or "—",
            "status": status,
            "assigned_at": (rec.assigned_at or "")[:10],
        })
    n_done = sum(1 for r in rows if r["status"] == "submitted")
    n_progress = sum(1 for r in rows if r["status"] == "in_progress")
    summary = (f"{len(rows)} assigned · {n_done} submitted · "
               f"{n_progress} in progress · "
               f"{len(rows) - n_done - n_progress} pending")
    return html.Div(
        [
            html.H3(f"My queue — {reviewer}",
                    style={"margin": "0 0 0.25em"}),
            html.Div(summary,
                     style={"color": "#666", "fontSize": "0.85em",
                            "marginBottom": "0.5em"}),
            dash_table.DataTable(
                id="dashboard-my-queue",
                data=rows,
                columns=[
                    {"name": "Source", "id": "source"},
                    {"name": "Target date", "id": "target_date"},
                    {"name": "Status", "id": "status"},
                    {"name": "Assigned", "id": "assigned_at"},
                ],
                style_table=_TABLE_STYLE,
                style_cell=_CELL_STYLE,
                style_data_conditional=[
                    _status_chip("submitted", "#1a7a1a"),
                    _status_chip("in_progress", "#b9770e"),
                    _status_chip("pending", "#888"),
                ],
                style_header={"fontWeight": 700, "background": "#f5f5f5"},
                page_size=50,
            ),
        ],
        style=_CARD_STYLE,
    )


def _team_table(
    store: AssignmentStore, recommendations_dir: Path,
    reviewers: list[str],
) -> html.Div:
    today = None  # is_stale default is today (real)
    rows = []
    for reviewer in reviewers:
        assigned = store.assignments.get(reviewer, [])
        n_assigned = len(assigned)
        n_submitted = sum(
            1 for r in assigned
            if is_submitted(recommendations_dir, r.source, reviewer))
        n_progress = sum(
            1 for r in assigned
            if not is_submitted(recommendations_dir, r.source, reviewer)
            and assignment_status(
                recommendations_dir, r.source, reviewer) == "in_progress")
        n_stale = sum(1 for r in assigned if is_stale(r, today=today))
        rows.append({
            "reviewer": reviewer,
            "assigned": n_assigned,
            "submitted": n_submitted,
            "in_progress": n_progress,
            "stale": n_stale,
        })
    return html.Div(
        [
            html.H3("The team", style={"margin": "0 0 0.5em"}),
            dash_table.DataTable(
                id="dashboard-team",
                data=rows,
                columns=[
                    {"name": "Reviewer", "id": "reviewer"},
                    {"name": "Assigned", "id": "assigned"},
                    {"name": "Submitted", "id": "submitted"},
                    {"name": "In progress", "id": "in_progress"},
                    {"name": "Stale", "id": "stale"},
                ],
                style_table=_TABLE_STYLE,
                style_cell=_CELL_STYLE,
                style_data_conditional=[
                    {"if": {"filter_query": "{stale} > 0",
                            "column_id": "stale"},
                     "color": "#c62828", "fontWeight": 700},
                ],
                style_header={"fontWeight": 700, "background": "#f5f5f5"},
                page_size=50,
            ),
        ],
        style=_CARD_STYLE,
    )


def _submissions_table(
    results_dir: Path, recommendations_dir: Path,
) -> html.Div:
    rows = []
    for src in list_sources(results_dir):
        n = count_submissions(recommendations_dir, src.source)
        if n == 0:
            continue
        slugs = list_other_reviewer_slugs(
            recommendations_dir, src.source, exclude_slug="")
        rows.append({
            "source": src.source,
            "n_submitted": n,
            "needs_more": needs_for(recommendations_dir, src.source),
            "reviewers": ", ".join(slugs) if slugs else "",
        })
    rows.sort(key=lambda r: (-r["n_submitted"], r["source"]))
    return html.Div(
        [
            html.H3("All submitted reviews",
                    style={"margin": "0 0 0.25em"}),
            html.Div(
                "Click-through to a reviewer's submitted JSON is a "
                "Phase 2 follow-up — for now the names list the "
                "reviewers who submitted.",
                style={"color": "#666", "fontSize": "0.82em",
                       "marginBottom": "0.5em"},
            ),
            dash_table.DataTable(
                id="dashboard-submissions",
                data=rows,
                columns=[
                    {"name": "Source", "id": "source"},
                    {"name": "Submitted", "id": "n_submitted"},
                    {"name": "Still needed", "id": "needs_more"},
                    {"name": "Reviewers", "id": "reviewers"},
                ],
                style_table=_TABLE_STYLE,
                style_cell={**_CELL_STYLE, "maxWidth": "320px",
                            "whiteSpace": "normal"},
                style_data_conditional=[
                    {"if": {"filter_query": "{needs_more} = 0",
                            "column_id": "needs_more"},
                     "color": "#1a7a1a", "fontWeight": 700},
                ],
                style_header={"fontWeight": 700, "background": "#f5f5f5"},
                page_size=200,
            ),
        ],
        style=_CARD_STYLE,
    )


def build_dashboard_page(
    results_dir: Path,
    recommendations_dir: Path,
    reviewer: str,
    admin: bool,
    tokens_path: Path | None,
    back_href: str = "/",
) -> html.Div:
    store = load_store(recommendations_dir)
    reviewers = known_reviewers(tokens_path, recommendations_dir, reviewer)

    # Overall progress: count (source, reviewer) pairs that need a review
    # and how many have one. Drives the top-of-page banner.
    total_open_slots = 0
    total_submissions = 0
    for src in list_sources(results_dir):
        sub = count_submissions(recommendations_dir, src.source)
        total_submissions += sub
        total_open_slots += store.default_review_target  # naive total target

    header = html.Div(
        [
            html.A("← Back to review", href=back_href,
                   style={"color": "#1f77b4", "textDecoration": "none",
                          "marginRight": "1.5em", "fontSize": "0.9em"}),
            html.Span(f"Viewing as: {reviewer or '(no identity)'}",
                      style={"color": "#555", "marginRight": "1em"}),
            html.Span("admin" if admin else "",
                      style={"color": "#b9770e", "fontWeight": 700,
                             "fontSize": "0.85em"}),
        ],
        style={"display": "flex", "alignItems": "center",
               "padding": "0.5em 1em",
               "borderBottom": "1px solid #e0e0e0",
               "background": "#fafafa"},
    )

    banner_parts = [
        f"{total_submissions} submissions across all sources",
        f"target {store.default_review_target} reviews each",
    ]
    if store.deadline:
        banner_parts.append(f"deadline {store.deadline}")
    banner = html.Div(
        " · ".join(banner_parts),
        style={"padding": "0.5em 1em", "color": "#444",
               "background": "#f5f9ff",
               "borderBottom": "1px solid #e0e0e0",
               "fontSize": "0.9em"},
    )

    body = html.Div(
        [
            _my_queue_table(store, recommendations_dir, reviewer),
            _team_table(store, recommendations_dir, reviewers),
            _submissions_table(results_dir, recommendations_dir),
        ],
        style={"padding": "1em", "maxWidth": "1100px", "margin": "0 auto"},
    )

    return html.Div(
        [header, banner, body],
        style={"fontFamily": "system-ui, sans-serif", "minHeight": "100vh"},
    )
