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


def open_suggestions_markdown(recommendations_dir: Path, source: str) -> str:
    """Live summary of every submitted recommendation for this source."""
    recs = _submitted_recs(recommendations_dir, source)
    if not recs:
        return "## Open suggestions (live)\n\n*No suggestions submitted yet.*"
    parts = ["## Open suggestions (live — from current submissions)"]
    parts += [_summarize(r) for r in recs]
    return "\n\n".join(parts)


def combined_markdown(notes_dir: Path, recommendations_dir: Path, source: str) -> str:
    """The notes file + the live open-suggestions, for the in-app panel."""
    if not source:
        return ""
    return (
        notes_markdown(notes_dir, source)
        + "\n\n---\n\n"
        + open_suggestions_markdown(recommendations_dir, source)
    )
