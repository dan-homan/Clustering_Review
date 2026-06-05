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
