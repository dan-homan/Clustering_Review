"""_backup_existing's plot handling now that the pipeline makes the PDF/MP4
opt-in (find_clusters.py --make_plots).

* regen path (move_plots=False, the historical behaviour): PDF/MP4 are
  COPIED into the backup and stay in place to be overwritten.
* skip path (move_plots=True): PDF/MP4 are MOVED, so a stale render can't
  sit next to the new CSV.
* plot-less source: nothing to do, no error.
"""

from __future__ import annotations

from mojave_review.cli.apply import _backup_existing

PREFIX = "0003-066u.1994.00-2026.00"


def _source_folder(tmp_path, with_plots=True):
    folder = tmp_path / "0003-066u_1994.00-2026.00"
    folder.mkdir()
    (folder / f"{PREFIX}.merged_win_results.csv").write_text("csv")
    (folder / "config_win.json").write_text("{}")
    (folder / "run_string.txt").write_text("python find_clusters.py ...")
    if with_plots:
        (folder / f"{PREFIX}.summary_plots.pdf").write_text("pdf")
        (folder / f"{PREFIX}.epoch_overplots.mp4").write_text("mp4")
    return folder


def test_backup_copies_plots_when_regenerating(tmp_path):
    folder = _source_folder(tmp_path)
    backups = _backup_existing(folder, PREFIX, "001", move_plots=False)
    # CSV is always renamed away; plots stay (they'll be overwritten by regen)
    assert not (folder / f"{PREFIX}.merged_win_results.csv").exists()
    assert (folder / f"{PREFIX}.summary_plots.pdf").is_file()
    assert (folder / f"{PREFIX}.epoch_overplots.mp4").is_file()
    assert (backups / "backup_001_summary_plots.pdf").is_file()
    assert (backups / "backup_001_epoch_overplots.mp4").is_file()


def test_backup_moves_plots_when_skipping_regen(tmp_path):
    folder = _source_folder(tmp_path)
    backups = _backup_existing(folder, PREFIX, "002", move_plots=True)
    # plots must be GONE from the live folder, present only in the backup
    assert not (folder / f"{PREFIX}.summary_plots.pdf").exists()
    assert not (folder / f"{PREFIX}.epoch_overplots.mp4").exists()
    assert (backups / "backup_002_summary_plots.pdf").is_file()
    assert (backups / "backup_002_epoch_overplots.mp4").is_file()
    # config / run_string are still copies (originals stay)
    assert (folder / "config_win.json").is_file()
    assert (folder / "run_string.txt").is_file()


def test_backup_plotless_source(tmp_path):
    folder = _source_folder(tmp_path, with_plots=False)
    backups = _backup_existing(folder, PREFIX, "003", move_plots=True)
    assert (backups / "backup_003_merged_win_results.csv").is_file()
    assert not (backups / "backup_003_summary_plots.pdf").exists()
