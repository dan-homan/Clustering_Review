"""Per-reviewer source assignments — the data layer for the dashboard.

This module owns one file:

    <recommendations_dir>/_admin/assignments.json

Schema (versioned for future upgrades):

    {
      "version": 1,
      "updated_at": "2026-06-29T19:14:02+00:00",
      "deadline": null,                    // ISO date string or null (phase 4)
      "default_review_target": 2,          // hard-coded for now
      "assignments": {
        "alice": [
          {"source": "0003-066u",
           "assigned_at": "2026-06-29T...",
           "target_date": null,            // ISO date string or null
           "assigned_by": "homand"}
        ]
      }
    }

The store NEVER writes under ``Results/``. Atomic temp-file + rename
write, same pattern as ``recommendations/store.py`` — partial writes
can't leave a corrupt assignments.json behind.

Two derived signals consumed by the dashboard (no extra storage):

* ``needs_for(recs_dir, source)`` — remaining open slots after counting
  already-submitted reviews for the source. Hard cap at
  ``default_review_target`` so the auto-balancer never overshoots.
* ``assignment_status`` — pending / in_progress / submitted, by checking
  ``recommendations/`` directly.

``STALE_DAYS`` is the per-source "overdue" threshold relative to
``target_date`` (Phase 4 uses it). An assignment with no ``target_date``
is never stale; only the global deadline applies there.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..recommendations.store import (
    count_submissions, is_submitted, load_recommendation,
)


SCHEMA_VERSION = 1
DEFAULT_REVIEW_TARGET = 2
STALE_DAYS = 7

_ASSIGNMENTS_REL = Path("_admin") / "assignments.json"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@dataclass
class AssignmentRecord:
    source: str
    assigned_at: str                       # ISO-8601 UTC
    target_date: str | None = None         # ISO date (YYYY-MM-DD), Phase 4
    assigned_by: str = ""                  # admin reviewer slug

    @classmethod
    def from_dict(cls, d: dict) -> "AssignmentRecord":
        return cls(
            source=d["source"],
            assigned_at=d.get("assigned_at", _utcnow_iso()),
            target_date=d.get("target_date"),
            assigned_by=d.get("assigned_by", ""),
        )


@dataclass
class AssignmentStore:
    version: int = SCHEMA_VERSION
    updated_at: str = ""
    deadline: str | None = None                                       # Phase 4
    default_review_target: int = DEFAULT_REVIEW_TARGET
    assignments: dict[str, list[AssignmentRecord]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "AssignmentStore":
        return cls(
            version=int(d.get("version", SCHEMA_VERSION)),
            updated_at=d.get("updated_at", ""),
            deadline=d.get("deadline"),
            default_review_target=int(
                d.get("default_review_target", DEFAULT_REVIEW_TARGET)
            ),
            assignments={
                reviewer: [AssignmentRecord.from_dict(r) for r in records]
                for reviewer, records in d.get("assignments", {}).items()
            },
        )

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "deadline": self.deadline,
            "default_review_target": self.default_review_target,
            "assignments": {
                reviewer: [asdict(r) for r in records]
                for reviewer, records in self.assignments.items()
            },
        }

    def touch(self) -> None:
        self.updated_at = _utcnow_iso()


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def assignments_path(recommendations_dir: Path) -> Path:
    return Path(recommendations_dir) / _ASSIGNMENTS_REL


def load_store(recommendations_dir: Path) -> AssignmentStore:
    """Return the persisted store, or a fresh empty one if absent."""
    p = assignments_path(recommendations_dir)
    if not p.is_file():
        return AssignmentStore()
    with p.open() as f:
        return AssignmentStore.from_dict(json.load(f))


def save_store(recommendations_dir: Path, store: AssignmentStore) -> Path:
    """Atomic write — temp file + ``os.replace``. Returns the file path."""
    store.touch()
    p = assignments_path(recommendations_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(store.to_dict(), indent=2, sort_keys=False)
    fd, tmp_path = tempfile.mkstemp(
        prefix=p.name + ".", dir=str(p.parent), suffix=".part",
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp_path, p)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
    return p


# ---------------------------------------------------------------------------
# Derived helpers (used by the dashboard layout + later by auto-balance)
# ---------------------------------------------------------------------------


def needs_for(
    recommendations_dir: Path,
    source: str,
    *,
    review_target: int = DEFAULT_REVIEW_TARGET,
) -> int:
    """Remaining review slots for a source: ``review_target`` minus the
    number of reviewers who have already submitted, clamped to zero."""
    submitted = count_submissions(recommendations_dir, source)
    return max(0, review_target - submitted)


def assignment_status(
    recommendations_dir: Path, source: str, reviewer: str,
) -> str:
    """``"submitted"`` / ``"in_progress"`` / ``"pending"`` for the given
    reviewer's *current-model* recommendation on a source.

    The hierarchy matches the source-picker badges: a submitted review
    always wins over a draft; an empty draft (no edits / no comments)
    is treated as ``"pending"`` so a reviewer who just clicked the
    source doesn't get falsely credited with "in progress."
    """
    if is_submitted(recommendations_dir, source, reviewer):
        return "submitted"
    draft = load_recommendation(
        recommendations_dir, source, "current", reviewer,
    )
    if not draft.is_empty():
        return "in_progress"
    return "pending"


def is_stale(
    record: AssignmentRecord, *, today: _dt.date | None = None,
) -> bool:
    """True when ``today > target_date + STALE_DAYS`` AND the assignment
    has a ``target_date`` set. No target ⇒ never stale (the global
    deadline banner is the only signal in that case).
    """
    if not record.target_date:
        return False
    target = _dt.date.fromisoformat(record.target_date)
    today = today or _dt.date.today()
    return today > target + _dt.timedelta(days=STALE_DAYS)


def assignments_for(
    store: AssignmentStore, reviewer: str,
) -> list[AssignmentRecord]:
    """Return the reviewer's queue (empty list if not in the store)."""
    return list(store.assignments.get(reviewer, []))


def reviewer_load(
    store: AssignmentStore, reviewer: str,
) -> int:
    """Count of currently-assigned sources for the reviewer (the
    auto-balancer's weight-bin size at the same granularity)."""
    return len(store.assignments.get(reviewer, []))


def all_assigned_sources(store: AssignmentStore) -> set[str]:
    """Every source that appears in any reviewer's queue."""
    return {
        rec.source
        for records in store.assignments.values()
        for rec in records
    }
