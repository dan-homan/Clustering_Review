"""Epoch Notes uses real dcc.Textarea rows (not a DataTable cell). Verify the
row builder + the round-trip the bridge/submit reconstruct into a Recommendation."""

from __future__ import annotations

from mojave_review.ui.recommendations_panel import build_epoch_rows
from mojave_review.ui.recommendations_callbacks import build_rec_from_ui_state


def _textareas(children):
    # children[0] is the header; each data row's 3rd child is the textarea.
    return [row.children[2] for row in children[1:]]


def test_build_epoch_rows_has_textarea_per_epoch():
    rows = build_epoch_rows([
        {"epoch": "2003_02_05", "epoch_val": 2003.10, "comment": "flare"},
        {"epoch": "2005_06_03", "epoch_val": 2005.42, "comment": ""},
    ])
    tas = _textareas(rows)
    assert len(tas) == 2
    assert tas[0].id == {"type": "epoch-comment", "epoch": "2003_02_05"}
    assert tas[0].value == "flare"
    # left-to-right editing (the original RTL complaint)
    assert tas[0].style["direction"] == "ltr"
    assert tas[0].style["textAlign"] == "left"


def test_epoch_roundtrip_into_recommendation():
    rows = build_epoch_rows([
        {"epoch": "2003_02_05", "epoch_val": 2003.10, "comment": "flare"},
        {"epoch": "2005_06_03", "epoch_val": 2005.42, "comment": ""},
    ])
    tas = _textareas(rows)
    # reconstruct the [{epoch, comment}] list the bridge/submit build
    epoch_rows = [{"epoch": t.id["epoch"], "comment": t.value or ""} for t in tas]
    rec = build_rec_from_ui_state(
        source="x", model="current", reviewer="r", source_comment="",
        no_robustness_changes=False, cluster_rows=[], epoch_rows=epoch_rows,
        edits=[])
    assert rec.epoch_feedback["2003_02_05"].comment == "flare"
    assert "2005_06_03" not in rec.epoch_feedback      # empty comment dropped


def test_empty_build_is_header_only():
    assert len(build_epoch_rows([])) == 1               # just the header
