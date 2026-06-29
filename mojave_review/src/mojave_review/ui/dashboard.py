"""Assignment & progress dashboard.

A separate page at ``/dashboard``, linked from the review page header.
Reviewers see three read-only tables (My queue, The team, All
submitted reviews); admins additionally get an admin-controls block at
the top with two buttons:

* **🔀 Auto-balance assignments** — runs the LPT load-balancer
  (``data/assignments.auto_balance``) using the current difficulty
  scores (``data/difficulty.score_all``) and the team roster. Opens a
  preview modal showing the proposed additions per reviewer before
  anything is written to disk. Apply on the modal commits to
  ``assignments.json`` and refreshes the page.
* **↪ Reassign queue** — bulk-move one reviewer's entire queue to
  another. Used when someone drops out. Skips sources the target has
  already submitted or already been assigned (those stay on the
  original reviewer for manual follow-up).

Per-row remove is intentionally deferred (Phase 3.5) — auto-balance
plus bulk-reassign covers the workflows the user described, and
adding per-row controls means converting the queue tables to a custom
html.Table with pattern-matching ids, which is a bigger lift than the
two buttons here.

Page state does not persist across navigation in either direction. The
header link uses ``target="_blank"`` so reviewers can keep the
dashboard open in its own tab while they work in the main review tab.
"""

from __future__ import annotations

from pathlib import Path

from dash import dash_table, dcc, html

from ..auth.tokens import load_store as load_token_store
from ..data.difficulty import score_all
from ..data.loader import list_sources
from ..recommendations.store import (
    count_submissions, is_submitted, list_other_reviewer_slugs,
    reviewer_slug, source_phase,
)
from ..data.assignments import (
    AssignmentStore, all_assigned_sources, assignment_status,
    get_source_target_date, is_paused, is_stale, load_store, needs_for,
)


# Source-progress table filter — values map to source_phase() outputs.
_FILTER_OPTIONS = [
    {"label": "In Progress (Stage 2 done)", "value": "in_progress"},
    {"label": "Finalized (Stage 3 done)",   "value": "final"},
    {"label": "Stage 1 / Stage 2",          "value": "preopen"},
]
DEFAULT_FILTER = "in_progress"


def _phase_for_filter(value: str) -> set[str]:
    """Map a filter value to the set of source_phase() strings it covers."""
    if value == "in_progress":
        return {"open"}
    if value == "final":
        return {"final"}
    if value == "preopen":
        return {"stage1", "stage2"}
    return {"open", "final", "stage1", "stage2"}


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
            get_source_target_date(store, r.source) or "9999-12-31",
            r.source)):
        status = assignment_status(
            recommendations_dir, rec.source, reviewer)
        tgt = get_source_target_date(store, rec.source) or "—"
        rows.append({
            "source": rec.source,
            "target_date": tgt,
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
        n_stale = sum(
            1 for r in assigned if is_stale(store, r.source, today=today))
        paused = is_paused(store, reviewer)
        rows.append({
            # Visual marker for paused; the DataTable column is text only
            # (this Dash version doesn't accept rich cell content in
            # standard columns) so the badge goes inline.
            "reviewer": f"{reviewer}  ⏸ paused" if paused else reviewer,
            "assigned": n_assigned,
            "submitted": n_submitted,
            "in_progress": n_progress,
            "stale": n_stale,
            "_paused": paused,           # internal — drives row styling
        })
    # Stable order: active first (alphabetical), paused after.
    rows.sort(key=lambda r: (r["_paused"], r["reviewer"]))
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
                    {"if": {"filter_query": "{_paused} = true"},
                     "color": "#888", "fontStyle": "italic",
                     "background": "#fafafa"},
                ],
                style_header={"fontWeight": 700, "background": "#f5f5f5"},
                page_size=50,
            ),
        ],
        style=_CARD_STYLE,
    )


def _source_progress_rows(
    *, results_dir: Path, recommendations_dir: Path,
    store: AssignmentStore, filter_value: str,
) -> list[dict]:
    """Build the rows for the source-progress table under one filter.

    Filter semantics (see ``_FILTER_OPTIONS``):
      * ``in_progress`` → phase == "open"
      * ``final``       → phase == "final"
      * ``preopen``     → phase in {"stage1", "stage2"}
    """
    phases = _phase_for_filter(filter_value)
    all_srcs = list_sources(results_dir)

    # Difficulty scores power the Rating column. score_all reads each
    # source's CSV — cheap (~1 ms / source) and fresh per render.
    by_folder: dict[str, object] = {}
    for d in score_all(all_srcs):
        by_folder[d.folder] = d

    # Who's assigned where? Walk the store once.
    assigned_by_source: dict[str, list[str]] = {}
    for reviewer, recs in store.assignments.items():
        for r in recs:
            assigned_by_source.setdefault(r.source, []).append(reviewer)

    rows = []
    for src in all_srcs:
        if source_phase(recommendations_dir, src.source) not in phases:
            continue
        n_sub = count_submissions(recommendations_dir, src.source)
        slugs = list_other_reviewer_slugs(
            recommendations_dir, src.source, exclude_slug="")
        diff = by_folder.get(src.folder.name)
        rating = (
            ("★" * diff.stars + ("  ⚠" if diff.outlier else ""))
            if diff is not None else "—"
        )
        # Target date is only meaningful for in-progress sources;
        # finalized + stage1/2 get a "—" per the user's spec.
        if filter_value == "in_progress":
            tgt = get_source_target_date(store, src.source) or ""
        else:
            tgt = "—"
        rows.append({
            "source": src.source,
            "rating": rating,
            "n_submitted": n_sub,
            "needs_more": needs_for(recommendations_dir, src.source),
            "reviewers": ", ".join(slugs) if slugs else "",
            "target_date": tgt,
            "assigned_to": ", ".join(
                sorted(assigned_by_source.get(src.source, []))),
        })
    rows.sort(key=lambda r: r["source"])
    return rows


def _source_progress_table(
    *, results_dir: Path, recommendations_dir: Path,
    store: AssignmentStore, initial_filter: str = DEFAULT_FILTER,
) -> html.Div:
    """Source-progress table. The dropdown above it filters by review
    phase; default = In Progress (Stage 2 done). All sources are shown
    even with zero submissions — the table is now a complete source
    progress view, not just "those with submissions"."""
    rows = _source_progress_rows(
        results_dir=results_dir,
        recommendations_dir=recommendations_dir,
        store=store, filter_value=initial_filter,
    )
    return html.Div(
        [
            html.Div(
                [
                    html.H3("Source progress",
                            style={"margin": "0",
                                   "display": "inline-block"}),
                    html.Span("Show:",
                              style={"marginLeft": "1em",
                                     "marginRight": "0.5em",
                                     "color": "#555"}),
                    dcc.Dropdown(
                        id="dashboard-src-filter",
                        options=_FILTER_OPTIONS,
                        value=initial_filter,
                        clearable=False,
                        style={"width": "260px",
                               "display": "inline-block",
                               "verticalAlign": "middle"},
                    ),
                ],
                style={"display": "flex", "alignItems": "center",
                       "flexWrap": "wrap", "marginBottom": "0.4em"},
            ),
            dash_table.DataTable(
                id="dashboard-sources",
                data=rows,
                columns=[
                    {"name": "Source", "id": "source"},
                    {"name": "Rating", "id": "rating"},
                    {"name": "Submitted", "id": "n_submitted"},
                    {"name": "Still needed", "id": "needs_more"},
                    {"name": "Reviewers", "id": "reviewers"},
                    {"name": "Target", "id": "target_date"},
                    {"name": "Assigned to", "id": "assigned_to"},
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


def _target_dates_modal_body(
    open_sources: list[str],
    current_targets: dict[str, str],
) -> list:
    """The per-source list + bulk-by-range form, both inside the modal.
    Pulled out of ``_admin_controls_panel`` so the latter stays
    readable."""
    src_opts = [{"label": s, "value": s} for s in open_sources]
    rows = []
    for s in open_sources:
        rows.append(html.Tr([
            html.Td(s, style={"padding": "4px 8px"}),
            html.Td(
                dcc.Input(
                    id={"type": "dashboard-td-input", "source": s},
                    type="date",
                    value=current_targets.get(s, "") or "",
                    style={"width": "150px", "fontSize": "0.85em"},
                ),
                style={"padding": "4px 8px"},
            ),
        ]))
    bulk_form = html.Div(
        [
            html.Span("Bulk: set date for range",
                      style={"marginRight": "0.5em",
                             "fontWeight": 600,
                             "fontSize": "0.85em"}),
            html.Span("From:", style={"marginRight": "0.25em"}),
            dcc.Dropdown(
                id="dashboard-td-from",
                options=src_opts, clearable=True,
                placeholder="(first)",
                style={"width": "140px",
                       "display": "inline-block",
                       "marginRight": "0.5em",
                       "verticalAlign": "middle"},
            ),
            html.Span("To:", style={"marginRight": "0.25em"}),
            dcc.Dropdown(
                id="dashboard-td-to",
                options=src_opts, clearable=True,
                placeholder="(last)",
                style={"width": "140px",
                       "display": "inline-block",
                       "marginRight": "0.5em",
                       "verticalAlign": "middle"},
            ),
            html.Span("Date:", style={"marginRight": "0.25em"}),
            dcc.Input(
                id="dashboard-td-bulk-date", type="date", value="",
                style={"marginRight": "0.5em",
                       "verticalAlign": "middle"},
            ),
            html.Button(
                "Apply to range",
                id="dashboard-td-apply-range", n_clicks=0,
                title="Fill the date inputs for every source in the "
                      "lexicographic range [From, To]. Tweak individual "
                      "rows after, then Save.",
                style={"padding": "0.3em 0.8em", "fontSize": "0.85em",
                       "verticalAlign": "middle"},
            ),
        ],
        style={"padding": "0.5em", "background": "#f7f9fb",
               "border": "1px solid #e0e0e0", "borderRadius": "4px",
               "marginBottom": "0.6em",
               "display": "flex", "alignItems": "center",
               "flexWrap": "wrap", "gap": "0.25em"},
    )
    per_source = html.Div(
        html.Table(
            [html.Thead(html.Tr([
                html.Th("Source",
                        style={"padding": "4px 8px",
                               "textAlign": "left"}),
                html.Th("Target date",
                        style={"padding": "4px 8px",
                               "textAlign": "left"}),
            ]))] + [html.Tbody(rows)],
            style={"width": "100%", "borderCollapse": "collapse",
                   "fontFamily": "system-ui, sans-serif",
                   "fontSize": "0.88em"},
        ),
        style={"maxHeight": "40vh", "overflowY": "auto",
               "border": "1px solid #e0e0e0", "borderRadius": "4px"},
    )
    return [bulk_form, per_source]


def _admin_controls_panel(
    reviewers: list[str], paused_set: set[str],
    open_sources: list[str],
    current_targets: dict[str, str],
) -> html.Div:
    """Top-of-dashboard admin section: Manage team + Auto-balance +
    Reassign-queue + Set-target-dates buttons, their modals, and a
    status line. Hidden when admin=False."""
    reviewer_opts = [{"label": r, "value": r} for r in reviewers]
    # Team-management rows: one per reviewer with a pause/active select.
    team_rows = []
    for r in reviewers:
        active = r not in paused_set
        team_rows.append(html.Tr([
            html.Td(r, style={"padding": "4px 8px",
                              "fontWeight": 600,
                              "color": "#666" if not active else "#222",
                              "fontStyle": "italic" if not active else "normal"}),
            html.Td(
                dcc.RadioItems(
                    id={"type": "dashboard-tm-status",
                        "reviewer": r},
                    options=[
                        {"label": " active",  "value": "active"},
                        {"label": " paused",  "value": "paused"},
                    ],
                    value="active" if active else "paused",
                    inline=True,
                    inputStyle={"marginRight": "0.25em",
                                "marginLeft": "0.5em"},
                ),
                style={"padding": "4px 8px"},
            ),
        ]))
    return html.Div(
        [
            html.Div(
                [
                    html.Button(
                        "👥 Manage team…",
                        id="dashboard-team-btn", n_clicks=0,
                        title="Toggle individual reviewers between active "
                              "(in the auto-balance pool) and paused "
                              "(excluded from new assignments).",
                        style={"padding": "0.4em 1em", "fontSize": "0.9em",
                               "background": "white", "color": "#555",
                               "border": "1px solid #bbb",
                               "borderRadius": "4px", "cursor": "pointer"},
                    ),
                    html.Button(
                        "🔀 Auto-balance assignments…",
                        id="dashboard-auto-balance-btn", n_clicks=0,
                        title="Preview a balanced assignment using LPT + the "
                              "difficulty score, then choose to apply.",
                        style={"marginLeft": "0.6em",
                               "padding": "0.4em 1em", "fontSize": "0.9em",
                               "background": "#1f77b4", "color": "white",
                               "border": "none", "borderRadius": "4px",
                               "cursor": "pointer"},
                    ),
                    html.Button(
                        "↪ Reassign queue…",
                        id="dashboard-reassign-btn", n_clicks=0,
                        title="Bulk-move one reviewer's queue to another. "
                              "Used when someone drops out mid-review.",
                        style={"marginLeft": "0.6em",
                               "padding": "0.4em 1em", "fontSize": "0.9em",
                               "background": "white", "color": "#555",
                               "border": "1px solid #bbb",
                               "borderRadius": "4px", "cursor": "pointer"},
                    ),
                    html.Button(
                        "📅 Set target dates…",
                        id="dashboard-td-btn", n_clicks=0,
                        title="Set per-source target dates. Bulk-set a "
                              "lexicographic range with one action, then "
                              "tweak individual rows before saving.",
                        style={"marginLeft": "0.6em",
                               "padding": "0.4em 1em", "fontSize": "0.9em",
                               "background": "white", "color": "#555",
                               "border": "1px solid #bbb",
                               "borderRadius": "4px", "cursor": "pointer"},
                    ),
                    html.Span(
                        id="dashboard-admin-status",
                        style={"marginLeft": "1em", "fontSize": "0.85em",
                               "color": "#1a7", "fontWeight": 600},
                    ),
                ],
                style={"display": "flex", "alignItems": "center",
                       "flexWrap": "wrap"},
            ),
            # Team-management modal -------------------------------------
            html.Div(
                id="dashboard-tm-modal", style={"display": "none"},
                children=[html.Div([
                    html.Div(
                        [
                            html.H4("Manage team", style={"margin": 0}),
                            html.Button(
                                "×", id="dashboard-tm-close", n_clicks=0,
                                style={"border": "none", "background": "transparent",
                                       "fontSize": "1.5em", "lineHeight": 1,
                                       "cursor": "pointer", "color": "#888"},
                            ),
                        ],
                        style={"display": "flex",
                               "justifyContent": "space-between",
                               "alignItems": "center",
                               "marginBottom": "0.4em"},
                    ),
                    html.Div(
                        "Paused reviewers stay visible on the dashboard "
                        "but are excluded from auto-balance. Existing "
                        "assignments are preserved either way — to move "
                        "them, use Reassign queue.",
                        style={"color": "#666", "fontSize": "0.85em",
                               "marginBottom": "0.5em"},
                    ),
                    html.Table(
                        [html.Thead(html.Tr([
                            html.Th("Reviewer",
                                    style={"padding": "4px 8px",
                                           "textAlign": "left"}),
                            html.Th("Status",
                                    style={"padding": "4px 8px",
                                           "textAlign": "left"}),
                        ]))] + [html.Tbody(team_rows)],
                        style={"width": "100%",
                               "borderCollapse": "collapse",
                               "fontFamily": "system-ui, sans-serif",
                               "fontSize": "0.88em"},
                    ),
                    html.Div(
                        [
                            html.Button(
                                "Save", id="dashboard-tm-save", n_clicks=0,
                                style={"padding": "0.45em 1em",
                                       "background": "#1f77b4",
                                       "color": "white", "border": "none",
                                       "borderRadius": "4px",
                                       "cursor": "pointer"},
                            ),
                            html.Button(
                                "Cancel", id="dashboard-tm-cancel",
                                n_clicks=0,
                                style={"padding": "0.45em 1em",
                                       "background": "white",
                                       "color": "#555",
                                       "border": "1px solid #bbb",
                                       "borderRadius": "4px",
                                       "cursor": "pointer"},
                            ),
                        ],
                        style={"display": "flex", "gap": "0.5em",
                               "justifyContent": "flex-end",
                               "marginTop": "0.6em"},
                    ),
                ], style={"background": "white", "padding": "1.5em",
                          "borderRadius": "6px", "maxWidth": "520px",
                          "margin": "6% auto",
                          "boxShadow": "0 4px 20px rgba(0,0,0,0.25)"})],
            ),
            # Auto-balance preview modal --------------------------------
            html.Div(
                id="dashboard-ab-modal", style={"display": "none"},
                children=[html.Div([
                    html.Div(
                        [
                            html.H4("Auto-balance preview",
                                    style={"margin": 0}),
                            html.Button(
                                "×", id="dashboard-ab-close", n_clicks=0,
                                style={"border": "none", "background": "transparent",
                                       "fontSize": "1.5em", "lineHeight": 1,
                                       "cursor": "pointer", "color": "#888"},
                            ),
                        ],
                        style={"display": "flex",
                               "justifyContent": "space-between",
                               "alignItems": "center",
                               "marginBottom": "0.4em"},
                    ),
                    html.Div(
                        "Proposed additions (existing assignments are "
                        "preserved). Apply commits to assignments.json and "
                        "refreshes the dashboard.",
                        style={"color": "#666", "fontSize": "0.85em",
                               "marginBottom": "0.4em"},
                    ),
                    html.Div(
                        id="dashboard-ab-preview-body",
                        style={"maxHeight": "44vh", "overflowY": "auto",
                               "fontSize": "0.88em"},
                    ),
                    html.Div(
                        [
                            html.Button(
                                "Apply", id="dashboard-ab-apply", n_clicks=0,
                                style={"padding": "0.45em 1em",
                                       "background": "#1f77b4",
                                       "color": "white", "border": "none",
                                       "borderRadius": "4px",
                                       "cursor": "pointer"},
                            ),
                            html.Button(
                                "Cancel", id="dashboard-ab-cancel",
                                n_clicks=0,
                                style={"padding": "0.45em 1em",
                                       "background": "white",
                                       "color": "#555",
                                       "border": "1px solid #bbb",
                                       "borderRadius": "4px",
                                       "cursor": "pointer"},
                            ),
                        ],
                        style={"display": "flex", "gap": "0.5em",
                               "justifyContent": "flex-end",
                               "marginTop": "0.6em"},
                    ),
                ], style={"background": "white", "padding": "1.5em",
                          "borderRadius": "6px", "maxWidth": "640px",
                          "margin": "6% auto",
                          "boxShadow": "0 4px 20px rgba(0,0,0,0.25)"})],
            ),
            # Reassign-queue modal --------------------------------------
            html.Div(
                id="dashboard-rq-modal", style={"display": "none"},
                children=[html.Div([
                    html.Div(
                        [
                            html.H4("Reassign queue", style={"margin": 0}),
                            html.Button(
                                "×", id="dashboard-rq-close", n_clicks=0,
                                style={"border": "none", "background": "transparent",
                                       "fontSize": "1.5em", "lineHeight": 1,
                                       "cursor": "pointer", "color": "#888"},
                            ),
                        ],
                        style={"display": "flex",
                               "justifyContent": "space-between",
                               "alignItems": "center",
                               "marginBottom": "0.4em"},
                    ),
                    html.Div(
                        "Bulk-move all assignments from one reviewer to "
                        "another. Sources the target already submitted or "
                        "already holds will stay on the source reviewer.",
                        style={"color": "#666", "fontSize": "0.85em",
                               "marginBottom": "0.6em"},
                    ),
                    html.Div(
                        [
                            html.Label("From:",
                                       style={"marginRight": "0.5em"}),
                            dcc.Dropdown(
                                id="dashboard-rq-from",
                                options=reviewer_opts,
                                clearable=False,
                                style={"width": "200px",
                                       "display": "inline-block",
                                       "marginRight": "1em"},
                            ),
                            html.Label("To:",
                                       style={"marginRight": "0.5em"}),
                            dcc.Dropdown(
                                id="dashboard-rq-to",
                                options=reviewer_opts,
                                clearable=False,
                                style={"width": "200px",
                                       "display": "inline-block"},
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center",
                               "flexWrap": "wrap", "gap": "0.5em",
                               "marginBottom": "0.6em"},
                    ),
                    html.Div(
                        [
                            html.Button(
                                "Apply", id="dashboard-rq-apply", n_clicks=0,
                                style={"padding": "0.45em 1em",
                                       "background": "#1f77b4",
                                       "color": "white", "border": "none",
                                       "borderRadius": "4px",
                                       "cursor": "pointer"},
                            ),
                            html.Button(
                                "Cancel", id="dashboard-rq-cancel",
                                n_clicks=0,
                                style={"padding": "0.45em 1em",
                                       "background": "white",
                                       "color": "#555",
                                       "border": "1px solid #bbb",
                                       "borderRadius": "4px",
                                       "cursor": "pointer"},
                            ),
                        ],
                        style={"display": "flex", "gap": "0.5em",
                               "justifyContent": "flex-end",
                               "marginTop": "0.6em"},
                    ),
                ], style={"background": "white", "padding": "1.5em",
                          "borderRadius": "6px", "maxWidth": "560px",
                          "margin": "6% auto",
                          "boxShadow": "0 4px 20px rgba(0,0,0,0.25)"})],
            ),
            # State shared between preview and apply: the proposed additions
            # dict ({reviewer: [src, ...]}) from auto_balance. Holds nothing
            # until the admin clicks "Auto-balance"; cleared on Apply/Cancel.
            dcc.Store(id="dashboard-ab-preview-store", data=None),
            # Target-dates modal ----------------------------------------
            html.Div(
                id="dashboard-td-modal", style={"display": "none"},
                children=[html.Div([
                    html.Div(
                        [
                            html.H4("Set target dates",
                                    style={"margin": 0}),
                            html.Button(
                                "×", id="dashboard-td-close", n_clicks=0,
                                style={"border": "none",
                                       "background": "transparent",
                                       "fontSize": "1.5em",
                                       "lineHeight": 1,
                                       "cursor": "pointer",
                                       "color": "#888"},
                            ),
                        ],
                        style={"display": "flex",
                               "justifyContent": "space-between",
                               "alignItems": "center",
                               "marginBottom": "0.4em"},
                    ),
                    html.Div(
                        "Only Stage-2-done sources are shown — "
                        "finalized and Stage 1/2 sources don't carry "
                        "target dates. Bulk-set a range, then tweak "
                        "individual rows. Save commits everything.",
                        style={"color": "#666", "fontSize": "0.85em",
                               "marginBottom": "0.5em"},
                    ),
                    *_target_dates_modal_body(
                        open_sources, current_targets),
                    html.Div(
                        [
                            html.Button(
                                "Save", id="dashboard-td-save",
                                n_clicks=0,
                                style={"padding": "0.45em 1em",
                                       "background": "#1f77b4",
                                       "color": "white", "border": "none",
                                       "borderRadius": "4px",
                                       "cursor": "pointer"},
                            ),
                            html.Button(
                                "Cancel", id="dashboard-td-cancel",
                                n_clicks=0,
                                style={"padding": "0.45em 1em",
                                       "background": "white",
                                       "color": "#555",
                                       "border": "1px solid #bbb",
                                       "borderRadius": "4px",
                                       "cursor": "pointer"},
                            ),
                        ],
                        style={"display": "flex", "gap": "0.5em",
                               "justifyContent": "flex-end",
                               "marginTop": "0.6em"},
                    ),
                ], style={"background": "white", "padding": "1.5em",
                          "borderRadius": "6px", "maxWidth": "640px",
                          "margin": "4% auto",
                          "boxShadow": "0 4px 20px rgba(0,0,0,0.25)"})],
            ),
        ],
        style={"padding": "0.6em 1em", "background": "#fffaf0",
               "borderBottom": "1px solid #f0e0bf"},
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

    # Overall progress banner: count total submissions across sources.
    total_submissions = 0
    for src in list_sources(results_dir):
        total_submissions += count_submissions(recommendations_dir, src.source)

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
            _source_progress_table(
                results_dir=results_dir,
                recommendations_dir=recommendations_dir,
                store=store,
            ),
        ],
        style={"padding": "1em", "maxWidth": "1100px", "margin": "0 auto"},
    )

    page_children = [header, banner]
    if admin:
        # Open-source list + current targets feed the Target-dates
        # modal — both small enough to compute at render time.
        open_src_names = sorted({
            s.source for s in list_sources(results_dir)
            if source_phase(recommendations_dir, s.source) == "open"
        })
        page_children.append(_admin_controls_panel(
            reviewers, set(store.paused_reviewers),
            open_src_names,
            {s: get_source_target_date(store, s) or ""
             for s in open_src_names},
        ))
    page_children.append(body)

    return html.Div(
        page_children,
        style={"fontFamily": "system-ui, sans-serif", "minHeight": "100vh"},
    )
