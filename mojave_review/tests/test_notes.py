"""Unit tests for the notes store + Google-doc seed parser."""

from __future__ import annotations

from mojave_review.notes import store
from mojave_review.notes.seed import parse_google_doc


# ---------------------------------------------------------------------------
# store: scaffold / sections / ledger
# ---------------------------------------------------------------------------

def test_scaffold_roundtrip_sections():
    md = store.scaffold("0003-066u", 1994.0, 2026.0,
                        status="Stage 2 complete", stage1="core ok",
                        stage2="--complex 4 + editN")
    assert "# 0003-066u  (1994.00–2026.00)" in md
    assert store.get_section(md, "stage1") == "core ok"
    assert store.get_section(md, "stage2") == "--complex 4 + editN"
    assert store.get_section(md, "ledger") == ""


def test_set_section_preserves_others():
    md = store.scaffold("x", stage1="one", stage2="two")
    md2 = store.set_section(md, "stage2", "TWO (updated)\nmore")
    assert store.get_section(md2, "stage2") == "TWO (updated)\nmore"
    assert store.get_section(md2, "stage1") == "one"   # untouched


def test_append_ledger_is_append_only():
    md = store.scaffold("x")
    md = store.append_ledger(md, "### 2026-06-10 — applied A")
    md = store.append_ledger(md, "### 2026-06-12 — applied B")
    led = store.get_section(md, "ledger")
    assert led.index("applied A") < led.index("applied B")   # newest last
    # other sections still intact / empty
    assert store.get_section(md, "stage1") == ""


# ---------------------------------------------------------------------------
# seed: parse the Google-doc shape
# ---------------------------------------------------------------------------

_DOC = """0003-066
Step 1 brief review
core ID ok; maxGap -> 2.03
Step 2 more detailed review
--complex 4; recommend cross-ID 7 as 4; everything except cluster 2 robust
Step 3 final cross-ID and robustness check
DCH 2026-04-30
uploaded
Next…
0003+380
Step 1 brief review
Step 2 more detailed review
N clusters updated to 4 in all epochs. Cluster 1 non-robust.
Step 3 final cross-ID and robustness check
DCH 2026-04-30
uploaded
Next…
"""


def test_parse_two_sources():
    recs = parse_google_doc(_DOC)
    assert [r.designation for r in recs] == ["0003-066", "0003+380"]


def test_parse_stage_content_and_baseline():
    recs = parse_google_doc(_DOC)
    r0 = recs[0]
    assert "maxGap -> 2.03" in r0.stage1
    assert "cross-ID 7 as 4" in r0.stage2
    assert "everything except cluster 2 robust" in r0.stage2
    # trailing 'Next…' / 'uploaded' stripped from stage blocks
    assert "Next" not in r0.stage1 and "uploaded" not in r0.stage2.lower()
    # baseline initials + date pulled from the step-3 block
    assert r0.baseline_initials == "DCH"
    assert r0.baseline_date == "2026-04-30"
    assert r0.uploaded is True
    # second source's stage 1 is empty (no prose), stage 2 has content
    assert recs[1].stage1 == ""
    assert "non-robust" in recs[1].stage2


# ---------------------------------------------------------------------------
# seed: parse the REAL Google-Docs Markdown export shape
# ---------------------------------------------------------------------------

_DOC_REAL = """[0003-066](#0003-066)

## Review Procedure {#review}

## 0003-066 {#0003-066}

- [x] ~~Step 1 brief review~~
      SOURCE: 0003-066u  (1994.00-2026.00)
      Recommended new maxGap: 2.03
- [x] ~~Step 2 more detailed review~~
      * Recommend cross-ID’ing 7 as 4.   Everything except cluster 2 robust.
`─────`
`[paste this into your notebook for 0003-066u]`
`<user entered notes for source here>`
`Changed to non-robust: 2`
`Robust (eligible):     0, 1, 3, 4`
`─────`
- [ ] Step 3 final cross-ID and robustness check
      * DCH 2026-04-30
        * uploaded

## 0003+380 {#0003+380}

- [x] ~~Step 1 brief review~~
- [x] ~~Step 2 more detailed review~~
      * N clusters \\= 4. Cluster 1 non-robust.
- [ ] Step 3
      * DCH 2026-05-01
"""


def test_parse_real_h2_format():
    recs = parse_google_doc(_DOC_REAL)
    # TOC link + "## Review Procedure" must NOT be parsed as sources
    assert [r.designation for r in recs] == ["0003-066", "0003+380"]
    r0 = recs[0]
    assert "maxGap: 2.03" in r0.stage1
    # bullet normalized, backslash-escape removed
    assert "- Recommend cross-ID" in r0.stage2
    # builder's notebook block kept; template cruft (──, paste-line, <user…>) dropped
    assert "Changed to non-robust: 2" in r0.stage2
    assert "Robust (eligible):     0, 1, 3, 4" in r0.stage2
    assert "paste this into your notebook" not in r0.stage2.lower()
    assert "─" not in r0.stage2 and "<user entered" not in r0.stage2.lower()
    assert (r0.baseline_initials, r0.baseline_date) == ("DCH", "2026-04-30")
    assert r0.uploaded is True
    # checkbox state: step 1 & 2 done, step 3 not.
    assert r0.stage1_done and r0.stage2_done and not r0.stage3_done
    # escaped "\\=" cleaned in source 2
    assert "N clusters = 4" in recs[1].stage2
    # KEY: source 2 has an EMPTY Step 1 (checked, no notes) — still counts as
    # completed ("looked good"), so it must be seedable.
    assert recs[1].stage1 == "" and recs[1].stage1_done is True


if __name__ == "__main__":
    test_scaffold_roundtrip_sections()
    test_set_section_preserves_others()
    test_append_ledger_is_append_only()
    test_parse_two_sources()
    test_parse_stage_content_and_baseline()
    test_parse_real_h2_format()
    print("PASS: notes store + seed parser (bare + real H2 formats)")
