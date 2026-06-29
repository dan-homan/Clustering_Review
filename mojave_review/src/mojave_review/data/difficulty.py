"""Per-source review-difficulty score.

The score estimates the reviewer load of one source from the saved
``merged_win_results.csv`` alone (no NPZ, no FITS) — cheap enough to
rescore every source on demand.

Formula:

    score          = n_epochs * mean(features_per_epoch)
    balance_weight = sqrt(score)
    stars          = absolute cutoff (see _STAR_CUTOFFS below)
    outlier        = score >= _OUTLIER_CUTOFF

where ``features_per_epoch`` is the count of *fitted* clusters at each
epoch — i.e. distinct ``clusterID`` values in the CSV, excluding
``clusterID == -1`` (unassigned bookkeeping rows) and
``clusterID >= 1000`` (synthetic). The core (``clusterID == 0``) counts
as a feature.

Two derived quantities serve different purposes:

* ``score`` and ``stars`` are the **display** numbers — they answer
  "how heavy is this source to review?" and they sort meaningfully.
* ``balance_weight`` is what the auto-balance algorithm in a later
  phase will use — sqrt compresses the right tail so one outlier
  (BL Lac at score 2343) doesn't consume a reviewer's whole quota.

Star cutoffs are absolute (not quintiles), so a source's rating does
not shift when the population changes. The ⚠ outlier flag is for
the small handful of very-well-monitored sources (BL Lac, 3C273,
3C279, …) whose review load is several × a typical ★★★★★.
"""

from __future__ import annotations

import collections
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .loader import SourceRef


# Absolute star cutoffs: score < edge[i] => i+1 stars; >= last edge => 5 stars.
_STAR_CUTOFFS: tuple[float, ...] = (20.0, 50.0, 100.0, 250.0)
# A separate "outlier" flag for the heaviest-tail sources.
_OUTLIER_CUTOFF: float = 500.0


@dataclass(frozen=True)
class SourceDifficulty:
    source: str           # band-suffixed name, e.g. "0003-066u"
    folder: str           # source folder name, joins back to SourceRef
    n_epochs: int
    mean_features: float
    score: float
    balance_weight: float  # sqrt(score) — for auto-balance, not display
    stars: int             # 1..5, absolute cutoff
    outlier: bool          # score >= _OUTLIER_CUTOFF


def stars_for(score: float) -> int:
    for i, edge in enumerate(_STAR_CUTOFFS):
        if score < edge:
            return i + 1
    return len(_STAR_CUTOFFS) + 1


def is_outlier(score: float) -> bool:
    return score >= _OUTLIER_CUTOFF


def balance_weight_for(score: float) -> float:
    return math.sqrt(max(score, 0.0))


def _csv_path(src: SourceRef) -> Path:
    return src.folder / f"{src.file_prefix}.merged_win_results.csv"


def difficulty_from_df(df: pd.DataFrame) -> tuple[int, float, float]:
    """(n_epochs, mean_features, score) from a loaded cluster_df."""
    real = df[(df["clusterID"] >= 0) & (df["clusterID"] < 1000)]
    if real.empty:
        return 0, 0.0, 0.0
    features_by_epoch = real.groupby("epoch")["clusterID"].nunique()
    n_epochs = int(features_by_epoch.size)
    mean_features = float(features_by_epoch.mean())
    return n_epochs, mean_features, float(n_epochs * mean_features)


def score_source(src: SourceRef) -> SourceDifficulty:
    """Read the source's current-model CSV and score it.

    Raises ``FileNotFoundError`` if the CSV isn't on disk.
    """
    df = pd.read_csv(_csv_path(src))
    n_epochs, mean_features, score = difficulty_from_df(df)
    return SourceDifficulty(
        source=src.source,
        folder=src.folder.name,
        n_epochs=n_epochs,
        mean_features=mean_features,
        score=score,
        balance_weight=balance_weight_for(score),
        stars=stars_for(score),
        outlier=is_outlier(score),
    )


def score_all(sources: list[SourceRef]) -> list[SourceDifficulty]:
    """Score every source, skipping any whose CSV can't be read."""
    out: list[SourceDifficulty] = []
    for src in sources:
        try:
            out.append(score_source(src))
        except (FileNotFoundError, pd.errors.EmptyDataError):
            continue
    return out


def score_stats(scores: list[float]) -> dict:
    """Summary distribution of a population of scores.

    Returns a dict with: ``count``, ``median``, ``max``, ``p25``,
    ``p75``, ``by_star`` (mapping star-count → number of sources),
    and ``n_outliers``. ``p25``/``p75`` are present whenever
    len(scores) >= 2; for one-element populations median == max.
    Empty input returns a stable dict with zero counts.
    """
    by_star: collections.Counter = collections.Counter()
    n_outliers = 0
    for s in scores:
        by_star[stars_for(s)] += 1
        if is_outlier(s):
            n_outliers += 1
    out: dict = {
        "count": len(scores),
        "by_star": dict(by_star),
        "n_outliers": n_outliers,
    }
    if not scores:
        return out
    out["max"] = max(scores)
    out["median"] = statistics.median(scores)
    if len(scores) >= 2:
        q = statistics.quantiles(scores, n=4)
        out["p25"], out["p75"] = q[0], q[2]
    return out
