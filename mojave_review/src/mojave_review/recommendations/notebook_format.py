"""Notebook-style text block for a reviewer's submitted recommendation.

Mirrors the layout of the ``mojave-apply`` notebook summary but is written
from the **pre-apply** recommendation directly (no backup index, no
on-disk side effects). Used by the web UI's "Submit Recommendation"
button so the reviewer gets a copy-pasteable record of what they submitted.

The CLI's apply summary lives in ``cli/apply.py`` and stays independent
from this — both have similar shape but different framing (deltas vs
recommendations, applied vs submitted).
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

import pandas as pd

from .apply import epoch_mask, ELIGIBILITY_MIN_FIT, PARK_ID
from .schema import Recommendation


_NOT_REAL_CLUSTERS = {-1, PARK_ID}     # exclude the unassigned + park IDs


_RULE = "─" * 65


# ---------------------------------------------------------------------------
# Robustness helpers (deltas + post-apply eligibility summary)
# ---------------------------------------------------------------------------


def _robust_state(df: pd.DataFrame) -> dict[int, bool]:
    return {
        int(cid): bool(g["robust"].iloc[0])
        for cid, g in df.groupby("clusterID")
        if int(cid) not in _NOT_REAL_CLUSTERS and int(cid) >= 0
    }


def _eligible_clusters(df: pd.DataFrame, min_fit: int = ELIGIBILITY_MIN_FIT) -> set[int]:
    out: set[int] = set()
    for cid, g in df.groupby("clusterID"):
        if int(cid) in _NOT_REAL_CLUSTERS or int(cid) < 0:
            continue
        if int(g["use_in_fit"].astype(bool).sum()) >= min_fit:
            out.add(int(cid))
    return out


def _robust_deltas(
    before: pd.DataFrame, after: pd.DataFrame,
) -> tuple[list[int], list[int]]:
    b, a = _robust_state(before), _robust_state(after)
    to_robust, to_nonrobust = [], []
    for cid, val in a.items():
        if cid in b and b[cid] != val:
            (to_robust if val else to_nonrobust).append(cid)
    return sorted(to_robust), sorted(to_nonrobust)


def _robustness_lines(
    base_df: pd.DataFrame, eff_df: pd.DataFrame,
) -> list[str]:
    out: list[str] = []
    to_r, to_n = _robust_deltas(base_df, eff_df)
    if to_r:
        out.append("Changed to robust:     " + ", ".join(str(c) for c in to_r))
    if to_n:
        out.append("Changed to non-robust: " + ", ".join(str(c) for c in to_n))
    eligible = _eligible_clusters(eff_df)
    state = _robust_state(eff_df)
    cur_r = sorted(c for c in eligible if state.get(c))
    cur_n = sorted(c for c in eligible if not state.get(c))
    if cur_r:
        out.append("Robust (eligible):     " + ", ".join(str(c) for c in cur_r))
    if cur_n:
        out.append("Non-robust (eligible): " + ", ".join(str(c) for c in cur_n))
    return out


# ---------------------------------------------------------------------------
# Cross-ID + use_in_fit recommendation lines
# ---------------------------------------------------------------------------


def _crossID_lines(rec: Recommendation) -> list[str]:
    """Lines describing change_clusterID recommendations (no backup ref)."""
    out: list[str] = []
    all_ep_groups: dict[int, list[int]] = {}
    single_edits: list[tuple[int, float, int]] = []
    for e in rec.edits:
        if e.op != "change_clusterID":
            continue
        if e.scope == "all_epochs" and e.from_id is not None and e.to_id is not None:
            all_ep_groups.setdefault(int(e.to_id), []).append(int(e.from_id))
        elif e.scope == "single" and e.epoch is not None \
                and e.from_id is not None and e.to_id is not None:
            single_edits.append((int(e.to_id), float(e.epoch), int(e.from_id)))
    for to_id, froms in sorted(all_ep_groups.items()):
        froms_str = ", ".join(str(f) for f in froms)
        out.append(
            f"CrossID {to_id} for whole time period (change {froms_str} to become {to_id})."
        )
    for to_id, ep, from_id in sorted(single_edits):
        out.append(f"CrossID {to_id} at epoch {ep:.4f} (was {from_id}).")
    return out


def _use_in_fit_lines(
    rec: Recommendation, base_df: pd.DataFrame, eff_df: pd.DataFrame,
) -> list[str]:
    out: list[str] = []
    per_cluster: dict[tuple[int, bool], list[float]] = {}
    for e in rec.edits:
        if e.op != "set_use_in_fit":
            continue
        if e.scope == "epoch" and e.epoch is not None and e.value is not None:
            em = epoch_mask(base_df, float(e.epoch))
            if bool(e.value):
                n_changed = int(
                    (~base_df.loc[em, "use_in_fit"].astype(bool)
                     & eff_df.loc[em, "use_in_fit"].astype(bool)).sum()
                )
                out.append(
                    f"use_in_fit=True for entire epoch {float(e.epoch):.4f} "
                    f"({n_changed} cluster{'s' if n_changed != 1 else ''} affected)."
                )
            else:
                n_changed = int(
                    (base_df.loc[em, "use_in_fit"].astype(bool)
                     & ~eff_df.loc[em, "use_in_fit"].astype(bool)).sum()
                )
                out.append(
                    f"Epoch {float(e.epoch):.4f} excluded entirely from fit "
                    f"({n_changed} cluster{'s' if n_changed != 1 else ''} affected)."
                )
        elif e.scope == "single" and e.epoch is not None \
                and e.clusterID is not None and e.value is not None:
            key = (int(e.clusterID), bool(e.value))
            per_cluster.setdefault(key, []).append(float(e.epoch))
    for (cid, val), epochs in sorted(per_cluster.items()):
        eps = ", ".join(f"{ep:.4f}" for ep in sorted(epochs))
        verb = "included in" if val else "excluded from"
        out.append(f"Cluster {cid} {verb} fit at epoch(s) {eps}.")
    return out


# ---------------------------------------------------------------------------
# Comment sections
# ---------------------------------------------------------------------------


def _cluster_comment_lines(rec: Recommendation) -> list[str]:
    out: list[str] = []
    for cid_str in sorted(rec.cluster_feedback.keys(), key=lambda s: int(s) if s.lstrip("-").isdigit() else 0):
        cf = rec.cluster_feedback[cid_str]
        if cf.comment.strip():
            out.append(f"Cluster {cid_str}: {cf.comment.strip()}")
    return out


def _epoch_comment_lines(rec: Recommendation) -> list[str]:
    out: list[str] = []
    for ep_str in sorted(rec.epoch_feedback.keys()):
        ef = rec.epoch_feedback[ep_str]
        if ef.comment.strip():
            out.append(f"Epoch {ep_str}: {ef.comment.strip()}")
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _indent(lines: Iterable[str], n: int = 2) -> list[str]:
    pad = " " * n
    return [pad + line for line in lines]


def format_submission_text(
    rec: Recommendation,
    base_df: pd.DataFrame, effective_df: pd.DataFrame,
    reviewer: str,
    when: datetime | None = None,
) -> str:
    """Build the copy-pasteable notebook block shown after a submission.

    ``base_df`` is the original cluster_df for the source; ``effective_df``
    is what the visualize logic produces (base + rec applied). Robustness
    deltas come from comparing the two; eligibility summary is computed
    from the effective state.
    """
    when = when or datetime.now()
    lines: list[str] = [_RULE]
    lines.append(
        f"[Submission for {rec.source} — {reviewer} — "
        f"{when.strftime('%Y-%m-%d %H:%M')}]"
    )
    lines.append("")

    if rec.source_comment.strip():
        lines.append(rec.source_comment.rstrip())
    else:
        lines.append("(no source comment)")
    lines.append("")

    if rec.no_robustness_changes:
        lines.append("Robustness: signed off on the model's robust flags as-is.")
        lines.append("")

    crossid = _crossID_lines(rec)
    if crossid:
        lines.append("Cross-ID recommendations:")
        lines.extend(_indent(crossid))
        lines.append("")

    robustness = _robustness_lines(base_df, effective_df)
    if robustness:
        lines.append("Robustness recommendations:")
        lines.extend(_indent(robustness))
        lines.append("")

    uif = _use_in_fit_lines(rec, base_df, effective_df)
    if uif:
        lines.append("use_in_fit recommendations:")
        lines.extend(_indent(uif))
        lines.append("")

    cluster_cmts = _cluster_comment_lines(rec)
    if cluster_cmts:
        lines.append("Cluster comments:")
        lines.extend(_indent(cluster_cmts))
        lines.append("")

    epoch_cmts = _epoch_comment_lines(rec)
    if epoch_cmts:
        lines.append("Epoch comments:")
        lines.extend(_indent(epoch_cmts))
        lines.append("")

    # Trim a trailing blank line if present, then close.
    while lines and lines[-1] == "":
        lines.pop()
    lines.append(_RULE)
    return "\n".join(lines)


def strip_for_notes(text: str) -> str:
    """Clean a ``format_submission_text`` block for embedding into the markdown
    notes file: drop the ``─``-rule lines, and unwrap the ``[Submission for …]``
    header brackets (markdown would otherwise treat ``[...]`` as a link
    reference). Leaves all other content untouched."""
    out: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s and set(s) <= {"─"}:          # a _RULE line (all box-drawing dashes)
            continue
        if s.startswith("[") and s.endswith("]") and len(s) >= 2:
            # keep any leading indentation, drop the surrounding [ ]
            out.append(line[: line.index("[")] + s[1:-1])
        else:
            out.append(line)
    return "\n".join(out).strip("\n")
