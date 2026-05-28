"""Dataclasses for the per-(source, model, reviewer) recommendation file.

The JSON on disk looks like:

    {
      "source": "0003-066u",
      "model": "current",                # or "backup_NNN"
      "model_sha": "<sha256 of the CSV reviewer saw>",
      "reviewer": "Reviewer Name",
      "updated_at": "2026-05-28T10:34:21+00:00",
      "source_comment": "...",
      "cluster_feedback": {
        "0": {"robust_agree": null, "comment": ""},
        "3": {"robust_agree": false, "comment": "merges with 4"}
      },
      "epoch_feedback": {
        "2003.10": {"comment": "ragged structure"}
      },
      "edits": [
        {"op": "change_clusterID", "scope": "all_epochs", "from_id": 3,
         "to_id": 4, "comment": ""},
        {"op": "set_use_in_fit", "scope": "epoch", "epoch": 2003.10,
         "value": false, "comment": "bad calibration"}
      ]
    }

The reviewer NEVER modifies anything under ``Results/`` — only files in the
recommendations directory.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# recommended_robust is the reviewer's absolute call:
#   True  => the cluster should be Robust
#   False => the cluster should be Non-robust
#   None  => no recommendation (the "-" choice in the UI)
#
# A `set_robust` edit is derived per cluster where `recommended_robust`
# differs from the model's current `robust` flag.
@dataclass
class ClusterFeedback:
    recommended_robust: bool | None = None
    comment: str = ""

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ClusterFeedback":
        # Back-compat: an early schema used `robust_agree` (relative,
        # agree/disagree); honor it if `recommended_robust` is missing.
        rr = d.get("recommended_robust", None)
        if rr is None and "robust_agree" in d:
            rr = d.get("robust_agree")
        return ClusterFeedback(
            recommended_robust=rr,
            comment=d.get("comment", ""),
        )


@dataclass
class EpochFeedback:
    comment: str = ""

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "EpochFeedback":
        return EpochFeedback(comment=d.get("comment", ""))


@dataclass
class Edit:
    """One suggested modification.

    Shape depends on `op`:

    * `op="change_clusterID"`:
        - `scope="single"`: rename one row.
            Required: `epoch`, `from_id`, `to_id`. Optional: `comment`.
        - `scope="all_epochs"`: renumber a cluster across the whole source.
            Required: `from_id`, `to_id`. Optional: `comment`.

    * `op="set_use_in_fit"`:
        - `scope="single"`: toggle one row.
            Required: `epoch`, `clusterID`, `value` (bool). Optional: `comment`.
        - `scope="epoch"`: toggle the whole epoch.
            Required: `epoch`, `value`. Optional: `comment`.
    """
    op: str           # "change_clusterID" | "set_use_in_fit"
    scope: str        # "single" | "all_epochs" | "epoch"
    epoch: float | None = None
    clusterID: int | None = None
    from_id: int | None = None
    to_id: int | None = None
    value: bool | None = None
    comment: str = ""

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Edit":
        return Edit(
            op=d["op"], scope=d["scope"],
            epoch=d.get("epoch"), clusterID=d.get("clusterID"),
            from_id=d.get("from_id"), to_id=d.get("to_id"),
            value=d.get("value"), comment=d.get("comment", ""),
        )


@dataclass
class Recommendation:
    source: str
    model: str
    reviewer: str
    model_sha: str = ""
    updated_at: str = ""
    source_comment: str = ""
    # "No changes suggested" checkbox on the Robustness tab. When True the
    # reviewer is signing off on the model's current robust flags as-is and
    # no `set_robust` edits should be derived from cluster_feedback, even
    # if the table still contains stale entries.
    no_robustness_changes: bool = False
    cluster_feedback: dict[str, ClusterFeedback] = field(default_factory=dict)
    epoch_feedback: dict[str, EpochFeedback] = field(default_factory=dict)
    edits: list[Edit] = field(default_factory=list)

    # --- serialization ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "model": self.model,
            "model_sha": self.model_sha,
            "reviewer": self.reviewer,
            "updated_at": self.updated_at,
            "source_comment": self.source_comment,
            "no_robustness_changes": self.no_robustness_changes,
            "cluster_feedback": {
                k: asdict(v) for k, v in self.cluster_feedback.items()
            },
            "epoch_feedback": {
                k: asdict(v) for k, v in self.epoch_feedback.items()
            },
            "edits": [asdict(e) for e in self.edits],
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Recommendation":
        return Recommendation(
            source=d.get("source", ""),
            model=d.get("model", ""),
            model_sha=d.get("model_sha", ""),
            reviewer=d.get("reviewer", ""),
            updated_at=d.get("updated_at", ""),
            source_comment=d.get("source_comment", ""),
            no_robustness_changes=bool(d.get("no_robustness_changes", False)),
            cluster_feedback={
                k: ClusterFeedback.from_dict(v)
                for k, v in (d.get("cluster_feedback") or {}).items()
            },
            epoch_feedback={
                k: EpochFeedback.from_dict(v)
                for k, v in (d.get("epoch_feedback") or {}).items()
            },
            edits=[Edit.from_dict(e) for e in (d.get("edits") or [])],
        )

    def touch(self) -> None:
        """Set ``updated_at`` to the current UTC timestamp."""
        self.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def is_empty(self) -> bool:
        """True if the reviewer hasn't supplied anything yet."""
        return (
            not self.source_comment.strip()
            and not self.no_robustness_changes
            and not any(
                cf.recommended_robust is not None or cf.comment.strip()
                for cf in self.cluster_feedback.values()
            )
            and not any(ef.comment.strip() for ef in self.epoch_feedback.values())
            and not self.edits
        )
