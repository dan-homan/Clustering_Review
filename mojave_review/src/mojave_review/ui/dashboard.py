"""Assignment & progress dashboard.

A separate page at ``/dashboard``, linked from the review page header.
Reviewers see two read-only tables: **My queue** (their assigned
sources, with a lifetime submitted / in-progress / current-assignment
summary) and **Source progress** (every source under the active
filter, who has reviewed it, and what's still outstanding). The admin's
My queue is instead every Stage 1/2 source — the baseline work only the
admin drives — and the admin is excluded from the Stage-3 auto-balance
pool. Admins additionally get an admin-controls block at the top with
buttons:

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

import re
from pathlib import Path

from dash import dash_table, dcc, html

from ..auth.tokens import load_store as load_token_store
from ..data.difficulty import score_all
from ..data.loader import list_sources
from ..recommendations.store import (
    all_review_submitters, count_submissions, drafting_by_slug, reviewer_slug,
    reviewer_in_progress_sources, reviewer_submitted_sources, source_phase,
)
from ..data.assignments import (
    AssignmentStore, assignment_status, get_source_target_date,
    load_store, manual_review_slugs_by_source, needs_for,
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


def discovered_reviewers(
    tokens_path: Path | None, recommendations_dir: Path,
    fallback_reviewer: str,
) -> set[str]:
    """Auto-discovered reviewers (NOT the manual roster): the union of

    * tokens.yaml (authoritative when present — only on the deployed
      server),
    * any reviewer with an open submission on disk
      (``<recs>/<src>/submitted/<slug>.json``),
    * the fallback (single-user) reviewer name.

    Returns a set of full reviewer names (NOT slugs). See
    :func:`known_reviewers` for the full roster (this ∪ the manual
    ``team_members`` ∪ assignment keys)."""
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

    return names


def known_reviewers(
    tokens_path: Path | None, recommendations_dir: Path,
    fallback_reviewer: str,
) -> list[str]:
    """The full team roster, so the dashboard never lies about who's on
    the project. The union of:

    * :func:`discovered_reviewers` (tokens.yaml ∪ open submitters ∪
      fallback),
    * the manually-curated ``team_members`` (v4) — the admin's machine
      has no tokens.yaml, so this is how teammates who haven't submitted
      yet get onto the roster; it syncs via ``recommendations/``,
    * existing assignment keys — keeps a reviewer on the roster even
      after their submissions are archived to ``considered/``/``applied/``
      (open-submission discovery alone would drop them).

    Returns alphabetically-sorted full reviewer names (NOT slugs)."""
    names = discovered_reviewers(
        tokens_path, recommendations_dir, fallback_reviewer)
    store = load_store(recommendations_dir)
    names.update(store.team_members)
    names.update(store.assignments.keys())
    return sorted(_fold_collision_names(names))


_COLLISION_NAME_RE = re.compile(r"^(?P<base>.+)_\d+$")


def _fold_collision_names(names: set[str]) -> set[str]:
    """Drop ``<base>_<N>`` names when ``<base>`` is also present — these
    are collision-rename artifacts (e.g. a ``considered/homand_2.json``
    that ``credit_prior_submissions`` once minted into a phantom
    "homand_2" reviewer). Only folds when the base genuinely exists, so a
    real reviewer literally named ``foo_2`` survives unless ``foo`` is
    also on the roster."""
    out = set(names)
    for n in list(out):
        m = _COLLISION_NAME_RE.match(n)
        if m and m.group("base") in out:
            out.discard(n)
    return out


def slug_name_map(
    tokens_path: Path | None, recommendations_dir: Path,
    fallback_reviewer: str,
) -> dict[str, str]:
    """``slug → full reviewer name`` for the Source-progress "Reviews"
    column. Built from the same roster as :func:`known_reviewers`, so a
    slug that exists only on disk (no tokens.yaml entry) maps to itself."""
    return {
        reviewer_slug(n): n
        for n in known_reviewers(
            tokens_path, recommendations_dir, fallback_reviewer)
    }


def moves_preview(moves: list, *, empty_msg: str = "No moves needed.") -> "html.Div":
    """Render a list of ``(source, from, to)`` moves as a preview table —
    shared by the Top-up rebalance and Redistribute modals. ``moves`` may
    be tuples or 3-lists (the latter after a JSON round-trip through a
    dcc.Store)."""
    if not moves:
        return html.Div(empty_msg, style={"color": "#666", "padding": "0.4em"})
    # Per-reviewer net change, so the admin sees the load shift at a glance.
    delta: dict[str, int] = {}
    for _s, frm, to in moves:
        delta[frm] = delta.get(frm, 0) - 1
        delta[to] = delta.get(to, 0) + 1
    rows = [html.Tr([
        html.Td(s, style={"padding": "3px 8px"}),
        html.Td(frm, style={"padding": "3px 8px", "color": "#b00"}),
        html.Td("→", style={"padding": "3px 8px", "textAlign": "center"}),
        html.Td(to, style={"padding": "3px 8px", "color": "#1a7a1a",
                           "fontWeight": 600}),
    ]) for s, frm, to in moves]
    summary = ", ".join(
        f"{r} {'+' if d > 0 else ''}{d}" for r, d in sorted(delta.items()))
    return html.Div([
        html.Div(f"{len(moves)} move(s): {summary}",
                 style={"marginBottom": "0.4em", "fontWeight": 600}),
        html.Table(
            [html.Thead(html.Tr([
                html.Th("Source", style={"padding": "3px 8px",
                                         "textAlign": "left"}),
                html.Th("From", style={"padding": "3px 8px",
                                       "textAlign": "left"}),
                html.Th("", style={"padding": "3px 8px"}),
                html.Th("To", style={"padding": "3px 8px",
                                     "textAlign": "left"}),
            ]))] + [html.Tbody(rows)],
            style={"width": "100%", "borderCollapse": "collapse",
                   "fontFamily": "system-ui, sans-serif", "fontSize": "0.85em"},
        ),
    ])


def _apply_btn(bid: str, label: str = "Apply") -> "html.Button":
    return html.Button(
        label, id=bid, n_clicks=0,
        style={"padding": "0.45em 1em", "background": "#1f77b4",
               "color": "white", "border": "none", "borderRadius": "4px",
               "cursor": "pointer"})


def _cancel_btn(bid: str, label: str = "Cancel") -> "html.Button":
    return html.Button(
        label, id=bid, n_clicks=0,
        style={"padding": "0.45em 1em", "background": "white", "color": "#555",
               "border": "1px solid #bbb", "borderRadius": "4px",
               "cursor": "pointer"})


def _modal_shell(
    modal_id: str, title: str, close_id: str,
    body: list, footer: list, *, max_width: str = "560px",
) -> "html.Div":
    """A hidden modal scaffold (overlay set by the open callback). Shared
    by the rebalance / redistribute / move-source modals."""
    header = html.Div(
        [html.H4(title, style={"margin": 0}),
         html.Button("×", id=close_id, n_clicks=0,
                     style={"border": "none", "background": "transparent",
                            "fontSize": "1.5em", "lineHeight": 1,
                            "cursor": "pointer", "color": "#888"})],
        style={"display": "flex", "justifyContent": "space-between",
               "alignItems": "center", "marginBottom": "0.4em"})
    footer_row = html.Div(
        footer, style={"display": "flex", "gap": "0.5em",
                       "justifyContent": "flex-end", "marginTop": "0.6em"})
    return html.Div(
        id=modal_id, style={"display": "none"},
        children=[html.Div(
            [header] + body + [footer_row],
            style={"background": "white", "padding": "1.5em",
                   "borderRadius": "6px", "maxWidth": max_width,
                   "margin": "5% auto", "maxHeight": "85vh",
                   "overflowY": "auto",
                   "boxShadow": "0 4px 20px rgba(0,0,0,0.25)"})])


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
    *,
    queue_sources: list[str],
    store: AssignmentStore,
    recommendations_dir: Path,
    reviewer: str,
    rating_by_source: dict[str, str],
    n_submitted_total: int,
    n_in_progress_total: int,
    admin_queue: bool,
) -> html.Div:
    """My-queue card.

    The summary line carries three lifetime/queue statistics (item 2):

    * **submitted** — distinct sources this reviewer has ever submitted a
      review for (open + Stage-3-archived ``considered/``), assigned or
      not.
    * **in progress** — distinct sources with a non-empty draft, not yet
      submitted, assigned or not.
    * **current assignments** — size of the queue below.

    The queue itself (``queue_sources``) is the reviewer's assigned
    sources; for an **admin** it is instead every Stage 1/2 source — the
    baseline work only the admin drives (item 4). Columns: Source /
    Rating / Status / Target Date.
    """
    n_current = len(queue_sources)
    summary = (
        f"{n_submitted_total} submitted · "
        f"{n_in_progress_total} in progress · "
        f"{n_current} current assignment"
        f"{'' if n_current == 1 else 's'}"
    )
    title = f"My queue — {reviewer}" if reviewer else "My queue"
    header = [
        html.H3(title, style={"margin": "0 0 0.25em"}),
        html.Div(summary,
                 style={"color": "#666", "fontSize": "0.85em",
                        "marginBottom": "0.5em"}),
    ]
    if not queue_sources:
        if admin_queue:
            msg = "No Stage 1/2 sources remain — all baseline work is done."
        elif reviewer:
            msg = "You have no assignments yet."
        else:
            msg = "No reviewer identity in this session."
        return html.Div(
            header + [html.Div(msg, style={"color": "#777"})],
            style=_CARD_STYLE,
        )
    rows = []
    for src in sorted(queue_sources, key=lambda s: (
            get_source_target_date(store, s) or "9999-12-31", s)):
        if admin_queue:
            # Status for the admin's baseline queue is how far the source
            # has progressed, not a reviewer submission state.
            status = ("Stage 1"
                      if source_phase(recommendations_dir, src) == "stage1"
                      else "Stage 2")
        else:
            status = assignment_status(recommendations_dir, src, reviewer)
        rows.append({
            "source": src,
            "rating": rating_by_source.get(src, "—"),
            "status": status,
            "target_date": get_source_target_date(store, src) or "—",
        })
    return html.Div(
        header + [
            dash_table.DataTable(
                id="dashboard-my-queue",
                data=rows,
                columns=[
                    {"name": "Source", "id": "source"},
                    {"name": "Rating", "id": "rating"},
                    {"name": "Status", "id": "status"},
                    {"name": "Target Date", "id": "target_date"},
                ],
                style_table=_TABLE_STYLE,
                style_cell=_CELL_STYLE,
                style_data_conditional=[
                    _status_chip("submitted", "#1a7a1a"),
                    _status_chip("in_progress", "#b9770e"),
                    _status_chip("pending", "#888"),
                    _status_chip("Stage 1", "#c62828"),
                    _status_chip("Stage 2", "#b9770e"),
                ],
                style_header={"fontWeight": 700, "background": "#f5f5f5"},
                page_size=200,
            ),
        ],
        style=_CARD_STYLE,
    )


def _source_progress_rows(
    *, results_dir: Path, recommendations_dir: Path,
    store: AssignmentStore, filter_value: str,
    name_for_slug: dict[str, str] | None = None,
) -> list[dict]:
    """Build the rows for the source-progress table under one filter.

    Filter semantics (see ``_FILTER_OPTIONS``):
      * ``in_progress`` → phase == "open"
      * ``final``       → phase == "final"
      * ``preopen``     → phase in {"stage1", "stage2"}

    Columns (item 5): Source / Rating / Still needed / Reviews /
    Pending Reviews / Target.

    * **Reviews** — names of *everyone who has ever submitted* a review of
      this source (open ``submitted/`` + Stage-3-archived
      ``considered/``), slug→name via ``name_for_slug`` (falls back to the
      slug).
    * **Pending Reviews** — assigned reviewers who have *not* submitted
      (open or archived) — i.e. assignments still outstanding.
    """
    name_for_slug = name_for_slug or {}
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

    # Everyone who has ever submitted a review (open + Stage-3 considered
    # + Stage-2 applied baseline), one disk walk, plus explicit manual
    # credits (admin self-credit for sources advanced without an
    # artifact).
    ever_submitted = all_review_submitters(
        recommendations_dir, [s.source for s in all_srcs])
    manual_by_source = manual_review_slugs_by_source(store)

    rows = []
    for src in all_srcs:
        if source_phase(recommendations_dir, src.source) not in phases:
            continue
        diff = by_folder.get(src.folder.name)
        rating = (
            ("★" * diff.stars + ("  ⚠" if diff.outlier else ""))
            if diff is not None else "—"
        )
        ever_slugs = (ever_submitted.get(src.source, set())
                      | manual_by_source.get(src.source, set()))
        reviews = sorted(
            name_for_slug.get(slug, slug) for slug in ever_slugs)
        # Pending = assigned reviewers who have not (ever) submitted.
        pending = sorted(
            r for r in assigned_by_source.get(src.source, [])
            if reviewer_slug(r) not in ever_slugs
        )
        rows.append({
            "source": src.source,
            "rating": rating,
            "needs_more": needs_for(recommendations_dir, src.source),
            "reviews": ", ".join(reviews),
            "pending": ", ".join(pending),
            "target_date": get_source_target_date(store, src.source) or "—",
        })
    rows.sort(key=lambda r: r["source"])
    return rows


def reviewer_summary_rows(
    *, results_dir: Path, recommendations_dir: Path,
    store: AssignmentStore, reviewers: list[str],
    weight_by_source: dict[str, float],
) -> list[dict]:
    """Per-reviewer summary (admin): current-queue breakdown +
    lifetime-completed history (item: the admin's reviewer summary).

    For each reviewer:
      * ``pending`` / ``in_progress`` / ``submitted`` — their current
        assigned queue bucketed by :func:`assignment_status`;
      * ``load`` — balance-weight of the pending sources;
      * ``completed`` — every distinct source they've ever reviewed (open
        ``submitted/`` ∪ Stage-3 ``considered/`` ∪ Stage-2 applied
        baseline ∪ manual self-credit), i.e. INCLUDING finalized sources;
      * ``finalized`` — how many of ``completed`` are now Stage-3 done;
      * ``off_queue`` — sources they're genuinely DRAFTING (non-empty,
        not yet submitted) that are NOT in their assigned queue — i.e.
        in-progress work nobody assigned them.

    Two disk walks (``all_review_submitters`` + ``drafting_by_slug``),
    inverted to slug→sources, so the whole roster is cheap and the
    queue breakdown is consistent with the rest of the app (an archived
    submission counts as done even with a stale ``current/`` draft)."""
    all_srcs = list_sources(results_dir)
    source_names = [s.source for s in all_srcs]
    phase = {s: source_phase(recommendations_dir, s) for s in source_names}
    submitters = all_review_submitters(recommendations_dir, source_names)
    completed_by_slug: dict[str, set[str]] = {}
    for src, slugs in submitters.items():
        for sl in slugs:
            completed_by_slug.setdefault(sl, set()).add(src)
    drafting = drafting_by_slug(recommendations_dir, source_names)

    rows = []
    for r in reviewers:
        slug = reviewer_slug(r)
        completed = (set(completed_by_slug.get(slug, set()))
                     | set(store.manual_reviews.get(r, [])))
        finalized = sum(1 for s in completed if phase.get(s) == "final")
        # Genuine in-progress = non-empty draft AND not already submitted.
        genuine_drafting = set(drafting.get(slug, set())) - completed
        assigned = {rec.source for rec in store.assignments.get(r, [])}
        pending, in_progress, submitted = [], [], 0
        for s in assigned:
            if s in completed:
                submitted += 1
            elif s in genuine_drafting:
                in_progress.append(s)
            else:
                pending.append(s)
        rows.append({
            "reviewer": r,
            "paused": r in store.paused_reviewers,
            "load": sum(weight_by_source.get(s, 0.0) for s in pending),
            "pending": sorted(pending),
            "in_progress": len(in_progress),
            "submitted": submitted,
            "completed": sorted(completed),
            "finalized": finalized,
            "off_queue": sorted(genuine_drafting - assigned),
        })
    rows.sort(key=lambda x: (x["paused"], x["reviewer"]))
    return rows


# Column widths for the reviewer-summary flex grid (header / rows / total
# share these so columns line up; rows are <summary> so a ~1.2em left pad
# on header + total compensates for the disclosure triangle).
_RS_COLS = [
    ("reviewer", "Reviewer", "180px", "left"),
    ("load", "Load", "62px", "right"),
    ("pending", "Pending", "70px", "right"),
    ("in_progress", "In prog", "66px", "right"),
    ("submitted", "Submitted", "84px", "right"),
    ("completed", "Completed", "86px", "right"),
    ("finalized", "(final)", "62px", "right"),
]


def _rs_cell(text, width, align, **extra) -> "html.Span":
    return html.Span(str(text), style={
        "display": "inline-block", "width": width,
        "textAlign": align, **extra})


def _reviewer_summary_card(rows: list[dict]) -> html.Div:
    """Admin per-reviewer summary: a flex-grid header + one expandable
    ``<details>`` per reviewer (summary line = the counts, body = the
    Completed / Pending source lists) + a TOTAL row. No callbacks — the
    native ``<details>`` element handles expand/collapse."""
    header = html.Div(
        [_rs_cell(label, w, align, fontWeight=700)
         for _k, label, w, align in _RS_COLS],
        style={"display": "flex", "gap": "0.5em", "padding": "4px 8px",
               "paddingLeft": "1.4em", "borderBottom": "2px solid #ddd"})

    items = []
    for row in rows:
        name = row["reviewer"] + ("  ⏸" if row["paused"] else "")
        if row.get("off_queue"):     # drafting sources nobody assigned them
            name += f"  ✎{len(row['off_queue'])}"
        cells = [
            _rs_cell(name, _RS_COLS[0][2], "left", fontWeight=600,
                     fontStyle="italic" if row["paused"] else "normal",
                     color="#888" if row["paused"] else "#222"),
            _rs_cell(f"{row['load']:.1f}" if row["load"] else "—",
                     _RS_COLS[1][2], "right"),
            _rs_cell(len(row["pending"]), _RS_COLS[2][2], "right"),
            _rs_cell(row["in_progress"], _RS_COLS[3][2], "right"),
            _rs_cell(row["submitted"], _RS_COLS[4][2], "right"),
            _rs_cell(len(row["completed"]), _RS_COLS[5][2], "right",
                     fontWeight=600, color="#1a7a1a"),
            _rs_cell(row["finalized"], _RS_COLS[6][2], "right", color="#888"),
        ]
        summary = html.Summary(
            html.Div(cells, style={"display": "inline-flex", "gap": "0.5em",
                                   "alignItems": "center"}),
            style={"cursor": "pointer", "padding": "4px 8px"})
        body_lines = [
            html.Div([html.B("Completed: "),
                      ", ".join(row["completed"]) or "—"],
                     style={"marginBottom": "0.25em"}),
            html.Div([html.B("Pending: "),
                      ", ".join(row["pending"]) or "—"]),
        ]
        if row.get("off_queue"):
            body_lines.append(html.Div(
                [html.B("Drafting (not assigned): "),
                 ", ".join(row["off_queue"])],
                style={"marginTop": "0.25em", "color": "#b9770e"}))
        body = html.Div(
            body_lines,
            style={"padding": "0.4em 1em", "fontSize": "0.82em",
                   "color": "#555", "background": "#fafafa",
                   "whiteSpace": "normal", "wordBreak": "break-word"})
        items.append(html.Details(
            [summary, body], style={"borderBottom": "1px solid #eee"}))

    tot = {
        "pending": sum(len(r["pending"]) for r in rows),
        "in_progress": sum(r["in_progress"] for r in rows),
        "submitted": sum(r["submitted"] for r in rows),
        "completed": sum(len(r["completed"]) for r in rows),
        "finalized": sum(r["finalized"] for r in rows),
    }
    total_row = html.Div(
        [_rs_cell("TOTAL", _RS_COLS[0][2], "left", fontWeight=700),
         _rs_cell("", _RS_COLS[1][2], "right"),
         _rs_cell(tot["pending"], _RS_COLS[2][2], "right", fontWeight=700),
         _rs_cell(tot["in_progress"], _RS_COLS[3][2], "right", fontWeight=700),
         _rs_cell(tot["submitted"], _RS_COLS[4][2], "right", fontWeight=700),
         _rs_cell(tot["completed"], _RS_COLS[5][2], "right", fontWeight=700),
         _rs_cell(tot["finalized"], _RS_COLS[6][2], "right", fontWeight=700)],
        style={"display": "flex", "gap": "0.5em", "padding": "4px 8px",
               "paddingLeft": "1.4em", "borderTop": "2px solid #ddd"})

    return html.Div(
        [html.H3("Reviewer summary", style={"margin": "0 0 0.25em"}),
         html.Div("Pending / In prog / Submitted break down the reviewer's "
                  "CURRENT assigned queue. Completed counts every source "
                  "they've ever reviewed (open + Stage-3 archived + Stage-2 "
                  "baseline + manual credit), including finalized and "
                  "no-longer-assigned — so Completed ≥ Submitted, the gap "
                  "being reviews on sources not in their queue. Click a row "
                  "for the source lists.",
                  style={"color": "#666", "fontSize": "0.82em",
                         "marginBottom": "0.5em"}),
         header, *items, total_row],
        style=_CARD_STYLE)


def _source_progress_table(
    *, results_dir: Path, recommendations_dir: Path,
    store: AssignmentStore, initial_filter: str = DEFAULT_FILTER,
    name_for_slug: dict[str, str] | None = None,
) -> html.Div:
    """Source-progress table. The dropdown above it filters by review
    phase; default = In Progress (Stage 2 done). All sources are shown
    even with zero submissions — the table is now a complete source
    progress view, not just "those with submissions"."""
    rows = _source_progress_rows(
        results_dir=results_dir,
        recommendations_dir=recommendations_dir,
        store=store, filter_value=initial_filter,
        name_for_slug=name_for_slug,
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
                    {"name": "Still needed", "id": "needs_more"},
                    {"name": "Reviews", "id": "reviews"},
                    {"name": "Pending Reviews", "id": "pending"},
                    {"name": "Target", "id": "target_date"},
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


def _td_section(
    title: str, sources: list[str], current_targets: dict[str, str],
) -> html.Div:
    """One labeled per-source date-input table inside the modal. Every
    input is a ``dashboard-td-input`` pattern-matching id keyed by
    source, so the shared save / bulk-range callbacks pick up both
    sections automatically."""
    if not sources:
        return html.Div(
            [html.Div(title, style={"fontWeight": 600,
                                    "fontSize": "0.85em",
                                    "margin": "0.4em 0 0.2em"}),
             html.Div("(none)", style={"color": "#999",
                                       "fontSize": "0.85em",
                                       "padding": "0 0 0.4em"})],
        )
    rows = []
    for s in sources:
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
    return html.Div([
        html.Div(title, style={"fontWeight": 600, "fontSize": "0.85em",
                               "margin": "0.4em 0 0.2em"}),
        html.Div(
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
            style={"maxHeight": "26vh", "overflowY": "auto",
                   "border": "1px solid #e0e0e0", "borderRadius": "4px"},
        ),
    ])


def _target_dates_modal_body(
    stage12_sources: list[str],
    open_sources: list[str],
    current_targets: dict[str, str],
) -> list:
    """The per-source list + bulk-by-range form, both inside the modal.
    Pulled out of ``_admin_controls_panel`` so the latter stays
    readable. Two sections (item 4): the admin's own Stage 1/2 baseline
    sources, and the Stage-2-done sources open to reviewers. Both write
    to the same ``source_target_dates`` map."""
    # From/To range dropdowns span every source shown in the modal.
    all_modal = sorted(set(stage12_sources) | set(open_sources))
    src_opts = [{"label": s, "value": s} for s in all_modal]
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
    return [
        bulk_form,
        _td_section("Stage 1/2 — your baseline sources",
                    stage12_sources, current_targets),
        _td_section("Stage 2 done — reviewer targets",
                    open_sources, current_targets),
    ]


def _admin_controls_panel(
    reviewers: list[str], paused_set: set[str],
    stage12_sources: list[str],
    open_sources: list[str],
    current_targets: dict[str, str],
    removable: set[str],
    assigned_sources: list[str],
) -> html.Div:
    """Top-of-dashboard admin section: Manage team + Auto-balance +
    Reassign-queue + Set-target-dates buttons, their modals, and a
    status line. Hidden when admin=False.

    ``removable`` is the subset of ``reviewers`` that exist *only* in the
    manual roster (``team_members``) — those get a Remove button in the
    Manage-team modal; auto-discovered reviewers don't (they'd reappear)."""
    reviewer_opts = [{"label": r, "value": r} for r in reviewers]
    # Team-management rows: one per reviewer with a pause/active select
    # and (for manual-only members) a Remove button.
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
            html.Td(
                html.Button(
                    "Remove",
                    id={"type": "dashboard-tm-remove", "reviewer": r},
                    n_clicks=0,
                    title="Remove this manually-added member from the "
                          "roster.",
                    style={"padding": "0.15em 0.6em", "fontSize": "0.8em",
                           "background": "white", "color": "#b00",
                           "border": "1px solid #e0b0b0",
                           "borderRadius": "4px", "cursor": "pointer"},
                ) if r in removable else "",
                style={"padding": "4px 8px"},
            ),
        ]))
    # Add-member row appended below the table inside the modal.
    add_member_row = html.Div(
        [
            dcc.Input(
                id="dashboard-tm-add-name", type="text", value="",
                placeholder="New teammate name (match tokens.yaml)",
                debounce=False,
                style={"width": "280px", "fontSize": "0.85em",
                       "marginRight": "0.5em"},
            ),
            html.Button(
                "Add member", id="dashboard-tm-add-btn", n_clicks=0,
                style={"padding": "0.3em 0.8em", "fontSize": "0.85em",
                       "background": "white", "color": "#1a7a1a",
                       "border": "1px solid #bcd9bc", "borderRadius": "4px",
                       "cursor": "pointer"},
            ),
        ],
        style={"display": "flex", "alignItems": "center",
               "flexWrap": "wrap", "gap": "0.25em",
               "marginTop": "0.6em"},
    )
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
                    html.Button(
                        "✓ Credit my Stage-2 reviews",
                        id="dashboard-credit-btn", n_clicks=0,
                        title="Record yourself as reviewer for every "
                              "Stage-2-done / finalized source where you "
                              "have no submission, considered, or applied "
                              "record — for sources you advanced past "
                              "Stage 2 without leaving an artifact. "
                              "Idempotent.",
                        style={"marginLeft": "0.6em",
                               "padding": "0.4em 1em", "fontSize": "0.9em",
                               "background": "white", "color": "#555",
                               "border": "1px solid #bbb",
                               "borderRadius": "4px", "cursor": "pointer"},
                    ),
                    html.Button(
                        "⚖ Top-up rebalance…",
                        id="dashboard-rb-btn", n_clicks=0,
                        title="Move PENDING assignments to even out load "
                              "across active reviewers — gives newly-added "
                              "reviewers a fair share without reshuffling "
                              "everyone. Preview before applying.",
                        style={"marginLeft": "0.6em",
                               "padding": "0.4em 1em", "fontSize": "0.9em",
                               "background": "white", "color": "#555",
                               "border": "1px solid #bbb",
                               "borderRadius": "4px", "cursor": "pointer"},
                    ),
                    html.Button(
                        "🏖 Redistribute (break)…",
                        id="dashboard-rd-btn", n_clicks=0,
                        title="Spread one reviewer's PENDING queue across "
                              "the rest of the active pool (not 1→1), "
                              "optionally pausing them.",
                        style={"marginLeft": "0.6em",
                               "padding": "0.4em 1em", "fontSize": "0.9em",
                               "background": "white", "color": "#555",
                               "border": "1px solid #bbb",
                               "borderRadius": "4px", "cursor": "pointer"},
                    ),
                    html.Button(
                        "↔ Move a source…",
                        id="dashboard-ms-btn", n_clicks=0,
                        title="Reassign a single source from one reviewer "
                              "to another.",
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
                        [
                            html.Div(
                                "Paused reviewers stay visible on the "
                                "dashboard but are excluded from "
                                "auto-balance. Existing assignments are "
                                "preserved either way — to move them, use "
                                "Reassign queue."),
                            html.Div(
                                "Add teammates who haven't submitted yet "
                                "below. The roster is stored in "
                                "assignments.json and syncs to the server, "
                                "so names must match the deployed "
                                "tokens.yaml exactly for identities to line "
                                "up. Auto-discovered reviewers (from "
                                "submissions / tokens) can't be removed.",
                                style={"marginTop": "0.3em"}),
                        ],
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
                            html.Th("",
                                    style={"padding": "4px 8px",
                                           "textAlign": "left"}),
                        ]))] + [html.Tbody(team_rows)],
                        style={"width": "100%",
                               "borderCollapse": "collapse",
                               "fontFamily": "system-ui, sans-serif",
                               "fontSize": "0.88em"},
                    ),
                    add_member_row,
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
            # Top-up rebalance modal ------------------------------------
            _modal_shell(
                "dashboard-rb-modal", "Top-up rebalance",
                "dashboard-rb-close",
                [html.Div(
                    "Moves PENDING assignments (not started) to even out "
                    "load across active reviewers. Submitted and in-progress "
                    "work stays put. Review the moves, then Apply.",
                    style={"color": "#666", "fontSize": "0.85em",
                           "marginBottom": "0.5em"}),
                 dcc.Checklist(
                     id="dashboard-rb-consider-completed",
                     options=[{"label": " Consider completed reviews "
                                        "(give past contributors a lighter "
                                        "share — for the first round)",
                               "value": "completed"}],
                     value=[],
                     style={"fontSize": "0.85em", "marginBottom": "0.5em"}),
                 html.Div(id="dashboard-rb-preview-body",
                          style={"maxHeight": "44vh", "overflowY": "auto"})],
                [_apply_btn("dashboard-rb-apply"),
                 _cancel_btn("dashboard-rb-cancel")],
                max_width="620px"),
            # Redistribute-on-break modal -------------------------------
            _modal_shell(
                "dashboard-rd-modal", "Redistribute a reviewer's queue",
                "dashboard-rd-close",
                [html.Div(
                    "Spread one reviewer's PENDING sources across the rest "
                    "of the active pool, by load. Their submitted / "
                    "in-progress work stays with them.",
                    style={"color": "#666", "fontSize": "0.85em",
                           "marginBottom": "0.5em"}),
                 html.Div([
                     html.Label("Reviewer:", style={"marginRight": "0.5em"}),
                     dcc.Dropdown(
                         id="dashboard-rd-from", options=reviewer_opts,
                         clearable=False,
                         style={"width": "200px", "display": "inline-block",
                                "verticalAlign": "middle",
                                "marginRight": "1em"}),
                     html.Label("Max to move:",
                                style={"marginRight": "0.5em"}),
                     dcc.Input(id="dashboard-rd-limit", type="number", min=1,
                               placeholder="all", value=None,
                               style={"width": "80px", "marginRight": "1em",
                                      "verticalAlign": "middle"}),
                     dcc.Checklist(
                         id="dashboard-rd-pause",
                         options=[{"label": " pause this reviewer",
                                   "value": "pause"}],
                         value=["pause"],
                         style={"display": "inline-block",
                                "verticalAlign": "middle"}),
                 ], style={"display": "flex", "alignItems": "center",
                           "flexWrap": "wrap", "gap": "0.25em",
                           "marginBottom": "0.6em"}),
                 html.Div(id="dashboard-rd-preview-body",
                          style={"maxHeight": "40vh", "overflowY": "auto"})],
                [_apply_btn("dashboard-rd-apply"),
                 _cancel_btn("dashboard-rd-cancel")],
                max_width="620px"),
            # Move-a-source modal ---------------------------------------
            _modal_shell(
                "dashboard-ms-modal", "Move a source",
                "dashboard-ms-close",
                [html.Div(
                    "Reassign one source from one reviewer to another.",
                    style={"color": "#666", "fontSize": "0.85em",
                           "marginBottom": "0.5em"}),
                 html.Div([
                     html.Label("Source:", style={"marginRight": "0.5em"}),
                     dcc.Dropdown(
                         id="dashboard-ms-source",
                         options=[{"label": s, "value": s}
                                  for s in assigned_sources],
                         clearable=False,
                         style={"width": "180px", "display": "inline-block",
                                "verticalAlign": "middle",
                                "marginRight": "1em"}),
                     html.Label("From:", style={"marginRight": "0.5em"}),
                     dcc.Dropdown(
                         id="dashboard-ms-from", options=reviewer_opts,
                         clearable=False,
                         style={"width": "160px", "display": "inline-block",
                                "verticalAlign": "middle",
                                "marginRight": "1em"}),
                     html.Label("To:", style={"marginRight": "0.5em"}),
                     dcc.Dropdown(
                         id="dashboard-ms-to", options=reviewer_opts,
                         clearable=False,
                         style={"width": "160px", "display": "inline-block",
                                "verticalAlign": "middle"}),
                 ], style={"display": "flex", "alignItems": "center",
                           "flexWrap": "wrap", "gap": "0.25em"})],
                [_apply_btn("dashboard-ms-apply", "Move"),
                 _cancel_btn("dashboard-ms-cancel")]),
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
                        "Set target dates for your Stage 1/2 baseline "
                        "sources and for the Stage-2-done sources open to "
                        "reviewers. Bulk-set a lexicographic range, then "
                        "tweak individual rows. Save commits everything.",
                        style={"color": "#666", "fontSize": "0.85em",
                               "marginBottom": "0.5em"},
                    ),
                    *_target_dates_modal_body(
                        stage12_sources, open_sources, current_targets),
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
                          "margin": "3% auto",
                          # Cap height + scroll so the Save/Cancel row at the
                          # bottom is always reachable even with both the
                          # Stage 1/2 and reviewer sections expanded.
                          "maxHeight": "90vh", "overflowY": "auto",
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
    name_for_slug = slug_name_map(
        tokens_path, recommendations_dir, reviewer)

    all_srcs = list_sources(results_dir)

    # Overall progress banner: count total submissions across sources.
    total_submissions = 0
    for src in all_srcs:
        total_submissions += count_submissions(recommendations_dir, src.source)

    # Difficulty ratings (My-queue Rating column) + balance weights
    # (reviewer-summary Load column) from one scoring pass.
    rating_by_source: dict[str, str] = {}
    weight_by_source: dict[str, float] = {}
    for d in score_all(all_srcs):
        rating_by_source[d.source] = (
            "★" * d.stars + ("  ⚠" if d.outlier else ""))
        weight_by_source[d.source] = d.balance_weight

    # My-queue contents (item 4): the admin's queue is every Stage 1/2
    # source (the baseline work only the admin drives); every other
    # reviewer's queue is their explicit assignments.
    stage12_names = sorted(
        s.source for s in all_srcs
        if source_phase(recommendations_dir, s.source) in ("stage1", "stage2")
    )
    if admin:
        queue_sources = stage12_names
    else:
        queue_sources = [
            rec.source for rec in store.assignments.get(reviewer, [])]

    # Lifetime stats (item 2) — across all sources, assigned or not.
    # Manual review credits count as submitted and never as in-progress.
    manual_for_me = set(store.manual_reviews.get(reviewer, []))
    n_submitted_total = len(
        reviewer_submitted_sources(recommendations_dir, reviewer)
        | manual_for_me)
    n_in_progress_total = len(
        reviewer_in_progress_sources(recommendations_dir, reviewer)
        - manual_for_me)

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

    body_children = [
        _my_queue_table(
            queue_sources=queue_sources,
            store=store,
            recommendations_dir=recommendations_dir,
            reviewer=reviewer,
            rating_by_source=rating_by_source,
            n_submitted_total=n_submitted_total,
            n_in_progress_total=n_in_progress_total,
            admin_queue=admin,
        ),
        _source_progress_table(
            results_dir=results_dir,
            recommendations_dir=recommendations_dir,
            store=store,
            name_for_slug=name_for_slug,
        ),
    ]
    if admin:
        body_children.append(_reviewer_summary_card(reviewer_summary_rows(
            results_dir=results_dir,
            recommendations_dir=recommendations_dir,
            store=store, reviewers=reviewers,
            weight_by_source=weight_by_source,
        )))
    body = html.Div(
        body_children,
        style={"padding": "1em", "maxWidth": "1100px", "margin": "0 auto"},
    )

    page_children = [header, banner]
    if admin:
        # Stage 1/2 (admin baseline) + open (reviewer) source lists feed
        # the Target-dates modal — both small enough to compute at render
        # time. Current targets cover the union (one source_target_dates
        # map backs both sections).
        open_src_names = sorted({
            s.source for s in all_srcs
            if source_phase(recommendations_dir, s.source) == "open"
        })
        td_sources = sorted(set(stage12_names) | set(open_src_names))
        # Manual-only members (in team_members but not auto-discovered or
        # assigned) are the ones safe to remove — removing an
        # auto-discovered reviewer would just re-appear next render.
        discovered = (
            discovered_reviewers(tokens_path, recommendations_dir, reviewer)
            | set(store.assignments.keys())
        )
        removable = set(store.team_members) - discovered
        assigned_sources = sorted({
            rec.source for recs in store.assignments.values()
            for rec in recs
        })
        page_children.append(_admin_controls_panel(
            reviewers, set(store.paused_reviewers),
            stage12_names,
            open_src_names,
            {s: get_source_target_date(store, s) or "" for s in td_sources},
            removable,
            assigned_sources,
        ))
    page_children.append(body)

    return html.Div(
        page_children,
        style={"fontFamily": "system-ui, sans-serif", "minHeight": "100vh"},
    )
