"""recommendations.store: reviewer-wide lifetime counts used by the
dashboard My-queue summary (submitted / in-progress, assigned or not)."""

from __future__ import annotations

from mojave_review.recommendations.schema import Recommendation
from mojave_review.recommendations.store import (
    archive_considered_submissions, reviewer_in_progress_sources,
    reviewer_submitted_sources, save_recommendation, save_submitted,
)


def _submit(recs, source, reviewer):
    save_submitted(
        recs,
        Recommendation(source=source, model="current", reviewer=reviewer,
                       source_comment="ok"),
    )


def _draft(recs, source, reviewer, *, empty):
    rec = Recommendation(source=source, model="current", reviewer=reviewer)
    if not empty:
        rec.source_comment = "wip"
    save_recommendation(recs, rec)


def test_submitted_sources_counts_open_submissions(tmp_path):
    _submit(tmp_path, "0003-066u", "Alice")
    _submit(tmp_path, "0007+106u", "Alice")
    _submit(tmp_path, "0007+106u", "Bob")
    assert reviewer_submitted_sources(tmp_path, "Alice") == {
        "0003-066u", "0007+106u"}
    assert reviewer_submitted_sources(tmp_path, "Bob") == {"0007+106u"}


def test_submitted_sources_includes_considered_archive(tmp_path):
    # A submission folded into Stage 3 moves to considered/<date>/ and must
    # still count toward the lifetime "reviews submitted" total.
    _submit(tmp_path, "0003-066u", "Alice")
    archive_considered_submissions(
        tmp_path, "0003-066u", ["alice"], date="2026-06-10")
    assert not (tmp_path / "0003-066u" / "submitted" / "alice.json").is_file()
    assert reviewer_submitted_sources(tmp_path, "Alice") == {"0003-066u"}


def test_submitted_sources_matches_collision_renamed_archive(tmp_path):
    # A second-round archive collides and becomes alice_2.json — still hers.
    _submit(tmp_path, "0003-066u", "Alice")
    archive_considered_submissions(
        tmp_path, "0003-066u", ["alice"], date="2026-06-10")
    _submit(tmp_path, "0003-066u", "Alice")
    archive_considered_submissions(
        tmp_path, "0003-066u", ["alice"], date="2026-06-10")
    assert (tmp_path / "0003-066u" / "considered" / "2026-06-10"
            / "alice_2.json").is_file()
    assert reviewer_submitted_sources(tmp_path, "Alice") == {"0003-066u"}


def test_submitted_sources_skips_admin_dir(tmp_path):
    # The _admin/ assignments dir is not a source — must never be counted.
    (tmp_path / "_admin").mkdir()
    (tmp_path / "_admin" / "assignments.json").write_text("{}")
    assert reviewer_submitted_sources(tmp_path, "Alice") == set()


def test_in_progress_sources_excludes_submitted_and_empty(tmp_path):
    _draft(tmp_path, "0003-066u", "Alice", empty=False)   # counts
    _draft(tmp_path, "0007+106u", "Alice", empty=True)    # empty → no
    _draft(tmp_path, "0010+405u", "Alice", empty=False)
    _submit(tmp_path, "0010+405u", "Alice")               # submitted wins → no
    assert reviewer_in_progress_sources(tmp_path, "Alice") == {"0003-066u"}


def test_counts_empty_when_dir_missing(tmp_path):
    missing = tmp_path / "nope"
    assert reviewer_submitted_sources(missing, "Alice") == set()
    assert reviewer_in_progress_sources(missing, "Alice") == set()


def test_drafting_by_slug_lists_nonempty_drafts(tmp_path):
    from mojave_review.recommendations.store import drafting_by_slug
    _draft(tmp_path, "A", "alice", empty=False)
    _draft(tmp_path, "B", "alice", empty=True)     # empty → excluded
    _draft(tmp_path, "A", "bob", empty=False)
    d = drafting_by_slug(tmp_path, ["A", "B"])
    assert d.get("alice") == {"A"}
    assert d.get("bob") == {"A"}


def test_fold_collision_names_drops_artifacts():
    from mojave_review.ui.dashboard import _fold_collision_names
    # <base>_<N> folds away when <base> is present...
    assert _fold_collision_names({"homand", "homand_2", "alice"}) == {
        "homand", "alice"}
    # ...but a standalone foo_2 with no "foo" survives.
    assert _fold_collision_names({"foo_2"}) == {"foo_2"}
