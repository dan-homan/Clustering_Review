"""Dash callbacks wiring the UI to the data + plot functions."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date as _date
from pathlib import Path

import plotly.graph_objects as go
from dash import ALL, Dash, Input, Output, State, ctx, html, no_update

from ..auth.runtime import current_reviewer
from ..data.fits_cache import split_source_band
from ..data.loader import (
    _SOURCE_DIR_RE, SourceRef, clear_bundle_cache, list_models, load_bundle,
)
from ..notes import (
    combined_markdown, notes_dir_for,
    read_note, write_note, get_section, set_section, scaffold,
    get_status, set_status, append_ledger, dated_note_entry, pending_notes_seed,
)
from ..plots.overlay import overlay_figure_for_epoch
from ..plots.summary import build_summary_figure
from ..recommendations.apply import apply_recommendation, robust_inconsistencies
from ..recommendations.aggregate import (
    build_aggregation_view, compose_aggregated, stage3_ledger_entry,
)
from ..recommendations.notebook_format import (
    format_submission_text, strip_for_notes,
)
from ..recommendations.schema import Recommendation
from ..recommendations.store import (
    archive_considered_submissions, is_submitted, list_other_reviewer_slugs,
    load_recommendation, load_recommendation_by_slug, reviewer_slug, source_phase,
)
from . import recommendations_callbacks
from .aggregation import build_aggregation_children
from .layout import build_source_options
from .recommendations_callbacks import build_rec_from_ui_state
from ..notes.render import _submitted_recs


def _collect_decisions(final_ids, final_vals, rob_reason_ids, rob_reason_vals,
                       accept_ids, accept_vals, edit_reason_ids, edit_reason_vals):
    """Parse the aggregation panel's pattern-matched inputs into plain dicts.
    Shared by the live-preview compose and the apply orchestration."""
    finals: dict[int, bool] = {}
    for i, v in zip(final_ids or [], final_vals or []):
        b = True if v == "robust" else False if v == "non-robust" else None
        if b is not None:
            finals[int(i["cid"])] = b
    rob_reasons = {int(i["cid"]): (v or "")
                   for i, v in zip(rob_reason_ids or [], rob_reason_vals or [])}
    accepted = [i["key"] for i, v in zip(accept_ids or [], accept_vals or [])
                if v and "y" in v]
    edit_reasons = {i["key"]: (v or "")
                    for i, v in zip(edit_reason_ids or [], edit_reason_vals or [])}
    return finals, rob_reasons, accepted, edit_reasons


def register_callbacks(
    app: Dash,
    *,
    results_dir: Path,
    recommendations_dir: Path,
    cache_dir: Path,
    reviewer: str,
    admin: bool = False,
    fits_data_dir: Path | None = None,
) -> None:
    # Recommendations tab — load + autosave behavior.
    recommendations_callbacks.register(
        app,
        results_dir=results_dir,
        recommendations_dir=recommendations_dir,
        reviewer=reviewer,
        admin=admin,
    )

    # Per-source redshift table (source_run_param.csv), loaded once. Drives the
    # (1+z) host-frame Tb correction and the beta_app Kinematics hovers. Empty
    # map => every source behaves as z unknown (z=0), exactly as before.
    from ..data.source_params import (
        find_source_params, load_redshifts, redshift_for)
    _redshift_map = load_redshifts(find_source_params(results_dir))

    # Local helpers that resolve a (source, model) pair into a possibly
    # rec-applied DataFrame, closing over recommendations_dir and the
    # reviewer-name resolver (token mode: g.reviewer; otherwise the
    # ``reviewer`` fallback captured here).
    def _effective_model_for_load(model_key: str) -> str:
        return "current" if (model_key or "").startswith("rec:") else model_key

    def _resolve_df_for_plot(
        source_folder: str, source_name: str, model_key: str,
        *, visualize_val, cluster_rows, edits, no_changes_val, agg_rec=None,
    ):
        eff_key = _effective_model_for_load(model_key)
        bundle = load_bundle(source_folder, eff_key)
        df = bundle.cluster_df.copy()
        if (model_key or "").startswith("rec:"):
            slug = model_key[4:]
            # Dropdown surfaces submitted recommendations only (see
            # store.list_other_reviewer_slugs).
            rec = load_recommendation_by_slug(
                recommendations_dir, source_name, "submitted", slug,
            )
            if rec is not None:
                df = apply_recommendation(df, rec)
        elif model_key == "current":
            # Admin Stage-3 "Preview aggregated" takes precedence over the
            # reviewer's own in-progress visualize: when on, the composed
            # aggregated recommendation is what the plots show.
            if agg_rec:
                df = apply_recommendation(df, Recommendation.from_dict(agg_rec))
            elif visualize_val:
                own_rec = build_rec_from_ui_state(
                    source=source_name, model="current",
                    reviewer=current_reviewer(reviewer),
                    source_comment=None,
                    no_robustness_changes=bool(no_changes_val),
                    cluster_rows=cluster_rows, epoch_rows=None,
                    edits=edits,
                )
                df = apply_recommendation(df, own_rec)
        return df

    # ---- ↻ Reload button -------------------------------------------------
    # Belt-and-braces fallback alongside the mtime-based invalidation in
    # data/loader.load_bundle: clicking forces a server-side cache wipe
    # AND bumps a counter that is an Input on every callback that touches
    # load_bundle, so all visible figures + dropdowns re-render with fresh
    # data. Useful when the reviewer just ran mojave-apply (or otherwise
    # edited Results/) and wants to be *sure* the displayed plots match
    # what's on disk.
    @app.callback(
        Output("reload-counter", "data"),
        Input("reload-bundles", "n_clicks"),
        State("reload-counter", "data"),
        prevent_initial_call=True,
    )
    def _bump_reload_counter(_n, current):
        clear_bundle_cache()
        return int(current or 0) + 1

    # ---- live source-picker badges --------------------------------------
    # Each source label carries a recommendation-status badge ([N] / [final]
    # / [final - M]; see store.source_badge). The initial layout computes them
    # once at page load; this keeps them fresh during the session when the
    # current reviewer submits (submit-trigger), the page is reloaded
    # (reload-counter), or a Stage-3 apply lands (also bumps reload-counter).
    # The picker's value is the source folder path, which never changes here,
    # so updating only the labels preserves the current selection.
    @app.callback(
        Output("source-picker", "options"),
        Input("reload-counter", "data"),
        Input("submit-trigger", "data"),
    )
    def _refresh_source_badges(_reload_counter, _submit_trigger):
        return build_source_options(results_dir, recommendations_dir)

    # ---- read-only source notes panel ------------------------------------
    # Renders notes/<source>.md (Stages 1-2 + ledger) plus the live
    # open-suggestions (from the submitted recommendation JSONs). Refreshes on
    # source change, Reload, and after the current user submits.
    notes_dir = notes_dir_for(recommendations_dir)

    @app.callback(
        Output("notes-content", "children"),
        Input("source-picker", "value"),
        Input("reload-counter", "data"),
        Input("submit-trigger", "data"),
        Input("notes-saved-counter", "data"),
    )
    def _refresh_notes(source_folder, _reload_counter, _submit_trigger, _notes_saved):
        src = _source_from_folder(source_folder) if source_folder else None
        if src is None:
            return ""
        return combined_markdown(notes_dir, recommendations_dir, src.source)

    # ---- admin/builder: edit the Stage 2 section of notes/<source>.md -----
    if admin:
        @app.callback(
            Output("stage2-editor", "value"),
            Input("source-picker", "value"),
            Input("reload-counter", "data"),
        )
        def _load_stage2_editor(source_folder, _reload_counter):
            src = _source_from_folder(source_folder) if source_folder else None
            if src is None:
                return ""
            md = read_note(notes_dir, src.source)
            return get_section(md, "stage2") if md else ""

        # Two explicit save buttons: "Save Stage 2 notes" marks the source
        # Stage 2 *in progress*; "Save & set Stage 2 done" marks it *done*.
        # (Per decision A the Stage 2 apply also sets a richer "done" status,
        # but the builder can mark done here directly too.)
        @app.callback(
            Output("stage2-save-status", "children"),
            Output("notes-saved-counter", "data"),
            Input("save-stage2-btn", "n_clicks"),
            Input("save-stage2-done-btn", "n_clicks"),
            State("stage2-editor", "value"),
            State("source-picker", "value"),
            State("notes-saved-counter", "data"),
            prevent_initial_call=True,
        )
        def _save_stage2(_n_prog, _n_done, content, source_folder, counter):
            src = _source_from_folder(source_folder) if source_folder else None
            if src is None:
                return "No source selected.", no_update
            status = ("Stage 2 done" if ctx.triggered_id == "save-stage2-done-btn"
                      else "Stage 2 in progress")
            content = content or ""
            try:
                md = read_note(notes_dir, src.source)
                if md is None:
                    # No notes file yet (e.g. Step 1 was unreviewed) — scaffold one.
                    md = scaffold(src.source, src.epoch_min, src.epoch_max,
                                  status=status, stage2=content)
                else:
                    md = set_section(md, "stage2", content)
                    md = set_status(md, status)
                write_note(notes_dir, src.source, md)
            except Exception as e:  # never lose the file on a bad write
                return f"Save failed: {e}", no_update
            from datetime import datetime
            return (f"Saved {datetime.now().strftime('%H:%M:%S')} ({status})",
                    int(counter or 0) + 1)

        # ---- Stage 2: seed the notes editor from the builder's own
        #      submission summary (cleaned for markdown) -------------------
        @app.callback(
            Output("stage2-editor", "value", allow_duplicate=True),
            Output("stage2-save-status", "children", allow_duplicate=True),
            Input("seed-stage2-summary-btn", "n_clicks"),
            State("source-picker", "value"),
            prevent_initial_call=True,
        )
        def _seed_stage2_from_summary(_n, source_folder):
            src = _source_from_folder(source_folder) if source_folder else None
            if src is None:
                return no_update, "No source selected."
            cur = current_reviewer(reviewer)
            # Prefer the submitted snapshot (what the baseline apply targets);
            # fall back to the in-progress current draft.
            rec = None
            if is_submitted(recommendations_dir, src.source, cur):
                rec = load_recommendation_by_slug(
                    recommendations_dir, src.source, "submitted", reviewer_slug(cur))
            if rec is None:
                rec = load_recommendation(
                    recommendations_dir, src.source, "current", cur)
            try:
                base_df = load_bundle(source_folder, "current").cluster_df
                eff_df = apply_recommendation(base_df, rec)
                text = strip_for_notes(
                    format_submission_text(rec, base_df, eff_df, cur))
            except Exception as e:
                return no_update, f"Seed failed: {e}"
            return text, "Seeded from submission summary (edit, then Save)."

        # ---- Stage 3: dated-note box — seed from pending reviewer comments -
        @app.callback(
            Output("stage3-note-input", "value"),
            Input("source-picker", "value"),
            Input("reseed-stage3-note-btn", "n_clicks"),
            Input("submit-trigger", "data"),
        )
        def _seed_stage3_note(source_folder, _reseed_n, _submit_trigger):
            src = _source_from_folder(source_folder) if source_folder else None
            if src is None:
                return ""
            return pending_notes_seed(recommendations_dir, src.source)

        # ---- Stage 3: dated-note box — append to the ledger ----------------
        @app.callback(
            Output("stage3-note-status", "children"),
            Output("notes-saved-counter", "data", allow_duplicate=True),
            Output("stage3-note-input", "value", allow_duplicate=True),
            Input("add-stage3-note-btn", "n_clicks"),
            State("stage3-note-input", "value"),
            State("source-picker", "value"),
            State("notes-saved-counter", "data"),
            prevent_initial_call=True,
        )
        def _add_stage3_note(_n, text, source_folder, counter):
            src = _source_from_folder(source_folder) if source_folder else None
            if src is None:
                return "No source selected.", no_update, no_update
            if not (text or "").strip():
                return "Nothing to add.", no_update, no_update
            today = _date.today().isoformat()
            entry = dated_note_entry(today, current_reviewer(reviewer), text)
            try:
                md = read_note(notes_dir, src.source)
                if md is None:
                    md = scaffold(src.source, src.epoch_min, src.epoch_max)
                md = append_ledger(md, entry)
                write_note(notes_dir, src.source, md)
            except Exception as e:
                return f"Add failed: {e}", no_update, no_update
            from datetime import datetime
            return (f"Added {datetime.now().strftime('%H:%M:%S')}.",
                    int(counter or 0) + 1, "")

        # ---- Stage 3 panel visibility: only once Stage 2 is done -----------
        # Pre-baseline (stage1 / stage2 phases) the Stage-3 box is hidden so it
        # can't be confused with the Stage-2 baseline apply; from Stage-2-done
        # onward (open / final) it is shown. Pairs with the baseline-apply
        # button's stage-gating in recommendations_callbacks_admin.
        @app.callback(
            Output("agg-details", "style"),
            Input("source-picker", "value"),
            Input("reload-counter", "data"),
        )
        def _toggle_agg_panel(source_folder, _reload_counter):
            base = {"borderBottom": "1px solid #ddd", "background": "#f7f9fb"}
            src = _source_from_folder(source_folder) if source_folder else None
            phase = source_phase(recommendations_dir, src.source) if src else "open"
            if phase in ("stage1", "stage2"):
                return {**base, "display": "none"}
            return base

        # ---- Stage 3 aggregation: build the admin panel for the source ----
        # Rebuilds whenever the source changes, the page reloads, or a new
        # submission lands (submit-trigger). Stashes the per-key edit dicts in
        # agg-view-store so _compose_agg can reconstruct accepted edits.
        @app.callback(
            Output("agg-panel-body", "children"),
            Output("agg-view-store", "data"),
            Input("source-picker", "value"),
            Input("reload-counter", "data"),
            Input("submit-trigger", "data"),
        )
        def _build_agg_panel(source_folder, _reload_counter, _submit_trigger):
            src = _source_from_folder(source_folder) if source_folder else None
            if src is None:
                return build_aggregation_children(
                    build_aggregation_view("", [], None)), None
            recs = _submitted_recs(recommendations_dir, src.source)
            try:
                current_df = load_bundle(source_folder, "current").cluster_df
            except Exception:
                current_df = None
            view = build_aggregation_view(src.source, recs, current_df)
            children = build_aggregation_children(view)
            # When the source is already finalized and there are no open
            # submissions, the empty panel can read as "broken". Make it clear
            # the panel is just waiting for a follow-up round.
            if not recs and source_phase(recommendations_dir, src.source) == "final":
                md = read_note(notes_dir, src.source)
                status = get_status(md) if md else ""
                children = [html.Div(
                    f"✓ {status or 'Finalized'}. New reviewer submissions will "
                    f"appear here for a follow-up Stage 3 run.",
                    style={"fontSize": "0.85em", "color": "#777",
                           "padding": "0.5em 0"},
                ), children]
            return children, view.store_payload()

        # ---- Stage 3 aggregation: compose decisions -> preview rec --------
        # Collects every per-decision input, composes the aggregated
        # Recommendation, and publishes it to agg-preview-rec (which the
        # summary + overlay callbacks consume) only while "Preview aggregated"
        # is ticked. The composed rec + reasons are what build-step #4's Apply
        # will consume.
        @app.callback(
            Output("agg-preview-rec", "data"),
            Output("agg-summary", "children"),
            Input({"type": "agg-rob-final", "cid": ALL}, "value"),
            Input({"type": "agg-rob-reason", "cid": ALL}, "value"),
            Input({"type": "agg-edit-accept", "key": ALL}, "value"),
            Input({"type": "agg-edit-reason", "key": ALL}, "value"),
            Input("agg-preview-toggle", "value"),
            State({"type": "agg-rob-final", "cid": ALL}, "id"),
            State({"type": "agg-rob-reason", "cid": ALL}, "id"),
            State({"type": "agg-edit-accept", "key": ALL}, "id"),
            State({"type": "agg-edit-reason", "key": ALL}, "id"),
            State("agg-view-store", "data"),
            State("source-picker", "value"),
        )
        def _compose_agg(final_vals, rob_reason_vals, accept_vals, edit_reason_vals,
                         preview_val, final_ids, rob_reason_ids, accept_ids,
                         edit_reason_ids, store_payload, source_folder):
            finals, rob_reasons, accepted, edit_reasons = _collect_decisions(
                final_ids, final_vals, rob_reason_ids, rob_reason_vals,
                accept_ids, accept_vals, edit_reason_ids, edit_reason_vals)
            src = _source_from_folder(source_folder) if source_folder else None
            source_name = (store_payload or {}).get("source") or (src.source if src else "")
            rec = compose_aggregated(
                source_name, current_reviewer(reviewer),
                robustness_finals=finals, robustness_reasons=rob_reasons,
                accepted_edit_keys=accepted, edit_reasons=edit_reasons,
                store_payload=store_payload or {},
            )
            on = bool(preview_val) and "on" in preview_val
            summary = (f"{len(finals)} robustness decision"
                       f"{'' if len(finals) == 1 else 's'} · "
                       f"{len(accepted)} edit{'' if len(accepted) == 1 else 's'} accepted"
                       f" · preview {'ON' if on else 'off'}")
            preview = rec.to_dict() if (on and not rec.is_empty()) else None
            return preview, summary

        # ---- Stage 3 apply: confirm modal open / cancel -------------------
        _AGG_MODAL_OVERLAY = {
            "display": "block", "position": "fixed",
            "top": 0, "left": 0, "right": 0, "bottom": 0,
            "background": "rgba(0,0,0,0.4)", "zIndex": 1000, "overflow": "auto",
        }

        @app.callback(
            Output("agg-apply-modal", "style"),
            Output("agg-apply-modal-text", "children"),
            Input("agg-apply-btn", "n_clicks"),
            State("agg-summary", "children"),
            State("source-picker", "value"),
            prevent_initial_call=True,
        )
        def _open_apply_modal(_n, summary, source_folder):
            src = _source_from_folder(source_folder) if source_folder else None
            name = src.source if src else "(no source selected)"
            return _AGG_MODAL_OVERLAY, f"{name} — {summary or 'no decisions yet'}"

        @app.callback(
            Output("agg-apply-modal", "style", allow_duplicate=True),
            Input("agg-apply-cancel", "n_clicks"),
            Input("agg-apply-close", "n_clicks"),
            prevent_initial_call=True,
        )
        def _close_apply_modal(*_):
            return {"display": "none"}

        # ---- Stage 3 apply: the one-click orchestration -------------------
        # Composes the aggregated rec, runs mojave-apply as a subprocess
        # (synchronous), and on success archives the considered submissions,
        # writes the notes ledger + bumps Status, and refreshes everything via
        # reload-counter. Destructive — only reachable behind the confirm modal.
        @app.callback(
            Output("agg-apply-modal", "style", allow_duplicate=True),
            Output("agg-apply-status", "children"),
            Output("reload-counter", "data", allow_duplicate=True),
            Output("agg-preview-rec", "data", allow_duplicate=True),
            Output("agg-preview-toggle", "value", allow_duplicate=True),
            Input("agg-apply-confirm", "n_clicks"),
            State({"type": "agg-rob-final", "cid": ALL}, "value"),
            State({"type": "agg-rob-final", "cid": ALL}, "id"),
            State({"type": "agg-rob-reason", "cid": ALL}, "value"),
            State({"type": "agg-rob-reason", "cid": ALL}, "id"),
            State({"type": "agg-edit-accept", "key": ALL}, "value"),
            State({"type": "agg-edit-accept", "key": ALL}, "id"),
            State({"type": "agg-edit-reason", "key": ALL}, "value"),
            State({"type": "agg-edit-reason", "key": ALL}, "id"),
            State("source-picker", "value"),
            State("reload-counter", "data"),
            prevent_initial_call=True,
        )
        def _apply_aggregated(_n, final_vals, final_ids, rob_reason_vals,
                              rob_reason_ids, accept_vals, accept_ids,
                              edit_reason_vals, edit_reason_ids,
                              source_folder, reload_counter):
            hide = {"display": "none"}
            err = (hide, no_update, no_update, no_update, no_update)
            src = _source_from_folder(source_folder) if source_folder else None
            if src is None:
                return (hide, "No source selected.", no_update, no_update, no_update)
            finals, rob_reasons, accepted, edit_reasons = _collect_decisions(
                final_ids, final_vals, rob_reason_ids, rob_reason_vals,
                accept_ids, accept_vals, edit_reason_ids, edit_reason_vals)
            recs = _submitted_recs(recommendations_dir, src.source)
            if not recs:
                return (hide, "No submissions to apply.", *err[2:])
            try:
                current_df = load_bundle(source_folder, "current").cluster_df
            except Exception as e:
                return (hide, f"Could not load current model: {e}", *err[2:])
            view = build_aggregation_view(src.source, recs, current_df)
            rec = compose_aggregated(
                src.source, "aggregated",
                robustness_finals=finals, robustness_reasons=rob_reasons,
                accepted_edit_keys=accepted, edit_reasons=edit_reasons,
                store_payload=view.store_payload())
            if rec.is_empty():
                return (hide, "No decisions to apply (set a Final or accept an "
                        "edit first).", *err[2:])

            today = _date.today().isoformat()
            stage3_dir = recommendations_dir / src.source / "stage3"
            stage3_dir.mkdir(parents=True, exist_ok=True)
            # mojave-apply archives this to applied/<date>__<stem>.json, so the
            # stem is just "aggregated" (the date prefix is added there).
            staging = stage3_dir / "aggregated.json"
            staging.write_text(json.dumps(rec.to_dict(), indent=2))

            prod_dir = os.environ.get("MOJAVE_CODE") or str(Path(results_dir).parent)
            cmd = [sys.executable, "-m", "mojave_review.cli.apply",
                   "--results-dir", str(results_dir),
                   "--source", Path(source_folder).name,
                   "--recommendation", str(staging),
                   "--recommendations-dir", str(recommendations_dir),
                   "--production-code-dir", prod_dir, "--no-confirm"]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      env=os.environ.copy(), timeout=1800)
            except Exception as e:
                return (hide, f"Apply failed to launch: {e}", *err[2:])
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout or "").strip()[-400:]
                return (hide, f"mojave-apply failed (rc={proc.returncode}): {tail}",
                        *err[2:])

            out = proc.stdout or ""
            m = re.search(r"backup_(\d+)", out)
            backup_ref = (f"backup_{m.group(1)}" if m else
                          "no changes (concluded)" if "no changes" in out.lower()
                          else "applied")

            slugs = [reviewer_slug(r.reviewer) for r in recs]
            try:
                archive_considered_submissions(
                    recommendations_dir, src.source, slugs, date=today)
            except Exception:
                pass
            ledger_note = ""
            try:
                md = read_note(notes_dir, src.source)
                if md is None:
                    md = scaffold(src.source, src.epoch_min, src.epoch_max)
                # Number repeated Stage-3 applies (run N) from the existing
                # ledger so the appended history is easy to scan.
                run_index = get_section(md, "ledger").count(
                    "Stage 3 reconciliation") + 1
                entry = stage3_ledger_entry(
                    view, finals=finals, rob_reasons=rob_reasons,
                    accepted_keys=accepted, edit_reasons=edit_reasons,
                    applied_by=current_reviewer(reviewer), date=today,
                    backup_ref=backup_ref, run_index=run_index)
                md = append_ledger(md, entry)
                md = set_status(md, f"Stage 3 done · applied {today}")
                write_note(notes_dir, src.source, md)
            except Exception as e:
                ledger_note = f" (ledger write failed: {e})"
            clear_bundle_cache()
            msg = (f"Applied ✓ {backup_ref} — archived {len(slugs)} considered "
                   f"submission(s), ledger written{ledger_note}.")
            return (hide, msg, int(reload_counter or 0) + 1, None, [])

    # ---- robust-consistency warning banner -------------------------------
    # Read-only: flag a source whose saved CSV has a per-epoch robust
    # inconsistency (latent data bug). The plots already render correctly via
    # the per-cluster robust rule; this just surfaces the source for repair via
    # the audit CLI. The bundle is cached (loaded by the plot callbacks too), so
    # this is a cheap groupby — no meaningful load-time cost.
    @app.callback(
        Output("robust-warning", "children"),
        Output("robust-warning", "style"),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
        Input("reload-counter", "data"),
    )
    def _robust_warning(source_folder, model_key, _reload_counter):
        hidden = {"display": "none"}
        if not source_folder or not model_key:
            return "", hidden
        try:
            bundle = load_bundle(source_folder, _effective_model_for_load(model_key))
        except Exception:
            return "", hidden
        bad = robust_inconsistencies(bundle.cluster_df)
        if not bad:
            return "", hidden
        cids = ", ".join(str(c) for c in sorted(bad))
        msg = (f"⚠ Saved robust flags are inconsistent across epochs for "
               f"cluster(s) {cids}. The plots show one value per cluster, but "
               f"the on-disk model has a data inconsistency — an admin can "
               f"repair it with: mojave-review-audit-robust --apply")
        style = {"display": "block", "background": "#fff3cd", "color": "#664d03",
                 "border": "1px solid #ffe69c", "borderRadius": "4px",
                 "padding": "0.4em 0.7em", "margin": "0.25em 0", "fontSize": "0.82em"}
        return msg, style

    # ---- model picker (depends on source) --------------------------------
    # Options include:
    #   - "current"                        — the live model
    #   - "backup_NNN"                     — saved backup runs
    #   - "rec:<slug>"                     — other reviewers' submitted recommendations
    #                                         applied on top of "current"
    # ``reload-counter`` is an Input so a Reload click also re-scans the
    # backups/ directory (in case a new backup landed on disk) and the
    # recommendations dir (in case a new reviewer's JSON appeared).
    @app.callback(
        Output("model-picker", "options"),
        Output("model-picker", "value"),
        Input("source-picker", "value"),
        Input("reload-counter", "data"),
    )
    def _populate_models(source_folder: str | None, _reload_counter):
        if not source_folder:
            return [], None
        src = _source_from_folder(source_folder)
        if src is None:
            return [], None
        models = list_models(src)
        opts = [{"label": mf.label, "value": mf.key} for mf in models]
        # Other reviewers' submitted rec files at
        # <recs>/<source>/submitted/<slug>.json, excluding the current user's
        # own slug (their own draft is visible via Visualize). The current
        # user's name is
        # resolved per-request — in token-auth mode that means each user
        # sees the dropdown filtered against *their* slug, not whichever
        # one happened to be captured at app start.
        own_slug = reviewer_slug(current_reviewer(reviewer))
        for slug in list_other_reviewer_slugs(recommendations_dir, src.source, own_slug):
            opts.append({"label": f"Rec: {slug}", "value": f"rec:{slug}"})
        return opts, (opts[0]["value"] if opts else None)

    # ---- summary figure --------------------------------------------------
    @app.callback(
        Output("summary-graph", "figure"),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
        Input("view-picker", "value"),
        Input("vector-scale", "value"),
        Input("selection-store", "data"),
        Input("visualize-checkbox", "value"),
        Input("cluster-feedback-table", "data"),
        Input("edits-store", "data"),
        Input("no-changes-checkbox", "value"),
        Input("hide-non-robust-checkbox", "value"),
        Input("only-3sigma-checkbox", "value"),
        Input("reload-counter", "data"),
        Input("agg-preview-rec", "data"),
    )
    def _refresh_summary(source_folder, model_key, view, vector_scale,
                         selection, visualize_val, cluster_rows, edits,
                         no_changes_val, hide_non_robust_val, only_3sigma_val,
                         _reload_counter, agg_rec):
        if not source_folder or not model_key:
            return go.Figure()
        src = _source_from_folder(source_folder)
        if src is None:
            return go.Figure()
        df = _resolve_df_for_plot(
            source_folder, src.source, model_key,
            visualize_val=visualize_val, cluster_rows=cluster_rows,
            edits=edits, no_changes_val=no_changes_val, agg_rec=agg_rec,
        )
        # 1-sigma centroid-position uncertainties from the clean components,
        # for error bars on the Position / XY Position views (other views
        # don't use them, so skip the work). Computed on the plotted
        # (rec-applied) df so CC→cluster membership matches what's shown.
        # Decoration only — never break the plot.
        if view in ("Position", "XY Position"):
            try:
                eff_bundle = load_bundle(
                    source_folder, _effective_model_for_load(model_key))
                if eff_bundle.plotdata is not None:
                    from ..plots.uncertainty import attach_position_uncertainties
                    df = attach_position_uncertainties(
                        df, eff_bundle.plotdata.cc_data,
                        eff_bundle.plotdata.cc_labels)
            except Exception:
                pass
        # Apply the user's current selection to the dataframe so the existing
        # gold open-diamond overlay highlights the chosen points across views.
        df["select"] = False
        if selection:
            sel_keys = {(int(s["cid"]), round(float(s["epoch"]), 4))
                        for s in selection
                        if s.get("cid") is not None and s.get("epoch") is not None}
            if sel_keys:
                cids = df["clusterID"].astype(int).to_numpy()
                eps = df["epoch"].round(4).to_numpy()
                mask = [(int(c), float(e)) in sel_keys for c, e in zip(cids, eps)]
                df.loc[mask, "select"] = True
        # z drives the (1+z) host-frame Tb correction and beta_app on the
        # Kinematics hovers; 0.0 (unknown) leaves Tb as the observed value.
        z = redshift_for(_redshift_map, src.source) or 0.0
        fig = build_summary_figure(
            df, view=view, z=z,
            vector_scale_factor=vector_scale or 1.0,
            hide_non_robust=bool(hide_non_robust_val),
            only_3sigma=bool(only_3sigma_val),
        )
        # Persist the user's zoom across selection clicks, visualize toggle,
        # vector scale change, edits etc. — anything that doesn't change the
        # axes themselves. The key changes on (source, model, view) because
        # those genuinely swap the underlying data domain or the axis
        # identities (Position vs Kinematics, etc.), and reusing the old
        # zoom there would just be confusing.
        fig.update_layout(uirevision=f"summary:{source_folder}:{model_key}:{view}")
        return fig

    # ---- selection store: click toggles -----------------------------------
    # Click-only on purpose. Box-select / lasso-select have been stripped
    # from the summary plot's modebar (see ui/layout.py) because their
    # all-or-nothing semantics surprised reviewers — there's no way to
    # partially undo a sweep, and an accidental select-mode drag could
    # strand them with a selection they couldn't reverse without first
    # finding the modebar's reset.
    #
    # The summary graph carries customdata=[cid, epoch] on every cluster
    # point, so identifying clicked points is just reading that field.
    # The callback fires only on Position / Flux / Polarization views;
    # Kinematics has its own interpretation of clicks.
    #
    # We must reset clickData to None after each handled click — Dash only
    # fires the callback when the Input *value* changes, and re-clicking
    # the same point would otherwise yield an identical clickData dict and
    # the toggle would silently fail to deselect on the second click.
    @app.callback(
        Output("selection-store", "data", allow_duplicate=True),
        Output("summary-graph", "clickData", allow_duplicate=True),
        Input("summary-graph", "clickData"),
        State("selection-store", "data"),
        State("view-picker", "value"),
        prevent_initial_call=True,
    )
    def _toggle_on_click(click_data, current, view):
        if view == "Kinematics" or not click_data:
            return no_update, no_update
        pts = click_data.get("points") or []
        out = list(current or [])
        existing = {(int(s["cid"]), round(float(s["epoch"]), 4))
                    for s in out
                    if s.get("cid") is not None and s.get("epoch") is not None}
        changed = False
        for p in pts:
            cd = p.get("customdata")
            if not cd or len(cd) < 2:
                continue
            try:
                cid = int(cd[0])
                epoch = round(float(cd[1]), 4)
            except (TypeError, ValueError):
                continue
            key = (cid, epoch)
            if key in existing:
                out = [s for s in out
                       if not (int(s["cid"]) == cid
                               and round(float(s["epoch"]), 4) == epoch)]
                existing.discard(key)
            else:
                out.append({"cid": cid, "epoch": epoch})
                existing.add(key)
            changed = True
        # Reset clickData so a repeat click on the same point fires again.
        return (out if changed else no_update), None

    # (Previously had a sibling _replace_on_box callback on
    # summary-graph.selectedData for the box-select / lasso flows. Since
    # those modebar buttons are now stripped in ui/layout.py, selectedData
    # can no longer fire and the callback was dead code.)

    # ---- visualize-checkbox state managed by the current model -----------
    # model=current  -> user-controllable, default off
    # model=backup_* -> disabled, off  (no recs apply to a backup)
    # model=rec:<>   -> disabled, on   (visualization is the whole point)
    @app.callback(
        Output("visualize-checkbox", "options"),
        Output("visualize-checkbox", "value", allow_duplicate=True),
        Input("model-picker", "value"),
        prevent_initial_call=True,
    )
    def _manage_visualize_checkbox(model_key):
        if not model_key:
            return [{"label": " Visualize recommendations",
                     "value": "yes", "disabled": True}], []
        if model_key == "current":
            return [{"label": " Visualize recommendations",
                     "value": "yes", "disabled": False}], no_update
        if model_key.startswith("rec:"):
            return [{"label": " Visualize recommendations",
                     "value": "yes", "disabled": True}], ["yes"]
        # backup_NNN or unknown
        return [{"label": " Visualize recommendations",
                 "value": "yes", "disabled": True}], []

    # ---- clear selection when source or model changes --------------------
    @app.callback(
        Output("selection-store", "data", allow_duplicate=True),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
        prevent_initial_call=True,
    )
    def _clear_selection_on_swap(*_):
        return []

    # ---- vector-scale visibility -----------------------------------------
    @app.callback(
        Output("vector-scale-row", "style"),
        Input("view-picker", "value"),
    )
    def _toggle_scale_row(view):
        base = {"alignItems": "center", "padding": "0.25em 1em", "fontSize": "0.9em"}
        return {**base, "display": "flex" if view == "Kinematics" else "none"}

    # ---- epoch slider population (depends on source + model) -------------
    @app.callback(
        Output("epoch-slider", "min"),
        Output("epoch-slider", "max"),
        Output("epoch-slider", "marks"),
        Output("epoch-slider", "value"),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
        Input("reload-counter", "data"),
        State("epoch-slider", "value"),
    )
    def _populate_epoch_slider(source_folder, model_key, _reload_counter, current_val):
        if not source_folder or not model_key:
            return 0, 0, {}, 0
        # rec:<slug> has no bundle of its own — its epochs are current's.
        bundle = load_bundle(source_folder, _effective_model_for_load(model_key))
        if bundle.plotdata is None:
            return 0, 0, {}, 0
        n = len(bundle.plotdata.epoch_info)
        if n == 0:
            return 0, 0, {}, 0
        # one mark every ~6 epochs, labeled with the integer year
        step = max(1, n // 6)
        marks = {
            int(i): f"{bundle.plotdata.epoch_info[i]['epoch_val']:.0f}"
            for i in range(0, n, step)
        }
        marks[n - 1] = f"{bundle.plotdata.epoch_info[n - 1]['epoch_val']:.0f}"
        # preserve current value if still valid; otherwise reset to 0
        new_val = current_val if current_val is not None and 0 <= current_val < n else 0
        return 0, n - 1, marks, new_val

    # ---- ◀ / ▶ buttons step the slider -----------------------------------
    @app.callback(
        Output("epoch-slider", "value", allow_duplicate=True),
        Input("epoch-prev", "n_clicks"),
        Input("epoch-next", "n_clicks"),
        State("epoch-slider", "value"),
        State("epoch-slider", "min"),
        State("epoch-slider", "max"),
        prevent_initial_call=True,
    )
    def _step_epoch(_prev_n, _next_n, value, lo, hi):
        if value is None or lo is None or hi is None:
            return no_update
        trig = ctx.triggered_id
        if trig == "epoch-prev":
            return max(int(lo), int(value) - 1)
        if trig == "epoch-next":
            return min(int(hi), int(value) + 1)
        return no_update

    # ---- epoch label readout ---------------------------------------------
    @app.callback(
        Output("epoch-label", "children"),
        Output("active-epoch", "data"),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
        Input("epoch-slider", "value"),
        Input("reload-counter", "data"),
    )
    def _epoch_label(source_folder, model_key, epoch_int, _reload_counter):
        if not source_folder or not model_key or epoch_int is None:
            return "", None
        # rec:<slug> has no bundle of its own — its epochs are current's.
        bundle = load_bundle(source_folder, _effective_model_for_load(model_key))
        if bundle.plotdata is None or epoch_int >= len(bundle.plotdata.epoch_info):
            return "", None
        info = bundle.plotdata.epoch_info[int(epoch_int)]
        epoch_val = float(info['epoch_val'])
        # active-epoch (decimal year) drives the vertical epoch marker the
        # clientside callback draws on the epoch-axis summary plots.
        return f"{info['epoch_name']}  ·  {epoch_val:.4f}", epoch_val

    # ---- overlay figure --------------------------------------------------
    @app.callback(
        Output("overlay-graph", "figure"),
        Output("beam-params", "data"),
        Input("source-picker", "value"),
        Input("model-picker", "value"),
        Input("epoch-slider", "value"),
        Input("visualize-checkbox", "value"),
        Input("cluster-feedback-table", "data"),
        Input("edits-store", "data"),
        Input("no-changes-checkbox", "value"),
        Input("use-fits-checkbox", "value"),
        Input("stack-image-checkbox", "value"),
        Input("overlay-reset-counter", "data"),
        Input("reload-counter", "data"),
        Input("agg-preview-rec", "data"),
    )
    def _refresh_overlay(source_folder, model_key, epoch_int,
                         visualize_val, cluster_rows, edits, no_changes_val,
                         use_fits_val, stacked_val, reset_counter,
                         _reload_counter, agg_rec):
        if not source_folder or not model_key or epoch_int is None:
            return go.Figure(), None
        src = _source_from_folder(source_folder)
        if src is None:
            return go.Figure(), None
        # The overlay needs the npz (cc_data / cc_labels / epoch_info), which
        # only the "current" bundle has. For rec:<slug>, fall back to current.
        eff_key = _effective_model_for_load(model_key)
        bundle = load_bundle(source_folder, eff_key)
        source_no_band, band = split_source_band(src.source)
        # If recommendations are being visualised, swap in the modified
        # cluster_df before rendering. The npz fields stay untouched.
        applied_df = _resolve_df_for_plot(
            source_folder, src.source, model_key,
            visualize_val=visualize_val, cluster_rows=cluster_rows,
            edits=edits, no_changes_val=no_changes_val, agg_rec=agg_rec,
        )
        if not applied_df.equals(bundle.cluster_df):
            # Construct a shallow shim bundle with the patched df. The
            # SourceBundle dataclass is mutable; cheap to copy fields.
            from dataclasses import replace as _replace
            bundle = _replace(bundle, cluster_df=applied_df)
        # Scope uirevision to (source, model) so the user's zoom persists
        # while they're scrubbing epochs, but a context change always
        # starts from a clean axis state. This is also our defence against
        # the rare "missing overlay on some epochs" symptom: a constant
        # uirevision lets Plotly carry stale axis or trace-visibility
        # state across hours of figure updates; a per-context key
        # guarantees that state never crosses a source/model boundary.
        return overlay_figure_for_epoch(
            bundle, int(epoch_int), cache_dir,
            source_no_band=source_no_band, band=band,
            fits_data_dir=fits_data_dir,
            image_source="fits" if use_fits_val else "synthesize",
            stacked=bool(stacked_val),
            uirevision=f"overlay:{source_folder}:{model_key}:{reset_counter or 0}",
        )

    # ---- overlay Reset view button ---------------------------------------
    # Increments a counter that participates in the overlay's uirevision
    # key, so the figure gets a brand-new key and Plotly does a full
    # redraw + axis reset. The escape hatch when Plotly's SVG layer ends
    # up stale after a long marathon of figure updates.
    @app.callback(
        Output("overlay-reset-counter", "data"),
        Input("overlay-reset", "n_clicks"),
        State("overlay-reset-counter", "data"),
        prevent_initial_call=True,
    )
    def _bump_reset_counter(_n, current):
        return int(current or 0) + 1

    # ---- clientside: reposition beam ellipse on zoom/pan -----------------
    # Runs in the browser. Uses Plotly.restyle directly on the graph div so
    # Dash never replaces the figure object — that's important because a
    # figure-output replacement here was clobbering uirevision-driven zoom
    # persistence on epoch change. Returning no_update keeps the dummy
    # output untouched.
    app.clientside_callback(
        """
        function(relayoutData, beamParams) {
            if (!beamParams || !relayoutData) {
                return window.dash_clientside.no_update;
            }
            var wrapper = document.getElementById('overlay-graph');
            if (!wrapper) return window.dash_clientside.no_update;
            var gd = wrapper.querySelector('.js-plotly-plot');
            if (!gd || !window.Plotly) return window.dash_clientside.no_update;

            var bmaj = beamParams.bmaj, bmin = beamParams.bmin, bpa = beamParams.bpa;
            var idx = beamParams.beam_idx;

            // Pick the current viewport. relayoutData may carry an explicit
            // range from a zoom/pan, or {xaxis.autorange: true} on reset.
            var xRange, yRange;
            if (relayoutData['xaxis.autorange'] !== undefined
                || relayoutData['autosize'] !== undefined) {
                xRange = beamParams.x_extent;
                yRange = beamParams.y_extent;
            } else if (relayoutData['xaxis.range[0]'] !== undefined
                       && relayoutData['yaxis.range[0]'] !== undefined) {
                xRange = [relayoutData['xaxis.range[0]'],
                          relayoutData['xaxis.range[1]']];
                yRange = [relayoutData['yaxis.range[0]'],
                          relayoutData['yaxis.range[1]']];
            } else {
                return window.dash_clientside.no_update;
            }

            var xLo = Math.min(xRange[0], xRange[1]);
            var xHi = Math.max(xRange[0], xRange[1]);
            var yLo = Math.min(yRange[0], yRange[1]);
            var yHi = Math.max(yRange[0], yRange[1]);
            var xSpan = xHi - xLo;
            var ySpan = yHi - yLo;

            if (xSpan < 5 * bmaj || ySpan < 5 * bmaj) {
                window.Plotly.restyle(gd, {visible: false}, [idx]);
                return window.dash_clientside.no_update;
            }

            // Place at high-x (visually LEFT, since +x is reversed) and
            // low-y corner of the current viewport, with a small inset.
            var bx = xHi - 0.08 * xSpan;
            var by = yLo + 0.08 * ySpan;
            var n = 60;
            var ex = new Array(n), ey = new Array(n);
            var cosPa = Math.cos(bpa * Math.PI / 180);
            var sinPa = Math.sin(bpa * Math.PI / 180);
            // Astronomical PA: 0 = major along +y (north), positive rotates
            // CCW from north in the displayed plot. The overlay axis has
            // +x reversed, so display-CCW is data-CW — sin signs flipped
            // accordingly. Keep in lock-step with plots/overlay._ellipse_xy.
            for (var i = 0; i < n; i++) {
                var t = 2 * Math.PI * i / (n - 1);
                var xr = (bmin / 2) * Math.cos(t);
                var yr = (bmaj / 2) * Math.sin(t);
                ex[i] = bx + xr * cosPa + yr * sinPa;
                ey[i] = by - xr * sinPa + yr * cosPa;
            }
            // Plotly.restyle update values must be wrapped in arrays
            // (one element per trace being updated).
            window.Plotly.restyle(gd,
                {x: [ex], y: [ey], visible: true}, [idx]);
            return window.dash_clientside.no_update;
        }
        """,
        Output("beam-params", "data", allow_duplicate=True),
        Input("overlay-graph", "relayoutData"),
        State("beam-params", "data"),
        prevent_initial_call=True,
    )

    # ---- clientside: vertical "active epoch" marker on the summary plots ---
    # Draws a thin vertical line at the overlay's current epoch on the summary
    # views whose x-axis is epoch (Position / Flux / Polarization). Runs in the
    # browser via Plotly.relayout on the figure's `shapes` (no trace rebuild),
    # so scrubbing epochs is cheap and the user's zoom is preserved
    # (uirevision untouched). Re-fires on summary-figure rebuilds too, since a
    # fresh figure comes back with no shapes. Shapes are exclusively ours —
    # no summary view uses layout shapes otherwise — so a blanket reset to []
    # on the non-epoch views (XY Position / Kinematics) is safe.
    app.clientside_callback(
        """
        function(epoch, view, _figure) {
            var wrapper = document.getElementById('summary-graph');
            if (!wrapper) return window.dash_clientside.no_update;
            var gd = wrapper.querySelector('.js-plotly-plot');
            if (!gd || !window.Plotly) return window.dash_clientside.no_update;

            var epochViews = {'Position': 1, 'Flux': 1, 'Polarization': 1};
            if (!epochViews[view] || epoch === null || epoch === undefined) {
                window.Plotly.relayout(gd, {shapes: []});
                return window.dash_clientside.no_update;
            }
            // Position/Flux/Polarization are 2-row figures: top x-axis is "x"
            // (paired with "y"), bottom is "x2" (paired with "y2"). One full-
            // height vertical line per subplot, spanning the axis domain.
            var lineStyle = {color: 'rgba(90,90,90,0.65)', width: 1.5};
            var shapes = [
                {type: 'line', xref: 'x',  yref: 'y domain',
                 x0: epoch, x1: epoch, y0: 0, y1: 1,
                 line: lineStyle, layer: 'below'},
                {type: 'line', xref: 'x2', yref: 'y2 domain',
                 x0: epoch, x1: epoch, y0: 0, y1: 1,
                 line: lineStyle, layer: 'below'}
            ];
            window.Plotly.relayout(gd, {shapes: shapes});
            return window.dash_clientside.no_update;
        }
        """,
        Output("epoch-line-dummy", "data"),
        Input("active-epoch", "data"),
        Input("view-picker", "value"),
        Input("summary-graph", "figure"),
        prevent_initial_call=True,
    )


def _source_from_folder(folder_str: str) -> SourceRef | None:
    folder = Path(folder_str)
    m = _SOURCE_DIR_RE.match(folder.name)
    if not m:
        return None
    return SourceRef(
        source=m.group("source"),
        epoch_min=float(m.group("emin")),
        epoch_max=float(m.group("emax")),
        folder=folder,
    )


