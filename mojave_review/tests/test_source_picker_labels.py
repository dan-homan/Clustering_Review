"""Per-reviewer source-picker status note (needs review / review in progress /
submitted), and the date range dropped from the label."""

from __future__ import annotations

import json

from mojave_review.ui.layout import _reviewer_status, build_source_options
from mojave_review.notes.store import notes_dir_for, note_path, scaffold
from mojave_review.recommendations.store import reviewer_slug


def _setup(tmp_path, status):
    recs = tmp_path / "recommendations"
    notes = notes_dir_for(recs)
    notes.mkdir(parents=True)
    note_path(notes, "0003-066u").write_text(scaffold("0003-066u", status=status))
    return recs


def _draft(recs, payload):
    d = recs / "0003-066u" / "current"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{reviewer_slug('Alice')}.json").write_text(json.dumps(payload))


def _submit(recs):
    d = recs / "0003-066u" / "submitted"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{reviewer_slug('Alice')}.json").write_text("{}")


def test_open_untouched_needs_review(tmp_path):
    recs = _setup(tmp_path, "Stage 2 done")
    text, style = _reviewer_status(recs, "0003-066u", "Alice")
    assert text == "needs review" and style.get("fontStyle") == "italic"


def test_empty_draft_still_needs_review(tmp_path):
    recs = _setup(tmp_path, "Stage 2 done")
    _draft(recs, {"source": "0003-066u", "model": "current", "reviewer": "Alice"})
    text, _ = _reviewer_status(recs, "0003-066u", "Alice")
    assert text == "needs review"          # empty draft doesn't count


def test_nonempty_draft_in_progress(tmp_path):
    recs = _setup(tmp_path, "Stage 2 done")
    _draft(recs, {"source": "0003-066u", "model": "current", "reviewer": "Alice",
                  "source_comment": "real content"})
    text, style = _reviewer_status(recs, "0003-066u", "Alice")
    assert text == "review in progress" and "fontStyle" not in style


def test_submitted_bold(tmp_path):
    recs = _setup(tmp_path, "Stage 2 done")
    _submit(recs)
    text, style = _reviewer_status(recs, "0003-066u", "Alice")
    assert text == "submitted" and style.get("fontWeight")


def test_locked_and_final_have_no_note(tmp_path):
    recs = _setup(tmp_path, "Stage 1 done")          # stage2 phase (locked)
    assert _reviewer_status(recs, "0003-066u", "Alice") == (None, {})
    note_path(notes_dir_for(recs), "0003-066u").write_text(
        scaffold("0003-066u", status="Stage 3 done · applied 2026-06-09"))
    assert _reviewer_status(recs, "0003-066u", "Alice") == (None, {})


def test_label_drops_date_range(tmp_path):
    import os
    res = tmp_path / "Results" / "0003-066u_1994.00-2026.00"
    res.mkdir(parents=True)
    (res / "0003-066u.1994.00-2026.00.merged_win_results.csv").write_text(
        "clusterID,epoch,robust\n0,2000.0,True\n")
    opts = build_source_options(tmp_path / "Results", None)
    # label is a plain string (dcc.Dropdown only accepts string|number labels
    # in this Dash version — a component there throws React error #31). With
    # recommendations_dir=None there's no status/badge, so it's the bare source
    # name with the date range dropped.
    assert opts[0]["label"] == "0003-066u"
    assert opts[0]["search"] == "0003-066u"
