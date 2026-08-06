"""Find reviewer comments that never reached ``notes/<source>.md``.

Until 2026-08-05 the Stage-3 *apply-with-decisions* path rendered its ledger
entry without the round's reviewer comments (the two other Stage-3 endings —
finalize-with-no-changes and flag-for-discussion — did include them). Applying
also moves the submissions out of ``submitted/`` into ``considered/``, so those
comments left the app entirely: still on disk in the archive, but in nothing a
human reads.

This module recovers them. Reviewer comments are extracted by the SAME
:func:`notes.render.comment_entries` the live paths use, and a comment already
recorded — by any Stage-3 path, or by the admin pasting a dated note, or as
prose in the Stage-2 submission summary — is recognized and skipped
(:func:`notes.render.unrecorded_entries` matches on the comment text, not its
formatting). That makes the audit safe to re-run and safe to run before or
after a Stage-3 apply.

Pure logic; ``cli/audit_notes.py`` is the thin CLI wrapper.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..recommendations.schema import Recommendation
from ..recommendations.store import _source_dirs
from . import store
from .render import CommentEntry, comment_entries, unrecorded_entries


def _load(path: Path) -> Recommendation | None:
    try:
        return Recommendation.from_dict(json.loads(path.read_text()))
    except Exception:
        return None


def _is_aggregated(stem: str) -> bool:
    """``applied/<date>__aggregated[_N].json`` is the admin's own composed
    Stage-3 decision set, not a reviewer submission. Its comments are the
    admin's accept/reject reasons, which the ledger entry already renders."""
    return stem == "aggregated" or stem.startswith("aggregated_")


def submission_files(recommendations_dir: Path, source: str) -> list[tuple[str, Path]]:
    """Every reviewer submission for ``source`` that exists on disk, as
    ``(origin, path)`` — open and archived alike:

    * ``submitted/<slug>.json``            → "submitted (open)"
    * ``considered/<date>/<slug>.json``    → "considered <date>"  (Stage-3 archive)
    * ``applied/<date>__<slug>.json``      → "applied <date>"     (Stage-2 archive)

    Sorted oldest-archive-first, open submissions last, so a recovered block
    reads chronologically.
    """
    src_dir = Path(recommendations_dir) / source
    out: list[tuple[str, Path]] = []

    considered = src_dir / "considered"
    if considered.is_dir():
        for date_dir in sorted(p for p in considered.iterdir() if p.is_dir()):
            for f in sorted(date_dir.glob("*.json")):
                out.append((f"considered {date_dir.name}", f))

    applied = src_dir / "applied"
    if applied.is_dir():
        for f in sorted(applied.glob("*.json")):
            stem = f.stem
            if "__" not in stem:
                continue
            date, slug = stem.split("__", 1)
            if _is_aggregated(slug):
                continue
            out.append((f"applied {date}", f))

    sub = src_dir / "submitted"
    if sub.is_dir():
        for f in sorted(sub.glob("*.json")):
            out.append(("submitted (open)", f))
    return out


@dataclass
class SourceAudit:
    source: str
    found: int = 0                              # distinct comment lines on disk
    missing: list[tuple[str, str]] = field(default_factory=list)  # (line, origin)
    notes_exists: bool = True

    @property
    def n_missing(self) -> int:
        return len(self.missing)


def audit_source(
    recommendations_dir: Path, notes_dir: Path, source: str,
) -> SourceAudit:
    """Which of ``source``'s reviewer comments are absent from its notes file.

    A comment line is attributed to the FIRST place it was found (archives
    before open submissions), so a reviewer whose submission was archived and
    later re-submitted verbatim is reported once.
    """
    seen: dict[CommentEntry, str] = {}          # entry -> origin (first wins)
    for origin, path in submission_files(recommendations_dir, source):
        rec = _load(path)
        if rec is None:
            continue
        for entry in comment_entries([rec]):
            seen.setdefault(entry, origin)

    md = store.read_note(notes_dir, source)
    return SourceAudit(
        source=source, found=len(seen),
        missing=[(e.line, seen[e]) for e in unrecorded_entries(seen, md)],
        notes_exists=md is not None,
    )


def audit_all(
    recommendations_dir: Path, notes_dir: Path,
    sources: list[str] | None = None,
) -> list[SourceAudit]:
    """Audit every source with a recommendations dir (or just ``sources``).
    Returns only the ones with something missing, alphabetical."""
    names = (sorted(sources) if sources is not None
             else sorted(d.name for d in _source_dirs(recommendations_dir)))
    out = [audit_source(recommendations_dir, notes_dir, s) for s in names]
    return [a for a in out if a.n_missing]


def recovery_entry(audit: SourceAudit, *, date: str, author: str = "") -> str:
    """The ledger entry that records ``audit``'s missing comments.

    Each line keeps its canonical ``comment_lines`` text and gets the origin
    appended as a SUFFIX — never a prefix — so the canonical text stays a
    contiguous substring of the notes file and a re-run recognizes it as
    recorded (idempotence).
    """
    by = f" (by {author})" if author else ""
    lines = [
        f"### {date} — Recovered reviewer comments{by}",
        "Comments found in submitted / archived recommendations that were not "
        "recorded in this file (recovered by mojave-review-audit-notes).",
        "",
        "Reviewer comments:",
    ]
    lines += [f"{ln}  — from {origin}" for ln, origin in audit.missing]
    return "\n".join(lines)


def apply_recovery(
    notes_dir: Path, audit: SourceAudit, *, date: str, author: str = "",
    emin: float | None = None, emax: float | None = None,
) -> Path:
    """Append ``audit``'s recovery entry to the source's notes ledger,
    scaffolding the notes file if it doesn't exist yet. Returns the path."""
    md = store.read_note(notes_dir, audit.source)
    if md is None:
        md = store.scaffold(audit.source, emin, emax)
    md = store.append_ledger(md, recovery_entry(audit, date=date, author=author))
    store.write_note(notes_dir, audit.source, md)
    return store.note_path(notes_dir, audit.source)
