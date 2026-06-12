"""Unit tests for data/window_fits.py (Window-N review data layer)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from mojave_review.data.window_fits import (
    bic_table, build_rerun_command, build_window_meta, global_window_extent,
    list_window_fits, load_nwin_choices, nwin_choices_path, save_nwin_choices,
)
from mojave_review.data.loader import SourceRef


SOURCE = "0003-066u"


def _window_csv(path, ref_epoch, n_values, bic_min_at, cent_x=0.0):
    """Synthetic per-window diagnostics CSV with the BIC* ingredients.
    mean_dsqr is shaped so the BIC* minimum lands at ``bic_min_at``.
    ``cent_x`` offsets the non-core cluster so the global-extent union
    can be tested across windows."""
    rows = []
    for n in n_values:
        rows.append(dict(
            ID=0, Nclusters=n, epoch=ref_epoch, k=4 * n + 10,
            Ndata_mean_inoise_cut=200.0,
            # decreasing misfit with N, kinked to put the optimum at bic_min_at
            mean_dsqr=1.0 / n if n <= bic_min_at else 1.0 / bic_min_at,
            mean_sum_beam_sqr=1.0,
            centX=0.0, centY=0.0, sizeMaj=0.2,
        ))
        # non-core rows must be ignored by bic_table
        rows.append(dict(ID=1, Nclusters=n, epoch=ref_epoch, k=999,
                         Ndata_mean_inoise_cut=1.0, mean_dsqr=99.0,
                         mean_sum_beam_sqr=1.0,
                         centX=cent_x, centY=1.0, sizeMaj=0.5))
    pd.DataFrame(rows).to_csv(path, index=False)


def _source_tree(tmp_path, labels_refs, complex_factor=3.0):
    """Lay out <tmp>/SRC_1994.00-2026.00/cluster_fits/* + config_win.json.
    ``labels_refs`` entries are (label, ref_epoch) or
    (label, ref_epoch, cent_x)."""
    folder = tmp_path / f"{SOURCE}_1994.00-2026.00"
    fits = folder / "cluster_fits"
    fits.mkdir(parents=True)
    for entry in labels_refs:
        label, ref_epoch = entry[0], entry[1]
        cent_x = entry[2] if len(entry) > 2 else 0.0
        base = fits / f"{SOURCE}.{label}"
        np.savez(str(base) + ".npz", placeholder=np.zeros(1))
        # NB: not base.with_suffix(".csv") — that would clobber the trailing
        # ".NN" of the epoch range
        _window_csv(fits / f"{SOURCE}.{label}.csv", ref_epoch,
                    n_values=range(1, 9), bic_min_at=4, cent_x=cent_x)
    (folder / "config_win.json").write_text(
        json.dumps({"complex": complex_factor}))
    return folder


def _src(folder):
    return SourceRef(source=SOURCE, epoch_min=1994.0, epoch_max=2026.0,
                     folder=folder)


# ---------------------------------------------------------------------------
# discovery + diagnostics
# ---------------------------------------------------------------------------

def test_list_window_fits_parses_and_sorts(tmp_path):
    folder = _source_tree(tmp_path, [("2001.83-2006.51", 2004.0),
                                     ("1995.57-2000.03", 1998.8)])
    refs = list_window_fits(folder, SOURCE)
    assert [r.label for r in refs] == ["1995.57-2000.03", "2001.83-2006.51"]
    assert refs[0].first_epoch == 1995.57
    assert refs[0].csv_path.name == f"{SOURCE}.1995.57-2000.03.csv"


def test_list_window_fits_missing_dir(tmp_path):
    assert list_window_fits(tmp_path / "nope", SOURCE) == []


def test_bic_table_minimum_and_core_rows_only(tmp_path):
    csv = tmp_path / "w.csv"
    _window_csv(csv, 1998.8, n_values=range(1, 9), bic_min_at=4)
    tab = bic_table(csv, complex_factor=3.0)
    assert tab is not None
    assert list(tab["Nclusters"]) == list(range(1, 9))
    # the non-core (ID=1) rows must not have leaked in
    assert len(tab) == 8
    assert float(tab["ref_epoch"].iloc[0]) == 1998.8
    n_best = int(tab["Nclusters"].iloc[int(np.argmin(tab["bicstar"]))])
    # past bic_min_at the misfit stops improving, so BIC* (which charges k
    # per cluster) must turn upward there
    assert n_best == 4


def test_bic_table_missing_ingredients(tmp_path):
    csv = tmp_path / "w.csv"
    pd.DataFrame([dict(ID=0, Nclusters=1)]).to_csv(csv, index=False)
    assert bic_table(csv, 3.0) is None


def test_build_window_meta(tmp_path):
    folder = _source_tree(tmp_path, [("1995.57-2000.03", 1998.8268),
                                     ("2001.83-2006.51", 2004.2200)])
    # current model: core rows at both ref epochs with Nclusters set
    current = pd.DataFrame([
        dict(epoch=1998.8268, clusterID=0, Nclusters=5),
        dict(epoch=1998.8268, clusterID=1, Nclusters=5),
        dict(epoch=2004.2200, clusterID=0, Nclusters=6),
        dict(epoch=2010.0000, clusterID=0, Nclusters=9),  # no matching window
    ])
    meta = build_window_meta(_src(folder), current)
    assert meta is not None
    assert meta.labels == ["1995.57-2000.03", "2001.83-2006.51"]
    assert meta.bic_N == [4, 4]
    assert meta.cur_N == [5, 6]
    assert meta.minN == 1 and meta.maxN == 8
    assert meta.complex_factor == 3.0
    # round-trips through the dcc.Store payload
    assert meta.to_store()["cur_N"] == [5, 6]


def test_global_window_extent_union(tmp_path):
    # window A's outermost cluster at x=-8, window B's at x=+3; the fixed
    # zoom box must cover both (positions ± 2*sizeMaj ± beam, then 5% pad)
    folder = _source_tree(tmp_path, [("1995.57-2000.03", 1998.8, -8.0),
                                     ("2001.83-2006.51", 2004.2, 3.0)])
    refs = list_window_fits(folder, SOURCE)
    extent = global_window_extent(refs, median_beam=1.0)
    assert extent is not None
    (x_lo, x_hi), (y_lo, y_hi) = extent
    # -8 - 2*0.5 - 1.5*1.0 = -10.5 before padding; +3 + 1 + 1.5 = 5.5
    assert x_lo < -10.5 < x_hi and x_lo < 5.5 < x_hi
    assert y_lo < -1.5 and y_hi > 3.5
    # meta carries it through the store payload
    current = pd.DataFrame([dict(epoch=1998.8, clusterID=0, Nclusters=5,
                                 bmaj=1.0)])
    meta = build_window_meta(_src(folder), current)
    assert meta.extent is not None
    assert meta.to_store()["extent"] == meta.extent


def test_global_window_extent_no_columns(tmp_path):
    folder = tmp_path / f"{SOURCE}_1994.00-2026.00"
    fits = folder / "cluster_fits"
    fits.mkdir(parents=True)
    np.savez(str(fits / f"{SOURCE}.2000.00-2002.00.npz"),
             placeholder=np.zeros(1))
    pd.DataFrame([dict(ID=0, Nclusters=1)]).to_csv(
        fits / f"{SOURCE}.2000.00-2002.00.csv", index=False)
    refs = list_window_fits(folder, SOURCE)
    assert global_window_extent(refs) is None


def test_build_window_meta_no_fits(tmp_path):
    folder = tmp_path / f"{SOURCE}_1994.00-2026.00"
    folder.mkdir()
    assert build_window_meta(_src(folder), None) is None


# ---------------------------------------------------------------------------
# choices store
# ---------------------------------------------------------------------------

def test_choices_round_trip(tmp_path):
    path = nwin_choices_path(tmp_path, SOURCE)
    choices = {"1995.57-2000.03": {"N": 6, "comment": "split the inner jet"},
               "2001.83-2006.51": {"N": 4, "comment": ""}}
    save_nwin_choices(path, SOURCE, choices, model_sha="abc123")
    on_disk = json.loads(path.read_text())
    assert on_disk["source"] == SOURCE
    assert on_disk["model_sha"] == "abc123"
    # comment-less entries are written as bare ints (the compact form
    # find_clusters.py --N_win_file also accepts)
    assert on_disk["choices"]["2001.83-2006.51"] == 4
    assert on_disk["choices"]["1995.57-2000.03"]["N"] == 6
    assert load_nwin_choices(path) == choices


def test_choices_accepts_bare_mapping(tmp_path):
    path = tmp_path / "bare.json"
    path.write_text(json.dumps({"1995.57-2000.03": 7}))
    assert load_nwin_choices(path) == {
        "1995.57-2000.03": {"N": 7, "comment": ""}}


def test_save_empty_choices_removes_file(tmp_path):
    path = nwin_choices_path(tmp_path, SOURCE)
    save_nwin_choices(path, SOURCE, {"a-b": {"N": 3, "comment": ""}})
    assert path.is_file()
    save_nwin_choices(path, SOURCE, {})
    assert not path.exists()


def test_load_choices_missing_or_bad(tmp_path):
    assert load_nwin_choices(tmp_path / "missing.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_nwin_choices(bad) == {}


# ---------------------------------------------------------------------------
# rerun command
# ---------------------------------------------------------------------------

def test_build_rerun_command_strips_and_appends(tmp_path):
    folder = tmp_path / f"{SOURCE}_1994.00-2026.00"
    folder.mkdir()
    (folder / "run_string.txt").write_text(
        "python find_clusters.py 0003-066 --band u --results_dir ./Results/ "
        "--editN --recalc_IDs --N_win_file /old/choices.json --complex 3\n")
    choices = tmp_path / "nwin_choices.json"
    cmd = build_rerun_command(folder, choices)
    assert cmd is not None
    assert "--editN" not in cmd
    assert "--recalc_IDs" not in cmd
    assert "/old/choices.json" not in cmd
    assert cmd.count("--N_win_file") == 1
    assert cmd.endswith(f"--N_win_file {choices.resolve()}")
    assert cmd.startswith("python find_clusters.py 0003-066 --band u")


def test_build_rerun_command_missing_run_string(tmp_path):
    assert build_rerun_command(tmp_path, tmp_path / "c.json") is None
