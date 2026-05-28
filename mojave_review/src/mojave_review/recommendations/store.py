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
# Multi-reviewer support — used by the model dropdown to show "Rec: <slug>"
# entries from other reviewers' files in <recs>/<source>/current/.
# ---------------------------------------------------------------------------


def list_other_reviewer_slugs(
    recommendations_dir: Path, source: str, exclude_slug: str,
) -> list[str]:
    """Reviewer slugs (filename stems) with a recommendation for source/current."""
    p = recommendations_dir / source / "current"
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
