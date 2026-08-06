"""Reviewer comments must reach notes/<source>.md however a Stage-3 round ends.

Regression: the apply-with-decisions path (``stage3_ledger_entry``) recorded
none of them, and since applying archives the submissions out of ``submitted/``
the prose left the app entirely. Covers the shared comment block on all Stage-3
ledger renderers, the "skip what's already recorded" filter, and the
``mojave-review-audit-notes`` recovery of comments lost before the fix.
"""

from __future__ import annotations

import json
from pathlib import Path

from mojave_review.notes import store
from mojave_review.notes.audit import (
    apply_recovery, audit_all, audit_source, builder_comments_missing,
    submission_files,
)
from mojave_review.notes.render import (
    CommentEntry, comment_entries, comment_lines, unrecorded_entries,
    unrecorded_notes_seed,
)
from mojave_review.recommendations.aggregate import (
    build_aggregation_view, stage3_ledger_entry, stage3_no_change_ledger_entry,
)
from mojave_review.recommendations.schema import Recommendation

COMMENTS = "- (Alice) cl 3: merges with 4\n- (Bob) source: core wobbles"


def _rec_dict(reviewer: str, **kw) -> dict:
    d = {"source": "0003-066u", "model": "current", "reviewer": reviewer}
    d.update(kw)
    return d


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _view():
    recs = [Recommendation.from_dict(_rec_dict("Alice", source_comment="ragged"))]
    import pandas as pd
    df = pd.DataFrame([{"clusterID": 1, "robust": True}])
    return build_aggregation_view("0003-066u", recs, df)


# ---------------------------------------------------------------------------
# Every Stage-3 ledger renderer folds the comments in
# ---------------------------------------------------------------------------

def test_apply_with_decisions_entry_records_reviewer_comments():
    # THE regression: this entry used to have no comments parameter at all.
    md = stage3_ledger_entry(
        _view(), finals={}, rob_reasons={}, accepted_keys=[], edit_reasons={},
        applied_by="homand", date="2026-08-05", backup_ref="backup_007",
        comments=COMMENTS,
    )
    assert "Reviewer comments:" in md
    assert "- (Alice) cl 3: merges with 4" in md
    assert "- (Bob) source: core wobbles" in md


def test_no_change_entry_records_reviewer_comments():
    md = stage3_no_change_ledger_entry(
        _view(), finalized_by="homand", date="2026-08-05", comments=COMMENTS)
    assert "Reviewer comments:" in md and "core wobbles" in md


def test_entries_omit_the_block_when_there_are_no_comments():
    for md in (
        stage3_ledger_entry(
            _view(), finals={}, rob_reasons={}, accepted_keys=[],
            edit_reasons={}, applied_by="h", date="d", backup_ref="b"),
        stage3_no_change_ledger_entry(
            _view(), finalized_by="h", date="d"),
    ):
        assert "Reviewer comments:" not in md
        assert not md.endswith("\n")


# ---------------------------------------------------------------------------
# "Skip what's already recorded"
# ---------------------------------------------------------------------------

ENTRIES = [CommentEntry("Alice", "cl 3", "merges with 4 in later epochs"),
           CommentEntry("Bob", "source", "core wobbles noticeably")]


def test_unrecorded_entries_skips_comments_already_in_the_notes():
    md = ("# 0003-066u\n\n## Decisions & applied history\n"
          "### 2026-07-01 — Note (by homand)\n"
          "- (Alice) cl 3: merges with 4 in later epochs\n")
    assert unrecorded_entries(ENTRIES, md) == [ENTRIES[1]]
    assert unrecorded_entries(ENTRIES, None) == ENTRIES    # no notes file yet
    assert unrecorded_entries(ENTRIES, "") == ENTRIES


def test_unrecorded_entries_match_the_prose_not_the_bullet_format():
    # The Stage-2 section holds a format_submission_text paste, which quotes the
    # comment as prose under its own heading — no "(reviewer) cl 3:" tag. That
    # still counts as recorded (matching the bullet form reported these as
    # missing when they were plainly in the notes).
    md = ("## Stage 2 — Baseline model\n[Submission for 0003-066u — Bob]\n\n"
          "core wobbles noticeably\n\nCluster comments:\n"
          "Cluster 3: merges with 4 in later epochs\n")
    assert unrecorded_entries(ENTRIES, md) == []


def test_unrecorded_entries_match_across_reflowed_whitespace():
    # A comment written into the notes may pick up hard-break spaces / wrapping.
    md = "text merges with 4   in later epochs  \nmore"
    assert unrecorded_entries([ENTRIES[0]], md) == []


def test_short_comments_need_their_cluster_or_epoch_beside_them():
    # "OK" on its own proves nothing — it occurs incidentally — but "Cluster 3:
    # OK" (Stage-2 paste) and "- (Alice) cl 3: OK" (seeded bullet) both count.
    e = CommentEntry("Alice", "cl 3", "OK")
    assert unrecorded_entries([e], "the fit looks OK to me") == [e]
    assert unrecorded_entries([e], "Cluster 3: OK") == []
    assert unrecorded_entries([e], "- (Alice) cl 3: OK") == []
    # ...and the identifier must be on the SAME line as the text
    assert unrecorded_entries([e], "Cluster 3: fine\nsomething else OK") == [e]

    # a short epoch comment, same rule
    ep = CommentEntry("Bob", "epoch 2003_01_13", "noisy")
    assert unrecorded_entries([ep], "Epoch 2003_01_13: noisy") == []
    assert unrecorded_entries([ep], "the data are noisy") == [ep]

    # a short comment with no cluster/epoch anchor needs the tagged form
    src = CommentEntry("Bob", "source", "messy")
    assert unrecorded_entries([src], "this source is messy") == [src]
    assert unrecorded_entries([src], "- (Bob) source: messy") == []


def test_a_reworded_comment_counts_as_unrecorded():
    # Preserving the reviewer's own words is the point; a paraphrase in the
    # notes does not stand in for it.
    e = CommentEntry("Bob", "source", "Reran with --complex 4 to follow "
                                     "extended flux in all epochs.")
    md = "Reran with --complex 4 to pick up the extended flux in all epochs."
    assert unrecorded_entries([e], md) == [e]


def test_unrecorded_notes_seed_reads_only_submitted(tmp_path):
    _write(tmp_path / "0003-066u" / "submitted" / "alice.json",
           _rec_dict("Alice", cluster_feedback={"3": {"comment": "merges with 4"}}))
    # an unsubmitted draft stays private (reviewer decision 2026-08-05)
    _write(tmp_path / "0003-066u" / "current" / "bob.json",
           _rec_dict("Bob", source_comment="half-written thought"))
    seed = unrecorded_notes_seed(tmp_path, "0003-066u", None)
    assert seed == "- (Alice) cl 3: merges with 4"


def test_comment_lines_collapse_duplicates():
    rec = Recommendation.from_dict(_rec_dict("Alice", source_comment="ragged"))
    assert comment_lines([rec, rec]) == ["- (Alice) source: ragged"]


# ---------------------------------------------------------------------------
# Recovery of comments lost before the fix
# ---------------------------------------------------------------------------

def _lost_setup(tmp_path) -> tuple[Path, Path]:
    """A source whose Stage-3 round was applied the old way: the submissions
    are archived under considered/, the ledger entry has no comments."""
    recs, notes = tmp_path / "recommendations", tmp_path / "notes"
    _write(recs / "0003-066u" / "considered" / "2026-07-02" / "alice.json",
           _rec_dict("Alice", cluster_feedback={"3": {"comment": "merges with 4"}},
                     epoch_feedback={"2003.10": {"comment": "flare"}}))
    _write(recs / "0003-066u" / "considered" / "2026-07-02" / "bob.json",
           _rec_dict("Bob", source_comment="core wobbles"))
    notes.mkdir(parents=True, exist_ok=True)
    store.write_note(notes, "0003-066u", store.append_ledger(
        store.scaffold("0003-066u"),
        "### 2026-07-02 — Stage 3 reconciliation (run 1, applied by homand) "
        "— backup_003\nConsidered: Alice (2026-07-01), Bob (2026-07-01)"))
    return recs, notes


def test_audit_finds_comments_lost_by_the_old_apply(tmp_path):
    recs, notes = _lost_setup(tmp_path)
    a = audit_source(recs, notes, "0003-066u")
    assert a.found == 3 and a.n_missing == 3
    assert {origin for _, origin in a.missing} == {"considered 2026-07-02"}
    assert audit_all(recs, notes) == [a]


def test_audit_ignores_the_aggregated_decision_file(tmp_path):
    # applied/<date>__aggregated.json is the admin's composed decisions, whose
    # comments are the accept/reject reasons the ledger already renders.
    recs, notes = _lost_setup(tmp_path)
    _write(recs / "0003-066u" / "applied" / "2026-07-02__aggregated.json",
           _rec_dict("homand", cluster_feedback={"3": {"comment": "consensus"}}))
    origins = dict(submission_files(recs, "0003-066u")).keys()
    assert "applied 2026-07-02" not in origins
    assert "consensus" not in " ".join(
        ln for ln, _ in audit_source(recs, notes, "0003-066u").missing)


def test_audit_counts_stage2_applied_submissions(tmp_path):
    recs, notes = _lost_setup(tmp_path)
    _write(recs / "0003-066u" / "applied" / "2026-06-02__homand.json",
           _rec_dict("Dan Homan", source_comment="baseline looks clean"))
    a = audit_source(recs, notes, "0003-066u")
    assert ("- (Dan Homan) source: baseline looks clean",
            "applied 2026-06-02") in a.missing


def test_apply_recovery_appends_and_is_idempotent(tmp_path):
    recs, notes = _lost_setup(tmp_path)
    a = audit_source(recs, notes, "0003-066u")
    path = apply_recovery(notes, a, date="2026-08-05", author="homand")

    md = path.read_text()
    assert "### 2026-08-05 — Recovered reviewer comments (by homand)" in md
    assert "- (Alice) cl 3: merges with 4  — from considered 2026-07-02" in md
    assert "- (Bob) source: core wobbles" in md
    # the earlier ledger entry is untouched (append-only)
    assert "Stage 3 reconciliation (run 1" in md

    # re-running finds nothing: the origin is a SUFFIX, so each canonical line
    # is still a substring of the notes file
    assert audit_source(recs, notes, "0003-066u").n_missing == 0
    assert audit_all(recs, notes) == []


def test_apply_recovery_scaffolds_a_missing_notes_file(tmp_path):
    recs, notes = tmp_path / "recommendations", tmp_path / "notes"
    _write(recs / "0003-066u" / "considered" / "2026-07-02" / "alice.json",
           _rec_dict("Alice", source_comment="ragged"))
    notes.mkdir()
    a = audit_source(recs, notes, "0003-066u")
    assert a.notes_exists is False and a.n_missing == 1
    md = apply_recovery(notes, a, date="2026-08-05").read_text()
    assert md.startswith("# 0003-066u")
    assert "- (Alice) source: ragged" in md


def test_audit_skips_sources_with_everything_recorded(tmp_path):
    recs, notes = _lost_setup(tmp_path)
    a = audit_source(recs, notes, "0003-066u")
    apply_recovery(notes, a, date="2026-08-05")
    assert audit_all(recs, notes) == []


def test_comments_survive_the_sidecar_to_notes_chain(tmp_path):
    """The whole forwarding path, end to end.

    The app never writes notes on an apply-with-decisions: it pre-renders the
    ledger entry into ``stage3/aggregated.stage3.json`` and ``mojave-apply
    --stage3-meta`` appends it *after* archiving the submissions. So the
    comments have to be baked into that string at generate time — this asserts
    they reach notes/<source>.md through that indirection, with the archived
    submissions no longer reachable.
    """
    from mojave_review.cli.apply import _apply_stage3_meta

    recs, notes = tmp_path / "recommendations", tmp_path / "notes"
    _write(recs / "0003-066u" / "submitted" / "alice.json",
           _rec_dict("Alice", cluster_feedback={"3": {"comment": "merges with 4"}}))
    notes.mkdir(parents=True)
    store.write_note(notes, "0003-066u", store.scaffold("0003-066u"))

    # what ui/callbacks._apply_aggregated writes into the sidecar
    md = store.read_note(notes, "0003-066u")
    entry = stage3_ledger_entry(
        _view(), finals={}, rob_reasons={}, accepted_keys=[], edit_reasons={},
        applied_by="homand", date="2026-08-05", backup_ref="{{BACKUP_REF}}",
        comments=unrecorded_notes_seed(recs, "0003-066u", md))
    sidecar = tmp_path / "aggregated.stage3.json"
    sidecar.write_text(json.dumps({
        "considered_slugs": ["alice"], "ledger_entry": entry,
        "status": "Stage 3 done · applied 2026-08-05", "date": "2026-08-05",
    }))

    _apply_stage3_meta(sidecar, recs, "0003-066u",
                       backup_ref="backup_004", fallback_date="2026-08-05")

    out = store.read_note(notes, "0003-066u")
    assert "backup_004" in out                       # placeholder resolved
    assert "- (Alice) cl 3: merges with 4" in out    # THE comment survived
    assert not (recs / "0003-066u" / "submitted" / "alice.json").exists()
    assert (recs / "0003-066u" / "considered" / "2026-08-05"
            / "alice.json").is_file()
    # and the audit agrees nothing was lost
    assert audit_source(recs, notes, "0003-066u").n_missing == 0


# ---------------------------------------------------------------------------
# Open (in-flight) vs archived (lost)
# ---------------------------------------------------------------------------

def test_open_submissions_are_in_flight_not_lost(tmp_path):
    """A comment in an open submitted/ rec is NOT missing: the notes panel shows
    it live and the Stage-3 ending will record it. Reviewer decision 2026-08-05
    after the first sweep flagged 155 in-flight comments as gaps."""
    recs, notes = _lost_setup(tmp_path)
    _write(recs / "0003-066u" / "submitted" / "carol.json",
           _rec_dict("Carol", source_comment="still thinking about cluster 7"))
    a = audit_source(recs, notes, "0003-066u")
    assert a.n_missing == 3                       # the archived ones only
    assert a.open_only == [("- (Carol) source: still thinking about cluster 7",
                            "submitted (open)")]
    assert all(o != "submitted (open)" for _, o in a.missing)

    # ...and --apply never writes them: Stage 3 will, with its decisions
    apply_recovery(notes, a, date="2026-08-05")
    assert "still thinking" not in store.read_note(notes, "0003-066u")
    assert audit_source(recs, notes, "0003-066u").n_open == 1


def test_audit_all_lists_open_only_sources_on_request(tmp_path):
    recs, notes = tmp_path / "recommendations", tmp_path / "notes"
    _write(recs / "0003-066u" / "submitted" / "carol.json",
           _rec_dict("Carol", source_comment="still thinking about cluster 7"))
    notes.mkdir()
    store.write_note(notes, "0003-066u", store.scaffold("0003-066u"))
    assert audit_all(recs, notes) == []                       # nothing lost
    assert len(audit_all(recs, notes, include_open=True)) == 1


def test_sync_scratch_dirs_are_not_sources(tmp_path):
    """unison leaves .unison.<source>.<hash>.unison.tmp dirs holding partial
    copies of submitted/ + current/; they were being enumerated as sources."""
    from mojave_review.recommendations.store import _source_dirs
    recs, notes = _lost_setup(tmp_path)
    ghost = recs / ".unison.0003-066u.abc123.unison.tmp"
    _write(ghost / "submitted" / "alice.json", _rec_dict("Alice",
           source_comment="a partial sync copy"))
    (recs / "_admin").mkdir(exist_ok=True)
    assert [d.name for d in _source_dirs(recs)] == ["0003-066u"]
    assert [a.source for a in audit_all(recs, notes)] == ["0003-066u"]


# ---------------------------------------------------------------------------
# Stage-2 builder warning
# ---------------------------------------------------------------------------

def test_builder_comments_missing_prefers_submitted_then_draft_then_applied(tmp_path):
    recs = tmp_path / "recommendations"
    notes_md = store.scaffold("0003-066u")

    # only an archived Stage-2 apply exists
    _write(recs / "0003-066u" / "applied" / "2026-06-02__homand.json",
           _rec_dict("homand", source_comment="baseline: complex 2.95 rerun"))
    miss = builder_comments_missing(recs, "0003-066u", "homand", notes_md)
    assert [e.text for e in miss] == ["baseline: complex 2.95 rerun"]

    # a live submission wins over the archive
    _write(recs / "0003-066u" / "submitted" / "homand.json",
           _rec_dict("homand", source_comment="baseline: complex 3.10 rerun"))
    miss = builder_comments_missing(recs, "0003-066u", "homand", notes_md)
    assert [e.text for e in miss] == ["baseline: complex 3.10 rerun"]

    # nothing to warn about once the text is in the notes
    recorded = store.set_section(notes_md, "stage2",
                                 "baseline: complex 3.10 rerun")
    assert builder_comments_missing(recs, "0003-066u", "homand", recorded) == []

    # another reviewer's submission is not the builder's business
    _write(recs / "0003-066u" / "submitted" / "alice.json",
           _rec_dict("Alice", source_comment="I think cluster 3 is fine"))
    assert builder_comments_missing(
        recs, "0003-066u", "homand", recorded) == []


def test_builder_comments_use_the_canonical_bullet_form(tmp_path):
    # What the "Add them to Stage 2 notes" button writes must be recognized as
    # recorded afterwards — by the warning, by Stage 3, and by the audit.
    recs, notes = tmp_path / "recommendations", tmp_path / "notes"
    _write(recs / "0003-066u" / "submitted" / "homand.json",
           _rec_dict("homand", cluster_feedback={"4": {"comment": "leading edge"}}))
    notes.mkdir()
    md = store.scaffold("0003-066u")
    miss = builder_comments_missing(recs, "0003-066u", "homand", md)
    md = store.set_section(md, "stage2",
                           "Comments from the baseline submission:\n"
                           + "\n".join(e.line for e in miss))
    store.write_note(notes, "0003-066u", md)
    assert builder_comments_missing(recs, "0003-066u", "homand", md) == []
    assert audit_source(recs, notes, "0003-066u").n_open == 0


def test_cli_reports_then_applies(tmp_path, capsys):
    from mojave_review.cli.audit_notes import main
    recs, notes = _lost_setup(tmp_path)
    args = ["--recommendations-dir", str(recs), "--notes-dir", str(notes)]

    assert main(args) == 0
    out = capsys.readouterr().out
    assert "0003-066u: 3 of 3 comment(s) lost" in out
    assert "report only" in out
    assert "Recovered reviewer comments" not in store.read_note(
        notes, "0003-066u")                     # report writes nothing

    assert main(args + ["--apply", "--author", "homand"]) == 0
    assert "Recovered: 3 comment(s)" in capsys.readouterr().out
    assert "Recovered reviewer comments (by homand)" in store.read_note(
        notes, "0003-066u")

    assert main(args) == 0
    assert "No lost comments" in capsys.readouterr().out


def test_cli_summarizes_open_comments_without_listing_them(tmp_path, capsys):
    from mojave_review.cli.audit_notes import main
    recs, notes = _lost_setup(tmp_path)
    _write(recs / "0003-066u" / "submitted" / "carol.json",
           _rec_dict("Carol", source_comment="still thinking about cluster 7"))
    args = ["--recommendations-dir", str(recs), "--notes-dir", str(notes)]

    assert main(args) == 0
    out = capsys.readouterr().out
    assert "1 further comment(s) sit in open submissions" in out
    assert "still thinking" not in out          # summarized, not listed

    assert main(args + ["--include-open"]) == 0
    out = capsys.readouterr().out
    assert "1 comment(s) in flight" in out
    assert "still thinking" in out


if __name__ == "__main__":
    import tempfile
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            if "tmp_path" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                fn(Path(tempfile.mkdtemp()))
            else:
                fn()
    print("PASS: notes comment forwarding")
