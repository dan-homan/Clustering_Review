"""Load per-source clustering results from a local Results/ tree.

The on-disk layout produced by find_clusters.py is:

    Results/
      <source>_<min>-<max>/
        <source>.<min>-<max>.merged_win_results.csv
        <source>.<min>-<max>.merged_win_results.plotdata.npz
        <source>.<min>-<max>.summary_plots.pdf      (optional reference)
        <source>.<min>-<max>.epoch_overplots.mp4    (optional reference)
        backups/
          backup_NNN_merged_win_results.csv
          backup_NNN_summary_plots.pdf
          backup_NNN_epoch_overplots.mp4
        cluster_fits/...
        config_win.json
        run_string.txt
        history.txt

This module exposes a thin API the rest of the app can consume without
caring about the directory layout.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Source discovery
# ---------------------------------------------------------------------------

# Source folder names look like "0003-066u_1994.00-2026.00".
_SOURCE_DIR_RE = re.compile(r"^(?P<source>.+?)_(?P<emin>[\d.]+)-(?P<emax>[\d.]+)$")


@dataclass(frozen=True)
class SourceRef:
    """Identifies one source-folder on disk."""

    source: str          # e.g. "0003-066u"
    epoch_min: float     # e.g. 1994.00
    epoch_max: float     # e.g. 2026.00
    folder: Path

    @property
    def label(self) -> str:
        return f"{self.source}  {self.epoch_min:.2f}-{self.epoch_max:.2f}"

    @property
    def file_prefix(self) -> str:
        """Filename prefix shared by the CSV and NPZ in this folder."""
        return f"{self.source}.{self.epoch_min:.2f}-{self.epoch_max:.2f}"


def list_sources(results_dir: Path) -> list[SourceRef]:
    """Return all source folders under results_dir, sorted by name."""
    out: list[SourceRef] = []
    for entry in sorted(results_dir.iterdir()):
        if not entry.is_dir():
            continue
        m = _SOURCE_DIR_RE.match(entry.name)
        if not m:
            continue
        out.append(
            SourceRef(
                source=m.group("source"),
                epoch_min=float(m.group("emin")),
                epoch_max=float(m.group("emax")),
                folder=entry,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Model files (current + backups)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelFile:
    """A single CSV results file (current or one of the backups)."""

    key: str             # "current" or "backup_001"
    label: str           # human-readable
    csv_path: Path
    npz_path: Path | None  # plotdata.npz, only present for "current"


def list_models(src: SourceRef) -> list[ModelFile]:
    """Return the current model first, followed by any backups."""
    current_csv = src.folder / f"{src.file_prefix}.merged_win_results.csv"
    current_npz = src.folder / f"{src.file_prefix}.merged_win_results.plotdata.npz"
    models: list[ModelFile] = []
    if current_csv.is_file():
        models.append(
            ModelFile(
                key="current",
                label="current",
                csv_path=current_csv,
                npz_path=current_npz if current_npz.is_file() else None,
            )
        )
    backups_dir = src.folder / "backups"
    if backups_dir.is_dir():
        for csv in sorted(backups_dir.glob("backup_*_merged_win_results.csv")):
            m = re.match(r"backup_(\d+)_merged_win_results\.csv$", csv.name)
            if not m:
                continue
            key = f"backup_{m.group(1)}"
            models.append(
                ModelFile(key=key, label=key, csv_path=csv, npz_path=None)
            )
    return models


# ---------------------------------------------------------------------------
# Bundled per-source data
# ---------------------------------------------------------------------------


@dataclass
class PlotData:
    """Contents of merged_win_results.plotdata.npz, as plain numpy arrays.

    ``cc_labels`` is ``None`` when this PlotData was *partially* borrowed
    from another model whose underlying fit doesn't match: the clean
    components themselves (positions, fluxes) are invariant across
    re-fits and can still be borrowed, but the per-CC cluster labels
    encode a particular clustering pass and would mis-colour points if
    reused on a different fit. Callers must handle ``cc_labels is None``
    by skipping or visually neutralising the per-cluster CC scatter.
    """

    epoch_info: np.ndarray              # structured array, see fields below
    cc_data: np.ndarray                 # structured array of clean components
    cc_labels: np.ndarray | None        # int32 array; None ⇒ not authoritative
    root_data_dir: str                  # original root_data_dir used when the model ran

    # epoch_info dtype:
    #   epoch_name (<U10), epoch_val (f8), band (<U1),
    #   cc_file (<U200), fits_file (<U200),
    #   inoise (f8), pnoise (f8), sigma_cut (f8), sigma_cut_area (f8),
    #   bmaj (f8), bmin (f8), bpa (f8), pix_to_mas (f8)
    # cc_data dtype:
    #   epoch (f8), x (f8), y (f8), stokes (<U1),
    #   flux (f8), sizex (f8), sizey (f8), group (f8), clusterID (i4)


@dataclass
class SourceBundle:
    """Everything the UI needs to render one source under one model."""

    source: SourceRef
    model: ModelFile
    cluster_df: pd.DataFrame
    plotdata: PlotData | None  # None when viewing a backup CSV (no npz)
    csv_sha: str               # content hash for recommendation provenance
    reference_pdf: Path | None
    reference_mp4: Path | None


def _file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _load_plotdata(npz_path: Path) -> PlotData:
    with np.load(npz_path, allow_pickle=True) as d:
        return PlotData(
            epoch_info=np.asarray(d["epoch_info"]),
            cc_data=np.asarray(d["cc_data"]),
            cc_labels=np.asarray(d["cc_labels"]),
            root_data_dir=str(d["root_data_dir"]),
        )


def _shares_underlying_fit(a_df: pd.DataFrame, b_df: pd.DataFrame) -> bool:
    """True if two cluster_dfs describe the same underlying CC clustering.

    The npz's ``cc_data`` + ``cc_labels`` reflect the production CC-to-
    cluster assignment via ``origID``. That assignment is invariant
    under pure clusterID-renumbering / robust / use_in_fit edits — only
    a re-fit (``--recalc_fits`` etc.) changes it. ``N_Icc`` per
    ``(epoch, origID)`` is the smallest practical fingerprint: identical
    on both sides ⇒ same underlying fit ⇒ the same npz applies to
    either CSV.

    Strict equality on the multi-key — different row counts or any
    differing ``N_Icc`` cell counts as "different fit" and disqualifies
    sharing.
    """
    keys = ["epoch", "origID"]
    if not all(c in a_df.columns and c in b_df.columns for c in keys + ["N_Icc"]):
        return False
    cols = keys + ["N_Icc"]
    a = a_df[cols].sort_values(keys).reset_index(drop=True)
    b = b_df[cols].sort_values(keys).reset_index(drop=True)
    if len(a) != len(b):
        return False
    return a.equals(b)


def _file_fingerprint(csv: Path, npz: Path | None) -> tuple:
    """Cheap stat-based fingerprint of the (CSV, NPZ) pair.

    Used as part of the lru_cache key on ``_load_bundle_cached`` so the
    cache invalidates automatically when the reviewer (or
    ``mojave-apply``) edits the files on disk. ``stat()`` is microseconds
    — we call it on every ``load_bundle`` and rely on the existing
    LRU cache to short-circuit when the fingerprint hasn't changed.

    We include both mtime (nanosecond resolution) and size — mtime
    alone can be fooled on filesystems with second-resolution timestamps
    or by tools that ``touch`` a file without changing it; size catches
    the common "file was rewritten" case independently.
    """
    csv_stat = csv.stat()
    npz_part: tuple | None
    if npz is not None and npz.is_file():
        npz_stat = npz.stat()
        npz_part = (npz_stat.st_mtime_ns, npz_stat.st_size)
    else:
        npz_part = None
    return (csv_stat.st_mtime_ns, csv_stat.st_size, npz_part)


def load_bundle(folder: str, model_key: str) -> SourceBundle:
    """Load one (source, model) combination.

    Stats the model's CSV (and NPZ, when present) on every call to build
    a cache fingerprint, then delegates to ``_load_bundle_cached``. When
    the files change on disk the fingerprint changes and the next call
    triggers a fresh read — no app restart needed.

    For non-``current`` models we also fold the *current* model's
    fingerprint into the key, because the bundle's ``plotdata`` may be
    *borrowed* from current (when the npz isn't present alongside the
    backup CSV). Without that, editing ``current`` would leave any cached
    backup bundles holding a stale plotdata reference.
    """
    folder_path = Path(folder)
    m = _SOURCE_DIR_RE.match(folder_path.name)
    if not m:
        raise ValueError(f"Not a source folder: {folder_path}")
    src = SourceRef(
        source=m.group("source"),
        epoch_min=float(m.group("emin")),
        epoch_max=float(m.group("emax")),
        folder=folder_path,
    )
    models = list_models(src)
    model = next((mf for mf in models if mf.key == model_key), None)
    if model is None:
        raise ValueError(f"Model {model_key!r} not found under {folder_path}")

    fp = _file_fingerprint(model.csv_path, model.npz_path)
    if model_key != "current":
        current_model = next((mf for mf in models if mf.key == "current"), None)
        current_fp = (_file_fingerprint(current_model.csv_path, current_model.npz_path)
                      if current_model is not None else None)
    else:
        current_fp = None
    return _load_bundle_cached(folder, model_key, fp, current_fp)


@lru_cache(maxsize=16)
def _load_bundle_cached(folder: str, model_key: str,
                        fingerprint: tuple,
                        current_fingerprint: tuple | None) -> SourceBundle:
    """Cached implementation. ``fingerprint`` and ``current_fingerprint``
    are part of the cache key only — their *values* (file mtimes + sizes)
    invalidate the cache when the files change on disk.
    """
    del fingerprint, current_fingerprint  # key-only, not used in body
    folder_path = Path(folder)
    m = _SOURCE_DIR_RE.match(folder_path.name)
    if not m:
        raise ValueError(f"Not a source folder: {folder_path}")
    src = SourceRef(
        source=m.group("source"),
        epoch_min=float(m.group("emin")),
        epoch_max=float(m.group("emax")),
        folder=folder_path,
    )

    model: ModelFile | None = next(
        (mf for mf in list_models(src) if mf.key == model_key), None
    )
    if model is None:
        raise ValueError(f"Model {model_key!r} not found under {folder_path}")

    cluster_df = pd.read_csv(model.csv_path)
    csv_sha = _file_sha256(model.csv_path)
    plotdata = _load_plotdata(model.npz_path) if model.npz_path else None

    # Backup CSVs ship without a sibling npz. We borrow current's NPZ in
    # tiers depending on how compatible the backup's fit is:
    #
    #   * shares-underlying-fit (only ID-renumber / robust / use_in_fit
    #     edits between current and backup): borrow the whole NPZ. The
    #     per-CC cluster labels are still accurate for this CSV.
    #   * different underlying fit (re-fit pass between current and
    #     backup): clean components themselves and per-epoch metadata
    #     (beam, inoise, pix_to_mas) are invariant across re-fits, so
    #     borrow ``epoch_info`` + ``cc_data`` only. Set ``cc_labels=None``
    #     to signal "we have no trustworthy CC→cluster mapping for this
    #     backup"; ``overlay.build_overlay_figure`` handles that by
    #     rendering CCs in a neutral colour instead of by cluster.
    #
    # Either way the overlay panel renders the contour image + the
    # backup's own cluster centroids / FWHM ellipses (from the CSV).
    if plotdata is None and model.key != "current":
        try:
            current_bundle = load_bundle(folder, "current")
        except (ValueError, FileNotFoundError):
            current_bundle = None
        if current_bundle is not None and current_bundle.plotdata is not None:
            cur_pd = current_bundle.plotdata
            if _shares_underlying_fit(cluster_df, current_bundle.cluster_df):
                plotdata = cur_pd
            else:
                plotdata = PlotData(
                    epoch_info=cur_pd.epoch_info,
                    cc_data=cur_pd.cc_data,
                    cc_labels=None,
                    root_data_dir=cur_pd.root_data_dir,
                )

    pdf = src.folder / f"{src.file_prefix}.summary_plots.pdf"
    mp4 = src.folder / f"{src.file_prefix}.epoch_overplots.mp4"

    return SourceBundle(
        source=src,
        model=model,
        cluster_df=cluster_df,
        plotdata=plotdata,
        csv_sha=csv_sha,
        reference_pdf=pdf if pdf.is_file() else None,
        reference_mp4=mp4 if mp4.is_file() else None,
    )


def clear_bundle_cache() -> None:
    """Wipe the in-process bundle cache. Used by the "↻ Reload" UI
    button as a belt-and-braces fallback alongside the mtime-based
    invalidation in ``load_bundle`` itself."""
    _load_bundle_cached.cache_clear()
