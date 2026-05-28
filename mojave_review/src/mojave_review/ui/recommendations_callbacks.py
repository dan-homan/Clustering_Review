"""Recommendations panel callbacks: load on source/model change + autosave.

Wires the four tabs (Source / Clusters / Epochs / Edits) to the on-disk
JSON store in `recommendations/`.

Save semantics:
- A single callback writes the whole JSON on any input change (source
  comment, cluster table, epoch table, or edits-store mutations).
- The on-disk file is keyed by (source, model, reviewer-slug).
- The save-indicator shows the file's modification timestamp.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from dash import (
    ALL, Dash, Input, Output, Patch, State, ctx, no_update, html,
)

from ..data.loader import load_bundle
from ..recommendations.schema import (
    ClusterFeedback, EpochFeedback, Edit, Recommendation,
)
from ..recommendations.store import (
    load_recommendation, load_recommendation_by_slug, save_recommendation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _epoch_key(epoch_val: float) -> str:
    """Stable string key for an epoch — 4 decimal digits matches the CSV."""
    return f"{float(epoch_val):.4f}"


def _recommended_to_str(rr: bool | None) -> str:
    return {True: "robust", False: "non-robust", None: ""}[rr]


def _recommended_from_str(s: str | None) -> bool | None:
    return {"robust": True, "non-robust": False}.get((s or "").lower(), None)


# A cluster is "eligible" for robustness review only if it has enough epochs
# actually used in the fit. Anything thinner than this gives noisy answers.
_ELIGIBILITY_MIN_FIT_EPOCHS = 5


def _table_for_clusters(bundle) -> list[dict]:
    """One row per *eligible* fitted clusterID in the model.

    Eligible := ≥ _ELIGIBILITY_MIN_FIT_EPOCHS rows with use_in_fit=True.
    """
    df = bundle.cluster_df
    rows: list[dict] = []
    for cid, grp in df.groupby("clusterID"):
        cid = int(cid)
        if cid < 0:
            continue
        n_fit = int(grp["use_in_fit"].astype(bool).sum())
        if n_fit < _ELIGIBILITY_MIN_FIT_EPOCHS:
            continue
        model_robust = bool(grp["robust"].iloc[0])
        rows.append({
            "clusterID": cid,
            "current_robust": "Robust" if model_robust else "Non-robust",
            "recommended_robust": "",
            "comment": "",
        })
    rows.sort(key=lambda r: r["clusterID"])
    return rows


def _derived_robust_edits(
    cluster_rows: list[dict] | None,
    no_changes: bool = False,
) -> list[dict]:
    """One synthetic edit per cluster whose recommendation flips the current status.

    Returns an empty list when the reviewer has checked "No changes suggested" —
    even if stale recommendations remain in the table.
    """
    if no_changes:
        return []
    out: list[dict] = []
    for row in cluster_rows or []:
        rec_str = (row.get("recommended_robust") or "").strip()
        if not rec_str:
            continue
        recommended = rec_str == "robust"
        current = (row.get("current_robust") or "") == "Robust"
        if recommended == current:
            continue
        out.append({
            "op": "set_robust",
            "scope": "cluster",
            "clusterID": int(row["clusterID"]),
            "value": recommended,
            "comment": (row.get("comment") or "").strip(),
            "_derived": True,
        })
    return out


def _table_for_epochs(bundle) -> list[dict]:
    """One row per distinct epoch."""
    if bundle.plotdata is not None:
        epochs = list(bundle.plotdata.epoch_info)
        return [
            {
                "epoch": str(e["epoch_name"]),
                "epoch_val": float(e["epoch_val"]),
                "comment": "",
            }
            for e in epochs
        ]
    # Fallback when reviewing a backup model (no npz).
    df = bundle.cluster_df
    return [
        {"epoch": _epoch_key(ev), "epoch_val": float(ev), "comment": ""}
        for ev in sorted(df["epoch"].unique())
    ]


def _populate_tables(bundle, rec: Recommendation) -> tuple[list[dict], list[dict]]:
    """Merge fresh model rows with the reviewer's existing comments."""
    cluster_rows = _table_for_clusters(bundle)
    for row in cluster_rows:
        cf = rec.cluster_feedback.get(str(row["clusterID"]))
        if cf is not None:
            row["recommended_robust"] = _recommended_to_str(cf.recommended_robust)
            row["comment"] = cf.comment
    epoch_rows = _table_for_epochs(bundle)
    for row in epoch_rows:
        ef = rec.epoch_feedback.get(row["epoch"])
        if ef is not None:
            row["comment"] = ef.comment
    return cluster_rows, epoch_rows


def _format_edit(e: dict) -> str:
    parts = [e.get("op", "?"), f"scope={e.get('scope', '?')}"]
    for k in ("epoch", "clusterID", "from_id", "to_id", "value"):
        v = e.get(k)
        if v not in (None, ""):
            parts.append(f"{k}={v}")
    line = "  ".join(parts)
    if e.get("comment"):
        line += f"  # {e['comment']}"
    return line


def _edits_to_components(manual: list[dict], derived: list[dict]) -> list:
    if not manual and not derived:
        return [html.Div("(no edits yet)",
                         style={"color": "#999", "fontStyle": "italic"})]
    out: list = []
    # Manual edits come first, with [remove] buttons indexed into the store.
    for i, e in enumerate(manual or []):
        out.append(
            html.Div(
                [
                    html.Span(_format_edit(e),
                              style={"fontFamily": "ui-monospace, monospace",
                                     "fontSize": "0.85em"}),
                    html.Button(
                        "remove",
                        id={"type": "remove-edit", "index": i},
                        n_clicks=0,
                        style={"marginLeft": "1em", "fontSize": "0.75em",
                               "padding": "0.1em 0.6em"},
                    ),
                ],
                style={"display": "flex", "alignItems": "center",
                       "padding": "0.25em 0", "borderBottom": "1px dashed #eee"},
            )
        )
    # Derived edits are read-only — to undo one, change the Clusters tab.
    for e in derived or []:
        out.append(
            html.Div(
                [
                    html.Span(_format_edit(e),
                              style={"fontFamily": "ui-monospace, monospace",
                                     "fontSize": "0.85em"}),
                    html.Span(" (from Clusters tab)",
                              style={"color": "#888", "fontStyle": "italic",
                                     "fontSize": "0.8em", "marginLeft": "0.75em"}),
                ],
                style={"display": "flex", "alignItems": "center",
                       "padding": "0.25em 0", "borderBottom": "1px dashed #eee",
                       "background": "#fafafa"},
            )
        )
    return out


def build_rec_from_ui_state(
    *, source: str, model: str, reviewer: str,
    source_comment: str | None,
    no_robustness_changes: bool,
    cluster_rows: list[dict] | None,
    epoch_rows: list[dict] | None,
    edits: list[dict] | None,
) -> Recommendation:
    rec = Recommendation(source=source, model=model, reviewer=reviewer)
    rec.source_comment = (source_comment or "").strip()
    rec.no_robustness_changes = bool(no_robustness_changes)
    for row in cluster_rows or []:
        cid = str(row.get("clusterID"))
        rr = _recommended_from_str(row.get("recommended_robust"))
        comment = (row.get("comment") or "").strip()
        if rr is not None or comment:
            rec.cluster_feedback[cid] = ClusterFeedback(
                recommended_robust=rr, comment=comment,
            )
    for row in epoch_rows or []:
        ek = row.get("epoch")
        comment = (row.get("comment") or "").strip()
        if ek and comment:
            rec.epoch_feedback[ek] = EpochFeedback(comment=comment)
    for e in edits or []:
        rec.edits.append(Edit(
            op=e.get("op") or "",
            scope=e.get("scope") or "",
            epoch=e.get("epoch"),
            clusterID=e.get("clusterID"),
            from_id=e.get("from_id"),
            to_id=e.get("to_id"),
            value=e.get("value"),
            comment=e.get("comment", ""),
        ))
    return rec


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(
    app: Dash, *,
    recommendations_dir: Path,
    reviewer: str,
) -> None:

    # ---- 1) Load on source/model change ----------------------------------
    # Where the data comes from:
    #   - "current"          : reviewer's own JSON at <recs>/<src>/current/<own>.json
    #   - "backup_NNN"       : blank panel (recommendations only valid against current)
    #   - "rec:<slug>"       : that reviewer's JSON at <recs>/<src>/current/<slug>.json
    @app.callback(
        Output("source-comment", "value"),
        Output("cluster-feedback-table", "data"),
        Output("epoch-feedback-table", "data"),
        Output("edits-store", "data"),
        Output("save-indicator", "children"),
        Output("no-changes-checkbox", "value"),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
    )
    def _load_for_source(source_folder: str | None, model_key: str | None):
        if not source_folder or not model_key:
            return "", [], [], [], "", []
        # Backup models don't accept recommendations — show an empty panel
        # using current's cluster list so the layout doesn't go blank.
        if model_key.startswith("backup_"):
            bundle = load_bundle(source_folder, "current")
            cluster_rows, epoch_rows = _populate_tables(
                bundle, Recommendation(source=bundle.source.source,
                                       model=model_key, reviewer=reviewer),
            )
            return "", cluster_rows, epoch_rows, [], \
                   "Recommendations are only made against the current model.", []
        # rec:<slug> — show slug's review (read-only is enforced elsewhere).
        if model_key.startswith("rec:"):
            slug = model_key[4:]
            bundle = load_bundle(source_folder, "current")
            rec = load_recommendation_by_slug(
                recommendations_dir, bundle.source.source, "current", slug,
            )
            if rec is None:
                # Stale dropdown entry — file was removed. Show a hint.
                return "", [], [], [], f"Could not find recommendation for {slug}.", []
            cluster_rows, epoch_rows = _populate_tables(bundle, rec)
            edits_data = [
                {
                    "op": e.op, "scope": e.scope, "epoch": e.epoch,
                    "clusterID": e.clusterID, "from_id": e.from_id, "to_id": e.to_id,
                    "value": e.value, "comment": e.comment,
                }
                for e in rec.edits
            ]
            msg = f"Viewing {slug}'s review · saved {rec.updated_at}"
            checkbox = ["yes"] if rec.no_robustness_changes else []
            return rec.source_comment, cluster_rows, epoch_rows, edits_data, msg, checkbox

        # Default: model=="current".
        bundle = load_bundle(source_folder, model_key)
        rec = load_recommendation(
            recommendations_dir, bundle.source.source, model_key, reviewer,
        )
        cluster_rows, epoch_rows = _populate_tables(bundle, rec)
        edits_data = [
            {
                "op": e.op, "scope": e.scope, "epoch": e.epoch,
                "clusterID": e.clusterID, "from_id": e.from_id, "to_id": e.to_id,
                "value": e.value, "comment": e.comment,
            }
            for e in rec.edits
        ]
        msg = f"Loaded {rec.updated_at}" if rec.updated_at else "New review"
        checkbox = ["yes"] if rec.no_robustness_changes else []
        return rec.source_comment, cluster_rows, epoch_rows, edits_data, msg, checkbox

    # ---- 2) Render the edits list (manual store + derived from clusters) -
    @app.callback(
        Output("edits-list", "children"),
        Input("edits-store", "data"),
        Input("cluster-feedback-table", "data"),
        Input("no-changes-checkbox", "value"),
    )
    def _render_edits(manual_edits, cluster_rows, no_changes_val):
        no_changes = bool(no_changes_val)
        derived = _derived_robust_edits(cluster_rows, no_changes=no_changes)
        return _edits_to_components(manual_edits or [], derived)

    # ---- 3) Remove a manual edit (pattern-matching id) -------------------
    @app.callback(
        Output("edits-store", "data", allow_duplicate=True),
        Input({"type": "remove-edit", "index": ALL}, "n_clicks"),
        State("edits-store", "data"),
        prevent_initial_call=True,
    )
    def _remove_edit(_clicks, current):
        trig = ctx.triggered_id
        if not trig or not isinstance(trig, dict):
            return no_update
        # Ignore the initial-fire where n_clicks==0 for every button.
        if not any(_clicks or []):
            return no_update
        idx = trig["index"]
        if not current or idx >= len(current):
            return no_update
        return current[:idx] + current[idx + 1:]

    # ---- 4b) Selection-driven actions ------------------------------------
    # Show / hide the action band, the no-selection note, and the summary
    # line in one go so they're always consistent.
    @app.callback(
        Output("selection-actions", "style"),
        Output("selection-summary", "children"),
        Output("no-selection-note", "style"),
        Input("selection-store", "data"),
    )
    def _selection_visibility(selection):
        base = {
            "padding": "0.5em 0.75em",
            "background": "#f0f7ff",
            "borderBottom": "1px solid #d6e8f7",
            "borderTop": "1px solid #d6e8f7",
            "marginBottom": "0.5em",
        }
        note_visible = {"padding": "0.75em 0.75em 0.5em",
                        "color": "#666", "fontStyle": "italic", "fontSize": "0.9em"}
        note_hidden = {**note_visible, "display": "none"}
        sel = selection or []
        if not sel:
            return {**base, "display": "none"}, "", note_visible
        cids = sorted({int(s["cid"]) for s in sel})
        epochs = sorted({round(float(s["epoch"]), 4) for s in sel})
        cid_label = ", ".join(str(c) for c in cids[:8])
        if len(cids) > 8:
            cid_label += f", …(+{len(cids) - 8})"
        epoch_label = ", ".join(f"{e:.4f}" for e in epochs[:6])
        if len(epochs) > 6:
            epoch_label += f", …(+{len(epochs) - 6})"
        summary = (f"{len(sel)} point(s) selected · clusters {{{cid_label}}} "
                   f"· epochs {{{epoch_label}}}")
        return {**base, "display": "block"}, summary, note_hidden

    # Apply a selection action -> append edit(s) to the manual edits store.
    # The Comment input above the action buttons becomes the `comment` field
    # on every edit produced by this click.
    @app.callback(
        Output("edits-store", "data", allow_duplicate=True),
        Output("selection-action-hint", "children"),
        Output("selection-comment", "value"),
        Input("apply-uif-single-btn", "n_clicks"),
        Input("apply-uif-epoch-btn", "n_clicks"),
        Input("apply-renumber-single-btn", "n_clicks"),
        Input("apply-renumber-all-btn", "n_clicks"),
        State("selection-store", "data"),
        State("renumber-to-id", "value"),
        State("selection-comment", "value"),
        State("edits-store", "data"),
        prevent_initial_call=True,
    )
    def _apply_selection_action(_a, _b, _c, _d, selection, to_id, comment,
                                current_edits):
        trig = ctx.triggered_id
        if not trig:
            return no_update, no_update, no_update
        sel = selection or []
        if not sel:
            return no_update, "Selection is empty.", no_update
        cmt = (comment or "").strip()
        new_edits: list[dict] = []
        msg = ""

        if trig == "apply-uif-single-btn":
            for s in sel:
                new_edits.append({
                    "op": "set_use_in_fit", "scope": "single",
                    "epoch": float(s["epoch"]), "clusterID": int(s["cid"]),
                    "from_id": None, "to_id": None,
                    "value": False, "comment": cmt,
                })
            msg = f"Added {len(new_edits)} use_in_fit=False edit(s)."

        elif trig == "apply-uif-epoch-btn":
            epochs = sorted({round(float(s["epoch"]), 4) for s in sel})
            for e in epochs:
                new_edits.append({
                    "op": "set_use_in_fit", "scope": "epoch",
                    "epoch": float(e), "clusterID": None,
                    "from_id": None, "to_id": None,
                    "value": False, "comment": cmt,
                })
            msg = f"Added {len(new_edits)} whole-epoch use_in_fit=False edit(s)."

        elif trig in ("apply-renumber-single-btn", "apply-renumber-all-btn"):
            if to_id is None:
                return no_update, "Enter a target clusterID first.", no_update
            try:
                target = int(to_id)
            except (TypeError, ValueError):
                return no_update, "Target clusterID must be an integer.", no_update
            if trig == "apply-renumber-single-btn":
                for s in sel:
                    new_edits.append({
                        "op": "change_clusterID", "scope": "single",
                        "epoch": float(s["epoch"]), "clusterID": None,
                        "from_id": int(s["cid"]), "to_id": target,
                        "value": None, "comment": cmt,
                    })
                msg = f"Added {len(new_edits)} per-point renumber edit(s)."
            else:
                cids = sorted({int(s["cid"]) for s in sel})
                for cid in cids:
                    new_edits.append({
                        "op": "change_clusterID", "scope": "all_epochs",
                        "epoch": None, "clusterID": None,
                        "from_id": cid, "to_id": target,
                        "value": None, "comment": cmt,
                    })
                if len(cids) > 1:
                    msg = (f"Added {len(new_edits)} renumber-all-epochs edit(s) "
                           f"— this MERGES clusters {cids} into {target}.")
                else:
                    msg = f"Added 1 renumber-all-epochs edit for cluster {cids[0]} → {target}."

        # Reset the comment input so the next click starts fresh.
        return (current_edits or []) + new_edits, msg, ""

    # Clear-selection button.
    @app.callback(
        Output("selection-store", "data", allow_duplicate=True),
        Output("selection-action-hint", "children", allow_duplicate=True),
        Input("clear-selection-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _clear_selection(_n):
        return [], ""

    # ---- 4c) "No changes suggested" toggle: greys out the cluster table -
    # `editable=False` blocks both regular and dropdown cells; the opacity
    # gives a visual cue that the table is inactive.
    @app.callback(
        Output("cluster-feedback-table", "editable"),
        Output("cluster-table-wrapper", "style"),
        Input("no-changes-checkbox", "value"),
    )
    def _toggle_no_changes(value):
        on = bool(value)
        wrapper_style = {"opacity": 0.45, "pointerEvents": "none"} if on else {}
        # When the table is "off", the dropdown column is also un-clickable.
        return (not on), wrapper_style

    # ---- Recommendations panel read-only style for non-current models ----
    # backup_* and rec:<slug> models are view-only; lock the whole panel.
    @app.callback(
        Output("recommendations-panel", "style"),
        Input("model-picker", "value"),
    )
    def _panel_readonly(model_key):
        base = {"borderTop": "1px solid #ddd", "background": "#fafafa",
                "marginTop": "0.5em"}
        if model_key and model_key != "current":
            return {**base, "opacity": 0.7, "pointerEvents": "none"}
        return base

    # ---- 5) Autosave on any field change ---------------------------------
    # Skipped when the model isn't "current" (rec:<slug> and backup_*).
    @app.callback(
        Output("save-indicator", "children", allow_duplicate=True),
        Input("source-comment", "value"),
        Input("cluster-feedback-table", "data"),
        Input("epoch-feedback-table", "data"),
        Input("edits-store", "data"),
        Input("no-changes-checkbox", "value"),
        State("source-picker", "value"),
        State("model-picker", "value"),
        prevent_initial_call=True,
    )
    def _autosave(source_comment, cluster_rows, epoch_rows, edits,
                  no_changes_val, source_folder, model_key):
        if not source_folder or not model_key:
            return no_update
        if model_key != "current":
            # Read-only view; never save.
            return no_update
        bundle = load_bundle(source_folder, model_key)
        rec = build_rec_from_ui_state(
            source=bundle.source.source, model=model_key, reviewer=reviewer,
            source_comment=source_comment,
            no_robustness_changes=bool(no_changes_val),
            cluster_rows=cluster_rows, epoch_rows=epoch_rows,
            edits=edits,
        )
        if rec.is_empty():
            # Don't create a file just to record "nothing here"; surface
            # status anyway so the reviewer sees the autosave heartbeat.
            return "No changes yet"
        save_recommendation(recommendations_dir, rec, model_sha=bundle.csv_sha)
        when = datetime.now().strftime("%H:%M:%S")
        return f"Saved {when}"
