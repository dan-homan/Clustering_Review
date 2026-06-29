"""mojave-review-difficulty: per-source score + CLI table."""

from __future__ import annotations

import json
import math
import pandas as pd

from mojave_review.cli.difficulty import main
from mojave_review.data.difficulty import (
    balance_weight_for, difficulty_from_df, is_outlier, score_all,
    score_source, score_stats, stars_for,
)
from mojave_review.data.loader import list_sources


def _write_source(results_dir, folder_name: str, rows: list[dict]) -> None:
    folder = results_dir / folder_name
    folder.mkdir(parents=True)
    src_name, _, eprange = folder_name.partition("_")
    csv = folder / f"{src_name}.{eprange}.merged_win_results.csv"
    pd.DataFrame(rows).to_csv(csv, index=False)


def _real_cluster_rows(epochs: list[float], cids_per_epoch: list[int]) -> list[dict]:
    """One row per (epoch, clusterID); origID == clusterID for simplicity."""
    out = []
    for ep, n in zip(epochs, cids_per_epoch):
        for cid in range(n):
            out.append(dict(epoch=ep, clusterID=cid, origID=cid))
    return out


def test_difficulty_from_df_excludes_unassigned_and_synthetic():
    rows = _real_cluster_rows([2000.0, 2001.0, 2002.0], [3, 4, 5])
    # Add unassigned bookkeeping (-1) and synthetic (>=1000) rows; must be ignored.
    rows += [
        dict(epoch=2000.0, clusterID=-1, origID=-1),
        dict(epoch=2001.0, clusterID=1000, origID=1000),
    ]
    n_ep, mean_f, score = difficulty_from_df(pd.DataFrame(rows))
    assert n_ep == 3
    assert mean_f == (3 + 4 + 5) / 3
    assert score == 3 * mean_f


def test_difficulty_from_df_empty():
    df = pd.DataFrame([dict(epoch=2000.0, clusterID=-1, origID=-1)])
    assert difficulty_from_df(df) == (0, 0.0, 0.0)


def test_score_source_carries_derived_fields(tmp_path):
    _write_source(tmp_path, "0415+379u_1994.00-2026.00",
                  _real_cluster_rows([2000.0 + i for i in range(20)], [10] * 20))
    src = list_sources(tmp_path)[0]
    d = score_source(src)
    assert d.n_epochs == 20 and d.mean_features == 10
    assert d.score == 200.0
    assert d.balance_weight == math.sqrt(200.0)
    assert d.stars == 4               # 100 <= 200 < 250
    assert d.outlier is False


def test_score_all_skips_unreadable(tmp_path):
    _write_source(tmp_path, "0003-066u_1994.00-2026.00",
                  _real_cluster_rows([2000.0, 2001.0], [2, 3]))
    # A second folder with no CSV at all — must be silently skipped.
    (tmp_path / "9999+999u_1994.00-2026.00").mkdir()
    sources = list_sources(tmp_path)
    assert len(sources) == 2
    assert len(score_all(sources)) == 1


def test_stars_for_absolute_cutoffs():
    # Boundary check at each edge: < edge → lower bucket; >= edge → next.
    assert stars_for(0) == 1
    assert stars_for(19.999) == 1
    assert stars_for(20.0) == 2
    assert stars_for(49.999) == 2
    assert stars_for(50.0) == 3
    assert stars_for(99.999) == 3
    assert stars_for(100.0) == 4
    assert stars_for(249.999) == 4
    assert stars_for(250.0) == 5
    assert stars_for(9999.0) == 5


def test_is_outlier_at_500():
    assert is_outlier(499.999) is False
    assert is_outlier(500.0) is True
    assert is_outlier(2343.0) is True


def test_balance_weight_compresses_tail():
    # The whole point: BL Lac-like score ~2343 must NOT be ~50× a median
    # source under the balance weight.
    bw_monster = balance_weight_for(2343.0)
    bw_median = balance_weight_for(50.0)
    assert bw_monster == math.sqrt(2343.0)
    assert bw_median == math.sqrt(50.0)
    assert bw_monster / bw_median < 10        # sqrt compresses 47× → ~6.8×


def test_score_stats_distribution():
    scores = [5.0, 25.0, 60.0, 150.0, 300.0, 700.0]
    s = score_stats(scores)
    assert s["count"] == 6
    assert s["max"] == 700.0
    assert s["median"] == 105.0                # mean of 60 and 150
    assert s["p25"] < s["median"] < s["p75"]
    # Star bucket counts: one of each tier.
    assert s["by_star"] == {1: 1, 2: 1, 3: 1, 4: 1, 5: 2}
    assert s["n_outliers"] == 1                # only 700 >= 500


def test_score_stats_degenerate():
    assert score_stats([])["count"] == 0
    one = score_stats([42.0])
    assert one["count"] == 1 and one["median"] == 42.0 and one["max"] == 42.0


def test_cli_table_and_json(tmp_path, capsys):
    _write_source(tmp_path, "0003-066u_1994.00-2026.00",
                  _real_cluster_rows([2000.0, 2001.0], [2, 3]))
    _write_source(tmp_path, "0415+379u_1994.00-2026.00",
                  _real_cluster_rows([2000.0 + i for i in range(30)], [20] * 30))

    rc = main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    # Default sort: score descending — the heavier source comes first.
    heavy_idx = out.find("0415+379u")
    light_idx = out.find("0003-066u")
    assert 0 < heavy_idx < light_idx
    # New columns + footer must be present.
    assert "bal_w" in out and "rating" in out
    assert "— Population:" in out and "median=" in out and "stars:" in out
    # The 30×20=600 source crosses the outlier line, so ⚠ must appear.
    assert "⚠" in out

    rc = main(["--results-dir", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert {r["source"] for r in payload["sources"]} == {"0003-066u", "0415+379u"}
    fields = {"score", "stars", "outlier", "balance_weight"}
    assert all(fields <= set(r) for r in payload["sources"])
    assert "stats" in payload and "by_star" in payload["stats"]


def test_cli_results_dir_missing(tmp_path, capsys):
    rc = main(["--results-dir", str(tmp_path / "does-not-exist")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "results dir not found" in err
