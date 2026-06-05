"""prune_applied_current_drafts: remove only current/ drafts that duplicate an
already-applied recommendation (content-equal, ignoring timestamps/model_sha)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from mojave_review.recommendations.store import prune_applied_current_drafts


def _write(p: Path, rec: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec))


def _rec(reviewer: str, *, comment: str, updated: str, sha: str) -> dict:
    return {
        "source": "0003-066u", "model": "current", "reviewer": reviewer,
        "model_sha": sha, "updated_at": updated, "source_comment": comment,
        "no_robustness_changes": False, "cluster_feedback": {},
        "epoch_feedback": {}, "edits": [],
    }


def _setup() -> Path:
    recs = Path(tempfile.mkdtemp())
    s = recs / "0003-066u"
    # alice: current draft content-equal to her applied rec (only ts/sha differ)
    _write(s / "applied" / "2026-06-02__alice.json",
           _rec("alice", comment="looks good", updated="2026-06-02T00:00:00", sha="OLD"))
    _write(s / "current" / "alice.json",
           _rec("alice", comment="looks good", updated="2026-06-03T00:00:00", sha="NEW"))
    # bob: current draft DIFFERS from any applied (he kept editing) -> keep
    _write(s / "applied" / "2026-06-02__bob.json",
           _rec("bob", comment="v1", updated="2026-06-02T00:00:00", sha="OLD"))
    _write(s / "current" / "bob.json",
           _rec("bob", comment="v2 — newer edits", updated="2026-06-03T00:00:00", sha="NEW"))
    return recs


def test_dry_run_lists_only_duplicates_and_deletes_nothing():
    recs = _setup()
    pruned = prune_applied_current_drafts(recs, execute=False)
    assert pruned == [("0003-066u", "alice")]
    # nothing deleted on a dry run
    assert (recs / "0003-066u" / "current" / "alice.json").exists()
    assert (recs / "0003-066u" / "current" / "bob.json").exists()


def test_execute_removes_only_the_duplicate():
    recs = _setup()
    pruned = prune_applied_current_drafts(recs, execute=True)
    assert pruned == [("0003-066u", "alice")]
    assert not (recs / "0003-066u" / "current" / "alice.json").exists()  # removed
    assert (recs / "0003-066u" / "current" / "bob.json").exists()        # kept


if __name__ == "__main__":
    test_dry_run_lists_only_duplicates_and_deletes_nothing()
    test_execute_removes_only_the_duplicate()
    print("PASS: prune_applied_current_drafts")
