"""Per-source review-difficulty score.

The score estimates the reviewer load of one source from the saved
``merged_win_results.csv`` alone (no NPZ, no FITS) — cheap enough to
rescore every source on demand.

Formula:

    score = n_epochs * mean(features_per_epoch)

where ``features_per_epoch`` is the count of *fitted* clusters at each
epoch — i.e. distinct ``clusterID`` values in the CSV, excluding
``clusterID == -1`` (unassigned bookkeeping rows) and
``clusterID >= 1000`` (synthetic). The core (``clusterID == 0``) counts
as a feature.

Star ratings (★ to ★★★★★) are quintile buckets across whatever
population of scores the caller passes in. They have no meaning outside
that population.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .loader import SourceRef


@dataclass(frozen=True)
class SourceDifficulty:
    source: str           # band-suffixed name, e.g. "0003-066u"
    folder: str           # source folder name, joins back to SourceRef
    n_epochs: int
    mean_features: float
    score: float


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


def star_ratings(scores: list[float]) -> list[int]:
    """Quintile-bucket each score into 1-5 (★ to ★★★★★).

    Ties are resolved by the caller's existing input order — pandas
    ``qcut`` with ``duplicates='drop'`` collapses bins that share an
    edge, so very flat populations get fewer than 5 levels. We then
    renormalize so the returned values still run 1..k contiguously.
    Empty input ⇒ empty output.
    """
    if not scores:
        return []
    if len(set(scores)) == 1:
        # Every source has the same score (degenerate, e.g. one source).
        return [1] * len(scores)
    bins = pd.qcut(scores, q=5, labels=False, duplicates="drop")
    # bins is a 0-indexed array of bucket numbers, possibly with < 5 levels.
    return [int(b) + 1 for b in bins]
