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


def source_phase(recommendations_dir: Path, source: str) -> str:
    """Review phase of a source, read from its notes ``Status:`` line.

    Returns one of:

    - ``"stage1"`` — "Stage 1 still needs review" (not yet reviewed)
    - ``"stage2"`` — "Stage 1 done" or "Stage 2 in progress" (baseline being
      built; not yet open for reviewer recommendations)
    - ``"open"``   — "Stage 2 done" (ready for reviewer recommendations)
    - ``"final"``  — "Stage 3 done …" (aggregation applied)

    We deliberately read the notes status rather than the ``applied/`` archive:
    ``mojave-apply`` archives a JSON for *any* apply (incl. Stage-2 baseline),
    so the archive is not a reliable Stage-3 signal. An unknown / missing
    status falls back to ``"open"`` so a source with odd status text isn't
    accidentally locked out of recommendations."""
    from ..notes.store import notes_dir_for, read_note, get_status
    md = read_note(notes_dir_for(recommendations_dir), source)
    status = (get_status(md) if md else "").strip().lower()
    if status.startswith("stage 3 done"):
        return "final"
    if status.startswith("stage 2 done"):
        return "open"
    if status.startswith("stage 1 done") or status.startswith("stage 2 in progress"):
        return "stage2"
    if status.startswith("stage 1"):     # "stage 1 still needs review"
        return "stage1"
    return "open"


def is_finalized(recommendations_dir: Path, source: str) -> bool:
    """True once Stage 3 is *done* for this source (see ``source_phase``)."""
    return source_phase(recommendations_dir, source) == "final"


def source_needs_discussion(recommendations_dir: Path, source: str) -> bool:
    """True when the admin has flagged this source for further discussion (the
    Stage-3 ``Needs Discussion`` button). Detected as a ``needs discussion``
    suffix on the notes ``Status:`` line — the source stays in Stage 2
    (phase ``open``) but the picker shows a global ``needs discussion`` tag
    so every reviewer sees the flag."""
    from ..notes.store import notes_dir_for, read_note, get_status
    md = read_note(notes_dir_for(recommendations_dir), source)
    status = (get_status(md) if md else "").strip().lower()
    return "needs discussion" in status


def recommendations_locked(
    recommendations_dir: Path, source: str, *, admin: bool,
) -> bool:
    """True when reviewer recommendations should be blocked for this source.

    Sources still in Stage 1 / Stage 2 are not open for recommendations, so
    the panel is locked for ordinary reviewers. Admins (``--admin``) are never
    locked — they need to drive Stage 2 / Stage 3."""
    if admin:
        return False
    return source_phase(recommendations_dir, source) in ("stage1", "stage2")


def source_badge(recommendations_dir: Path, source: str) -> str:
    """Bracketed status badge shown beside a source in the picker.

    - ``[stage 1]`` — Stage 1 still needs review
    - ``[stage 2]`` — Stage 1 done / Stage 2 in progress (baseline being built)
    - ``[N]``       — ready for recommendations; N open submitted recs (0 = none)
    - ``[final]``   — Stage 3 applied, no new recs
    - ``[final - M]`` — Stage 3 applied + M new submissions since
    """
    phase = source_phase(recommendations_dir, source)
    if phase == "stage1":
        return "[stage 1]"
    if phase == "stage2":
        return "[stage 2]"
    n = count_submissions(recommendations_dir, source)
    if phase == "final":
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


def _considered_has_slug(date_dir: Path, slug: str) -> bool:
    """True if a considered-archive date dir holds a submission from
    ``slug`` — matches ``<slug>.json`` and the ``<slug>_N.json``
    collision-renamed variants (see ``archive_considered_submissions``)."""
    for f in date_dir.glob("*.json"):
        st = f.stem
        if st == slug or st.startswith(slug + "_"):
            return True
    return False


_COLLISION_RE = re.compile(r"_\d+$")


def _applied_review_slugs(src_dir: Path) -> set[str]:
    """Reviewer slugs with a recommendation archived under
    ``applied/<date>__<slug>.json`` — the admin's **Stage-2 baseline**
    applies (``mojave-apply --recommendation <own JSON>``). These are
    real reviews of the source, just archived to ``applied/`` rather than
    ``considered/``. Aggregated Stage-3 applies
    (``<date>__aggregated[_N].json``) carry no single reviewer and are
    excluded."""
    applied = src_dir / "applied"
    out: set[str] = set()
    if not applied.is_dir():
        return out
    for f in applied.glob("*.json"):
        stem = f.stem
        if "__" not in stem:
            continue
        slug = stem.split("__", 1)[1]
        if slug == "aggregated" or slug.startswith("aggregated_"):
            continue
        # Same-source/date collisions become <slug>_N — fold back to the
        # base slug so a re-applied reviewer is listed once (parity with
        # the considered-archive handling).
        out.add(_COLLISION_RE.sub("", slug))
    return out


def _reviewer_submitted_source(src_dir: Path, slug: str) -> bool:
    """Has ``slug`` submitted a review of this one source *at any time*?
    Open ``submitted/`` OR Stage-3-archived ``considered/`` OR
    Stage-2-archived ``applied/<date>__<slug>.json``."""
    if (src_dir / "submitted" / f"{slug}.json").is_file():
        return True
    considered = src_dir / "considered"
    if considered.is_dir() and any(
        _considered_has_slug(d, slug)
        for d in considered.iterdir() if d.is_dir()
    ):
        return True
    return slug in _applied_review_slugs(src_dir)


def _source_dirs(recommendations_dir: Path):
    """Yield the per-source subdirs of ``recommendations_dir``, skipping
    the ``_admin`` (and any other ``_``-prefixed) bookkeeping dir."""
    rd = Path(recommendations_dir)
    if not rd.is_dir():
        return
    for src_dir in rd.iterdir():
        if src_dir.is_dir() and not src_dir.name.startswith("_"):
            yield src_dir


def reviewer_submitted_sources(
    recommendations_dir: Path, reviewer: str,
) -> set[str]:
    """Distinct sources this reviewer has submitted a review for *at any
    time* — currently-open ``submitted/``, Stage-3-archived
    ``considered/<date>/``, OR Stage-2-archived
    ``applied/<date>__<slug>.json``. Matched by slug. Used for the
    dashboard's lifetime "reviews submitted" count (independent of
    whether the source was ever assigned)."""
    slug = reviewer_slug(reviewer)
    if not slug:
        return set()
    return {
        src_dir.name for src_dir in _source_dirs(recommendations_dir)
        if _reviewer_submitted_source(src_dir, slug)
    }


def reviewer_in_progress_sources(
    recommendations_dir: Path, reviewer: str,
) -> set[str]:
    """Distinct sources where this reviewer has a non-empty in-progress
    draft (``current/<slug>.json``) and has NOT submitted it *in any
    form* — open, considered, or applied baseline. (A leftover draft
    beside an already-archived submission is completed work, not "in
    progress".) Mirrors the ``in_progress`` rule of
    ``assignments.assignment_status`` swept across every source,
    regardless of assignment."""
    slug = reviewer_slug(reviewer)
    if not slug:
        return set()
    out: set[str] = set()
    for src_dir in _source_dirs(recommendations_dir):
        src = src_dir.name
        if _reviewer_submitted_source(src_dir, slug):
            continue
        draft = load_recommendation(
            recommendations_dir, src, "current", reviewer)
        if not draft.is_empty():
            out.add(src)
    return out


def all_review_submitters(
    recommendations_dir: Path, sources: list[str],
) -> dict[str, set[str]]:
    """``{source: {reviewer_slug, ...}}`` for **every review ever
    submitted** of each source: open ``submitted/`` ∪ Stage-3-archived
    ``considered/`` ∪ Stage-2-archived ``applied/<date>__<slug>.json``.

    Drives the dashboard Source-progress "Reviews" column (a complete
    roster of who has reviewed the object, including archived Stage-2 and
    Stage-3 work). Collision-renamed considered files (``<slug>_N.json``)
    are folded back to the base slug so a re-archived reviewer is listed
    once."""
    rd = Path(recommendations_dir)
    out: dict[str, set[str]] = {}
    for src in sources:
        src_dir = rd / src
        slugs: set[str] = set()
        sub = src_dir / "submitted"
        if sub.is_dir():
            slugs.update(f.stem for f in sub.glob("*.json") if f.stem)
        considered = src_dir / "considered"
        if considered.is_dir():
            for d in considered.iterdir():
                if not d.is_dir():
                    continue
                for f in d.glob("*.json"):
                    if f.stem:
                        slugs.add(_COLLISION_RE.sub("", f.stem))
        slugs.update(_applied_review_slugs(src_dir))
        out[src] = slugs
    return out


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
