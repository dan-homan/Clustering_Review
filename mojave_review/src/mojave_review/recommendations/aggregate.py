"""Aggregate multiple reviewers' *submitted* recommendations for one source.

Stage 3 of the review workflow (admin-only): the builder looks at every
reviewer's submission side-by-side, decides each suggested change, and previews
the result before applying. This module is the **pure logic** — no Dash — so it
is unit-testable:

* :func:`build_aggregation_view` turns the submitted recs + the current model
  into the rows the admin panel renders (one robustness decision per cluster any
  reviewer weighed in on; one accept/reject row per *unique* suggested edit;
  read-only reviewer comments for context).
* :func:`compose_aggregated` turns the admin's decisions back into a single
  :class:`Recommendation` — exactly what ``recommendations/apply.py`` consumes
  for the preview (and, in build-step #4, what ``mojave-apply`` applies).

Robustness default (per cluster): the **majority** of the reviewers who voted
*plus the current model* as one equal vote; a tie defaults to the current
flag. See docs/review_workflow.md.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from .schema import ClusterFeedback, Edit, Recommendation


# ---------------------------------------------------------------------------
# Robustness default vote
# ---------------------------------------------------------------------------


def default_final_robust(reviewer_votes: list[bool], current: bool) -> bool:
    """Majority of ``reviewer_votes`` + the current flag as one equal vote;
    ties resolve to ``current``."""
    trues = sum(1 for v in reviewer_votes if v) + (1 if current else 0)
    total = len(reviewer_votes) + 1
    falses = total - trues
    if trues > falses:
        return True
    if falses > trues:
        return False
    return bool(current)


# ---------------------------------------------------------------------------
# Edit identity / description
# ---------------------------------------------------------------------------


def edit_key(e: Edit) -> str:
    """A stable content key so identical edits from different reviewers collapse
    to one decision row. Comments are deliberately excluded from the key."""
    if e.op == "change_clusterID":
        if e.scope == "all_epochs":
            return f"cid|all|{e.from_id}|{e.to_id}"
        return f"cid|single|{_efmt(e.epoch)}|{e.from_id}|{e.to_id}"
    if e.op == "set_use_in_fit":
        if e.scope == "epoch":
            return f"uif|epoch|{_efmt(e.epoch)}|{int(bool(e.value))}"
        return f"uif|single|{_efmt(e.epoch)}|{e.clusterID}|{int(bool(e.value))}"
    return f"{e.op}|{e.scope}|{_efmt(e.epoch)}|{e.clusterID}|{e.from_id}|{e.to_id}|{e.value}"


def _efmt(epoch: float | None) -> str:
    return "" if epoch is None else f"{float(epoch):.4f}"


def describe_edit(e: Edit) -> str:
    """A short human-readable label for the decision table."""
    if e.op == "change_clusterID":
        where = "all epochs" if e.scope == "all_epochs" else f"epoch {_efmt(e.epoch)}"
        return f"Re-ID {e.from_id} → {e.to_id} ({where})"
    if e.op == "set_use_in_fit":
        val = bool(e.value)
        if e.scope == "epoch":
            return f"use_in_fit={val} — whole epoch {_efmt(e.epoch)}"
        return f"use_in_fit={val} — cl {e.clusterID} @ {_efmt(e.epoch)}"
    return f"{e.op}/{e.scope}"


# ---------------------------------------------------------------------------
# View dataclasses (what the admin panel renders)
# ---------------------------------------------------------------------------


@dataclass
class RobustnessRow:
    cid: int
    current_robust: bool
    votes: dict[str, bool]          # reviewer -> True/False (only those who opined)
    default_final: bool             # majority(current + votes); tie -> current


@dataclass
class EditRow:
    key: str
    description: str
    op: str
    proposers: list[str]
    edit: dict[str, Any]            # representative edit (asdict form, comment dropped)


@dataclass
class ReviewerComments:
    reviewer: str
    source_comment: str
    cluster_comments: list[tuple[int, str]]
    epoch_comments: list[tuple[str, str]]
    signs_off_robustness: bool = False

    def is_empty(self) -> bool:
        return not (self.source_comment or self.cluster_comments
                    or self.epoch_comments or self.signs_off_robustness)


@dataclass
class AggregationView:
    source: str
    submissions: list[tuple[str, str]]      # (reviewer, when)
    reviewers: list[str]
    robustness_rows: list[RobustnessRow]
    edit_rows: list[EditRow]
    comments: list[ReviewerComments]

    def is_empty(self) -> bool:
        return not self.submissions

    def store_payload(self) -> dict[str, Any]:
        """Minimal JSON for the dcc.Store the compose step reads back: the
        per-key edit dicts and their apply order."""
        return {
            "source": self.source,
            "edits": {row.key: row.edit for row in self.edit_rows},
            "edit_order": [row.key for row in self.edit_rows],
        }


# ---------------------------------------------------------------------------
# Build the view
# ---------------------------------------------------------------------------


def _current_robust_map(current_df: pd.DataFrame | None) -> dict[int, bool]:
    out: dict[int, bool] = {}
    if current_df is None or "clusterID" not in getattr(current_df, "columns", []):
        return out  # unknown current flags → default to robust via cur.get(cid, True)
    for cid, sub in current_df.groupby("clusterID"):
        out[int(cid)] = bool(sub["robust"].astype(bool).iloc[0])
    return out


def build_aggregation_view(
    source: str,
    submitted_recs: list[Recommendation],
    current_df: pd.DataFrame,
) -> AggregationView:
    cur = _current_robust_map(current_df)
    reviewers = [r.reviewer or "(unknown)" for r in submitted_recs]
    submissions = [
        (r.reviewer or "(unknown)", (r.updated_at or "")[:16].replace("T", " "))
        for r in submitted_recs
    ]

    # --- robustness votes, per cluster any reviewer opined on ---------------
    votes_by_cid: dict[int, dict[str, bool]] = {}
    for r in submitted_recs:
        if r.no_robustness_changes:
            continue  # "robust flags as-is" — no per-cluster vote
        for cid_str, cf in r.cluster_feedback.items():
            if cf.recommended_robust is None:
                continue
            try:
                cid = int(cid_str)
            except (TypeError, ValueError):
                continue
            votes_by_cid.setdefault(cid, {})[r.reviewer or "(unknown)"] = \
                bool(cf.recommended_robust)

    robustness_rows: list[RobustnessRow] = []
    for cid in sorted(votes_by_cid):
        votes = votes_by_cid[cid]
        current_robust = cur.get(cid, True)
        robustness_rows.append(RobustnessRow(
            cid=cid,
            current_robust=current_robust,
            votes=votes,
            default_final=default_final_robust(list(votes.values()), current_robust),
        ))

    # --- unique edits, first-seen order, change_clusterID before use_in_fit -
    edit_map: dict[str, EditRow] = {}
    seen_order: list[str] = []
    for r in submitted_recs:
        who = r.reviewer or "(unknown)"
        for e in r.edits:
            k = edit_key(e)
            if k not in edit_map:
                d = asdict(e)
                d.pop("comment", None)
                edit_map[k] = EditRow(
                    key=k, description=describe_edit(e), op=e.op,
                    proposers=[who], edit=d,
                )
                seen_order.append(k)
            elif who not in edit_map[k].proposers:
                edit_map[k].proposers.append(who)

    def _order(k: str) -> tuple[int, int]:
        return (0 if edit_map[k].op == "change_clusterID" else 1, seen_order.index(k))

    edit_rows = [edit_map[k] for k in sorted(edit_map, key=_order)]

    # --- read-only reviewer comments ---------------------------------------
    comments: list[ReviewerComments] = []
    for r in submitted_recs:
        cc = [(int(cid), cf.comment) for cid, cf in r.cluster_feedback.items()
              if cf.comment.strip()] if r.cluster_feedback else []
        try:
            cc.sort()
        except TypeError:
            pass
        ec = [(ep, fb.comment) for ep, fb in r.epoch_feedback.items()
              if fb.comment.strip()] if r.epoch_feedback else []
        rc = ReviewerComments(
            reviewer=r.reviewer or "(unknown)",
            source_comment=r.source_comment.strip(),
            cluster_comments=cc,
            epoch_comments=ec,
            signs_off_robustness=bool(r.no_robustness_changes),
        )
        if not rc.is_empty():
            comments.append(rc)

    return AggregationView(
        source=source, submissions=submissions, reviewers=reviewers,
        robustness_rows=robustness_rows, edit_rows=edit_rows, comments=comments,
    )


# ---------------------------------------------------------------------------
# Compose decisions -> a single Recommendation (preview + apply input)
# ---------------------------------------------------------------------------


def compose_aggregated(
    source: str,
    reviewer: str,
    *,
    robustness_finals: dict[int, bool],     # cid -> True/False (— omitted)
    robustness_reasons: dict[int, str],
    accepted_edit_keys: list[str],
    edit_reasons: dict[str, str],
    store_payload: dict[str, Any],
) -> Recommendation:
    """Build the aggregated :class:`Recommendation` from the admin's decisions.

    Edits are emitted in ``store_payload['edit_order']`` (change_clusterID
    before set_use_in_fit) so the apply order is deterministic and matches what
    the panel showed."""
    cluster_feedback: dict[str, ClusterFeedback] = {}
    for cid, final in robustness_finals.items():
        if final is None:
            continue
        cluster_feedback[str(int(cid))] = ClusterFeedback(
            recommended_robust=bool(final),
            comment=(robustness_reasons.get(cid, "") or "").strip(),
        )

    edits_by_key = (store_payload or {}).get("edits", {})
    order = (store_payload or {}).get("edit_order", list(accepted_edit_keys))
    accepted = set(accepted_edit_keys)
    edits: list[Edit] = []
    for k in order:
        if k not in accepted or k not in edits_by_key:
            continue
        d = dict(edits_by_key[k])
        d["comment"] = (edit_reasons.get(k, "") or "").strip()
        edits.append(Edit.from_dict(d))

    return Recommendation(
        source=source, model="current", reviewer=reviewer,
        source_comment="", no_robustness_changes=False,
        cluster_feedback=cluster_feedback, epoch_feedback={}, edits=edits,
    )
