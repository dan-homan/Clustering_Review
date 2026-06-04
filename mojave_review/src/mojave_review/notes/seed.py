"""One-time seeding of notes/<source>.md from the existing Google-doc export.

The doc is one block per source with a regular shape::

    0003-066
    Step 1 brief review
    …stage 1 prose…
    Step 2 more detailed review
    …stage 2 prose…
    Step 3 final cross-ID and robustness check
    DCH 2026-04-30
    uploaded
    Next…
    0003+380
    …

``parse_google_doc`` turns that into per-source ``ParsedSource`` records;
``seed_notes`` resolves each bare designation to a real source folder and writes
the scaffolded ``notes/<source>.md`` with Stages 1–2 filled in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..data.loader import list_sources
from . import store


# A source block starts at a bare designation line like "0003-066" or "0003+380".
_DESIGNATION = re.compile(r"^\s*(\d{4}[+-]\d{3})\s*$")
_STEP = re.compile(r"^\s*Step\s*([123])\b", re.IGNORECASE)
# "DCH 2026-04-30" — reviewer initials + ISO date in the step-3 block.
_BASELINE = re.compile(r"\b([A-Z]{2,4})\s+(\d{4}-\d{2}-\d{2})\b")
_TRAILING = re.compile(r"^\s*(next\b.*|uploaded\.?)\s*$", re.IGNORECASE)


@dataclass
class ParsedSource:
    designation: str               # bare, e.g. "0003-066"
    stage1: str = ""
    stage2: str = ""
    stage3: str = ""
    baseline_initials: str = ""    # e.g. "DCH"
    baseline_date: str = ""        # e.g. "2026-04-30"
    uploaded: bool = False


def _clean_block(lines: list[str]) -> str:
    """Drop trailing 'Next…' / 'uploaded' bookkeeping lines and surrounding
    blank lines from a stage block."""
    out = [ln for ln in lines if not _TRAILING.match(ln)]
    return "\n".join(out).strip()


def parse_google_doc(text: str) -> list[ParsedSource]:
    """Parse the doc export into per-source records (in document order)."""
    lines = text.splitlines()
    # Index where each source block begins.
    starts = [i for i, ln in enumerate(lines) if _DESIGNATION.match(ln)]
    out: list[ParsedSource] = []
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        designation = _DESIGNATION.match(lines[start]).group(1)
        block = lines[start + 1:end]

        # Partition the block by "Step N" headers.
        stages: dict[int, list[str]] = {1: [], 2: [], 3: []}
        cur = 0
        for ln in block:
            m = _STEP.match(ln)
            if m:
                cur = int(m.group(1))
                continue
            if cur in stages:
                stages[cur].append(ln)

        rec = ParsedSource(
            designation=designation,
            stage1=_clean_block(stages[1]),
            stage2=_clean_block(stages[2]),
            stage3=_clean_block(stages[3]),
        )
        bm = _BASELINE.search("\n".join(stages[3]))
        if bm:
            rec.baseline_initials, rec.baseline_date = bm.group(1), bm.group(2)
        rec.uploaded = any(
            re.match(r"^\s*uploaded", ln, re.IGNORECASE) for ln in stages[3]
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
    unmatched: list[str] = field(default_factory=list)


def seed_notes(
    text: str, notes_dir: Path, results_dir: Path, *, force: bool = False,
) -> SeedResult:
    """Write notes/<source>.md for every parsed source that resolves to a
    real source folder. Existing files are skipped unless ``force``."""
    parsed = parse_google_doc(text)
    sources = list_sources(Path(results_dir))
    res = SeedResult()
    for rec in parsed:
        ref = _resolve(rec.designation, sources)
        if ref is None:
            res.unmatched.append(rec.designation)
            continue
        p = store.note_path(notes_dir, ref.source)
        if p.exists() and not force:
            res.skipped_existing.append(ref.source)
            continue
        status = "Stage 2 complete · awaiting review"
        if rec.baseline_initials and rec.baseline_date:
            status += f" · baseline by {rec.baseline_initials} {rec.baseline_date}"
        md = store.scaffold(
            ref.source, ref.epoch_min, ref.epoch_max,
            status=status, stage1=rec.stage1, stage2=rec.stage2,
        )
        store.write_note(notes_dir, ref.source, md)
        res.written.append(ref.source)
    return res
