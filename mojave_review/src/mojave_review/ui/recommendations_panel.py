"""Layout for the bottom-of-page Recommendations panel.

Four tabs:

* **Source** — free-text comment about the source as a whole.
* **Clusters** — editable table, one row per cluster in the current model.
* **Epochs** — editable table, one row per epoch.
* **Edits** — form to add a structured edit (clusterID change, use_in_fit
  toggle) plus a list of edits the reviewer has accumulated.

The panel autosaves to disk on every field change — there is no "save"
button. Last-saved timestamp is shown in the header.
"""

from __future__ import annotations

from dash import dash_table, dcc, html


def build_epoch_rows(epoch_rows: list[dict]) -> list:
    """Build the Epoch Notes rows: one ``dcc.Textarea`` per epoch (a real text
    field, so editing behaves normally — cursor / arrows / backspace / click /
    LTR). Each textarea's id carries the epoch key so a bridge callback can
    reconstruct the ``[{epoch, comment}]`` store the consumers expect. The
    bridge keys on ``n_blur`` (commit on blur), so typing stays purely
    client-side — no per-keystroke server round-trip — matching the old
    DataTable's commit-on-blur cadence."""
    header = html.Div(
        [
            html.Div("Epoch", style={"width": "26%", "fontWeight": 600}),
            html.Div("Year", style={"width": "16%", "fontWeight": 600,
                                    "textAlign": "right"}),
            html.Div("Comment", style={"flex": "1", "fontWeight": 600,
                                       "marginLeft": "0.6em"}),
        ],
        style={"display": "flex", "gap": "0.4em", "padding": "2px 0",
               "borderBottom": "1px solid #ddd", "fontSize": "0.9em"},
    )
    rows = [header]
    for r in epoch_rows:
        rows.append(html.Div(
            [
                html.Div(str(r["epoch"]), style={"width": "26%"}),
                html.Div(f"{float(r['epoch_val']):.4f}",
                         style={"width": "16%", "textAlign": "right",
                                "color": "#666"}),
                dcc.Textarea(
                    id={"type": "epoch-comment", "epoch": str(r["epoch"])},
                    value=r.get("comment") or "",
                    style={"flex": "1", "marginLeft": "0.6em",
                           "minHeight": "2.2em", "resize": "vertical",
                           "fontFamily": "system-ui, sans-serif",
                           "fontSize": "0.9em", "direction": "ltr",
                           "textAlign": "left"},
                ),
            ],
            style={"display": "flex", "gap": "0.4em", "alignItems": "center",
                   "padding": "2px 0", "fontSize": "0.9em"},
        ))
    return rows


# ---------------------------------------------------------------------------
# Tab: Source
# ---------------------------------------------------------------------------

def _source_tab() -> dcc.Tab:
    return dcc.Tab(
        label="Source Notes",
        value="source",
        children=[
            html.Div(
                [
                    html.Label("Overall comment about this source:",
                               style={"fontSize": "0.9em", "color": "#444"}),
                    dcc.Textarea(
                        id="source-comment",
                        placeholder="Anything notable about this source as a whole — "
                                    "e.g. unusual jet morphology, epochs to be wary of, "
                                    "overall agreement / disagreement with the model.",
                        style={"width": "100%", "minHeight": "120px",
                               "fontFamily": "system-ui, sans-serif"},
                    ),
                ],
                style={"padding": "0.75em"},
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Tab: Clusters
# ---------------------------------------------------------------------------

def _clusters_tab() -> dcc.Tab:
    return dcc.Tab(
        label="Robustness",
        value="clusters",
        children=[
            html.Div(
                [
                    # Quick "agree with everything" shortcut. When checked, the
                    # cluster table below becomes uneditable and visually greys
                    # out; derived set_robust edits are suppressed.
                    html.Div(
                        [
                            dcc.Checklist(
                                id="no-changes-checkbox",
                                options=[{"label": " No changes suggested",
                                          "value": "yes"}],
                                value=[],
                                inputStyle={"marginRight": "0.4em"},
                                style={"fontWeight": 600},
                            ),
                        ],
                        style={"marginBottom": "0.5em"},
                    ),
                    html.Div(
                        "Only eligible clusters are listed — those with at least 5 "
                        "epochs of use_in_fit=True. Pick a 'Recommended Changes' "
                        "value that differs from 'Current Robust Status' and an "
                        "edit is added to the list automatically.",
                        style={"fontSize": "0.85em", "color": "#666",
                               "marginBottom": "0.5em"},
                    ),
                    html.Div(
                        id="cluster-table-wrapper",
                        children=[
                    dash_table.DataTable(
                        id="cluster-feedback-table",
                        columns=[
                            {"name": "Eligible Clusters", "id": "clusterID",
                             "type": "numeric", "editable": False},
                            {"name": "Current Robust Status", "id": "current_robust",
                             "editable": False},
                            {"name": "Recommended Changes", "id": "recommended_robust",
                             "presentation": "dropdown", "editable": True},
                            {"name": "Comment", "id": "comment", "editable": True},
                        ],
                        data=[],
                        dropdown={
                            "recommended_robust": {
                                "options": [
                                    {"label": "—", "value": ""},
                                    {"label": "Robust", "value": "robust"},
                                    {"label": "Non-robust", "value": "non-robust"},
                                ],
                            },
                        },
                        # Cluster 0 is the fitted core — its robust status is
                        # immutable, so restrict its dropdown to just "—" and
                        # grey the cell. The Comment cell stays editable.
                        dropdown_conditional=[
                            {
                                "if": {"column_id": "recommended_robust",
                                       "filter_query": "{clusterID} = 0"},
                                "options": [{"label": "—", "value": ""}],
                            },
                        ],
                        editable=True,
                        style_table={"maxHeight": "320px", "overflowY": "auto"},
                        style_cell={"textAlign": "left", "padding": "4px 8px",
                                    "fontFamily": "system-ui, sans-serif",
                                    "fontSize": "0.9em",
                                    # Wrap long comments instead of letting
                                    # them scroll horizontally inside a
                                    # narrow input — the original behaviour
                                    # parked the edit cursor at the right
                                    # edge with no way to see the start of
                                    # the text.
                                    "whiteSpace": "normal", "height": "auto",
                                    "lineHeight": "1.3em"},
                        # Give the Comment column the lion's share of the
                        # width; the other three are fixed-content. Without
                        # this, Plotly's auto-layout makes Comment narrow,
                        # which is the root cause of the cramped-edit feel.
                        style_cell_conditional=[
                            {"if": {"column_id": "clusterID"},
                             "width": "12%", "textAlign": "right"},
                            {"if": {"column_id": "current_robust"},
                             "width": "18%"},
                            {"if": {"column_id": "recommended_robust"},
                             "width": "20%"},
                            {"if": {"column_id": "comment"},
                             "width": "50%", "minWidth": "260px"},
                        ],
                        # Force left-alignment on the in-place edit input.
                        # The DataTable's editing widget is an
                        # ``<input class="dash-cell-value">`` (the class
                        # is on the input itself, not on a parent) and
                        # Dash's default stylesheet leaves it inheriting
                        # ``text-align: right``, which parks the cursor
                        # at the right edge of long text and makes
                        # editing painful. Targeting ``input.dash-cell-
                        # value`` / ``textarea.dash-cell-value`` with
                        # !important overrides that. Also pin direction
                        # to ltr defensively.
                        css=[
                            {"selector": "input.dash-cell-value,"
                                         " textarea.dash-cell-value",
                             "rule": "text-align: left !important; "
                                     "direction: ltr !important;"},
                        ],
                        style_header={"fontWeight": 600, "background": "#f0f0f0"},
                        style_data_conditional=[
                            # Recommendation differs from current → highlight.
                            {"if": {"filter_query":
                                    "{recommended_robust} = 'non-robust' && "
                                    "{current_robust} = 'Robust'"},
                             "backgroundColor": "#fff5f5"},
                            {"if": {"filter_query":
                                    "{recommended_robust} = 'robust' && "
                                    "{current_robust} = 'Non-robust'"},
                             "backgroundColor": "#fff5f5"},
                            # Recommendation matches current → soft confirm.
                            {"if": {"filter_query":
                                    "{recommended_robust} = 'robust' && "
                                    "{current_robust} = 'Robust'"},
                             "backgroundColor": "#f5fff5"},
                            {"if": {"filter_query":
                                    "{recommended_robust} = 'non-robust' && "
                                    "{current_robust} = 'Non-robust'"},
                             "backgroundColor": "#f5fff5"},
                            # Core row — grey out the Recommended Changes cell
                            # so the lock is visually obvious.
                            {"if": {"column_id": "recommended_robust",
                                    "filter_query": "{clusterID} = 0"},
                             "backgroundColor": "#eee",
                             "color": "#999",
                             "fontStyle": "italic"},
                        ],
                    ),
                        ],
                    ),  # /cluster-table-wrapper
                ],
                style={"padding": "0.75em"},
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Tab: Epochs
# ---------------------------------------------------------------------------

def _epochs_tab() -> dcc.Tab:
    return dcc.Tab(
        label="Epoch Notes",
        value="epochs",
        children=[
            html.Div(
                [
                    html.Div(
                        "Leave comments per epoch. To suggest excluding an entire "
                        "epoch from the fit, add a 'use_in_fit=False, scope=epoch' "
                        "entry on the Edits tab.",
                        style={"fontSize": "0.85em", "color": "#666",
                               "marginBottom": "0.5em"},
                    ),
                    # Real dcc.Textarea per epoch (NOT a DataTable cell) so the
                    # comment edits like a normal text field — proper cursor,
                    # arrow keys, backspace, click-to-position, left-to-right.
                    # `epoch-feedback-table` is now a dcc.Store mirroring the old
                    # table's `.data` shape ([{epoch, comment}]) so every
                    # consumer (autosave / submit / build_rec) is unchanged; a
                    # bridge callback keeps it in sync from the textareas.
                    dcc.Store(id="epoch-feedback-table", data=[]),
                    html.Div(
                        id="epoch-feedback-rows",
                        children=build_epoch_rows([]),
                        style={"maxHeight": "320px", "overflowY": "auto"},
                    ),
                ],
                style={"padding": "0.75em"},
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Tab: Edits
# ---------------------------------------------------------------------------

def _edits_tab() -> dcc.Tab:
    # ---- Selection-driven action panel -----------------------------------
    # Visibility toggled by a callback based on selection-store contents.
    selection_actions = html.Div(
        id="selection-actions",
        children=[
            html.Div(id="selection-summary",
                     style={"fontWeight": 600, "marginBottom": "0.5em"}),
            html.Div(
                [
                    html.Label("Comment for these edits (optional):",
                               style={"fontSize": "0.85em", "color": "#444",
                                      "marginRight": "0.5em"}),
                    dcc.Input(id="selection-comment", type="text",
                              placeholder="will be attached to each edit (shown after '#')",
                              style={"width": "min(420px, 70%)"}),
                ],
                style={"display": "flex", "alignItems": "center",
                       "gap": "0.4em", "marginBottom": "0.5em"},
            ),
            html.Div(
                [
                    html.Label("Set use_in_fit to:",
                               style={"fontSize": "0.85em", "color": "#444",
                                      "marginRight": "0.5em"}),
                    dcc.RadioItems(
                        id="uif-value",
                        options=[{"label": "False", "value": "false"},
                                 {"label": "True",  "value": "true"}],
                        value="false",
                        inline=True,
                        inputStyle={"marginRight": "0.25em",
                                    "marginLeft": "0.5em"},
                        style={"marginRight": "1em"},
                    ),
                    html.Button("Apply to selected points",
                                id="apply-uif-single-btn", n_clicks=0,
                                style={"marginRight": "0.5em"}),
                    html.Button("Apply to whole epoch",
                                id="apply-uif-epoch-btn", n_clicks=0,
                                style={"marginRight": "0.5em"}),
                ],
                style={"display": "flex", "flexWrap": "wrap",
                       "alignItems": "center",
                       "gap": "0.4em", "marginBottom": "0.5em"},
            ),
            html.Div(
                [
                    html.Label("New clusterID:",
                               style={"fontSize": "0.85em", "color": "#444",
                                      "marginRight": "0.4em"}),
                    dcc.Input(id="renumber-to-id", type="number",
                              style={"width": "80px", "marginRight": "0.75em"}),
                    html.Button("Renumber selected points to this ID",
                                id="apply-renumber-single-btn", n_clicks=0,
                                style={"marginRight": "0.5em"}),
                    html.Button("Renumber all epochs of selected clusters to this ID",
                                id="apply-renumber-all-btn", n_clicks=0),
                ],
                style={"display": "flex", "flexWrap": "wrap",
                       "alignItems": "center", "gap": "0.4em",
                       "marginBottom": "0.5em"},
            ),
            html.Div(
                [
                    html.Button("Clear selection", id="clear-selection-btn",
                                n_clicks=0,
                                style={"fontSize": "0.85em"}),
                    html.Span(id="selection-action-hint",
                              style={"marginLeft": "1em", "color": "#0a8",
                                     "fontSize": "0.85em"}),
                ],
            ),
        ],
        style={"display": "none",
               "padding": "0.5em 0.75em",
               "background": "#f0f7ff",
               "borderBottom": "1px solid #d6e8f7",
               "borderTop": "1px solid #d6e8f7",
               "marginBottom": "0.5em"},
    )

    # Visible only when the selection is empty.
    no_selection_note = html.Div(
        id="no-selection-note",
        children=(
            "Click a point in the Position, Flux, or Polarization plots to "
            "select it (click the same point again to deselect). Then come "
            "back here to turn that selection into edits."
        ),
        style={"padding": "0.75em 0.75em 0.5em",
               "color": "#666", "fontStyle": "italic", "fontSize": "0.9em"},
    )

    edit_list_view = html.Div(
        [
            html.Hr(style={"margin": "0.5em 0"}),
            html.Div("Pending edits", style={"fontWeight": 600,
                                              "marginBottom": "0.25em",
                                              "padding": "0 0.75em"}),
            html.Div(id="edits-list",
                     style={"padding": "0 0.75em 0.75em"}),
        ],
    )

    return dcc.Tab(
        label="ID / use-in-fit Edits", value="edits",
        children=[selection_actions, no_selection_note, edit_list_view],
    )


# ---------------------------------------------------------------------------
# Composite panel
# ---------------------------------------------------------------------------

def build_recommendations_panel(admin: bool = False) -> html.Div:
    header_buttons = [
        html.Span(id="submit-status",
                  style={"marginRight": "0.75em",
                         "fontSize": "0.85em",
                         "color": "#666"}),
        html.Button(
            "Submit Recommendation",
            id="submit-recommendation-btn",
            n_clicks=0,
            style={"padding": "0.35em 0.9em", "fontSize": "0.9em",
                   "background": "#1f77b4", "color": "white",
                   "border": "none", "borderRadius": "4px",
                   "cursor": "pointer"},
        ),
        # Opens the reset dialog (reset-to-submitted / delete / cancel).
        # Visibility is managed alongside the Submit button — current model
        # only.
        html.Button(
            "Reset Recommendation",
            id="reset-recommendation-btn",
            n_clicks=0,
            style={"padding": "0.35em 0.9em", "fontSize": "0.9em",
                   "background": "white", "color": "#555",
                   "border": "1px solid #bbb", "borderRadius": "4px",
                   "cursor": "pointer", "marginLeft": "0.5em"},
        ),
    ]
    # NOTE: the admin "Generate baseline apply command (Stage 2)" button used to
    # live here in the header. It moved to the Stage-2 admin block in
    # ui/layout.py so the Stage-2 (baseline) and Stage-3 (aggregated) apply
    # paths are grouped and labelled by stage. Its modal + callbacks are
    # unchanged (id "generate-apply-cmd-btn").

    return html.Div(
        [
            html.Div(
                [
                    html.H4("Recommendations",
                            style={"margin": "0"}),
                    html.Span(id="save-indicator",
                              style={"marginLeft": "1em",
                                     "fontSize": "0.85em", "color": "#888"}),
                    html.Div(
                        header_buttons,
                        style={"marginLeft": "auto",
                               "display": "flex",
                               "alignItems": "center"},
                    ),
                ],
                style={"padding": "0.25em 0.75em",
                       "display": "flex",
                       "alignItems": "center"},
            ),
            dcc.Tabs(
                id="rec-tabs", value="clusters",
                children=[_clusters_tab(), _edits_tab(),
                          _source_tab(), _epochs_tab()],
            ),
            # Owns the in-memory edits list (the dash_table data fields
            # already store the cluster/epoch feedback).
            dcc.Store(id="edits-store", data=[]),
            # Intermediate trigger for Submit: a clientside callback fires
            # on the Submit button click, blurs whatever DataTable cell is
            # currently being edited (so its in-progress text commits to
            # the table's ``data`` prop), and only THEN bumps this store.
            # The server-side submit callback then listens to this store
            # instead of the raw button click, so it always reads the
            # freshest table state. Without this, typing a comment and
            # clicking Submit without leaving the cell silently loses the
            # text.
            dcc.Store(id="submit-trigger", data=0),
            # Holds the renumber action that's waiting on the conflict
            # dialog's confirmation; cleared whenever the dialog resolves.
            dcc.Store(id="pending-conflict-action", data=None),
            # Native confirm dialog used to warn about clusterID collisions
            # before adding renumber edits.
            dcc.ConfirmDialog(id="conflict-confirm", message=""),
            # Bumped whenever the reset dialog resets/deletes the
            # recommendation, so the Submit button label re-evaluates
            # (Resubmit ⇄ Submit) without needing a source/model change.
            dcc.Store(id="rec-reset-counter", data=0),
            # Reset dialog: a 3-choice modal (reset to last submitted /
            # delete draft + submitted / cancel). Native dcc.ConfirmDialog
            # only offers OK+Cancel, so this is a custom modal like the
            # submission one.
            html.Div(
                id="reset-rec-modal",
                style={"display": "none"},
                children=[
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H4("Reset recommendation",
                                            style={"margin": "0"}),
                                    html.Button(
                                        "×", id="close-reset-rec-modal",
                                        n_clicks=0,
                                        style={"border": "none",
                                               "background": "transparent",
                                               "fontSize": "1.5em",
                                               "lineHeight": "1",
                                               "cursor": "pointer",
                                               "color": "#888"},
                                    ),
                                ],
                                style={"display": "flex",
                                       "justifyContent": "space-between",
                                       "alignItems": "center",
                                       "marginBottom": "0.4em"},
                            ),
                            html.P("This affects only your own draft and "
                                   "submission for this source.",
                                   style={"color": "#666", "fontSize": "0.9em",
                                          "margin": "0 0 0.5em"}),
                            # Filled by callback when no submission exists.
                            html.Div(id="reset-modal-info",
                                     style={"color": "#a00",
                                            "fontSize": "0.85em",
                                            "marginBottom": "0.5em"}),
                            html.Div(
                                [
                                    html.Button(
                                        "Reset to last submitted",
                                        id="reset-to-submitted-btn",
                                        n_clicks=0,
                                        style={"padding": "0.45em 1em",
                                               "background": "#1f77b4",
                                               "color": "white",
                                               "border": "none",
                                               "borderRadius": "4px",
                                               "cursor": "pointer"},
                                    ),
                                    html.Button(
                                        "Delete draft & submitted",
                                        id="delete-recs-btn",
                                        n_clicks=0,
                                        style={"padding": "0.45em 1em",
                                               "background": "#c0392b",
                                               "color": "white",
                                               "border": "none",
                                               "borderRadius": "4px",
                                               "cursor": "pointer"},
                                    ),
                                    html.Button(
                                        "Cancel",
                                        id="reset-cancel-btn",
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
                                       "flexWrap": "wrap",
                                       "marginTop": "0.75em"},
                            ),
                        ],
                        style={
                            "background": "white",
                            "padding": "1.5em",
                            "borderRadius": "6px",
                            "maxWidth": "520px",
                            "margin": "8% auto",
                            "boxShadow": "0 4px 20px rgba(0,0,0,0.25)",
                        },
                    ),
                ],
            ),
            # Admin-only modal: a copy-pasteable mojave-apply command line.
            # Only emitted into the layout when admin=True so non-admin
            # users can't trigger any of the related callbacks (also not
            # registered in that case).
            *([] if not admin else [
                html.Div(
                    id="apply-cmd-modal",
                    style={"display": "none"},
                    children=[
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.H4("Apply command",
                                                style={"margin": "0"}),
                                        html.Button(
                                            "×", id="close-apply-cmd-modal",
                                            n_clicks=0,
                                            style={"border": "none",
                                                   "background": "transparent",
                                                   "fontSize": "1.5em",
                                                   "lineHeight": "1",
                                                   "cursor": "pointer",
                                                   "color": "#888"},
                                        ),
                                    ],
                                    style={"display": "flex",
                                           "justifyContent": "space-between",
                                           "alignItems": "center",
                                           "marginBottom": "0.4em"},
                                ),
                                html.P("Copy this and run it in a terminal "
                                       "where you have write access to the "
                                       "Results/ directory:",
                                       style={"color": "#666",
                                              "fontSize": "0.9em",
                                              "margin": "0 0 0.5em"}),
                                dcc.Textarea(
                                    id="apply-cmd-text",
                                    value="",
                                    readOnly=True,
                                    style={"width": "100%",
                                           "height": "140px",
                                           "fontFamily": "ui-monospace, monospace",
                                           "fontSize": "0.85em",
                                           "padding": "0.5em",
                                           "border": "1px solid #ccc",
                                           "borderRadius": "4px",
                                           "resize": "vertical",
                                           "whiteSpace": "pre"},
                                ),
                                html.Div(id="apply-cmd-hint",
                                         style={"fontSize": "0.8em",
                                                "color": "#888",
                                                "marginTop": "0.4em"}),
                                html.Div(
                                    [
                                        html.Button(
                                            "Copy command",
                                            id="copy-apply-cmd",
                                            n_clicks=0,
                                            style={"padding": "0.4em 1em",
                                                   "marginRight": "0.5em"},
                                        ),
                                        html.Button(
                                            "Close",
                                            id="close-apply-cmd-modal-2",
                                            n_clicks=0,
                                            style={"padding": "0.4em 1em"},
                                        ),
                                    ],
                                    style={"textAlign": "right",
                                           "marginTop": "0.75em"},
                                ),
                            ],
                            style={
                                "background": "white",
                                "padding": "1.5em",
                                "borderRadius": "6px",
                                "maxWidth": "780px",
                                "margin": "5% auto",
                                "boxShadow": "0 4px 20px rgba(0,0,0,0.25)",
                            },
                        ),
                    ],
                ),
            ]),
            # Modal shown after a successful Submit — contains the
            # copy-pasteable notebook block.
            html.Div(
                id="submission-modal",
                style={"display": "none"},
                children=[
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H4("Recommendation submitted",
                                            style={"margin": "0"}),
                                    html.Button(
                                        "×", id="close-submission-modal",
                                        n_clicks=0,
                                        style={"border": "none",
                                               "background": "transparent",
                                               "fontSize": "1.5em",
                                               "lineHeight": "1",
                                               "cursor": "pointer",
                                               "color": "#888"},
                                    ),
                                ],
                                style={"display": "flex",
                                       "justifyContent": "space-between",
                                       "alignItems": "center",
                                       "marginBottom": "0.4em"},
                            ),
                            html.P("Copy this to your notebook for your "
                                   "records:",
                                   style={"color": "#666",
                                          "fontSize": "0.9em",
                                          "margin": "0 0 0.5em"}),
                            dcc.Textarea(
                                id="submission-text",
                                value="",
                                readOnly=True,
                                style={"width": "100%",
                                       "height": "420px",
                                       "fontFamily": "ui-monospace, monospace",
                                       "fontSize": "0.85em",
                                       "padding": "0.5em",
                                       "border": "1px solid #ccc",
                                       "borderRadius": "4px",
                                       "resize": "vertical",
                                       "whiteSpace": "pre"},
                            ),
                            html.Div(
                                [
                                    html.Button(
                                        "Copy text", id="copy-submission-text",
                                        n_clicks=0,
                                        style={"padding": "0.4em 1em",
                                               "marginRight": "0.5em"},
                                    ),
                                    html.Button(
                                        "Close", id="close-submission-modal-2",
                                        n_clicks=0,
                                        style={"padding": "0.4em 1em"},
                                    ),
                                ],
                                style={"textAlign": "right",
                                       "marginTop": "0.75em"},
                            ),
                        ],
                        style={
                            "background": "white",
                            "padding": "1.5em",
                            "borderRadius": "6px",
                            "maxWidth": "780px",
                            "margin": "5% auto",
                            "boxShadow": "0 4px 20px rgba(0,0,0,0.25)",
                        },
                    ),
                ],
            ),
        ],
        id="recommendations-panel",
        style={"borderTop": "1px solid #ddd", "background": "#fafafa",
               "marginTop": "0.5em"},
    )
