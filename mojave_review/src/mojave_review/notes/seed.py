"""One-time seeding of notes/<source>.md from the Google-doc Markdown export.

The Google-Docs → Markdown export shapes each source as an H2 heading with
checkbox step items, e.g.::

    ## 0003-066 {#0003-066}

    - [x] ~~Step 1 brief review~~
          SOURCE: 0003-066u  (1994.00-2026.00)
          …
    - [x] ~~Step 2 more detailed review~~
          * …recommendation prose…
          `[paste this into your notebook for 0003-066u]`
          `Changed to non-robust: 2`
    - [ ] Step 3 final cross-ID and robustness check
          * DCH 2026-04-30
          * uploaded

``parse_google_doc`` turns that into per-source ``ParsedSource`` records;
``seed_notes`` resolves each bare designation to a real source folder and writes
the scaffolded ``notes/<source>.md`` with Stages 1–2 filled in. It also accepts
the simpler bare-designation shape (``0003-066`` on its own line) used in tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..data.loader import list_sources
from . import store


# Source block start: an H2 heading "## 0003-066 {#…}" OR a bare designation
# line "0003-066" (the latter only used by tests). NOT a TOC link "[0003-066](…)".
_SOURCE_HEAD = re.compile(
    r"^\s*(?:##\s+)?(\d{4}[+-]\d{3})(?:\s*\{#[^}]*\})?\s*$"
)
# Step header: optional "- [x] " checkbox + optional "~~" strikethrough + "Step N".
# Group 1 captures the checkbox state (' ' / 'x' / None when bare); group 2 the
# step number. A checked box means that step WAS completed (empty notes just
# means "nothing to change").
_STEP = re.compile(
    r"^\s*(?:-\s*\[([ xX])\]\s*)?~{0,2}\s*Step\s+([123])\b", re.IGNORECASE)
# "DCH 2026-04-30" in the step-3 block → baseline initials + date.
_BASELINE = re.compile(r"\b([A-Z]{2,4})\s+(\d{4}-\d{2}-\d{2})\b")

# Google-doc backslash escaping of punctuation (\=  \-  2007\.  \[  \--complex …).
_ESCAPE = re.compile(r"\\([-=~.<>*\[\]`(){}+#&_])")
_DASHRULE = re.compile(r"^[─—\-]{5,}$")
_BACKTICKED = re.compile(r"^`(.*)`$")


def _unescape(s: str) -> str:
    return _ESCAPE.sub(r"\1", s)


def _clean(raw_lines: list[str]) -> str:
    """Tidy a stage's raw lines into readable markdown: unescape, unwrap
    single-backtick spans, drop the doc's '──' rules and 'paste into notebook'
    template cruft, flatten leading bullets to '- ', collapse blank runs."""
    out: list[str] = []
    for ln in raw_lines:
        s = _unescape(ln).strip()
        m = _BACKTICKED.match(s)
        if m:
            s = m.group(1).strip()
        if not s:
            out.append("")
            continue
        if _DASHRULE.match(s):
            continue
        if re.fullmatch(r"#+", s):     # stray empty heading marker from the export
            continue
        low = s.lower()
        if "paste this into your notebook" in low:
            continue
        if low.startswith("<user entered notes"):
            continue
        s = re.sub(r"^\*\s+", "- ", s)        # "* bullet" -> "- bullet"
        out.append(s)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return text


@dataclass
class ParsedSource:
    designation: str               # bare, e.g. "0003-066"
    stage1: str = ""
    stage2: str = ""               # includes the builder's notebook block
    stage3: str = ""
    stage1_done: bool = False      # step 1 box checked (or step 2 done)
    stage2_done: bool = False      # step 2 box checked
    stage3_done: bool = False      # step 3 box checked
    baseline_initials: str = ""    # e.g. "DCH"
    baseline_date: str = ""        # e.g. "2026-04-30"
    uploaded: bool = False


def parse_google_doc(text: str) -> list[ParsedSource]:
    """Parse the doc export into per-source records (in document order)."""
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if _SOURCE_HEAD.match(ln)]
    out: list[ParsedSource] = []
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        designation = _SOURCE_HEAD.match(lines[start]).group(1)

        stages: dict[int, list[str]] = {1: [], 2: [], 3: []}
        done: dict[int, bool] = {1: False, 2: False, 3: False}
        cur = 0
        for ln in lines[start + 1:end]:
            m = _STEP.match(ln)
            if m:
                cur = int(m.group(2))
                box = m.group(1)
                # box is None for the bare (no-checkbox) shape used in tests —
                # treat a present-but-uncheckable header as done.
                done[cur] = (box is None) or (box.strip().lower() == "x")
                continue
            if cur in stages:
                stages[cur].append(ln)

        rec = ParsedSource(
            designation=designation,
            stage1=_clean(stages[1]),
            stage2=_clean(stages[2]),
            stage3=_clean(stages[3]),
            # A done step 2 implies step 1 was completed.
            stage1_done=done[1] or done[2],
            stage2_done=done[2],
            stage3_done=done[3],
        )
        bm = _BASELINE.search(_unescape("\n".join(stages[3])))
        if bm:
            rec.baseline_initials, rec.baseline_date = bm.group(1), bm.group(2)
        rec.uploaded = any(
            re.search(r"\buploaded\b", _unescape(ln), re.IGNORECASE)
            for ln in stages[3]
        )
        out.append(rec)
    return out


def _resolve(designation: str, sources) -> object | None:
    """Match a bare designation (e.g. '0003-066') to a SourceRef. The folder
    source id carries a one-character band suffix ('0003-066u'), so accept an
    exact match or a single trailing band character."""
    for s in sources:
        if s.source == designation or s.source[:-1] == designation:
            return s
    return None


@dataclass
class SeedResult:
    written: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    skipped_unreviewed: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)


def seed_notes(
    text: str, notes_dir: Path, results_dir: Path, *,
    force: bool = False, include_stage2: bool = False,
) -> SeedResult:
    """Write notes/<source>.md for every source that has been **completed at
    Step 1** (its checkbox is checked, or Step 2 is done — which implies Step 1)
    and resolves to a real source folder.

    Empty Step 1 notes are fine — that just means "looked good, nothing to
    change", and the file is still seeded (with an empty Stage 1 section).
    Sources not yet reviewed at Step 1 are skipped.

    By default only **Stage 1** is imported; **Stage 2 is left empty** (the
    builder adds the baseline-model notes via the app during their submit/apply
    round). ``include_stage2=True`` also imports the doc's existing Step 2 prose.
    Existing files are skipped unless ``force``.
    """
    parsed = parse_google_doc(text)
    sources = list_sources(Path(results_dir))
    res = SeedResult()
    for rec in parsed:
        ref = _resolve(rec.designation, sources)
        if ref is None:
            res.unmatched.append(rec.designation)
            continue
        if not rec.stage1_done:          # not yet reviewed at Step 1
            res.skipped_unreviewed.append(ref.source)
            continue
        p = store.note_path(notes_dir, ref.source)
        if p.exists() and not force:
            res.skipped_existing.append(ref.source)
            continue
        stage2 = rec.stage2 if include_stage2 else ""
        if rec.stage2_done:
            status = "Stage 2 done"
            if rec.baseline_date:
                status += f" · baseline by {rec.baseline_initials} {rec.baseline_date}"
        else:
            status = "Stage 1 done"
        md = store.scaffold(
            ref.source, ref.epoch_min, ref.epoch_max,
            status=status, stage1=rec.stage1, stage2=stage2,
        )
        store.write_note(notes_dir, ref.source, md)
        res.written.append(ref.source)
    return res
