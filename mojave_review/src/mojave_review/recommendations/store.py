"""Read / write recommendation JSON files.

Layout on disk::

    <recommendations_dir>/
      <source>/
        <model_key>/
          <reviewer_slug>.json

A reviewer's review of "0003-066u current" lives in a different file from
their review of "0003-066u backup_001" — the two models are distinct
opinions worth keeping separate.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .schema import Recommendation


_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def reviewer_slug(reviewer: str) -> str:
    """Turn an arbitrary reviewer name/email into a safe filename component."""
    s = _SLUG_RE.sub("_", reviewer.strip()) or "anonymous"
    return s.lower()


def rec_path(
    recommendations_dir: Path, source: str, model: str, reviewer: str
) -> Path:
    return recommendations_dir / source / model / f"{reviewer_slug(reviewer)}.json"


def load_recommendation(
    recommendations_dir: Path, source: str, model: str, reviewer: str
) -> Recommendation:
    """Load the reviewer's existing recommendation, or return a fresh empty one."""
    p = rec_path(recommendations_dir, source, model, reviewer)
    if not p.is_file():
        return Recommendation(source=source, model=model, reviewer=reviewer)
    with p.open() as f:
        data = json.load(f)
    return Recommendation.from_dict(data)


def save_recommendation(
    recommendations_dir: Path, rec: Recommendation, *, model_sha: str | None = None,
) -> Path:
    """Write atomically (temp file + rename). Returns the file path."""
    if model_sha is not None:
        rec.model_sha = model_sha
    rec.touch()
    p = rec_path(recommendations_dir, rec.source, rec.model, rec.reviewer)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rec.to_dict(), indent=2, sort_keys=False)
    # Atomic write — never leave a partial file under p if something crashes.
    fd, tmp_path = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent), suffix=".part")
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


def list_reviewer_files(recommendations_dir: Path) -> list[Path]:
    """For admin/export — every reviewer JSON under the directory."""
    if not recommendations_dir.is_dir():
        return []
    return sorted(recommendations_dir.glob("*/*/*.json"))


# ---------------------------------------------------------------------------
# Submission — the reviewer's "I'm done with this source" snapshot
# ---------------------------------------------------------------------------
# Each reviewer can have at most one submitted recommendation per source at
# a time (resubmit overwrites). Submissions live at
# `<recs>/<source>/submitted/<slug>.json`. These are exactly what the
# multi-reviewer model dropdown surfaces as `Rec: <slug>` entries — in-progress
# drafts under `<source>/current/` stay private until the reviewer submits.


def submission_path(
    recommendations_dir: Path, source: str, reviewer: str,
) -> Path:
    return recommendations_dir / source / "submitted" / f"{reviewer_slug(reviewer)}.json"


def is_submitted(
    recommendations_dir: Path, source: str, reviewer: str,
) -> bool:
    return submission_path(recommendations_dir, source, reviewer).is_file()


def submitted_at(
    recommendations_dir: Path, source: str, reviewer: str,
) -> str | None:
    """``updated_at`` of the submitted JSON, or None if not submitted."""
    p = submission_path(recommendations_dir, source, reviewer)
    if not p.is_file():
        return None
    try:
        with p.open() as f:
            return json.load(f).get("updated_at")
    except Exception:
        return None


def save_submitted(
    recommendations_dir: Path, rec: Recommendation, *, model_sha: str | None = None,
) -> Path:
    """Write atomically to ``<recs>/<source>/submitted/<slug>.json`` —
    overwrites any previous submission from the same reviewer."""
    if model_sha is not None:
        rec.model_sha = model_sha
    rec.touch()
    p = submission_path(recommendations_dir, rec.source, rec.reviewer)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rec.to_dict(), indent=2, sort_keys=False)
    fd, tmp_path = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent), suffix=".part")
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
# Deletion — used by the "Reset recommendation" dialog.
# ---------------------------------------------------------------------------


def delete_recommendation(
    recommendations_dir: Path, source: str, model: str, reviewer: str,
) -> bool:
    """Delete the reviewer's draft (autosaved) recommendation file for
    (source, model). Returns True if a file was removed, False if none
    existed."""
    p = rec_path(recommendations_dir, source, model, reviewer)
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False


def archive_considered_submissions(
    recommendations_dir: Path, source: str, slugs: list[str],
    *, date: str, execute: bool = True,
) -> list[Path]:
    """After a Stage-3 reconciliation is applied, move the reviewer
    submissions that were folded in out of ``<source>/submitted/`` into
    ``<source>/considered/<date>/`` — preserving the full submissions while
    clearing them from "open suggestions" and the ``Rec:`` dropdown. A later
    re-submission writes a fresh ``submitted/<slug>.json`` and reappears as
    open. Returns the destination paths (that were / would be) written."""
    src_dir = Path(recommendations_dir) / source
    sub = src_dir / "submitted"
    dst_dir = src_dir / "considered" / date
    moved: list[Path] = []
    for slug in slugs:
        p = sub / f"{slug}.json"
        if not p.is_file():
            continue
        target = dst_dir / f"{slug}.json"
        n = 2
        while target.exists():
            target = dst_dir / f"{slug}_{n}.json"
            n += 1
        if execute:
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(target))
        moved.append(target)
    return moved


def delete_submission(
    recommendations_dir: Path, source: str, reviewer: str,
) -> bool:
    """Delete the reviewer's submitted recommendation for this source.
    Returns True if a file was removed, False if none existed."""
    p = submission_path(recommendations_dir, source, reviewer)
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False


def _content_key(raw: dict) -> dict:
    """Normalized recommendation content for equality, ignoring volatile
    metadata (timestamps / model_sha). Round-trips through ``Recommendation``
    so two files compare equal regardless of field ordering / defaults."""
    d = Recommendation.from_dict(raw).to_dict()
    d["updated_at"] = None
    d["model_sha"] = None
    return d


def prune_applied_current_drafts(
    recommendations_dir: Path, *, execute: bool = False,
) -> list[tuple[str, str]]:
    """Remove ``current/<slug>.json`` drafts that duplicate an already-applied
    recommendation for the same source.

    Old ``mojave-apply`` archived ``submitted/`` but left the matching
    ``current/`` draft behind, so a now-applied recommendation kept showing as a
    pending draft. This finds drafts whose content **equals** an applied rec
    (ignoring timestamps / model_sha — safe: a draft edited after the apply
    won't match and is kept) and deletes them when ``execute`` is True.

    Returns the ``(source, slug)`` list pruned (or that *would* be pruned in a
    dry run).
    """
    recommendations_dir = Path(recommendations_dir)
    pruned: list[tuple[str, str]] = []
    if not recommendations_dir.is_dir():
        return pruned
    for src_dir in sorted(p for p in recommendations_dir.iterdir() if p.is_dir()):
        cur_dir, app_dir = src_dir / "current", src_dir / "applied"
        if not cur_dir.is_dir() or not app_dir.is_dir():
            continue
        applied_keys: list[dict] = []
        for ap in app_dir.rglob("*.json"):
            try:
                applied_keys.append(_content_key(json.loads(ap.read_text())))
            except Exception:
                continue
        if not applied_keys:
            continue
        for draft in sorted(cur_dir.glob("*.json")):
            try:
                dk = _content_key(json.loads(draft.read_text()))
            except Exception:
                continue
            if any(dk == ak for ak in applied_keys):
                pruned.append((src_dir.name, draft.stem))
                if execute:
                    draft.unlink()
    return pruned


# ---------------------------------------------------------------------------
# Multi-reviewer support — used by the model dropdown to show "Rec: <slug>"
# entries from other reviewers' submitted files in <recs>/<source>/submitted/.
# ---------------------------------------------------------------------------


def count_submissions(recommendations_dir: Path, source: str) -> int:
    """How many reviewers currently have an open *submitted* recommendation
    for this source (files under ``<recs>/<source>/submitted/*.json``).

    After a Stage-3 apply the folded submissions are moved to
    ``considered/<date>/`` (see ``archive_considered_submissions``), so a
    finalized source reads 0 here until a *new* submission lands."""
    p = Path(recommendations_dir) / source / "submitted"
    if not p.is_dir():
        return 0
    return sum(1 for f in p.glob("*.json") if f.stem)


def is_finalized(recommendations_dir: Path, source: str) -> bool:
    """True once Stage 3 is *done* for this source.

    Read from the source's notes ``Status:`` line (``Stage 3 done · applied
    <date>``), NOT from the ``applied/`` archive dir: ``mojave-apply`` archives
    a JSON to ``applied/`` for *any* apply — including Stage-2 baseline applies
    — so an archive there does not mean Stage 3 finished. The notes status is
    only set to "Stage 3 done" by the Stage-3 aggregation apply
    (``ui/callbacks._apply_aggregated``)."""
    from ..notes.store import notes_dir_for, read_note, get_status
    md = read_note(notes_dir_for(recommendations_dir), source)
    if not md:
        return False
    return get_status(md).strip().lower().startswith("stage 3 done")


def source_badge(recommendations_dir: Path, source: str) -> str:
    """Bracketed status badge shown beside a source in the picker.

    - not finalized        -> ``[N]``         (N = open submitted recs, 0 = none yet)
    - finalized, no new rec -> ``[final]``
    - finalized + M new recs-> ``[final - M]`` (M new submissions since apply)
    """
    n = count_submissions(recommendations_dir, source)
    if is_finalized(recommendations_dir, source):
        return "[final]" if n == 0 else f"[final - {n}]"
    return f"[{n}]"


def list_other_reviewer_slugs(
    recommendations_dir: Path, source: str, exclude_slug: str,
) -> list[str]:
    """Reviewer slugs (filename stems) with a *submitted* recommendation for
    this source. Only submissions are surfaced — in-progress drafts under
    `<source>/current/` stay private until the reviewer clicks Submit."""
    p = recommendations_dir / source / "submitted"
    if not p.is_dir():
        return []
    return sorted(
        f.stem for f in p.glob("*.json")
        if f.stem and f.stem != exclude_slug
    )


def load_recommendation_by_slug(
    recommendations_dir: Path, source: str, model: str, slug: str,
) -> "Recommendation | None":
    """Load the named slug's recommendation file directly, or None if absent."""
    import json
    p = recommendations_dir / source / model / f"{slug}.json"
    if not p.is_file():
        return None
    with p.open() as f:
        data = json.load(f)
    return Recommendation.from_dict(data)
