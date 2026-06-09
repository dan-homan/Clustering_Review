"""Render the per-source notes for display in the app.

Combines the durable ``notes/<source>.md`` (Stages 1-2 + ledger) with the
**live open-suggestions** assembled from the submitted recommendation JSONs.
The live part is computed here on every read — it is never written to the
notes file (see docs/review_workflow.md).
"""

from __future__ import annotations

import json
import re
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


def pending_notes_seed(recommendations_dir: Path, source: str) -> str:
    """Collate every non-empty reviewer *comment* from the open submissions into
    a plain bullet list, tagged by reviewer, for seeding the Stage-3 dated-note
    box. Includes source / cluster / epoch / edit comments. Reviewer tags use
    parentheses, NOT square brackets, so the appended ledger markdown doesn't
    turn the tag into a stray link. Empty string when there are no comments."""
    lines: list[str] = []
    for rec in _submitted_recs(recommendations_dir, source):
        rv = rec.reviewer or "(unknown)"
        if rec.source_comment and rec.source_comment.strip():
            lines.append(f"- ({rv}) source: {rec.source_comment.strip()}")
        for cid, fb in rec.cluster_feedback.items():
            if fb.comment and fb.comment.strip():
                lines.append(f"- ({rv}) cl {cid}: {fb.comment.strip()}")
        for ep, fb in rec.epoch_feedback.items():
            if fb.comment and fb.comment.strip():
                lines.append(f"- ({rv}) epoch {ep}: {fb.comment.strip()}")
        for e in rec.edits:
            cm = (getattr(e, "comment", "") or "").strip()
            if not cm:
                continue
            if e.op == "change_clusterID" and e.from_id is not None and e.to_id is not None:
                what = f"change_clusterID {e.from_id}→{e.to_id}"
            else:
                what = e.op
            lines.append(f"- ({rv}) edit ({what}): {cm}")
    return "\n".join(lines)


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
