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
from .difficulty import SourceDifficulty


SCHEMA_VERSION = 4          # v4: adds team_members (manual roster, syncs)
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
    # Per-record target_date is retained for backward compat with v1/v2
    # stores but is NOT the authoritative target. The current source of
    # truth is the v3 source-level ``AssignmentStore.source_target_dates``
    # map (every reviewer assigned to a source shares the same target).
    # Helpers like :func:`get_source_target_date` always consult the
    # source-level map.
    target_date: str | None = None
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
    # Reviewer names the admin has temporarily excluded from
    # auto-balance. Their existing assignments are preserved and shown
    # on the dashboard (with a "paused" badge); they are simply not
    # eligible to receive *new* assignments. See ``active_reviewers``.
    paused_reviewers: list[str] = field(default_factory=list)
    # Source-level target dates (v3). One target per source — every
    # reviewer assigned to a source shares the same target. Sources
    # absent from this map have no target (the dashboard shows "—"
    # and :func:`is_stale` returns False).
    source_target_dates: dict[str, str] = field(default_factory=dict)
    # Manually-curated team roster (v4). The admin's machine has no
    # tokens.yaml (that lives on the deployed server), so the roster
    # can't be discovered from tokens here. These names let the admin
    # define teammates who haven't submitted anything yet and assign
    # work to them. Stored in assignments.json (under recommendations/)
    # so the usual server sync carries the roster to the deployment.
    # Unioned with the auto-discovered reviewers by
    # ``dashboard.known_reviewers``. Names should match the deployed
    # tokens.yaml ``name:`` fields so identities line up.
    team_members: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "AssignmentStore":
        # Schema compat (read path is monotonic):
        # * v1 stores: no paused_reviewers, no source_target_dates
        # * v2 stores: no source_target_dates
        # * v3 stores: no team_members
        # * v4 (current): everything present
        # Defaults make older versions load cleanly. Per-record
        # target_date (v1/v2) is preserved as a hint but the source
        # map wins everywhere — to migrate explicitly, callers can
        # call :func:`migrate_per_record_targets_to_source` once.
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
            paused_reviewers=list(d.get("paused_reviewers", []) or []),
            source_target_dates={
                k: v for k, v in
                (d.get("source_target_dates", {}) or {}).items()
                if v                                # drop blanks/nulls on read
            },
            team_members=list(d.get("team_members", []) or []),
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
            "paused_reviewers": list(self.paused_reviewers),
            "source_target_dates": dict(self.source_target_dates),
            "team_members": list(self.team_members),
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
    store: AssignmentStore, source: str,
    *, today: _dt.date | None = None,
) -> bool:
    """True when ``today > target_date + STALE_DAYS`` AND the source
    has a target date set in ``store.source_target_dates``. No target
    ⇒ never stale (the global deadline banner is the only signal in
    that case).

    Signature changed in schema v3: stale is now per-SOURCE, not
    per-AssignmentRecord (since target dates moved to the source
    level). Callers should be ``is_stale(store, src)`` — the old
    ``is_stale(record)`` form was removed.
    """
    raw = store.source_target_dates.get(source)
    if not raw:
        return False
    target = _dt.date.fromisoformat(raw)
    today = today or _dt.date.today()
    return today > target + _dt.timedelta(days=STALE_DAYS)


# ---------------------------------------------------------------------------
# Source-level target dates (v3)
# ---------------------------------------------------------------------------


def get_source_target_date(
    store: AssignmentStore, source: str,
) -> str | None:
    """Return the source's target date (``YYYY-MM-DD``) or ``None``."""
    return store.source_target_dates.get(source) or None


def set_source_target_date(
    store: AssignmentStore, source: str, target_date: str | None,
) -> None:
    """Set or clear a source's target date. Passing ``None`` or an
    empty string removes the entry."""
    if not target_date:
        store.source_target_dates.pop(source, None)
        return
    # Light validation — full date parse so a typo doesn't silently
    # corrupt the store.
    _dt.date.fromisoformat(target_date)
    store.source_target_dates[source] = target_date


def set_source_target_dates_bulk(
    store: AssignmentStore, sources: list[str], target_date: str | None,
) -> int:
    """Apply the same target date to a list of sources. Pass
    ``target_date=None`` to clear them. Returns the number of sources
    actually mutated (i.e. whose value changed)."""
    n = 0
    for src in sources:
        prev = store.source_target_dates.get(src)
        if target_date:
            _dt.date.fromisoformat(target_date)        # validate once
            if prev != target_date:
                store.source_target_dates[src] = target_date
                n += 1
        else:
            if prev is not None:
                store.source_target_dates.pop(src, None)
                n += 1
    return n


def sources_in_range(
    all_sources: list[str], from_src: str, to_src: str,
) -> list[str]:
    """Lexicographic inclusive range from ``from_src`` to ``to_src``
    over ``all_sources`` (the canonical alphabetical order shown in
    the picker). If ``from_src`` > ``to_src`` lexicographically the
    bounds are swapped silently — the admin shouldn't have to care
    which dropdown they touched first."""
    if from_src > to_src:
        from_src, to_src = to_src, from_src
    return [s for s in all_sources if from_src <= s <= to_src]


def migrate_per_record_targets_to_source(
    store: AssignmentStore,
) -> int:
    """One-shot upgrade: copy any per-record ``target_date`` from v1/v2
    stores into the source-level map (only when the source has no
    target yet). Idempotent. Returns the number of sources upgraded.

    Intended to be called once on a freshly-loaded older store before
    saving it back as v3. Not auto-invoked by ``load_store`` so the
    decision to migrate stays with the caller.
    """
    n = 0
    for records in store.assignments.values():
        for r in records:
            if r.target_date and r.source not in store.source_target_dates:
                try:
                    _dt.date.fromisoformat(r.target_date)
                except ValueError:
                    continue                                # skip bad data
                store.source_target_dates[r.source] = r.target_date
                n += 1
    return n


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


# ---------------------------------------------------------------------------
# Auto-balance (LPT) and store mutations
# ---------------------------------------------------------------------------


def submitted_by_map(
    recommendations_dir: Path, sources: list[str],
) -> dict[str, set[str]]:
    """Return {source: {reviewer_slug, ...}} from the submitted JSONs on
    disk. The slugs are the filename stems, the same identity that the
    auto-balancer's ``reviewers`` argument must use to match correctly
    (``auth.tokens.reviewer_slug(name)``)."""
    out: dict[str, set[str]] = {}
    for src in sources:
        sub_dir = Path(recommendations_dir) / src / "submitted"
        if not sub_dir.is_dir():
            out[src] = set()
            continue
        out[src] = {f.stem for f in sub_dir.glob("*.json") if f.stem}
    return out


def all_submitting_reviewers(
    recommendations_dir: Path, sources: list[str],
) -> dict[str, set[str]]:
    """Like :func:`submitted_by_map`, but for **all submissions ever**:
    the union of currently-open submissions (``submitted/``) and those
    already folded into a Stage-3 apply (``considered/<date>/``).
    Returns ``{source: {reviewer_slug, ...}}``.

    Used by :func:`credit_prior_submissions` so the first-round
    auto-balance gives credit for reviewer work even on sources that
    have since been finalized — those submissions live under
    ``considered/`` after Stage 3 (see
    :func:`recommendations.store.archive_considered_submissions`)
    and would otherwise look "unsubmitted" to the load balancer.
    """
    out: dict[str, set[str]] = {}
    for src in sources:
        src_dir = Path(recommendations_dir) / src
        slugs: set[str] = set()
        if (src_dir / "submitted").is_dir():
            slugs.update(
                f.stem for f in (src_dir / "submitted").glob("*.json")
                if f.stem
            )
        considered = src_dir / "considered"
        if considered.is_dir():
            for date_dir in considered.iterdir():
                if not date_dir.is_dir():
                    continue
                slugs.update(
                    f.stem for f in date_dir.glob("*.json") if f.stem
                )
        out[src] = slugs
    return out


# ---------------------------------------------------------------------------
# Team-pause helpers
# ---------------------------------------------------------------------------


def is_paused(store: AssignmentStore, reviewer: str) -> bool:
    return reviewer in store.paused_reviewers


def set_paused(
    store: AssignmentStore, reviewer: str, paused: bool,
) -> None:
    """Mark the reviewer as paused / active. Idempotent. Paused
    reviewers stay in ``store.assignments`` (and on the dashboard with
    a "paused" badge); they are simply excluded from
    :func:`auto_balance`'s eligibility set."""
    if paused:
        if reviewer not in store.paused_reviewers:
            store.paused_reviewers.append(reviewer)
    else:
        store.paused_reviewers = [
            r for r in store.paused_reviewers if r != reviewer
        ]


def add_team_member(store: AssignmentStore, name: str) -> bool:
    """Add a name to the manually-curated roster (v4). Returns True if a
    new name was added, False if it was blank or already present.
    Whitespace is trimmed; matching is exact (case-sensitive) so the
    admin can mirror the deployed tokens.yaml ``name:`` fields verbatim."""
    name = (name or "").strip()
    if not name or name in store.team_members:
        return False
    store.team_members.append(name)
    return True


def remove_team_member(store: AssignmentStore, name: str) -> bool:
    """Drop a name from the manual roster. Returns True if it was
    present. Only the manual entry is removed — a teammate who has
    submitted reviews or holds assignments is still auto-discovered, so
    removal is only meaningful for names that exist solely in
    ``team_members``."""
    if name in store.team_members:
        store.team_members = [m for m in store.team_members if m != name]
        return True
    return False


def active_reviewers(
    store: AssignmentStore, all_reviewers: list[str],
) -> list[str]:
    """The subset of ``all_reviewers`` not currently paused. Order is
    preserved."""
    paused = set(store.paused_reviewers)
    return [r for r in all_reviewers if r not in paused]


# ---------------------------------------------------------------------------
# Credit prior submissions (first-round bookkeeping)
# ---------------------------------------------------------------------------


def credit_prior_submissions(
    store: AssignmentStore,
    *,
    recommendations_dir: Path,
    sources: list[str],
    name_for_slug: dict[str, str],
    assigned_by: str = "credit",
) -> int:
    """Pre-populate ``store`` with assignment records for every existing
    reviewer submission (open OR considered/archived). Returns the
    number of records added.

    Designed for the **first-round** auto-balance: it gives reviewers
    credit for work they completed before the assignments system
    existed, so :func:`auto_balance` sees their prior work as load and
    distributes new sources accordingly. Idempotent — sources already
    in the reviewer's queue are silently skipped.

    Identity translation: submission files are keyed by slug
    (``reviewer_slug(name)``); the store keys on full reviewer names.
    ``name_for_slug`` is the lookup the caller is expected to build
    from tokens.yaml + any known names. Slugs without a matching name
    fall through to ``slug`` itself — better to credit a slug-keyed
    reviewer than to lose the record entirely.
    """
    submitting = all_submitting_reviewers(
        recommendations_dir, sources)
    n_added = 0
    for src, slugs in submitting.items():
        for slug in slugs:
            name = name_for_slug.get(slug, slug)
            bucket = store.assignments.setdefault(name, [])
            if any(r.source == src for r in bucket):
                continue
            bucket.append(AssignmentRecord(
                source=src, assigned_at=_utcnow_iso(),
                target_date=None, assigned_by=assigned_by,
            ))
            n_added += 1
    return n_added


def auto_balance(
    *,
    scored_sources: list[SourceDifficulty],
    reviewers: list[str],
    current_assignments: dict[str, list[str]],
    submitted_by: dict[str, set[str]],
    review_target: int = DEFAULT_REVIEW_TARGET,
) -> dict[str, list[str]]:
    """Greedy longest-processing-time-first (LPT) load balancer.

    Returns ``{reviewer: [source, ...]}`` ADDITIONS to append to the
    existing assignments — existing records are never removed or moved
    by this function. (Bulk reassignment is a separate explicit
    operation; see :func:`reassign_queue`.)

    Rules (in order):

    1. A source is processed only if ``review_target - len(committed) > 0``,
       where ``committed`` is the set of reviewers either currently
       assigned the source or who have already submitted it.
    2. Sources are processed by ``balance_weight`` desc (LPT) — the
       heaviest first. Ties tie-break on ``source`` name (stable).
    3. For each slot to fill, pick the eligible reviewer with the
       smallest current weight; ties break alphabetically on the
       reviewer's name (so the result is reproducible across runs).
    4. A reviewer is **eligible** if they're not already committed to
       that source.

    ``balance_weight`` (``sqrt(score)``) is the scheduling weight —
    raw scores have an 18× tail (one BL Lac vs. one median source),
    which would let one outlier consume a whole quota.
    """
    loads: dict[str, float] = {r: 0.0 for r in reviewers}
    committed_per_source: dict[str, set[str]] = {
        sd.source: set(submitted_by.get(sd.source, set()))
        for sd in scored_sources
    }
    weight_by_source = {sd.source: sd.balance_weight for sd in scored_sources}

    # Seed current load and "committed" sets from existing assignments.
    for reviewer in reviewers:
        for src in current_assignments.get(reviewer, []):
            if src in weight_by_source:
                loads[reviewer] += weight_by_source[src]
            committed_per_source.setdefault(src, set()).add(reviewer)

    additions: dict[str, list[str]] = {r: [] for r in reviewers}

    sources_sorted = sorted(
        scored_sources,
        key=lambda d: (-d.balance_weight, d.source),
    )
    for sd in sources_sorted:
        committed = committed_per_source.get(sd.source, set())
        slots = review_target - len(committed)
        if slots <= 0:
            continue
        eligible = [r for r in reviewers if r not in committed]
        # Stable tie-break on name keeps successive runs reproducible.
        eligible.sort(key=lambda r: (loads[r], r))
        for r in eligible[:slots]:
            additions[r].append(sd.source)
            loads[r] += sd.balance_weight
            committed.add(r)

    return additions


def apply_additions(
    store: AssignmentStore,
    additions: dict[str, list[str]],
    *,
    assigned_by: str = "",
    target_date: str | None = None,
) -> int:
    """Merge ``auto_balance`` output into the store. Returns the number
    of records added. Sources already in the reviewer's queue are
    silently skipped (idempotent re-runs are safe)."""
    now = _utcnow_iso()
    n_added = 0
    for reviewer, sources in additions.items():
        if not sources:
            continue
        bucket = store.assignments.setdefault(reviewer, [])
        existing = {r.source for r in bucket}
        for src in sources:
            if src in existing:
                continue
            bucket.append(AssignmentRecord(
                source=src, assigned_at=now,
                target_date=target_date, assigned_by=assigned_by,
            ))
            existing.add(src)
            n_added += 1
    return n_added


def remove_assignment(
    store: AssignmentStore, reviewer: str, source: str,
) -> bool:
    """Drop the named source from ``reviewer``'s queue. Returns True if
    a record was removed, False if there was nothing to remove. The
    reviewer's entry is left in the store (possibly empty) so they
    still appear on the dashboard's "The team" table."""
    bucket = store.assignments.get(reviewer)
    if not bucket:
        return False
    for i, rec in enumerate(bucket):
        if rec.source == source:
            del bucket[i]
            return True
    return False


def reassign_queue(
    store: AssignmentStore,
    *,
    from_reviewer: str, to_reviewer: str,
    submitted_by: dict[str, set[str]] | None = None,
) -> tuple[list[str], list[str]]:
    """Move all of ``from_reviewer``'s assignments to ``to_reviewer``.

    Returns ``(moved, skipped)`` source-name lists. A source is
    SKIPPED (not moved) when:

      * the target already has that source assigned, OR
      * the target has already submitted that source
        (per ``submitted_by``, when supplied).

    Skipped sources stay on the source reviewer — the admin can
    follow up with a manual fix. The ``assigned_at`` / ``assigned_by``
    timestamps on moved records are refreshed.
    """
    src_bucket = store.assignments.get(from_reviewer, [])
    if not src_bucket:
        return [], []
    tgt_bucket = store.assignments.setdefault(to_reviewer, [])
    tgt_sources = {r.source for r in tgt_bucket}
    submitted_by = submitted_by or {}
    moved: list[str] = []
    skipped: list[str] = []
    remaining: list[AssignmentRecord] = []
    now = _utcnow_iso()
    for rec in src_bucket:
        s = rec.source
        if s in tgt_sources or to_reviewer in submitted_by.get(s, set()):
            skipped.append(s)
            remaining.append(rec)
            continue
        tgt_bucket.append(AssignmentRecord(
            source=s, assigned_at=now,
            target_date=rec.target_date,
            assigned_by=from_reviewer,         # provenance: came from A
        ))
        tgt_sources.add(s)
        moved.append(s)
    store.assignments[from_reviewer] = remaining
    return moved, skipped
