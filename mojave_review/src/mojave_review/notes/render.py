"""Render the per-source notes for display in the app.

Combines the durable ``notes/<source>.md`` (Stages 1-2 + ledger) with the
**live open-suggestions** assembled from the submitted recommendation JSONs.
The live part is computed here on every read — it is never written to the
notes file (see docs/review_workflow.md).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..recommendations.schema import Recommendation
from . import store

# Section begin/end marker comments — stripped for display.
_MARKER = re.compile(r"^[ \t]*<!--\s*\w+:(?:begin|end)\s*-->[ \t]*\n?", re.MULTILINE)


def notes_markdown(notes_dir: Path, source: str) -> str:
    """The durable notes file with the section-marker comments removed, or a
    placeholder if there's no file yet."""
    md = store.read_note(notes_dir, source)
    if md is None:
        return f"### {source}\n\n*No notes file for this source yet.*"
    return _MARKER.sub("", md).strip()


def _submitted_recs(recommendations_dir: Path, source: str) -> list[Recommendation]:
    d = Path(recommendations_dir) / source / "submitted"
    if not d.is_dir():
        return []
    out: list[Recommendation] = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(Recommendation.from_dict(json.loads(p.read_text())))
        except Exception:
            continue
    return out


def _summarize(rec: Recommendation) -> str:
    # Trim the ISO timestamp (2026-05-31T19:15:31+00:00) to a readable form.
    when = (rec.updated_at or "?")[:16].replace("T", " ")
    out = [f"**{rec.reviewer or '(unknown)'}** · submitted {when}"]
    if rec.no_robustness_changes:
        out.append("- Signs off on robustness as-is")
    rob = [
        f"cl {cid} → {'robust' if fb.recommended_robust else 'non-robust'}"
        for cid, fb in rec.cluster_feedback.items()
        if fb.recommended_robust is not None
    ]
    if rob:
        out.append("- **Robustness:** " + "; ".join(rob))

    xids: list[str] = []
    uif = 0
    for e in rec.edits:
        if e.op == "change_clusterID":
            where = "all epochs" if e.scope == "all_epochs" else f"epoch {e.epoch}"
            xids.append(f"{e.from_id}→{e.to_id} ({where})")
        elif e.op == "set_use_in_fit":
            uif += 1
    if xids:
        out.append("- **Cross-ID:** " + "; ".join(xids))
    if uif:
        out.append(f"- **use-in-fit edits:** {uif}")

    cnotes = [f"cl {cid}: {fb.comment}"
              for cid, fb in rec.cluster_feedback.items() if fb.comment]
    if cnotes:
        out.append("- **Cluster notes:** " + "; ".join(cnotes))
    enotes = [f"{ep}: {fb.comment}"
              for ep, fb in rec.epoch_feedback.items() if fb.comment]
    if enotes:
        out.append("- **Epoch notes:** " + "; ".join(enotes))
    if rec.source_comment:
        out.append(f"- **Comment:** {rec.source_comment}")
    return "\n".join(out)


@dataclass(frozen=True)
class CommentEntry:
    """One reviewer comment: who wrote it, what it is about, and the prose."""
    reviewer: str
    where: str          # "source" | "cl 3" | "epoch 2003.10" | "edit (…)"
    text: str

    @property
    def line(self) -> str:
        """THE canonical bullet rendering for the notes file. Reviewer tags use
        parentheses, NOT square brackets, so appended ledger markdown doesn't
        turn the tag into a stray link."""
        return f"- ({self.reviewer}) {self.where}: {self.text}"


def comment_entries(recs: "Iterable[Recommendation]") -> list[CommentEntry]:
    """Every non-empty reviewer *comment* in ``recs``: source / cluster / epoch
    / edit comments, in that order per reviewer.

    The one place comments are extracted, so every Stage-3 path and the
    ``mojave-review-audit-notes`` recovery tool see the same set, rendered the
    same way. Exact duplicates (the same submission archived twice) collapse;
    order preserved.
    """
    out: list[CommentEntry] = []
    for rec in recs:
        rv = rec.reviewer or "(unknown)"
        if rec.source_comment and rec.source_comment.strip():
            out.append(CommentEntry(rv, "source", rec.source_comment.strip()))
        for cid, fb in rec.cluster_feedback.items():
            if fb.comment and fb.comment.strip():
                out.append(CommentEntry(rv, f"cl {cid}", fb.comment.strip()))
        for ep, fb in rec.epoch_feedback.items():
            if fb.comment and fb.comment.strip():
                out.append(CommentEntry(rv, f"epoch {ep}", fb.comment.strip()))
        for e in rec.edits:
            cm = (getattr(e, "comment", "") or "").strip()
            if not cm:
                continue
            if e.op == "change_clusterID" and e.from_id is not None and e.to_id is not None:
                what = f"change_clusterID {e.from_id}→{e.to_id}"
            else:
                what = e.op
            out.append(CommentEntry(rv, f"edit ({what})", cm))
    seen: set[CommentEntry] = set()
    return [e for e in out if not (e in seen or seen.add(e))]


def comment_lines(recs: "Iterable[Recommendation]") -> list[str]:
    """``comment_entries`` rendered as bullet lines."""
    return [e.line for e in comment_entries(recs)]


def _norm(text: str) -> str:
    """Collapse whitespace so text written into the notes file still matches
    the freshly-rendered comment (markdown wrapping / hard-break spaces)."""
    return re.sub(r"\s+", " ", text).strip()


#: Below this many non-space characters a comment ("OK", "same as 4") is not
#: distinctive enough that finding its text somewhere in the notes proves it was
#: recorded — see ``_is_recorded``.
_MIN_DISTINCTIVE = 12

#: "cl 111" / "epoch 2003_01_13" → the identifier a short comment must sit
#: beside to count. Source / edit comments have no such anchor.
_QUALIFIER_RE = re.compile(r"(?:cl|epoch)\s+(\S+)$")


def _is_recorded(entry: CommentEntry, haystack: str,
                 hay_lines: list[str]) -> bool:
    """Is ``entry`` already written somewhere in the (normalized) notes text?

    Matches on the comment PROSE, not on our bullet formatting, because the
    same comment is quoted several ways across a notes file: the Stage-2
    section holds a ``format_submission_text`` paste ("Cluster 111: Similar to
    50"), a dated note holds the seeded bullet ("- (homand) cl 111: Similar to
    50"). Requiring the bullet form reported comments as missing that were
    plainly right there in the notes.

    A SHORT comment's text alone proves nothing — "OK" occurs incidentally — so
    it must appear on one line together with its cluster / epoch identifier,
    which both quoting styles satisfy. Comments with no such anchor (source,
    edit) fall back to the fully-tagged form. The asymmetry is deliberate:
    re-recording a comment costs a duplicate line, dropping one loses it.
    """
    text = _norm(entry.text)
    if len(text.replace(" ", "")) >= _MIN_DISTINCTIVE:
        return text in haystack
    m = _QUALIFIER_RE.match(entry.where)
    if m is None:
        return _norm(entry.line.lstrip("- ")) in haystack
    qualifier = m.group(1)
    return any(text in ln and qualifier in ln for ln in hay_lines)


def unrecorded_entries(
    entries: "Iterable[CommentEntry]", notes_md: str | None,
) -> list[CommentEntry]:
    """The subset of ``entries`` not already somewhere in ``notes_md`` — any
    section, not just the ledger.

    Reviewer decision (2026-08-05): when the admin has already recorded the
    comments (a pasted dated note, the Stage-2 submission summary), the
    automatic Stage-3 entry adds only what's missing rather than repeating them.
    This is also what makes the recovery tool safe to re-run.

    A comment the admin *reworded* in the notes does NOT match, and is reported
    as unrecorded — by design: the point is to preserve the reviewer's own
    words.
    """
    if not notes_md:
        return list(entries)
    hay = _norm(notes_md)
    hay_lines = [_norm(ln) for ln in notes_md.splitlines() if ln.strip()]
    return [e for e in entries if not _is_recorded(e, hay, hay_lines)]


def pending_notes_seed(recommendations_dir: Path, source: str) -> str:
    """Reviewer comments from the source's OPEN submissions, for seeding the
    Stage-3 dated-note box. Empty string when there are none.

    Only ``submitted/`` counts — unsubmitted ``current/`` drafts stay private
    here as everywhere else in the app (reviewer decision, 2026-08-05)."""
    return "\n".join(comment_lines(_submitted_recs(recommendations_dir, source)))


def unrecorded_notes_seed(
    recommendations_dir: Path, source: str, notes_md: str | None,
) -> str:
    """``pending_notes_seed`` minus anything already written in ``notes_md``.

    What the Stage-3 endings fold into their ledger entry — ALL of them, so a
    source's reviewer comments reach notes/<source>.md however the round ends
    (decisions applied / finalized with no changes / flagged for discussion).
    Before 2026-08-05 the apply-with-decisions path passed no comments at all
    and silently dropped them; ``mojave-review-audit-notes`` recovers those."""
    return "\n".join(e.line for e in unrecorded_entries(
        comment_entries(_submitted_recs(recommendations_dir, source)), notes_md))


def open_suggestions_markdown(recommendations_dir: Path, source: str) -> str:
    """Live summary of every submitted recommendation for this source."""
    recs = _submitted_recs(recommendations_dir, source)
    if not recs:
        return "## Open suggestions (live)\n\n*No suggestions submitted yet.*"
    parts = ["## Open suggestions (live — from current submissions)"]
    parts += [_summarize(r) for r in recs]
    return "\n\n".join(parts)


def _hard_breaks(md: str) -> str:
    """dcc.Markdown (CommonMark) collapses a single newline into a space, so
    freeform notes — each item on its own line — render as a wall of text.
    Force a Markdown hard break (two trailing spaces) on any line that is
    directly followed by another non-blank line, so every source line keeps its
    own display line. Blank-line paragraph breaks and fenced code blocks are
    left untouched, and headings/list markers still work."""
    lines = md.split("\n")
    n = len(lines)
    out: list[str] = []
    in_fence = False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(ln)
            continue
        nxt = lines[i + 1] if i + 1 < n else ""
        if not in_fence and ln.strip() and nxt.strip() and not ln.endswith("  "):
            out.append(ln.rstrip() + "  ")
        else:
            out.append(ln)
    return "\n".join(out)


def combined_markdown(notes_dir: Path, recommendations_dir: Path, source: str) -> str:
    """The notes file + the live open-suggestions, for the in-app panel."""
    if not source:
        return ""
    return _hard_breaks(
        notes_markdown(notes_dir, source)
        + "\n\n---\n\n"
        + open_suggestions_markdown(recommendations_dir, source)
    )
