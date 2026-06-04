"""The no-op detection that drives mojave-apply's fast path.

A recommendation with no edits and no robustness changes must leave the CSV
byte-identical, so ``new_df.equals(existing.df)`` is True and apply takes the
'concluded — no changes' path (no backup / rewrite / plot regen). The end-to-end
behaviour (history line + archive, real changes taking the normal path) is
covered by manual verification against a real submission.
"""

from __future__ import annotations

import pandas as pd

from mojave_review.cli.apply import _apply_to_csv
from mojave_review.recommendations.schema import Recommendation


def _minimal_df() -> pd.DataFrame:
    return pd.DataFrame([
        dict(epoch=2000.0, clusterID=0, origID=0, robust=True, use_in_fit=True),
        dict(epoch=2000.0, clusterID=1, origID=1, robust=True, use_in_fit=True),
        dict(epoch=2001.0, clusterID=1, origID=1, robust=True, use_in_fit=True),
    ])


def test_empty_recommendation_is_noop():
    df = _minimal_df()
    rec = Recommendation(source="x", model="current", reviewer="tester")
    new_df, edit_history = _apply_to_csv(df, rec)
    assert edit_history == []
    assert new_df.equals(df)        # → no_op == True in main()


def test_recommendation_with_only_a_comment_is_noop():
    # A submission that just leaves a source comment (no edits, no robust
    # changes) must still be a no-op on disk.
    df = _minimal_df()
    rec = Recommendation(source="x", model="current", reviewer="tester",
                         source_comment="Looks good — no changes.")
    new_df, edit_history = _apply_to_csv(df, rec)
    assert edit_history == []
    assert new_df.equals(df)


if __name__ == "__main__":
    test_empty_recommendation_is_noop()
    test_recommendation_with_only_a_comment_is_noop()
    print("PASS: mojave-apply no-op detection")
