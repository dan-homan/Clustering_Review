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

import numpy as np

from ..data.loader import _SOURCE_DIR_RE, load_bundle
from ..recommendations.apply import apply_recommendation
from ..recommendations.notebook_format import format_submission_text
from ..recommendations.schema import (
    ClusterFeedback, EpochFeedback, Edit, Recommendation,
)
from ..recommendations.store import (
    is_submitted, load_recommendation, load_recommendation_by_slug,
    save_recommendation, save_submitted, submitted_at,
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


# ---------------------------------------------------------------------------
# Conflict-detection helpers for the renumber action buttons
# ---------------------------------------------------------------------------


_RESERVED_IDS = {0, 999}        # core + park-overlap
_AUTO_DECONFLICT_COMMENT = "auto-deconfliction for next edit"


def _next_free_cluster_id(df, exclude: set[int]) -> int:
    """Smallest int ≥ 1 not used as a clusterID anywhere in `df`, excluding
    the reserved core/park IDs and any caller-supplied exclusions."""
    existing = set(df["clusterID"].astype(int).unique())
    existing |= _RESERVED_IDS
    existing |= set(exclude)
    i = 1
    while i in existing:
        i += 1
    return i


def _effective_df(bundle_df, edits_data):
    """Apply any already-staged edits before checking for conflicts.

    Without this, a sequence of renumbers that *would* collide (e.g.
    second edit goes to a cluster that the first edit just created)
    silently slips past the check and trips the 999-park rule at
    `mojave-apply` time. We rebuild the effective `cluster_df` from
    `bundle.cluster_df` + the pending edits so the second click sees
    the world the first click left behind.
    """
    if not edits_data:
        return bundle_df
    rec = Recommendation(source="", model="", reviewer="")
    rec.edits = [Edit(
        op=e.get("op") or "", scope=e.get("scope") or "",
        epoch=e.get("epoch"), clusterID=e.get("clusterID"),
        from_id=e.get("from_id"), to_id=e.get("to_id"),
        value=e.get("value"), comment=e.get("comment", ""),
    ) for e in edits_data]
    return apply_recommendation(bundle_df, rec)


def _detect_renumber_single_conflicts(
    df, selection: list[dict], target: int,
) -> list[float]:
    """Return sorted list of epochs where the target ID is already taken
    by some row OTHER than the one(s) being renumbered."""
    sel_keys = {(int(s["cid"]), round(float(s["epoch"]), 4))
                for s in selection}
    bad: set[float] = set()
    for s in selection:
        cid = int(s["cid"])
        if cid == target:
            continue                     # no-op renumber
        ep = round(float(s["epoch"]), 4)
        em = np.isclose(df["epoch"].astype(float), ep, atol=1e-4)
        # any row at this epoch already labeled `target` that isn't being
        # renumbered itself
        target_rows = em & (df["clusterID"] == target)
        if not target_rows.any():
            continue
        # exclude rows in the selection (the row being renamed)
        for idx in df.index[target_rows]:
            other_cid = int(df.at[idx, "clusterID"])
            other_ep = round(float(df.at[idx, "epoch"]), 4)
            if (other_cid, other_ep) in sel_keys:
                continue
            bad.add(ep)
            break
    return sorted(bad)


def _detect_renumber_all_epochs_conflict(
    df, selection: list[dict], target: int,
) -> bool:
    """True if the target cluster exists in `df` outside the cids being
    renumbered."""
    sel_cids = {int(s["cid"]) for s in selection}
    if target in sel_cids:
        return False
    return bool((df["clusterID"] == target).any())


def _build_renumber_user_edits(
    selection: list[dict], trig: str, target: int, comment: str,
) -> list[dict]:
    """The renumber edits the user originally asked for (without
    deconfliction). Used both on the no-conflict fast-path and on the
    confirm-dialog path."""
    edits: list[dict] = []
    if trig == "apply-renumber-single-btn":
        for s in selection:
            edits.append({
                "op": "change_clusterID", "scope": "single",
                "epoch": float(s["epoch"]), "clusterID": None,
                "from_id": int(s["cid"]), "to_id": target,
                "value": None, "comment": comment,
            })
    else:                                # apply-renumber-all-btn
        for cid in sorted({int(s["cid"]) for s in selection}):
            edits.append({
                "op": "change_clusterID", "scope": "all_epochs",
                "epoch": None, "clusterID": None,
                "from_id": cid, "to_id": target,
                "value": None, "comment": comment,
            })
    return edits


def _build_deconflict_edits(
    target: int, substitute: int,
    *, single_epochs: list[float] | None = None, all_epochs: bool = False,
) -> list[dict]:
    """Edits that move existing cluster `target` out to `substitute`.

    Always returned BEFORE the user's renumber edits so by the time the
    user's edits apply, the target ID is free.
    """
    out: list[dict] = []
    if all_epochs:
        out.append({
            "op": "change_clusterID", "scope": "all_epochs",
            "epoch": None, "clusterID": None,
            "from_id": int(target), "to_id": int(substitute),
            "value": None, "comment": _AUTO_DECONFLICT_COMMENT,
        })
    else:
        for ep in (single_epochs or []):
            out.append({
                "op": "change_clusterID", "scope": "single",
                "epoch": float(ep), "clusterID": None,
                "from_id": int(target), "to_id": int(substitute),
                "value": None, "comment": _AUTO_DECONFLICT_COMMENT,
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
    results_dir: Path,
    recommendations_dir: Path,
    reviewer: str,
    admin: bool = False,
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
    # on every edit produced by this click. For renumber actions we ALSO
    # check for "target cluster already in use in some epoch" collisions
    # and route the user through a confirmation dialog if so — see
    # _confirm_conflict_action / _cancel_conflict_action below.
    @app.callback(
        Output("edits-store", "data", allow_duplicate=True),
        Output("selection-action-hint", "children"),
        Output("selection-comment", "value"),
        Output("pending-conflict-action", "data", allow_duplicate=True),
        Output("conflict-confirm", "message"),
        Output("conflict-confirm", "displayed"),
        Input("apply-uif-single-btn", "n_clicks"),
        Input("apply-uif-epoch-btn", "n_clicks"),
        Input("apply-renumber-single-btn", "n_clicks"),
        Input("apply-renumber-all-btn", "n_clicks"),
        State("selection-store", "data"),
        State("renumber-to-id", "value"),
        State("selection-comment", "value"),
        State("edits-store", "data"),
        State("source-picker", "value"),
        State("model-picker", "value"),
        State("uif-value", "value"),
        prevent_initial_call=True,
    )
    def _apply_selection_action(
        _a, _b, _c, _d, selection, to_id, comment, current_edits,
        source_folder, model_key, uif_value_str,
    ):
        trig = ctx.triggered_id
        if not trig:
            return (no_update,) * 6
        sel = selection or []
        if not sel:
            return no_update, "Selection is empty.", no_update, no_update, no_update, no_update
        cmt = (comment or "").strip()

        # ---- Non-renumber paths (no conflict possible) ------------------
        if trig in ("apply-uif-single-btn", "apply-uif-epoch-btn"):
            uif_value = (uif_value_str or "false").lower() == "true"
            new_edits: list[dict] = []
            if trig == "apply-uif-single-btn":
                for s in sel:
                    new_edits.append({
                        "op": "set_use_in_fit", "scope": "single",
                        "epoch": float(s["epoch"]), "clusterID": int(s["cid"]),
                        "from_id": None, "to_id": None,
                        "value": uif_value, "comment": cmt,
                    })
                msg = f"Added {len(new_edits)} use_in_fit={uif_value} edit(s)."
            else:
                epochs = sorted({round(float(s["epoch"]), 4) for s in sel})
                for e in epochs:
                    new_edits.append({
                        "op": "set_use_in_fit", "scope": "epoch",
                        "epoch": float(e), "clusterID": None,
                        "from_id": None, "to_id": None,
                        "value": uif_value, "comment": cmt,
                    })
                msg = f"Added {len(new_edits)} whole-epoch use_in_fit={uif_value} edit(s)."
            return (current_edits or []) + new_edits, msg, "", None, no_update, no_update

        # ---- Renumber paths --------------------------------------------
        if to_id is None:
            return no_update, "Enter a target clusterID first.", no_update, no_update, no_update, no_update
        try:
            target = int(to_id)
        except (TypeError, ValueError):
            return no_update, "Target clusterID must be an integer.", no_update, no_update, no_update, no_update

        user_edits = _build_renumber_user_edits(sel, trig, target, cmt)
        if not user_edits:
            return no_update, no_update, no_update, no_update, no_update, no_update

        # Build the effective df (base + already-staged edits) so a second
        # renumber click sees the state the first one left.
        if source_folder and model_key:
            bundle = load_bundle(source_folder,
                                 "current" if (model_key or "").startswith("rec:") else model_key)
            eff_df = _effective_df(bundle.cluster_df, current_edits or [])
        else:
            eff_df = None

        deconflict_edits: list[dict] = []
        message = ""
        if eff_df is not None:
            sel_cids = {int(s["cid"]) for s in sel}
            substitute = _next_free_cluster_id(eff_df,
                                                exclude=sel_cids | {target})
            if trig == "apply-renumber-single-btn":
                conflict_epochs = _detect_renumber_single_conflicts(
                    eff_df, sel, target,
                )
                if conflict_epochs:
                    deconflict_edits = _build_deconflict_edits(
                        target, substitute, single_epochs=conflict_epochs,
                    )
                    eps_str = ", ".join(f"{e:.4f}" for e in conflict_epochs)
                    message = (
                        f"Cluster {target} already exists at {len(conflict_epochs)} "
                        f"epoch(s) ({eps_str}) where your selection points are. "
                        f"To avoid an in-epoch ID collision, those instances will "
                        f"first be renumbered to {substitute} (the smallest unused "
                        f"ID). Then your selection becomes cluster {target}.\n\n"
                        f"Apply both changes?"
                    )
            else:  # apply-renumber-all-btn
                if _detect_renumber_all_epochs_conflict(eff_df, sel, target):
                    deconflict_edits = _build_deconflict_edits(
                        target, substitute, all_epochs=True,
                    )
                    message = (
                        f"Cluster {target} currently exists in the data. To "
                        f"avoid in-epoch ID collisions when your selection "
                        f"becomes cluster {target}, the existing cluster "
                        f"{target} will first be renumbered to {substitute} "
                        f"(the smallest unused ID) across all epochs.\n\n"
                        f"Apply both changes?"
                    )

        if deconflict_edits:
            # Stash the pending action; the dialog drives the actual write.
            pending = {
                "deconflict_edits": deconflict_edits,
                "user_edits": user_edits,
            }
            return no_update, "", no_update, pending, message, True

        # No conflict — apply directly.
        return ((current_edits or []) + user_edits,
                f"Added {len(user_edits)} renumber edit(s).",
                "", None, no_update, no_update)

    # Confirm dialog: OK -> deconflict edit(s) then user's renumber edits.
    @app.callback(
        Output("edits-store", "data", allow_duplicate=True),
        Output("selection-action-hint", "children", allow_duplicate=True),
        Output("selection-comment", "value", allow_duplicate=True),
        Output("pending-conflict-action", "data", allow_duplicate=True),
        Input("conflict-confirm", "submit_n_clicks"),
        State("pending-conflict-action", "data"),
        State("edits-store", "data"),
        prevent_initial_call=True,
    )
    def _confirm_conflict_action(_n, pending, current_edits):
        if not pending:
            return no_update, no_update, no_update, None
        deconflict = pending.get("deconflict_edits") or []
        user_edits = pending.get("user_edits") or []
        all_new = deconflict + user_edits
        msg = (f"Added {len(deconflict)} deconfliction + "
               f"{len(user_edits)} renumber edit(s).")
        return (current_edits or []) + all_new, msg, "", None

    # Confirm dialog: Cancel -> drop the pending action; no edits added.
    @app.callback(
        Output("selection-action-hint", "children", allow_duplicate=True),
        Output("pending-conflict-action", "data", allow_duplicate=True),
        Input("conflict-confirm", "cancel_n_clicks"),
        prevent_initial_call=True,
    )
    def _cancel_conflict_action(_n):
        return "Conflict resolution cancelled. No edits added.", None

    # Clear-selection button.
    @app.callback(
        Output("selection-store", "data", allow_duplicate=True),
        Output("selection-action-hint", "children", allow_duplicate=True),
        Input("clear-selection-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _clear_selection(_n):
        return [], ""

    # ---- 4b2) Re-sync Robustness table to the visualize-effective df ----
    # Whenever the visualize checkbox toggles or the staged edits change,
    # recompute the eligible-clusters list (and each cluster's
    # "Current Robust Status") from the effective DataFrame. This makes
    # the table reflect what the visualize plots show: clusters that
    # dropped below the use_in_fit threshold disappear, newly-eligible
    # clusters appear, and current-state auto-demotions / auto-promotions
    # are visible.
    #
    # The effective df here is built from STRUCTURAL edits only
    # (change_clusterID / set_use_in_fit) — NOT from cluster_feedback —
    # so the "Current Robust Status" stays a meaningful baseline for the
    # user's Recommended Changes column to differ from. (If we folded
    # cluster_feedback into the baseline, the user's picks would
    # self-neutralize on every keystroke.)
    @app.callback(
        Output("cluster-feedback-table", "data", allow_duplicate=True),
        Input("visualize-checkbox", "value"),
        Input("edits-store", "data"),
        State("cluster-feedback-table", "data"),
        State("source-picker", "value"),
        State("model-picker", "value"),
        prevent_initial_call=True,
    )
    def _resync_table_with_effective(
        visualize_val, edits, current_rows, source_folder, model_key,
    ):
        if not source_folder or not model_key:
            return no_update
        # rec:<slug> reads from the current model; backup_* uses the backup.
        effective_model = "current" if (model_key or "").startswith("rec:") else model_key
        bundle = load_bundle(source_folder, effective_model)

        if visualize_val and edits:
            # Build an edits-only Recommendation so the displayed status
            # reflects use_in_fit / change_clusterID effects + auto-eligibility,
            # but stays independent of the user's robust picks.
            transient = Recommendation(
                source=bundle.source.source, model="(transient)", reviewer="(transient)",
            )
            transient.edits = [Edit(
                op=e.get("op") or "", scope=e.get("scope") or "",
                epoch=e.get("epoch"), clusterID=e.get("clusterID"),
                from_id=e.get("from_id"), to_id=e.get("to_id"),
                value=e.get("value"), comment=e.get("comment", ""),
            ) for e in edits]
            eff_df = apply_recommendation(bundle.cluster_df, transient)
        else:
            eff_df = bundle.cluster_df

        # Build new table rows from the effective df using the same
        # eligibility filter the load callback uses.
        from dataclasses import replace as _replace
        new_rows = _table_for_clusters(_replace(bundle, cluster_df=eff_df))

        # Preserve whatever the user has already entered in the Recommended
        # Changes / Comment columns — by clusterID, so the merge survives
        # the eligibility-list reshuffle.
        user_input: dict[int, tuple[str, str]] = {
            int(r.get("clusterID", -1)):
                (r.get("recommended_robust") or "",
                 r.get("comment") or "")
            for r in (current_rows or [])
        }
        for row in new_rows:
            cid = int(row["clusterID"])
            if cid in user_input:
                row["recommended_robust"], row["comment"] = user_input[cid]
        return new_rows

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

    def _source_name_from_folder(folder_str: str | None) -> str | None:
        if not folder_str:
            return None
        m = _SOURCE_DIR_RE.match(Path(folder_str).name)
        return m.group("source") if m else None

    # ---- Submit Recommendation: button state, click handler, modal -------
    # Button label flips to "Resubmit Recommendation" once a submitted/
    # file exists for (source, reviewer). Style includes a hidden-when-
    # non-current state so backup_* / rec:<slug> views can't submit.
    @app.callback(
        Output("submit-recommendation-btn", "children"),
        Output("submit-recommendation-btn", "style"),
        Output("submit-status", "children"),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
        Input("submit-recommendation-btn", "n_clicks"),
    )
    def _submit_button_state(source_folder, model_key, _n):
        base_style = {
            "padding": "0.35em 0.9em", "fontSize": "0.9em",
            "background": "#1f77b4", "color": "white",
            "border": "none", "borderRadius": "4px", "cursor": "pointer",
        }
        if not source_folder or model_key != "current":
            return ("Submit Recommendation",
                    {**base_style, "display": "none"}, "")
        source_name = _source_name_from_folder(source_folder)
        if source_name is None:
            return ("Submit Recommendation",
                    {**base_style, "display": "none"}, "")
        when = submitted_at(recommendations_dir, source_name, reviewer)
        if when:
            return ("Resubmit Recommendation", base_style,
                    f"Last submitted {when}")
        return ("Submit Recommendation", base_style, "Not yet submitted")

    # Click handler: build a fresh Recommendation from current UI state,
    # write it to submitted/, format the notebook text, open the modal.
    @app.callback(
        Output("submission-modal", "style"),
        Output("submission-text", "value"),
        Input("submit-recommendation-btn", "n_clicks"),
        State("source-picker", "value"),
        State("model-picker", "value"),
        State("source-comment", "value"),
        State("cluster-feedback-table", "data"),
        State("epoch-feedback-table", "data"),
        State("edits-store", "data"),
        State("no-changes-checkbox", "value"),
        prevent_initial_call=True,
    )
    def _do_submit(_n, source_folder, model_key, source_comment,
                   cluster_rows, epoch_rows, edits, no_changes_val):
        # Defensive: don't fire on non-current models (button is hidden but
        # could still be triggered by stale state).
        if not source_folder or model_key != "current":
            return no_update, no_update
        source_name = _source_name_from_folder(source_folder)
        if source_name is None:
            return no_update, no_update
        bundle = load_bundle(source_folder, "current")
        rec = build_rec_from_ui_state(
            source=source_name, model="current", reviewer=reviewer,
            source_comment=source_comment,
            no_robustness_changes=bool(no_changes_val),
            cluster_rows=cluster_rows, epoch_rows=epoch_rows,
            edits=edits,
        )
        # Write the submission JSON.
        save_submitted(recommendations_dir, rec, model_sha=bundle.csv_sha)
        # Generate the notebook block from the same Recommendation.
        eff_df = apply_recommendation(bundle.cluster_df, rec)
        text = format_submission_text(rec, bundle.cluster_df, eff_df, reviewer)
        overlay_style = {
            "display": "block", "position": "fixed",
            "top": "0", "left": "0", "right": "0", "bottom": "0",
            "background": "rgba(0,0,0,0.4)", "zIndex": 1000,
            "overflow": "auto",
        }
        return overlay_style, text

    # Close button (header X or footer "Close") — both hide the modal.
    @app.callback(
        Output("submission-modal", "style", allow_duplicate=True),
        Input("close-submission-modal", "n_clicks"),
        Input("close-submission-modal-2", "n_clicks"),
        prevent_initial_call=True,
    )
    def _close_submission_modal(_a, _b):
        return {"display": "none"}

    # Clientside "Copy text" inside the submission modal — writes the
    # textarea contents to the clipboard and flashes the button label.
    app.clientside_callback(
        """
        function(n_clicks, text) {
            if (!n_clicks) return window.dash_clientside.no_update;
            if (!text) return window.dash_clientside.no_update;
            // Modern path
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).catch(() => {});
            } else {
                // Fallback for non-https / older browsers
                const ta = document.createElement("textarea");
                ta.value = text;
                ta.style.position = "fixed";
                ta.style.opacity = "0";
                document.body.appendChild(ta);
                ta.select();
                try { document.execCommand("copy"); } catch (_) {}
                document.body.removeChild(ta);
            }
            // Revert the label after a short delay.
            setTimeout(() => {
                const btn = document.getElementById("copy-submission-text");
                if (btn) btn.textContent = "Copy text";
            }, 1500);
            return "Copied!";
        }
        """,
        Output("copy-submission-text", "children"),
        Input("copy-submission-text", "n_clicks"),
        State("submission-text", "value"),
        prevent_initial_call=True,
    )

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

    # =====================================================================
    # Admin-only: "Generate Apply Command" button.
    # =====================================================================
    if not admin:
        return

    from .recommendations_callbacks_admin import register_admin
    register_admin(
        app,
        results_dir=results_dir,
        recommendations_dir=recommendations_dir,
        reviewer=reviewer,
    )
