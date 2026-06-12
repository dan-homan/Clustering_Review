"""Per-window cluster-fit results for the admin Window-N review mode.

The pipeline (``run_epoch_window_fits`` in cluster_code.py) fits every
candidate cluster count N in [min_clusters, max_clusters] to every time
window and caches the lot under the source folder:

    cluster_fits/<source>.<first_ep>-<last_ep>.npz   (fit results, all N)
    cluster_fits/<source>.<first_ep>-<last_ep>.csv   (per-N diagnostics)

The npz holds, per N, a ``cluster_epoch_df`` (same schema as the merged
results CSV), per-CC ``labels``, plus the window's clean components
(``data``) and per-epoch metadata (``ep_info``) — exactly the shapes the
overlay rendering already consumes, so ``window_bundle`` adapts one
(window, N) fit into a ``SourceBundle`` and ``overlay_figure_for_epoch``
renders it unchanged.

The ``--editN`` replacement loop:

1. the admin reviews windows here and records per-window N choices;
2. choices autosave to ``<recs>/<source>/nwin_edits/nwin_choices.json``;
3. ``find_clusters.py ... --N_win_file <that json>`` re-runs the pipeline
   with the chosen N values (cached fits make this fast).

``cluster_fits/`` is excluded from the server sync
(server_sync/server_update_exclude.txt), so this whole mode is
effectively local/admin-only — the panel shows a hint when the files are
absent.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .loader import ModelFile, PlotData, SourceBundle, SourceRef


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowFitRef:
    """One window's cached fit files on disk."""

    label: str           # "<first_ep>-<last_ep>", e.g. "1995.57-2000.03".
                         # Two-decimal formatting straight from the filename —
                         # the same key find_clusters.py --N_win_file matches
                         # against win_info (load_N_win_choices).
    first_epoch: float
    last_epoch: float
    npz_path: Path
    csv_path: Path


def list_window_fits(src_folder: Path, source: str) -> list[WindowFitRef]:
    """All window-fit refs under <src_folder>/cluster_fits, sorted by epoch
    range. Returns [] when the directory is missing (e.g. a server deploy,
    where cluster_fits/ is excluded from the sync)."""
    fits_dir = Path(src_folder) / "cluster_fits"
    if not fits_dir.is_dir():
        return []
    pat = re.compile(
        rf"^{re.escape(source)}\.(?P<lo>\d+\.\d+)-(?P<hi>\d+\.\d+)\.npz$")
    out: list[WindowFitRef] = []
    for p in sorted(fits_dir.glob(f"{source}.*.npz")):
        m = pat.match(p.name)
        if not m:
            continue
        out.append(
            WindowFitRef(
                label=f"{m.group('lo')}-{m.group('hi')}",
                first_epoch=float(m.group("lo")),
                last_epoch=float(m.group("hi")),
                npz_path=p,
                csv_path=p.with_suffix(".csv"),
            )
        )
    out.sort(key=lambda r: (r.first_epoch, r.last_epoch))
    return out


# ---------------------------------------------------------------------------
# Heavy per-window npz load (cached)
# ---------------------------------------------------------------------------


@dataclass
class WindowFit:
    """In-memory contents of one window's npz."""

    clusters: np.ndarray          # available N values (ints, ascending)
    ep_info: np.ndarray           # epoch_info rows for the window's epochs
    cc_data: np.ndarray           # window clean components (i + q/u rows)
    results: dict[int, dict]      # N -> raw result dict from the pipeline:
                                  #   cluster_epoch_df, labels, ref_epoch, ...

    @property
    def ref_epoch(self) -> float:
        first = next(iter(self.results.values()))
        return float(first["ref_epoch"])


def load_window_fit(npz_path: Path) -> WindowFit:
    p = Path(npz_path)
    return _load_window_fit_cached(str(p), p.stat().st_mtime_ns)


@lru_cache(maxsize=6)
def _load_window_fit_cached(path: str, _mtime_ns: int) -> WindowFit:
    with np.load(path, allow_pickle=True) as d:
        clusters = np.asarray(d["clusters"]).astype(int)
        results_arr = np.asarray(d["test_results"])
        ep_info = np.asarray(d["ep_info"])
        cc_data = np.asarray(d["data"])
    results = {int(n): r for n, r in zip(clusters, results_arr)}
    return WindowFit(clusters=clusters, ep_info=ep_info,
                     cc_data=cc_data, results=results)


def window_bundle(src: SourceRef, ref: WindowFitRef, n: int) -> SourceBundle:
    """Adapt one (window, N) fit into the SourceBundle shape that
    ``overlay_figure_for_epoch`` consumes. The window's ``cluster_epoch_df``
    plays the cluster_df, its ``labels`` the cc_labels — no plotting code
    changes needed."""
    wf = load_window_fit(ref.npz_path)
    n = int(n)
    if n not in wf.results:
        raise ValueError(f"N={n} not in window {ref.label} "
                         f"(available: {wf.clusters.tolist()})")
    result = wf.results[n]
    cluster_df = result["cluster_epoch_df"].copy()
    plotdata = PlotData(
        epoch_info=wf.ep_info,
        cc_data=wf.cc_data,
        cc_labels=np.asarray(result["labels"]),
        root_data_dir="",
    )
    model = ModelFile(
        key=f"window:{ref.label}:N{n}",
        label=f"window {ref.label} (N={n})",
        csv_path=ref.csv_path,
        npz_path=ref.npz_path,
    )
    return SourceBundle(
        source=src,
        model=model,
        cluster_df=cluster_df,
        plotdata=plotdata,
        csv_sha=f"window:{ref.label}:N{n}",
        reference_pdf=None,
        reference_mp4=None,
    )


# ---------------------------------------------------------------------------
# Per-window diagnostics (from the cheap CSVs)
# ---------------------------------------------------------------------------


def bic_table(csv_path: Path, complex_factor: float) -> pd.DataFrame | None:
    """BIC* vs N for one window, replicating the pipeline formula
    (``run_epoch_window_fits`` in cluster_code.py):

        bic* = ln(Ndata_est) * k + complex * Ndata_est * <d²> / <Σbeam²>

    Returns columns ``Nclusters``, ``bicstar``, ``ref_epoch`` — or None when
    the CSV is missing the ingredients (older pipeline versions without the
    ``k`` column)."""
    try:
        df = pd.read_csv(csv_path)
    except (OSError, ValueError):
        return None
    if "Nclusters" not in df.columns and "Ncluster" in df.columns:
        df = df.rename(columns={"Ncluster": "Nclusters"})
    needed = {"ID", "Nclusters", "k", "Ndata_mean_inoise_cut",
              "mean_dsqr", "mean_sum_beam_sqr", "epoch"}
    if not needed.issubset(df.columns):
        return None
    sub = df[df["ID"] == 0].copy()
    nd = sub["Ndata_mean_inoise_cut"]
    sub["bicstar"] = (np.log(nd) * sub["k"]
                      + complex_factor * nd * sub["mean_dsqr"]
                      / sub["mean_sum_beam_sqr"])
    out = sub[["Nclusters", "bicstar"]].copy()
    out["ref_epoch"] = float(sub["epoch"].iloc[0])
    return out.reset_index(drop=True)


def load_complex_factor(src_folder: Path) -> float:
    """The --complex factor the source was run with (config_win.json),
    needed to reproduce the pipeline's BIC* suggestion. 3.0 is the
    find_clusters.py default."""
    cfg = Path(src_folder) / "config_win.json"
    try:
        return float(json.loads(cfg.read_text()).get("complex", 3.0))
    except (OSError, ValueError, TypeError):
        return 3.0


@dataclass
class WindowMeta:
    """Cheap per-source summary driving the Window-N panel: one entry per
    window, all derived from the CSVs + the merged results CSV."""

    folder: str                   # source folder (str for dcc.Store round-trip)
    source: str
    labels: list[str]
    ref_epochs: list[float]
    bic_N: list[int | None]       # BIC* argmin per window (None: no diagnostics)
    cur_N: list[int | None]       # current model's N per window (None: no match)
    minN: int
    maxN: int
    complex_factor: float

    def to_store(self) -> dict:
        return {
            "folder": self.folder, "source": self.source,
            "labels": self.labels, "ref_epochs": self.ref_epochs,
            "bic_N": self.bic_N, "cur_N": self.cur_N,
            "minN": self.minN, "maxN": self.maxN,
            "complex_factor": self.complex_factor,
        }


def build_window_meta(src: SourceRef,
                      current_df: pd.DataFrame | None) -> WindowMeta | None:
    """Scan cluster_fits + the current merged CSV into a WindowMeta.
    Returns None when the source has no window fits on disk."""
    refs = list_window_fits(src.folder, src.source)
    if not refs:
        return None
    complex_factor = load_complex_factor(src.folder)

    labels: list[str] = []
    ref_epochs: list[float] = []
    bic_N: list[int | None] = []
    all_n: set[int] = set()
    for ref in refs:
        labels.append(ref.label)
        tab = bic_table(ref.csv_path, complex_factor)
        if tab is None or not len(tab):
            # Fall back to the npz for the ref epoch; no BIC suggestion.
            ref_epochs.append(load_window_fit(ref.npz_path).ref_epoch)
            bic_N.append(None)
            continue
        ref_epochs.append(float(tab["ref_epoch"].iloc[0]))
        bic_N.append(int(tab["Nclusters"].iloc[int(np.argmin(tab["bicstar"]))]))
        all_n.update(int(v) for v in tab["Nclusters"])

    minN = min(all_n) if all_n else 1
    maxN = max(all_n) if all_n else 16

    # Current model's N per window: the Nclusters column of the core row at
    # the window's reference (median) epoch — the same lookup the pipeline's
    # get_previous_Nclusters_labels does.
    cur_N: list[int | None] = [None] * len(refs)
    if current_df is not None and {"epoch", "clusterID",
                                   "Nclusters"}.issubset(current_df.columns):
        eps = current_df["epoch"].to_numpy(dtype=float)
        core = current_df["clusterID"].to_numpy() == 0
        for i, ref_ep in enumerate(ref_epochs):
            hit = np.isclose(eps, ref_ep, rtol=0.0, atol=1e-4) & core
            if np.any(hit):
                cur_N[i] = int(current_df.loc[hit, "Nclusters"].iloc[0])

    return WindowMeta(
        folder=str(src.folder), source=src.source,
        labels=labels, ref_epochs=ref_epochs,
        bic_N=bic_N, cur_N=cur_N,
        minN=minN, maxN=maxN, complex_factor=complex_factor,
    )


# ---------------------------------------------------------------------------
# nwin_choices.json store
# ---------------------------------------------------------------------------
#
# The on-disk schema is what find_clusters.py --N_win_file consumes
# (load_N_win_choices in cluster_code.py):
#
#   { "source": "0003-066u",
#     "updated_at": "...",
#     "model_sha": "<sha256 of the merged CSV the choices were made against>",
#     "choices": { "<first_ep>-<last_ep>": <N>,
#                  "<first_ep>-<last_ep>": {"N": <n>, "comment": "..."} } }
#
# Values are written as a bare int when there's no comment. Windows not
# listed keep their pipeline N. The directory name is deliberately
# stage-agnostic ("nwin_edits", not "stage1"/"stage2").


def nwin_choices_path(recommendations_dir: Path, source: str) -> Path:
    return Path(recommendations_dir) / source / "nwin_edits" / "nwin_choices.json"


def load_nwin_choices(path: Path) -> dict[str, dict]:
    """Normalized choices mapping: label -> {"N": int, "comment": str}.
    Empty dict when the file is missing or unreadable."""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    choices = data.get("choices", data) if isinstance(data, dict) else {}
    if not isinstance(choices, dict):
        return {}
    out: dict[str, dict] = {}
    for label, value in choices.items():
        try:
            if isinstance(value, dict):
                out[str(label)] = {"N": int(value["N"]),
                                   "comment": str(value.get("comment") or "")}
            else:
                out[str(label)] = {"N": int(value), "comment": ""}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def save_nwin_choices(path: Path, source: str, choices: dict[str, dict],
                      model_sha: str | None = None) -> None:
    """Write (or remove, when empty) the choices file. Atomic
    write-then-rename so a crashed save can't leave a torn file for
    --N_win_file to trip over."""
    p = Path(path)
    if not choices:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    serial: dict[str, object] = {}
    for label in sorted(choices):
        entry = choices[label]
        n = int(entry["N"])
        comment = str(entry.get("comment") or "")
        serial[label] = {"N": n, "comment": comment} if comment else n
    payload = {
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **({"model_sha": model_sha} if model_sha else {}),
        "choices": serial,
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(p)


# ---------------------------------------------------------------------------
# Rerun command
# ---------------------------------------------------------------------------

# Flags stripped from the recorded run string when composing the rerun:
# --editN is what the choices file replaces; the recalc family would throw
# away exactly the cached fits / cross-IDs the fast rerun relies on; and an
# older --N_win_file (with its value) is superseded by ours.
_DROP_BARE_FLAGS = {"--editN", "--show_results", "--recalc_all",
                    "--recalc_fits", "--recalc_N", "--recalc_IDs"}
_DROP_VALUE_FLAGS = {"--N_win_file"}


def build_rerun_command(src_folder: Path, choices_path: Path) -> str | None:
    """Compose the find_clusters.py rerun command from the source's
    run_string.txt, with --editN (and recalc flags) stripped and
    --N_win_file <choices> appended. None when run_string.txt is missing."""
    rs_path = Path(src_folder) / "run_string.txt"
    try:
        recorded = rs_path.read_text().strip()
    except OSError:
        return None
    if not recorded:
        return None
    tokens = shlex.split(recorded)
    out: list[str] = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok in _DROP_BARE_FLAGS:
            continue
        if tok in _DROP_VALUE_FLAGS:
            skip_next = True
            continue
        if any(tok.startswith(f + "=") for f in _DROP_VALUE_FLAGS):
            continue
        out.append(tok)
    out += ["--N_win_file", str(Path(choices_path).resolve())]
    return " ".join(shlex.quote(t) for t in out)
