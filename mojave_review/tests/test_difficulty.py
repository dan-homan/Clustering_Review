"""mojave-review-difficulty: per-source score + CLI table."""

from __future__ import annotations

import json
import pandas as pd

from mojave_review.cli.difficulty import main
from mojave_review.data.difficulty import (
    difficulty_from_df, score_all, score_source, star_ratings,
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


def test_score_source_and_score_all(tmp_path):
    _write_source(tmp_path, "0003-066u_1994.00-2026.00",
                  _real_cluster_rows([2000.0, 2001.0], [2, 3]))
    _write_source(tmp_path, "0415+379u_1994.00-2026.00",
                  _real_cluster_rows([2000.0, 2001.0, 2002.0, 2003.0], [5, 6, 7, 8]))
    sources = list_sources(tmp_path)
    by_folder = {s.folder.name: score_source(s) for s in sources}
    light = by_folder["0003-066u_1994.00-2026.00"]
    heavy = by_folder["0415+379u_1994.00-2026.00"]
    assert light.n_epochs == 2 and light.mean_features == 2.5
    assert heavy.n_epochs == 4 and heavy.mean_features == 6.5
    assert heavy.score > light.score
    # score_all returns one entry per loadable source.
    assert len(score_all(sources)) == 2


def test_star_ratings_distinct_population():
    # 10 distinct scores ⇒ five quintile bins ⇒ ratings span 1..5.
    scores = [float(i) for i in range(1, 11)]
    rated = star_ratings(scores)
    assert set(rated) == {1, 2, 3, 4, 5}
    assert rated[0] == 1 and rated[-1] == 5


def test_star_ratings_degenerate():
    assert star_ratings([]) == []
    assert star_ratings([7.0, 7.0, 7.0]) == [1, 1, 1]


def test_cli_table_and_json(tmp_path, capsys):
    _write_source(tmp_path, "0003-066u_1994.00-2026.00",
                  _real_cluster_rows([2000.0, 2001.0], [2, 3]))
    _write_source(tmp_path, "0415+379u_1994.00-2026.00",
                  _real_cluster_rows([2000.0, 2001.0, 2002.0, 2003.0], [5, 6, 7, 8]))

    rc = main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    # Default sort: score descending — the heavier source comes first.
    heavy_idx = out.find("0415+379u")
    light_idx = out.find("0003-066u")
    assert 0 < heavy_idx < light_idx
    assert "score" in out and "rating" in out

    rc = main(["--results-dir", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert {r["source"] for r in payload} == {"0003-066u", "0415+379u"}
    assert all("rating" in r and "score" in r for r in payload)


def test_cli_results_dir_missing(tmp_path, capsys):
    rc = main(["--results-dir", str(tmp_path / "does-not-exist")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "results dir not found" in err
