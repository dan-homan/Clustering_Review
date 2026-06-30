"""Dash layout for the review tool."""

from __future__ import annotations

from pathlib import Path

from dash import dcc, html

from ..data.loader import list_sources
from ..recommendations.store import (
    source_badge, source_phase, source_needs_discussion,
    is_submitted, load_recommendation)
from .nwin_panel import build_nwin_panel
from .recommendations_panel import build_recommendations_panel
from .urls import rel


def _reviewer_status(recommendations_dir: Path, source: str,
                     reviewer: str | None) -> tuple[str | None, dict]:
    """Per-reviewer review-state note for the picker: where THIS reviewer is on
    THIS source, so they can see what they've done and resume. Returns
    ``(text, style)`` or ``(None, {})``:

    - **needs discussion** (bold) — admin has flagged the source via the
      Stage-3 panel. Source-level override: shown for every reviewer
      regardless of their personal state, so the flag is unmissable.
    - **submitted** (bold) — the reviewer has a submitted review.
    - **review in progress** (plain) — a non-empty ``current/`` draft, not yet
      submitted. (An empty draft does NOT count.)
    - **needs review** (italic) — the source is open for recommendations
      (``open`` phase = Stage 2 done) and the reviewer hasn't touched it.
    - nothing — locked (Stage 1/2) or finalized sources aren't actionable for
      the reviewer; the bracket badge already conveys their state.
    """
    if recommendations_dir is None or not reviewer:
        return None, {}
    if source_needs_discussion(recommendations_dir, source):
        return "needs discussion", {"fontWeight": 700, "color": "#d68a00"}
    if is_submitted(recommendations_dir, source, reviewer):
        return "submitted", {"fontWeight": 700, "color": "#1a7a1a"}
    if source_phase(recommendations_dir, source) != "open":
        return None, {}
    draft = load_recommendation(recommendations_dir, source, "current", reviewer)
    if not draft.is_empty():
        return "review in progress", {"color": "#555"}
    return "needs review", {"fontStyle": "italic", "color": "#999"}


def build_source_options(results_dir: Path, recommendations_dir: Path,
                         reviewer: str | None = None) -> list[dict]:
    """Source-picker options. Each label is ``<source>  <reviewer-status>  [badge]``:

    - the source name only (the date range is dropped — it's the same for every
      source in this review; revisit if mixed ranges ever appear);
    - a per-reviewer status note (``_reviewer_status``) so reviewers can see what
      they've done / resume where they left off;
    - the bracket badge from ``store.source_badge`` (``[N]`` / ``[final]`` /
      ``[stage 1]`` …), kept as-is.

    Labels are **plain strings** (``<source>   <status>   [badge]``). The
    dcc.Dropdown in this Dash version only accepts ``string | number`` for an
    option's ``label`` — a component (html.Span) there emits a raw
    ``{props,type,namespace}`` dict that react-select renders directly, which
    throws React error #31 ("Objects are not valid as a React child") and the
    error boundary blanks the surrounding render. So the rich italic/bold note
    is folded into the text instead. A ``search`` field keeps type-to-filter
    working. With ``recommendations_dir=None`` (introspection) the note + badge
    are omitted.

    The current reviewer's **outstanding assignments** (assigned to them and
    not yet submitted) are listed **first** and prefixed with ``★`` so they
    can dive straight in. (This Dash version's ``dcc.Dropdown`` only accepts
    plain-string option labels — a component there throws React error #31 —
    so the marker stands in for true bold text.)"""
    # Which sources are this reviewer's outstanding (unsubmitted) assignments?
    outstanding: set[str] = set()
    if recommendations_dir is not None and reviewer:
        try:
            from ..data.assignments import load_store, assignment_status
            store = load_store(recommendations_dir)
            for rec in store.assignments.get(reviewer, []):
                if assignment_status(
                        recommendations_dir, rec.source, reviewer) != "submitted":
                    outstanding.add(rec.source)
        except Exception:
            outstanding = set()

    out: list[dict] = []
    for s in list_sources(results_dir):
        parts: list[str] = [s.source]
        text, _style = _reviewer_status(recommendations_dir, s.source, reviewer)
        if text:
            parts.append(text)
        if recommendations_dir is not None:
            parts.append(source_badge(recommendations_dir, s.source))
        mine = s.source in outstanding
        label = ("★ " if mine else "") + "   ".join(parts)
        out.append({"label": label, "value": str(s.folder),
                    "search": s.source, "_mine": mine})
    # Outstanding assignments first (then everything else), each group
    # alphabetical by source name.
    out.sort(key=lambda o: (0 if o["_mine"] else 1, o["search"]))
    for o in out:
        o.pop("_mine", None)
    return out


def build_layout(results_dir: Path, reviewer: str, admin: bool = False,
                 recommendations_dir: Path | None = None) -> html.Div:
    source_options = build_source_options(results_dir, recommendations_dir,
                                          reviewer)
    initial = source_options[0]["value"] if source_options else None

    header = html.Div(
        [
            html.Div(
                [
                    html.H2("MOJAVE Cluster Review",
                            style={"margin": "0", "display": "inline-block"}),
                    # New tab on purpose — dashboard and review are separate
                    # pages with separate state, and we want reviewers to be
                    # able to keep both open while they work. Direct URL
                    # access at /dashboard also works (router callback in
                    # app.py).
                    html.A(
                        "📋 Assignment Dashboard",
                        href=rel("/dashboard"), target="_blank",
                        style={"marginLeft": "1.5em", "color": "#1f77b4",
                               "textDecoration": "none", "fontSize": "0.9em",
                               "verticalAlign": "middle"},
                        title="Open the assignments & progress dashboard "
                              "(new tab)",
                    ),
                ],
                style={"display": "flex", "alignItems": "baseline"},
            ),
            html.Div(
                [
                    html.Span(f"Reviewer: {reviewer}",
                              style={"marginRight": "1.5em", "color": "#555"}),
                    html.Span("Source:", style={"marginRight": "0.5em"}),
                    dcc.Dropdown(
                        id="source-picker",
                        options=source_options,
                        value=initial,
                        clearable=False,
                        style={"minWidth": "320px", "display": "inline-block"},
                    ),
                    html.Span("Model:", style={"margin": "0 0.5em 0 1.5em"}),
                    dcc.Dropdown(
                        id="model-picker",
                        clearable=False,
                        style={"minWidth": "180px", "display": "inline-block"},
                    ),
                    # Reload the on-disk CSV / NPZ for the current source.
                    # ``load_bundle`` already invalidates its cache when
                    # the file mtimes change, so the explicit button is
                    # really a belt-and-braces — useful when the reviewer
                    # just ran ``mojave-apply`` (or otherwise edited
                    # Results/) and wants to be *sure* the app is showing
                    # fresh data, without leaving them guessing whether
                    # the auto-detect caught it.
                    html.Button(
                        "↻ Reload",
                        id="reload-bundles",
                        n_clicks=0,
                        title="Re-read CSV + NPZ from disk for all "
                              "sources / models (auto-detected on file "
                              "change too)",
                        style={"marginLeft": "0.5em", "padding": "0.2em 0.6em",
                               "fontSize": "0.85em"},
                    ),
                    html.Span("View:", style={"margin": "0 0.5em 0 1.5em"}),
                    dcc.RadioItems(
                        id="view-picker",
                        # label kept short ("PA") to save a line; the value
                        # stays "Position Angle" (used throughout summary.py /
                        # callbacks.py).
                        options=[{"label": ("PA" if v == "Position Angle" else v),
                                  "value": v}
                                 for v in ("Position", "Position Angle", "Flux",
                                           "Polarization", "Kinematics")],
                        value="Position",
                        inline=True,
                        inputStyle={"marginRight": "0.25em", "marginLeft": "0.5em"},
                    ),
                    # "Visualize recommendations": when ON + model=current, the
                    # summary + overlay are rendered with the user's pending
                    # edits applied to the underlying CSV. Auto-managed when
                    # model is a backup or a "Rec: <slug>" entry (disabled,
                    # implicit for the latter).
                    dcc.Checklist(
                        id="visualize-checkbox",
                        options=[{"label": " Visualize recommendations",
                                  "value": "yes"}],
                        # Default ON: reviewers almost always want to see their
                        # in-progress edits applied. No-op when there are no
                        # recommendations yet.
                        value=["yes"],
                        inputStyle={"marginRight": "0.3em"},
                        style={"marginLeft": "1.5em",
                               "fontSize": "0.9em",
                               "color": "#444"},
                    ),
                    # The overlay panel synthesizes the Stokes I image from
                    # the epoch's clean components convolved with the
                    # restoring beam by default — no NRAO fetch, no on-disk
                    # cache, ~1 ms per epoch, and the result matches the
                    # restored CLEAN FITS to within a fraction of a percent
                    # at the contour levels that matter for review. Tick
                    # this checkbox to fall back to the real CLEAN FITS
                    # image (which carries the residual noise sea synthesis
                    # cannot reproduce).
                    dcc.Checklist(
                        id="use-fits-checkbox",
                        options=[{"label": " Use FITS images",
                                  "value": "yes"}],
                        value=[],
                        inputStyle={"marginRight": "0.3em"},
                        style={"marginLeft": "1em",
                               "fontSize": "0.9em",
                               "color": "#444"},
                    ),
                    # Replace the single-epoch contour background with the
                    # epoch-averaged "stacked" image (all epochs' clean
                    # components / N, convolved with the median beam). The
                    # per-epoch cluster overlay still tracks the slider.
                    # Overrides "Use FITS images" when both are ticked.
                    dcc.Checklist(
                        id="stack-image-checkbox",
                        options=[{"label": " Stacked image",
                                  "value": "yes"}],
                        value=[],
                        inputStyle={"marginRight": "0.3em"},
                        style={"marginLeft": "1em",
                               "fontSize": "0.9em",
                               "color": "#444"},
                    ),
                ],
                style={"display": "flex", "alignItems": "center", "marginTop": "0.5em"},
            ),
        ],
        style={"padding": "0.75em 1em", "borderBottom": "1px solid #ddd",
               "background": "#fafafa"},
    )

    vector_scale_row = html.Div(
        [
            html.Span("Vector scale (Kinematics):",
                      style={"marginRight": "0.75em", "color": "#555"}),
            html.Div(
                dcc.Slider(
                    id="vector-scale",
                    min=0.2, max=5.0, step=0.1, value=1.0,
                    marks={0.5: "½×", 1: "1×", 2: "2×", 3: "3×", 5: "5×"},
                    tooltip={"placement": "bottom", "always_visible": False},
                    updatemode="mouseup",
                ),
                style={"flex": "1 1 0", "minWidth": "200px"},
            ),
        ],
        id="vector-scale-row",
        style={"display": "flex", "alignItems": "center",
               "padding": "0.25em 1em", "fontSize": "0.9em"},
    )

    summary_panel = html.Div(
        [
            html.Div(
                [
                    html.H4("Summary plots", style={"margin": "0.25em 0"}),
                    # Hide the non-robust (slategray) clusters from both the
                    # plots and the legend. Unassigned (-1) / synthetic
                    # (>=1000) clusters are unaffected.
                    dcc.Checklist(
                        id="hide-non-robust-checkbox",
                        options=[{"label": " Hide non-robust clusters",
                                  "value": "yes"}],
                        value=[],
                        inputStyle={"marginRight": "0.3em"},
                        style={"marginLeft": "1.5em", "fontSize": "0.85em",
                               "color": "#444"},
                    ),
                    # Projected motion (Position fit line + Kinematics points /
                    # vectors) is shown for ALL robust clusters by default;
                    # ticking this restores the old behaviour of only drawing
                    # motions that clear the >=3σ (or slow-and-tight) gate.
                    # Labelled "Hide uncertain motions" since the kept set also
                    # includes slow-but-tightly-constrained fits, not just 3σ.
                    dcc.Checklist(
                        id="only-3sigma-checkbox",
                        options=[{"label": " Hide uncertain motions",
                                  "value": "yes"}],
                        value=[],
                        inputStyle={"marginRight": "0.3em"},
                        style={"marginLeft": "1.5em", "fontSize": "0.85em",
                               "color": "#444"},
                    ),
                ],
                style={"display": "flex", "alignItems": "center"},
            ),
            # Read-only warning when the loaded model's saved CSV has a
            # per-epoch robust inconsistency (a latent data bug). The viewer
            # renders correctly regardless (per-cluster robust), but this flags
            # the source for repair via `mojave-review-audit-robust`. Hidden
            # when consistent. The app never writes to Results/ itself.
            html.Div(
                id="robust-warning",
                style={"display": "none"},
            ),
            vector_scale_row,
            dcc.Loading(
                dcc.Graph(
                    id="summary-graph",
                    style={"height": "720px"},
                    responsive=True,
                    # Strip the box-select and lasso-select tools from the
                    # modebar. Selection is click-only on purpose: those
                    # two modes behave quite differently (e.g. don't toggle
                    # on a repeat click, can't be partially undone) and
                    # reviewers reported getting stranded with an
                    # accidental box-selection they couldn't reverse.
                    # Clicking individual points (with the click-toggle
                    # callback in ui/callbacks.py) is the supported flow.
                    config={
                        "modeBarButtonsToRemove": ["select2d", "lasso2d"],
                        "displaylogo": False,
                    },
                ),
                type="default",
            ),
        ],
        id="summary-panel",
        style={"flex": "1 1 0", "padding": "0.5em", "minWidth": "0"},
    )

    epoch_controls = html.Div(
        [
            html.Button("◀", id="epoch-prev", n_clicks=0,
                        style={"width": "2.2em", "marginRight": "0.5em"}),
            html.Button("▶", id="epoch-next", n_clicks=0,
                        style={"width": "2.2em", "marginRight": "0.5em"}),
            # Escape hatch when Plotly's SVG layer ends up stale (rare but
            # reported in marathon review sessions): clicking this forces
            # the overlay figure to redraw with a fresh uirevision key, so
            # any stale axis-state or hidden-trace state is discarded.
            # Also useful as a "back to the full source view" shortcut
            # after the reviewer has zoomed in to inspect one cluster.
            html.Button("Reset view", id="overlay-reset", n_clicks=0,
                        title="Force the overlay panel to redraw and "
                              "reset to the default zoom",
                        style={"marginRight": "1em", "padding": "0.2em 0.6em",
                               "fontSize": "0.85em"}),
            html.Div(
                dcc.Slider(
                    id="epoch-slider", min=0, max=0, step=1, value=0,
                    marks={}, included=False,
                    tooltip={"placement": "bottom", "always_visible": False},
                    updatemode="mouseup",
                ),
                style={"flex": "1 1 0", "minWidth": "200px"},
            ),
            html.Span(id="epoch-label",
                      style={"marginLeft": "1em", "minWidth": "9em",
                             "color": "#555", "fontFamily": "ui-monospace, monospace"}),
            # Opens MOJAVE's per-epoch montage.png for the active epoch in a new
            # tab. Styled as a button; its href is set by the epoch-label
            # callback. target=_blank + rel for a safe external link.
            html.A(
                "MOJAVE montage ↗",
                id="montage-link",
                href="#",
                target="_blank",
                rel="noopener noreferrer",
                title="Open this epoch's MOJAVE montage plot in a new tab",
                style={"marginLeft": "1em", "padding": "0.2em 0.6em",
                       "fontSize": "0.85em", "whiteSpace": "nowrap",
                       "border": "1px solid #ccc", "borderRadius": "4px",
                       "background": "#f7f7f7", "color": "#1f77b4",
                       "textDecoration": "none"},
            ),
        ],
        style={"display": "flex", "alignItems": "center",
               "padding": "0.25em 0.5em"},
    )

    overlay_panel = html.Div(
        [
            html.H4("Epoch overlay", style={"margin": "0.25em 0"}),
            epoch_controls,
            dcc.Loading(
                dcc.Graph(
                    id="overlay-graph",
                    style={"height": "720px"},
                    responsive=True,
                ),
                type="default",
            ),
        ],
        id="overlay-panel",
        style={"flex": "1 1 0", "padding": "0.5em", "minWidth": "0"},
    )

    # Vertical drag handle between the two panels — wired up by
    # assets/resizable.js. Initial flex is 1/1, drag updates to fixed-px.
    # Shares the .split-handle class with the Window-N panel's divider.
    split_handle = html.Div(id="split-handle", className="split-handle",
                            title="Drag to resize panels")

    body = html.Div(
        [summary_panel, split_handle, overlay_panel],
        style={"display": "flex", "padding": "0.5em"},
    )

    return html.Div(
        [
            dcc.Store(id="reviewer-store", data=reviewer),
            dcc.Store(id="beam-params"),
            # Decimal year of the epoch currently shown in the overlay panel,
            # published by the epoch-label callback. A clientside callback
            # draws a vertical marker at this epoch on the summary plots whose
            # x-axis is epoch (Position / Flux / Polarization). epoch-line-dummy
            # is just that callback's no-op output target.
            dcc.Store(id="active-epoch", data=None),
            dcc.Store(id="epoch-line-dummy", data=None),
            # Increments whenever the user clicks "Reset view"; folds into
            # the overlay's uirevision key so a click forces a complete
            # redraw + axis reset.
            dcc.Store(id="overlay-reset-counter", data=0),
            # Increments whenever the user clicks "↻ Reload"; participates
            # as an Input on every callback that reads load_bundle, so a
            # click forces a re-render with freshly-read data. Pairs with
            # the mtime-based invalidation in data/loader.py.
            dcc.Store(id="reload-counter", data=0),
            # Selection of summary-graph points: a list of {"cid", "epoch"}
            # dicts. Updated by the click-toggle callback only — box-select
            # and lasso are disabled by stripping select2d/lasso2d from
            # the summary plot's modebar (see above). Drives both the
            # gold-diamond highlight in the summary figure and the
            # selection-driven actions in the Edits tab.
            dcc.Store(id="selection-store", data=[]),
            # Bumped when the builder saves Stage 2 notes (admin mode); an Input
            # on _refresh_notes so the rendered panel updates immediately.
            dcc.Store(id="notes-saved-counter", data=0),
            # Stage-3 aggregation (admin). agg-preview-rec holds the composed
            # aggregated Recommendation (dict) when "Preview aggregated" is on,
            # else None — it is an Input on the summary + overlay callbacks so
            # the plots show the aggregated model. agg-view-store holds the
            # per-key edit dicts so the compose step can reconstruct accepted
            # edits. Both are always present (non-admin leaves them None).
            dcc.Store(id="agg-preview-rec", data=None),
            dcc.Store(id="agg-view-store", data=None),
            header,
            # Read-only source lab-notebook: the durable notes/<source>.md
            # (Stages 1-2 + decisions ledger) plus the live open-suggestions
            # assembled from the submitted recommendation JSONs. Collapsible so
            # it doesn't crowd the plots; rendered by ui/callbacks._refresh_notes.
            html.Details(
                [
                    html.Summary(
                        "📓  Source notes & open suggestions",
                        style={"cursor": "pointer", "padding": "0.4em 1em",
                               "fontWeight": 600, "color": "#333",
                               "userSelect": "none"},
                    ),
                    dcc.Markdown(
                        id="notes-content",
                        style={"padding": "0.25em 1.25em 1em",
                               "maxHeight": "42vh", "overflowY": "auto",
                               "fontSize": "0.9em", "lineHeight": "1.4"},
                    ),
                    # Admin/builder-only: edit the Stage 2 (baseline) notes
                    # section of notes/<source>.md. Reviewers never see this.
                    *([] if not admin else [
                        html.Div(
                            [
                                html.Hr(style={"margin": "0 1.25em 0.5em"}),
                                html.Div(
                                    "✏️ Edit Stage 2 (baseline) notes — markdown, "
                                    "saved to notes/<source>.md",
                                    style={"padding": "0 1.25em 0.25em",
                                           "fontSize": "0.8em", "color": "#666"},
                                ),
                                dcc.Textarea(
                                    id="stage2-editor",
                                    style={"width": "calc(100% - 2.5em)",
                                           "margin": "0 1.25em",
                                           "minHeight": "120px",
                                           "fontFamily": "ui-monospace, monospace",
                                           "fontSize": "0.85em"},
                                ),
                                html.Div(
                                    [
                                        html.Button(
                                            "↻ Seed from submission summary",
                                            id="seed-stage2-summary-btn", n_clicks=0,
                                            title="Fill the editor with your own "
                                                  "submission's notebook summary "
                                                  "(cleaned for markdown)",
                                            style={"padding": "0.3em 0.9em",
                                                   "fontSize": "0.85em"},
                                        ),
                                        html.Button(
                                            "Save Stage 2 notes",
                                            id="save-stage2-btn", n_clicks=0,
                                            title="Save and mark Stage 2 in progress",
                                            style={"padding": "0.3em 0.9em",
                                                   "fontSize": "0.85em",
                                                   "marginLeft": "0.5em"},
                                        ),
                                        html.Button(
                                            "Save & set Stage 2 done",
                                            id="save-stage2-done-btn", n_clicks=0,
                                            title="Save and mark Stage 2 done",
                                            style={"padding": "0.3em 0.9em",
                                                   "fontSize": "0.85em",
                                                   "marginLeft": "0.5em",
                                                   "background": "#1f77b4",
                                                   "color": "white",
                                                   "border": "none",
                                                   "borderRadius": "4px"},
                                        ),
                                        html.Span(
                                            id="stage2-save-status",
                                            style={"marginLeft": "0.75em",
                                                   "fontSize": "0.8em",
                                                   "color": "#0a8"},
                                        ),
                                    ],
                                    style={"padding": "0.4em 1.25em 0.5em"},
                                ),
                                # Stage-2 BASELINE apply (moved here from the
                                # recommendations header). Distinct from the
                                # Stage-3 aggregated apply: this applies the
                                # builder's OWN single recommendation. Visibility
                                # is stage-gated (shown until Stage 2 is done).
                                html.Div(
                                    [
                                        html.Span(
                                            "Stage 2 — baseline apply (your own "
                                            "single recommendation):",
                                            style={"fontSize": "0.8em",
                                                   "color": "#666",
                                                   "marginRight": "0.5em"},
                                        ),
                                        html.Button(
                                            "Generate baseline apply command (Stage 2)",
                                            id="generate-apply-cmd-btn", n_clicks=0,
                                            title="Copy-pasteable mojave-apply "
                                                  "command for YOUR recommendation "
                                                  "(the Stage-2 baseline apply)",
                                            style={"padding": "0.3em 0.9em",
                                                   "fontSize": "0.85em",
                                                   "background": "#d68a00",
                                                   "color": "white", "border": "none",
                                                   "borderRadius": "4px",
                                                   "cursor": "pointer"},
                                        ),
                                    ],
                                    style={"padding": "0 1.25em 0.6em",
                                           "display": "flex", "alignItems": "center",
                                           "flexWrap": "wrap"},
                                ),
                            ],
                        ),
                    ]),
                ],
                id="notes-details",
                open=False,
                style={"borderBottom": "1px solid #ddd", "background": "#fbfbfb"},
            ),
            # Admin-only Stage-3 aggregation: review every reviewer's submitted
            # recommendation side-by-side, decide each change, preview the
            # result. The actual apply (mojave-apply + ledger) is build-step #4.
            *([] if not admin else [
                html.Details(
                    [
                        html.Summary(
                            "🧩  Aggregate reviews (Stage 3 — admin)",
                            style={"cursor": "pointer", "padding": "0.4em 1em",
                                   "fontWeight": 600, "color": "#333",
                                   "userSelect": "none"},
                        ),
                        html.Div(
                            [
                                dcc.Checklist(
                                    id="agg-preview-toggle",
                                    options=[{"label": " Preview aggregated on plots",
                                              "value": "on"}],
                                    value=[],
                                    style={"display": "inline-block",
                                           "fontSize": "0.85em"},
                                ),
                                html.Button(
                                    "Apply aggregated decisions (Stage 3)…",
                                    id="agg-apply-btn", n_clicks=0,
                                    title="Apply the reconciled reviewer decisions "
                                          "to Results/ (runs mojave-apply, writes "
                                          "the ledger)",
                                    style={"marginLeft": "1em", "padding": "0.3em 0.9em",
                                           "fontSize": "0.85em", "background": "#b9770e",
                                           "color": "white", "border": "none",
                                           "borderRadius": "4px", "cursor": "pointer"},
                                ),
                                # Flag the source as needing more discussion:
                                # leaves it in Stage 2 (phase ``open``) with the
                                # open submissions intact, but stamps a
                                # ``needs discussion`` suffix on the notes
                                # Status. Every reviewer sees a global
                                # "needs discussion" tag in the source picker
                                # (overrides their personal status — see
                                # ``_reviewer_status``). Writes only under
                                # recommendations/, never Results/.
                                html.Button(
                                    "Needs Discussion",
                                    id="agg-needs-discussion-btn", n_clicks=0,
                                    title="Flag this source for more discussion. "
                                          "Source stays in Stage 2 (submissions "
                                          "stay open); reviewers see a "
                                          "'needs discussion' tag in the picker.",
                                    style={"marginLeft": "0.6em", "padding": "0.3em 0.9em",
                                           "fontSize": "0.85em", "background": "#d68a00",
                                           "color": "white", "border": "none",
                                           "borderRadius": "4px", "cursor": "pointer"},
                                ),
                                html.Span(
                                    id="agg-summary",
                                    style={"marginLeft": "1em", "fontSize": "0.8em",
                                           "color": "#555"},
                                ),
                                html.Span(
                                    id="agg-apply-status",
                                    style={"marginLeft": "1em", "fontSize": "0.8em",
                                           "fontWeight": 600, "color": "#1a7"},
                                ),
                            ],
                            style={"padding": "0.2em 1.25em 0.4em",
                                   "display": "flex", "alignItems": "center",
                                   "flexWrap": "wrap"},
                        ),
                        # The decisions (submissions list, robustness table,
                        # cross-ID / use-in-fit Accept checkboxes, reviewer
                        # comments) come FIRST — they're the actionable content,
                        # so they sit directly under the buttons rather than
                        # below the dated-note box (which buried them).
                        html.Div(
                            id="agg-panel-body",
                            style={"padding": "0.1em 1.25em 1em",
                                   "maxHeight": "46vh", "overflowY": "auto"},
                        ),
                        # Add a dated note to the source log (section 3 ledger).
                        # Seeded with pending reviewer comments; editable; the
                        # admin trims and clicks "Add" to append a dated entry.
                        # Secondary to the decisions above, so it lives below them.
                        html.Div(
                            [
                                html.Div(
                                    "📝 Add a dated note to the source log "
                                    "(appended to section 3; seeded with pending "
                                    "reviewer comments):",
                                    style={"fontSize": "0.8em", "color": "#666",
                                           "padding": "0 0 0.25em"},
                                ),
                                dcc.Textarea(
                                    id="stage3-note-input",
                                    style={"width": "100%", "minHeight": "80px",
                                           "fontFamily": "ui-monospace, monospace",
                                           "fontSize": "0.85em"},
                                ),
                                html.Div(
                                    [
                                        html.Button(
                                            "➕ Add dated note to log",
                                            id="add-stage3-note-btn", n_clicks=0,
                                            style={"padding": "0.3em 0.9em",
                                                   "fontSize": "0.85em",
                                                   "background": "#1f77b4",
                                                   "color": "white", "border": "none",
                                                   "borderRadius": "4px",
                                                   "cursor": "pointer"},
                                        ),
                                        html.Button(
                                            "↻ Reseed from submissions",
                                            id="reseed-stage3-note-btn", n_clicks=0,
                                            title="Re-pull pending reviewer comments "
                                                  "into the box (discards edits)",
                                            style={"padding": "0.3em 0.9em",
                                                   "fontSize": "0.85em",
                                                   "marginLeft": "0.5em"},
                                        ),
                                        html.Span(
                                            id="stage3-note-status",
                                            style={"marginLeft": "0.75em",
                                                   "fontSize": "0.8em",
                                                   "color": "#0a8"},
                                        ),
                                    ],
                                    style={"padding": "0.4em 0 0.2em"},
                                ),
                            ],
                            style={"padding": "0.4em 1.25em 0.6em",
                                   "borderTop": "1px dashed #ddd"},
                        ),
                    ],
                    id="agg-details",
                    open=False,
                    style={"borderBottom": "1px solid #ddd", "background": "#f7f9fb"},
                ),
                # Confirm dialog for the (destructive) aggregated apply.
                html.Div(
                    id="agg-apply-modal",
                    style={"display": "none"},
                    children=[
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.H4("Apply aggregated decisions",
                                                style={"margin": 0}),
                                        html.Button(
                                            "×", id="agg-apply-close", n_clicks=0,
                                            style={"border": "none",
                                                   "background": "transparent",
                                                   "fontSize": "1.5em", "lineHeight": 1,
                                                   "cursor": "pointer", "color": "#888"},
                                        ),
                                    ],
                                    style={"display": "flex",
                                           "justifyContent": "space-between",
                                           "alignItems": "center",
                                           "marginBottom": "0.4em"},
                                ),
                                html.P(
                                    "If decisions are pending: generates a "
                                    "copy-paste mojave-apply command (the app "
                                    "does NOT modify Results/). Running that "
                                    "command in a terminal backs up + "
                                    "regenerates Results/ (CSV; plus PDF + MP4 "
                                    "when --make-plots is given and the source "
                                    "carries them), archives the considered "
                                    "submissions, and writes the Stage-3 ledger "
                                    "+ Status — all in one step. "
                                    "If there are NO decisions: finalizes the "
                                    "source in-app — archives the considered "
                                    "submissions, appends a Stage-3 ledger "
                                    "entry (preserving reviewer comments), and "
                                    "sets Status to \"Stage 3 done · finalized "
                                    "(no changes)\". Nothing in Results/ is "
                                    "touched.",
                                    style={"color": "#666", "fontSize": "0.88em",
                                           "margin": "0 0 0.5em"},
                                ),
                                html.Div(id="agg-apply-modal-text",
                                         style={"fontSize": "0.9em",
                                                "marginBottom": "0.6em"}),
                                html.Div(
                                    [
                                        html.Button(
                                            "Generate apply command",
                                            id="agg-apply-confirm",
                                            n_clicks=0,
                                            style={"padding": "0.45em 1em",
                                                   "background": "#b9770e",
                                                   "color": "white", "border": "none",
                                                   "borderRadius": "4px",
                                                   "cursor": "pointer"},
                                        ),
                                        html.Button(
                                            "Cancel", id="agg-apply-cancel",
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
                                           "marginTop": "0.5em"},
                                ),
                            ],
                            style={"background": "white", "padding": "1.5em",
                                   "borderRadius": "6px", "maxWidth": "560px",
                                   "margin": "8% auto",
                                   "boxShadow": "0 4px 20px rgba(0,0,0,0.25)"},
                        ),
                    ],
                ),
            ]),
            # Admin-only Window-N review (the --editN replacement): scrub the
            # pipeline's per-window cluster fits, record per-window N choices
            # (autosaved under <recs>/<source>/nwin_edits/), and generate the
            # find_clusters.py --N_win_file rerun command. Needs the local
            # cluster_fits/ files (excluded from the server sync), so on a
            # server deploy it just shows a hint.
            *([] if not admin else [build_nwin_panel()]),
            body,
            build_recommendations_panel(admin=admin),
        ],
        style={"fontFamily": "system-ui, sans-serif"},
    )
