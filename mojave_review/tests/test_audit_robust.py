"""mojave-review-audit-robust: dry-run reports, --apply repairs + backs up.

Two defect classes: a flag inconsistent across a cluster's epochs, and a flag
that's robust on a cluster with too few use_in_fit epochs to be eligible.
"""

from __future__ import annotations

import pandas as pd

from mojave_review.cli.audit_robust import main


def _make_source(results_dir, robust_by_epoch, extra_rows=()):
    folder = results_dir / "0003-066u_1994.00-2026.00"
    folder.mkdir(parents=True)
    rows = []
    for i, rob in enumerate(robust_by_epoch):
        ep = 2000.0 + i
        rows.append(dict(epoch=ep, clusterID=0, origID=0, robust=True,
                         use_in_fit=True))
        rows.append(dict(epoch=ep, clusterID=1, origID=1, robust=rob,
                         use_in_fit=True))
    rows.extend(extra_rows)
    csv = folder / "0003-066u.1994.00-2026.00.merged_win_results.csv"
    pd.DataFrame(rows).to_csv(csv, index=False)
    return folder, csv


def test_dryrun_reports_without_modifying(tmp_path, capsys):
    folder, csv = _make_source(tmp_path, [True, False, True, True, True, True])
    before = csv.read_text()
    rc = main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cluster 1" in out and "inconsistent" in out
    assert "dry run" in out
    assert csv.read_text() == before          # unchanged
    assert not (folder / "backups").exists()


def test_apply_repairs_and_backs_up(tmp_path):
    folder, csv = _make_source(tmp_path, [True, False, True, True, True, True])
    rc = main(["--results-dir", str(tmp_path), "--apply"])
    assert rc == 0
    df = pd.read_csv(csv)
    assert df.loc[df["clusterID"] == 1, "robust"].nunique() == 1
    assert bool(df.loc[df["clusterID"] == 1, "robust"].iloc[0]) is True
    # prior CSV backed up + repair logged
    assert (folder / "backups" / "backup_001_merged_win_results.csv").is_file()
    assert "robust-flag repair" in (folder / "history.txt").read_text()


def test_core_forced_true(tmp_path):
    # An inconsistent core normalizes to True regardless of earliest value.
    folder, csv = _make_source(tmp_path, [True] * 6)
    df = pd.read_csv(csv)
    df.loc[(df["clusterID"] == 0) & (df["epoch"] == 2000.0), "robust"] = False
    df.to_csv(csv, index=False)
    main(["--results-dir", str(tmp_path), "--apply"])
    df = pd.read_csv(csv)
    core = df.loc[df["clusterID"] == 0, "robust"]
    assert core.nunique() == 1 and bool(core.iloc[0]) is True


# --- robust-but-ineligible (< 5 use_in_fit=True epochs) --------------------

def _thin_robust(cid=9, n=3, use_in_fit=True):
    return [dict(epoch=2100.0 + i, clusterID=cid, origID=cid, robust=True,
                 use_in_fit=use_in_fit) for i in range(n)]


def test_dryrun_reports_thin_robust_cluster(tmp_path, capsys):
    # Consistent flags everywhere — the old audit saw nothing here.
    folder, csv = _make_source(tmp_path, [True] * 6, _thin_robust())
    before = csv.read_text()
    rc = main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cluster 9" in out and "3 use_in_fit=True epoch(s)" in out
    assert "robust-but-ineligible" in out
    assert csv.read_text() == before          # unchanged
    assert not (folder / "backups").exists()


def test_apply_demotes_thin_robust_cluster(tmp_path):
    folder, csv = _make_source(tmp_path, [True] * 6, _thin_robust())
    assert main(["--results-dir", str(tmp_path), "--apply"]) == 0
    df = pd.read_csv(csv)
    assert not df.loc[df["clusterID"] == 9, "robust"].any()
    assert df.loc[df["clusterID"] == 1, "robust"].all()       # eligible, kept
    assert (folder / "backups" / "backup_001_merged_win_results.csv").is_file()
    assert "3 use_in_fit rows < 5" in (folder / "history.txt").read_text()


def test_apply_demotes_parked_cluster(tmp_path):
    # The 0415+379u case: one parked row carrying the core's robust flag.
    folder, csv = _make_source(
        tmp_path, [True] * 6, _thin_robust(cid=999, n=1, use_in_fit=False))
    assert main(["--results-dir", str(tmp_path), "--apply"]) == 0
    df = pd.read_csv(csv)
    assert not df.loc[df["clusterID"] == 999, "robust"].any()


def test_clean_source_is_a_noop(tmp_path, capsys):
    folder, csv = _make_source(tmp_path, [True] * 6)
    before = csv.read_text()
    assert main(["--results-dir", str(tmp_path)]) == 0
    assert "dry run" not in capsys.readouterr().out
    assert csv.read_text() == before
