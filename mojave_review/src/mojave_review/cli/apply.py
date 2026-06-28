"""`mojave-apply`: apply a recommendation JSON to a `Results/<source>/` folder.

Mirrors the save behavior of ``find_clusters.py --show_results`` — backup
the existing CSV / PDF / MP4 / config / run_string, write the modified
CSV, regenerate the PDF + MP4 via ``find_clusters.save_summary_plots``,
append `history.txt`, archive the recommendation JSON, and print a
copy-pasteable notebook-summary block.

Plot files are opt-in in the pipeline now (``find_clusters.py
--make_plots``), and mojave-apply mirrors that: regeneration is itself
opt-in via ``--make-plots`` and runs only when the source actually carries
a PDF/MP4. By default (no ``--make-plots``), any existing plot files are
MOVED into the backup (not copied) so a stale render never sits next to
the new CSV — and a plot-less apply doesn't need the production code or
matplotlib at all.

Important data-model assumptions (per the project author):

* The ``.plotdata.npz`` is **not** rewritten. The npz is interpreted
  alongside the csv via the ``origID`` column (which never changes), so
  edits to ``clusterID`` / ``robust`` / ``use_in_fit`` in the csv are
  picked up automatically without an npz mutation.
* Rows in the csv are preserved — only the three editable columns above
  change, plus ``core_x`` / ``core_y`` when a `change_clusterID` edit
  re-assigns a cluster to ``to_id=0`` (the core).
* The 999-overlap rule from ``cluster_code.update_clusterIDs``: if a
  `change_clusterID` causes a same-epoch collision with another row
  whose `clusterID == to_id`, that other row is re-IDed to 999.

Run-string text file is **never** overwritten (it documents the recipe
to reproduce the fit), but it is backed up for completeness.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..data.loader import _SOURCE_DIR_RE
from ..recommendations.apply import (
    apply_recommendation_with_history,
    epoch_mask as _epoch_mask,
)
from ..recommendations.schema import Recommendation
from ..recommendations.store import delete_recommendation, reviewer_slug


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


@dataclass
class _Existing:
    folder: Path
    prefix: str             # "0003-066u.1994.00-2026.00"
    source_name: str        # "0003-066u"
    csv_path: Path
    npz_path: Path
    df: pd.DataFrame
    csv_sha: str
    plotdata: dict[str, Any]


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_existing(results_dir: Path, source_folder_name: str) -> _Existing:
    folder = results_dir / source_folder_name
    m = _SOURCE_DIR_RE.match(folder.name)
    if not m:
        raise SystemExit(
            f"Source folder name doesn't match expected "
            f"<source>_<emin>-<emax> pattern: {folder.name}"
        )
    source_name = m.group("source")
    emin, emax = float(m.group("emin")), float(m.group("emax"))
    prefix = f"{source_name}.{emin:.2f}-{emax:.2f}"
    csv_path = folder / f"{prefix}.merged_win_results.csv"
    npz_path = folder / f"{prefix}.merged_win_results.plotdata.npz"
    if not csv_path.is_file():
        raise SystemExit(f"CSV not found: {csv_path}")
    if not npz_path.is_file():
        raise SystemExit(f"NPZ not found: {npz_path}")
    df = pd.read_csv(csv_path)
    csv_sha = _file_sha256(csv_path)
    with np.load(npz_path, allow_pickle=True) as d:
        plotdata = {k: d[k] for k in d.files}
    return _Existing(
        folder=folder, prefix=prefix, source_name=source_name,
        csv_path=csv_path, npz_path=npz_path,
        df=df, csv_sha=csv_sha, plotdata=plotdata,
    )


# ---------------------------------------------------------------------------
# Apply edits to the dataframe
# ---------------------------------------------------------------------------
# The actual mutation logic lives in `recommendations/apply.py` so the
# web UI's visualize path and this CLI path stay in lock-step. The
# `apply_recommendation_with_history` engine returns the modified df plus
# the list of history lines that go under our timestamped header in
# `history.txt`.


def _apply_to_csv(
    df: pd.DataFrame, rec: Recommendation,
) -> tuple[pd.DataFrame, list[str]]:
    return apply_recommendation_with_history(df, rec)


# ---------------------------------------------------------------------------
# Backup + save
# ---------------------------------------------------------------------------

_BACKUP_RE = re.compile(r"backup_(\d+)_merged_win_results\.csv$")


def _next_backup_index(backups_dir: Path) -> str:
    if not backups_dir.is_dir():
        return "001"
    indices = []
    for f in backups_dir.glob("backup_*_merged_win_results.csv"):
        m = _BACKUP_RE.search(f.name)
        if m:
            indices.append(int(m.group(1)))
    return f"{(max(indices) if indices else 0) + 1:03d}"


def _backup_existing(folder: Path, prefix: str, idx: str,
                     move_plots: bool = False) -> Path:
    """Move/copy the existing artifacts into `backups/backup_<idx>_*`.

    The CSV is renamed (the old file disappears from ``folder``; the new
    one will be written next). Config / run_string are copied so the
    originals stay in place. The PDF / MP4 (when present — they're opt-in
    in the pipeline now) are copied when they're about to be regenerated,
    or MOVED (``move_plots=True``) when regeneration is skipped, so a
    stale render never sits next to the new CSV.
    """
    backups_dir = folder / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    # (src_basename -> dst_basename, is_rename)
    items: list[tuple[str, str, bool]] = [
        (f"{prefix}.merged_win_results.csv",
         f"backup_{idx}_merged_win_results.csv", True),
        (f"{prefix}.summary_plots.pdf",
         f"backup_{idx}_summary_plots.pdf", move_plots),
        (f"{prefix}.epoch_overplots.mp4",
         f"backup_{idx}_epoch_overplots.mp4", move_plots),
        ("config_win.json",     f"backup_{idx}_config.json", False),
        ("run_string.txt",      f"backup_{idx}_run_string.txt", False),
    ]
    for src_name, dst_name, is_rename in items:
        src = folder / src_name
        dst = backups_dir / dst_name
        if not src.is_file():
            continue
        if is_rename:
            shutil.move(str(src), str(dst))
        else:
            shutil.copy(str(src), str(dst))
    return backups_dir


def _save_new_csv(folder: Path, prefix: str, df: pd.DataFrame) -> Path:
    p = folder / f"{prefix}.merged_win_results.csv"
    df.to_csv(p, index=False)
    return p


# ---------------------------------------------------------------------------
# Regenerate plots — imports save_summary_plots from find_clusters
# ---------------------------------------------------------------------------


def _import_save_summary_plots(production_code_dir: Path):
    """Import ``find_clusters.save_summary_plots`` lazily, raising a clear
    error if the production-code directory isn't set up correctly. Called
    once early so we fail fast before doing any destructive writes."""
    sys.path.insert(0, str(production_code_dir))
    try:
        from find_clusters import save_summary_plots
    except Exception as e:
        raise SystemExit(
            f"Could not import save_summary_plots from "
            f"{production_code_dir}/find_clusters.py — {e!r}\n"
            f"Pass --production-code-dir if find_clusters.py lives "
            f"somewhere other than the parent of --results-dir."
        )
    return save_summary_plots


def _resolve_root_data_dir(plotdata: dict[str, Any]) -> str:
    """Root of the FITS/CC tree used when regenerating the overplots.

    Prefer the ``MOJAVE_DATA`` environment variable — the location of the
    FITS tree on the machine running ``mojave-apply`` — and fall back to the
    ``root_data_dir`` baked into the ``.plotdata.npz`` when the model was
    first produced (which often points at a path that doesn't exist on this
    machine). An empty/unset ``MOJAVE_DATA`` falls through to the default.
    """
    env_dir = os.environ.get("MOJAVE_DATA")
    if env_dir:
        return os.path.expanduser(env_dir)
    return str(plotdata.get("root_data_dir", ""))


def _regen_plots(
    folder: Path, prefix: str, plotdata: dict[str, Any], df: pd.DataFrame,
    save_summary_plots,
) -> None:
    file_prefix = str(folder / f"{prefix}.")
    root_data_dir = _resolve_root_data_dir(plotdata)
    print(f"  using root_data_dir = {root_data_dir or '(empty)'}"
          + ("  [from $MOJAVE_DATA]" if os.environ.get("MOJAVE_DATA") else
             "  [from .plotdata.npz]"))
    save_summary_plots(
        plotdata["epoch_info"], plotdata["cc_data"],
        root_data_dir,
        df, plotdata["cc_labels"],
        file_prefix=file_prefix,
        colorImages=False,
    )


# ---------------------------------------------------------------------------
# History + notebook summary
# ---------------------------------------------------------------------------


# Matches the separator find_clusters.py already writes before each
# pipeline-run entry, so the file stays homogenous when scrolled.
_HISTORY_SEPARATOR = "#" + "-" * 50


def _append_history(folder: Path, header: str, edit_lines: list[str]) -> None:
    with (folder / "history.txt").open("a") as f:
        f.write(_HISTORY_SEPARATOR + "\n")
        f.write(header + "\n")
        for line in edit_lines:
            f.write(line + "\n")


def _robust_state(df: pd.DataFrame) -> dict[int, bool]:
    return {
        int(cid): bool(g["robust"].iloc[0])
        for cid, g in df.groupby("clusterID") if int(cid) >= 0
    }


def _compute_robust_deltas(
    before: pd.DataFrame, after: pd.DataFrame,
) -> tuple[list[int], list[int]]:
    """``(changed-to-robust, changed-to-non-robust)`` — clusters whose flag
    actually flipped during this apply."""
    b, a = _robust_state(before), _robust_state(after)
    to_robust, to_nonrobust = [], []
    for cid, val in a.items():
        if cid in b and b[cid] != val:
            (to_robust if val else to_nonrobust).append(cid)
    return sorted(to_robust), sorted(to_nonrobust)


def _eligible_clusters(df: pd.DataFrame, min_fit_epochs: int = 5) -> set[int]:
    """Clusters with at least ``min_fit_epochs`` use_in_fit=True rows.
    Matches the eligibility filter the Robustness tab uses in the web UI."""
    out: set[int] = set()
    for cid, grp in df.groupby("clusterID"):
        if int(cid) < 0:
            continue
        if int(grp["use_in_fit"].astype(bool).sum()) >= min_fit_epochs:
            out.add(int(cid))
    return out


def _format_robustness_lines(
    before: pd.DataFrame, after: pd.DataFrame,
) -> list[str]:
    """The four-line robustness block in the notebook summary:

    * ``Changed to robust:``     — deltas this apply turned on
    * ``Changed to non-robust:`` — deltas this apply turned off
    * ``Robust (eligible):``     — full current state across eligible clusters
    * ``Non-robust (eligible):`` — same, the other half

    Delta lines are emitted only when there are actual changes. The two
    summary lines are always emitted (even with no robustness changes) so
    the notebook records the post-apply state of the model.
    """
    out: list[str] = []
    to_robust, to_nonrobust = _compute_robust_deltas(before, after)
    if to_robust:
        out.append("Changed to robust:     " + ", ".join(str(c) for c in to_robust))
    if to_nonrobust:
        out.append("Changed to non-robust: " + ", ".join(str(c) for c in to_nonrobust))
    eligible = _eligible_clusters(after)
    state = _robust_state(after)
    current_robust = sorted(c for c in eligible if state.get(c))
    current_nonrobust = sorted(c for c in eligible if not state.get(c))
    if current_robust:
        out.append("Robust (eligible):     " + ", ".join(str(c) for c in current_robust))
    if current_nonrobust:
        out.append("Non-robust (eligible): " + ", ".join(str(c) for c in current_nonrobust))
    return out


def _format_crossID_lines(rec: Recommendation, backup_idx: str) -> list[str]:
    out: list[str] = []
    # Collapse all_epochs renumbers that share a target.
    all_ep_groups: dict[int, list[int]] = {}
    single_edits: list[tuple[int, float, int]] = []
    for e in rec.edits:
        if e.op != "change_clusterID":
            continue
        if e.scope == "all_epochs" and e.from_id is not None and e.to_id is not None:
            all_ep_groups.setdefault(int(e.to_id), []).append(int(e.from_id))
        elif e.scope == "single" and e.epoch is not None \
                and e.from_id is not None and e.to_id is not None:
            single_edits.append((int(e.to_id), float(e.epoch), int(e.from_id)))
    for to_id, froms in sorted(all_ep_groups.items()):
        froms_str = ", ".join(str(f) for f in froms)
        out.append(
            f"CrossID {to_id} for whole time period (change {froms_str} to become {to_id}), "
            f"see backup_{backup_idx}_*.* for previous to this change."
        )
    for to_id, ep, from_id in sorted(single_edits):
        out.append(
            f"CrossID {to_id} at epoch {ep:.4f} (was {from_id}), "
            f"see backup_{backup_idx}_*.* for previous to this change."
        )
    return out


def _format_use_in_fit_lines(
    rec: Recommendation, before: pd.DataFrame, after: pd.DataFrame,
) -> list[str]:
    out: list[str] = []
    # Group per-cluster `single` edits with value=False
    per_cluster: dict[int, list[float]] = {}
    for e in rec.edits:
        if e.op != "set_use_in_fit":
            continue
        if e.scope == "epoch" and e.epoch is not None and e.value is False:
            em = _epoch_mask(before, float(e.epoch))
            n_changed = int(
                (before.loc[em, "use_in_fit"].astype(bool)
                 & ~after.loc[em, "use_in_fit"].astype(bool)).sum()
            )
            out.append(
                f"use_in_fit: epoch {float(e.epoch):.4f} excluded entirely "
                f"({n_changed} cluster{'s' if n_changed != 1 else ''} affected)."
            )
        elif e.scope == "single" and e.epoch is not None \
                and e.clusterID is not None and e.value is False:
            per_cluster.setdefault(int(e.clusterID), []).append(float(e.epoch))
    for cid, epochs in sorted(per_cluster.items()):
        ep_str = ", ".join(f"{ep:.4f}" for ep in sorted(epochs))
        out.append(f"use_in_fit: cluster {cid} excluded at epoch(s) {ep_str}.")
    return out


def _format_notebook_summary(
    rec: Recommendation, before_df: pd.DataFrame, after_df: pd.DataFrame,
    backup_idx: str,
) -> str:
    rule = "─" * 65
    lines: list[str] = [rule, f"[paste this into your notebook for {rec.source}]", ""]
    if rec.source_comment.strip():
        lines.append(rec.source_comment.rstrip())
    else:
        lines.append("<user entered notes for source here>")
    lines.append("")
    crossid = _format_crossID_lines(rec, backup_idx)
    if crossid:
        lines.extend(crossid)
        lines.append("")
    robustness = _format_robustness_lines(before_df, after_df)
    if robustness:
        lines.extend(robustness)
    uif = _format_use_in_fit_lines(rec, before_df, after_df)
    if uif:
        lines.extend(uif)
    lines.append(rule)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Archive the JSON
# ---------------------------------------------------------------------------


def _archive_recommendation(
    json_path: Path, recommendations_dir: Path, source_name: str,
) -> Path:
    applied_dir = recommendations_dir / source_name / "applied"
    applied_dir.mkdir(parents=True, exist_ok=True)
    date = _dt.date.today().isoformat()
    target = applied_dir / f"{date}__{json_path.stem}.json"
    n = 2
    while target.exists():
        target = applied_dir / f"{date}__{json_path.stem}_{n}.json"
        n += 1
    shutil.move(str(json_path), str(target))
    return target


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mojave-apply",
        description=(
            "Apply a recommendation JSON to a Results/<source>/ folder. "
            "Backs up the existing CSV / PDF / MP4 / config / run_string, "
            "writes the modified CSV, regenerates the PDF + MP4 via "
            "find_clusters.save_summary_plots (only when the source carries "
            "them — they are opt-in in the pipeline now), appends "
            "history.txt, archives the JSON, and prints a notebook-summary "
            "block."
        ),
    )
    p.add_argument("--results-dir", type=Path, required=True,
                   help="Path to the Results/ directory.")
    p.add_argument("--source", required=True,
                   help="Source folder name under Results/, "
                        "e.g. 0003-066u_1994.00-2026.00")
    p.add_argument("--recommendation", type=Path, required=True,
                   help="Path to the recommendation JSON to apply.")
    p.add_argument("--recommendations-dir", type=Path, default=None,
                   help="Where applied JSONs are archived. "
                        "Defaults to <results-dir>/../recommendations.")
    p.add_argument("--production-code-dir", type=Path, default=None,
                   help="Directory containing find_clusters.py + cluster_code.py. "
                        "Defaults to <results-dir>/..")
    p.add_argument("--no-confirm", action="store_true",
                   help="Skip the interactive confirmation prompt.")
    p.add_argument("--make-plots", action="store_true",
                   help="Regenerate the summary PDF / epoch MP4 when the "
                        "source carries them. Default is to SKIP regeneration "
                        "(matching find_clusters.py, where plots are opt-in "
                        "via --make_plots) — prior PDF/MP4 are moved into the "
                        "backup instead of being copied. When the source has "
                        "no plot files, this flag is a no-op.")
    p.add_argument("--stage3-meta", type=Path, default=None,
                   help="Stage-3 sidecar JSON (considered_slugs / ledger_entry "
                        "/ status). After applying the aggregated recommendation "
                        "this archives the folded submissions, appends the "
                        "ledger entry, and bumps the notes Status. Written by "
                        "the web app's 'Apply aggregated decisions (Stage 3)'.")
    return p


def _apply_stage3_meta(
    meta_path: Path, recommendations_dir: Path, source_name: str,
    backup_ref: str, fallback_date: str,
) -> None:
    """Run the Stage-3 post-apply bookkeeping recorded in the sidecar: move the
    folded reviewer submissions to ``considered/<date>/``, append the
    (app-rendered) ledger entry — with ``{{BACKUP_REF}}`` resolved to this run's
    backup — and set the notes Status. Pure file ops; needs no production code."""
    import json as _json
    from ..recommendations.store import archive_considered_submissions
    from ..notes import (notes_dir_for, read_note, write_note,
                         append_ledger, set_status, scaffold)
    with meta_path.open() as f:
        meta = _json.load(f)
    date = meta.get("date") or fallback_date
    slugs = meta.get("considered_slugs") or []
    if slugs:
        archive_considered_submissions(
            recommendations_dir, source_name, slugs, date=date)
    notes_dir = notes_dir_for(recommendations_dir)
    md = read_note(notes_dir, source_name) or scaffold(source_name)
    entry = (meta.get("ledger_entry") or "").replace("{{BACKUP_REF}}", backup_ref)
    if entry:
        md = append_ledger(md, entry)
    status = meta.get("status") or ""
    if status:
        md = set_status(md, status)
    write_note(notes_dir, source_name, md)
    print(f"Stage 3: archived {len(slugs)} considered submission(s), "
          f"wrote ledger + status.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    results_dir = args.results_dir.expanduser().resolve()
    recommendations_dir = (
        args.recommendations_dir.expanduser().resolve()
        if args.recommendations_dir
        else results_dir.parent / "recommendations"
    )
    production_code_dir = (
        args.production_code_dir.expanduser().resolve()
        if args.production_code_dir
        else results_dir.parent
    )
    rec_path = args.recommendation.expanduser().resolve()
    if not rec_path.is_file():
        raise SystemExit(f"Recommendation JSON not found: {rec_path}")
    stage3_meta = (args.stage3_meta.expanduser().resolve()
                   if args.stage3_meta else None)
    if stage3_meta is not None and not stage3_meta.is_file():
        raise SystemExit(f"Stage-3 meta JSON not found: {stage3_meta}")

    # ---- All read-only checks first (fail fast before destructive writes) -
    with rec_path.open() as f:
        rec = Recommendation.from_dict(json.load(f))
    existing = _load_existing(results_dir, args.source)

    if rec.source != existing.source_name:
        raise SystemExit(
            f"Recommendation's source ({rec.source!r}) doesn't match folder "
            f"({existing.source_name!r}). Aborting."
        )

    # Stale model_sha → warn + prompt.
    if rec.model_sha and rec.model_sha != existing.csv_sha:
        print()
        print("WARNING: this recommendation was made against a DIFFERENT")
        print(f"  version of {existing.csv_path.name}.")
        print(f"  Recommendation's model_sha: {rec.model_sha[:16]}…")
        print(f"  Current     model_sha:      {existing.csv_sha[:16]}…")
        print("  The recommendation may not apply cleanly, or may apply to")
        print("  unintended rows.")
        if not args.no_confirm:
            resp = input("  Continue anyway? [y/N]: ").strip().lower()
            if resp not in ("y", "yes"):
                print("Aborted.")
                return 1

    new_df, edit_history = _apply_to_csv(existing.df, rec)
    n_edits = len(edit_history)
    # No-op = the recommendation changes nothing on disk (no clusterID / robust
    # / use_in_fit difference — comments don't alter the CSV).
    no_op = new_df.equals(existing.df)

    timestamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec_label = (
        str(rec_path.relative_to(recommendations_dir))
        if rec_path.is_relative_to(recommendations_dir) else str(rec_path)
    )

    # ---- No-op fast path --------------------------------------------------
    # Conclude a no-change recommendation WITHOUT a redundant backup, CSV
    # rewrite, or plot regeneration — just record it in history.txt and archive
    # the JSON out of submitted/. This is what lets "always conclude Step 2 with
    # mojave-apply" stay cheap (and a no-op conclude doesn't even need the
    # production code, since no plots are regenerated).
    if no_op:
        if not args.no_confirm:
            print()
            print(f"{existing.csv_path.name}: recommendation has NO actionable "
                  f"changes.")
            print("  Will conclude it (history line + archive) — no backup, no "
                  "plot regeneration.")
            resp = input("  Continue? [y/N]: ").strip().lower()
            if resp not in ("y", "yes"):
                print("Aborted.")
                return 1
        header = (f"# {timestamp} Concluded recommendation {rec_label} "
                  f"— no changes")
        _append_history(existing.folder, header, [])
        print("Appended to history.txt (no changes)")
        archived = _archive_recommendation(rec_path, recommendations_dir,
                                           existing.source_name)
        try:
            archived_label = archived.relative_to(recommendations_dir.parent)
        except ValueError:
            archived_label = archived
        print(f"Archived recommendation to {archived_label}")
        if delete_recommendation(recommendations_dir, existing.source_name,
                                 "current", rec.reviewer):
            print(f"Removed now-applied current/ draft "
                  f"({reviewer_slug(rec.reviewer)})")
        if stage3_meta is not None:
            _apply_stage3_meta(stage3_meta, recommendations_dir,
                               existing.source_name,
                               backup_ref="no changes (concluded)",
                               fallback_date=timestamp[:10])
        print(f"\nNo changes for {existing.source_name}; concluded "
              f"(no backup / regen).")
        return 0

    # ---- Changes to apply -------------------------------------------------
    # Plot regeneration is opt-in (the pipeline already moved to opt-in plots
    # via find_clusters.py --make_plots): regenerate only when the source
    # actually carries a PDF/MP4 AND --make-plots was given. When skipping,
    # the backup below MOVES any existing plot files so a stale render can't
    # sit next to the new CSV. Confirm save_summary_plots is importable BEFORE
    # we touch any files (only needed when regenerating).
    plots_present = (
        (existing.folder / f"{existing.prefix}.summary_plots.pdf").is_file()
        or (existing.folder / f"{existing.prefix}.epoch_overplots.mp4").is_file()
    )
    regen_plots = plots_present and args.make_plots
    save_summary_plots = (_import_save_summary_plots(production_code_dir)
                          if regen_plots else None)
    backup_idx = _next_backup_index(existing.folder / "backups")

    if not args.no_confirm:
        print()
        print(f"About to overwrite {existing.csv_path.name}")
        print(f"  with {n_edits} edit line(s) — backup_{backup_idx}_*.* will be created.")
        resp = input("  Continue? [y/N]: ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted.")
            return 1

    # ---- Destructive operations from here ---------------------------------
    _backup_existing(existing.folder, existing.prefix, backup_idx,
                     move_plots=not regen_plots)
    print(f"Wrote backups/backup_{backup_idx}_*.*")
    _save_new_csv(existing.folder, existing.prefix, new_df)
    print(f"Wrote {existing.prefix}.merged_win_results.csv")

    header = (f"# {timestamp} Applied recommendation {rec_label} "
              f"(prior CSV -> backups/backup_{backup_idx})")
    _append_history(existing.folder, header, edit_history)
    print("Appended to history.txt")

    if regen_plots:
        print("Regenerating summary_plots.pdf + epoch_overplots.mp4 …")
        _regen_plots(existing.folder, existing.prefix, existing.plotdata,
                     new_df, save_summary_plots)
        print("  done.")
    elif plots_present:
        print("Skipped plot regeneration (default; pass --make-plots to "
              "regenerate); prior PDF/MP4 moved into the backup.")
    else:
        print("No PDF/MP4 to regenerate (plots are opt-in via "
              "find_clusters.py --make_plots).")

    archived = _archive_recommendation(rec_path, recommendations_dir,
                                       existing.source_name)
    try:
        archived_label = archived.relative_to(recommendations_dir.parent)
    except ValueError:
        archived_label = archived
    print(f"Archived recommendation to {archived_label}")
    if delete_recommendation(recommendations_dir, existing.source_name,
                             "current", rec.reviewer):
        print(f"Removed now-applied current/ draft "
              f"({reviewer_slug(rec.reviewer)})")

    if stage3_meta is not None:
        _apply_stage3_meta(stage3_meta, recommendations_dir,
                           existing.source_name,
                           backup_ref=f"backups/backup_{backup_idx}",
                           fallback_date=timestamp[:10])

    summary = _format_notebook_summary(rec, existing.df, new_df, backup_idx)
    print()
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
