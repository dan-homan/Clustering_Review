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


#: Origin tag for a live (unarchived) submission — see ``SourceAudit``.
_OPEN = "submitted (open)"


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
            out.append((_OPEN, f))
    return out


def builder_comments_missing(
    recommendations_dir: Path, source: str, reviewer: str, notes_md: str | None,
) -> list[CommentEntry]:
    """The Stage-2 builder's OWN comments that aren't in ``notes_md`` yet.

    The Stage-2 apply writes no ledger entry (by design — the ledger is Stage-3
    only) and the Stage-2 prose is hand-written, so a builder who skips "Seed
    from submission summary" leaves their build notes only in
    ``applied/<date>__<slug>.json``. That is how 46 of this project's comments
    went unrecorded — every one of them the builder's own. The app warns at
    "Save & set Stage 2 done" using this (reviewer decision, 2026-08-05:
    prompt at the deciding moment rather than auto-writing prose).

    Looks at the builder's live submission first, then their draft, then their
    most recent Stage-2 archive — whichever exists.
    """
    from ..recommendations.store import reviewer_slug
    slug = reviewer_slug(reviewer)
    src_dir = Path(recommendations_dir) / source
    applied = sorted((src_dir / "applied").glob(f"*__{slug}.json"))
    for path in (src_dir / "submitted" / f"{slug}.json",
                 src_dir / "current" / f"{slug}.json",
                 applied[-1] if applied else None):
        if path is None or not path.is_file():
            continue
        rec = _load(path)
        if rec is None:
            continue
        return unrecorded_entries(comment_entries([rec]), notes_md)
    return []


@dataclass
class SourceAudit:
    """One source's unrecorded comments, split by whether they are still
    reachable in the app.

    ``missing`` — from an ARCHIVED submission (``considered/`` / ``applied/``).
    Nothing in the app shows these any more, so an unrecorded one is simply
    lost: this is the actionable list.

    ``open_only`` — from a live ``submitted/`` recommendation. Not lost: the
    notes panel renders them under "Open suggestions (live)" and whichever
    Stage-3 ending the round gets will write them to the ledger. Reported
    separately (and not by default) so in-flight work isn't confused with a
    gap — reviewer decision, 2026-08-05.
    """
    source: str
    found: int = 0                              # distinct comments on disk
    missing: list[tuple[str, str]] = field(default_factory=list)  # (line, origin)
    open_only: list[tuple[str, str]] = field(default_factory=list)
    notes_exists: bool = True

    @property
    def n_missing(self) -> int:
        return len(self.missing)

    @property
    def n_open(self) -> int:
        return len(self.open_only)


def audit_source(
    recommendations_dir: Path, notes_dir: Path, source: str,
) -> SourceAudit:
    """Which of ``source``'s reviewer comments are absent from its notes file,
    split into archived (lost) and open (in flight).

    A comment is attributed to the FIRST place it was found — archives before
    open submissions — so a reviewer whose submission was archived and later
    re-submitted verbatim counts once, as archived.
    """
    seen: dict[CommentEntry, str] = {}          # entry -> origin (first wins)
    for origin, path in submission_files(recommendations_dir, source):
        rec = _load(path)
        if rec is None:
            continue
        for entry in comment_entries([rec]):
            seen.setdefault(entry, origin)

    md = store.read_note(notes_dir, source)
    absent = unrecorded_entries(seen, md)
    archived = [(e.line, seen[e]) for e in absent if seen[e] != _OPEN]
    return SourceAudit(
        source=source, found=len(seen), missing=archived,
        open_only=[(e.line, seen[e]) for e in absent if seen[e] == _OPEN],
        notes_exists=md is not None,
    )


def audit_all(
    recommendations_dir: Path, notes_dir: Path,
    sources: list[str] | None = None,
    *, include_open: bool = False,
) -> list[SourceAudit]:
    """Audit every source with a recommendations dir (or just ``sources``),
    alphabetical. Returns the sources with archived comments missing; with
    ``include_open`` also those that only have in-flight ones."""
    names = (sorted(sources) if sources is not None
             else sorted(d.name for d in _source_dirs(recommendations_dir)))
    out = [audit_source(recommendations_dir, notes_dir, s) for s in names]
    return [a for a in out
            if a.n_missing or (include_open and a.n_open)]


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
    if not audit.missing:
        return ""                    # nothing archived to recover
    return "\n".join(lines)


def apply_recovery(
    notes_dir: Path, audit: SourceAudit, *, date: str, author: str = "",
    emin: float | None = None, emax: float | None = None,
) -> Path:
    """Append ``audit``'s recovery entry — its ARCHIVED (lost) comments only —
    to the source's notes ledger, scaffolding the notes file if it doesn't
    exist yet. In-flight ``open_only`` comments are deliberately left alone:
    recording them here would pre-empt the Stage-3 entry that is going to
    record them properly, alongside the decisions they informed."""
    entry = recovery_entry(audit, date=date, author=author)
    if not entry:
        return store.note_path(notes_dir, audit.source)
    md = store.read_note(notes_dir, audit.source)
    if md is None:
        md = store.scaffold(audit.source, emin, emax)
    store.write_note(notes_dir, audit.source, store.append_ledger(md, entry))
    return store.note_path(notes_dir, audit.source)
