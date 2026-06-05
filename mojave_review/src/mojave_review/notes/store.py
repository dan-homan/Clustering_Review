"""Read / write the per-source notes markdown file.

Layout on disk (alongside ``recommendations/``)::

    notes/<source>.md

The file has three machine-editable sections delimited by HTML-comment
markers so tooling can update one without disturbing the hand-written prose
in the others:

    <!-- stage1:begin --> … <!-- stage1:end -->     (Stage 1 brief review)
    <!-- stage2:begin --> … <!-- stage2:end -->     (Stage 2 baseline model)
    <!-- ledger:begin --> … <!-- ledger:end -->     (append-only decisions log)

See docs/review_workflow.md for the full design.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

# Section names, in file order.
SECTIONS = ("stage1", "stage2", "ledger")


def notes_dir_for(recommendations_dir: Path) -> Path:
    """Default notes directory: a sibling of ``recommendations/``."""
    return Path(recommendations_dir).parent / "notes"


def note_path(notes_dir: Path, source: str) -> Path:
    return Path(notes_dir) / f"{source}.md"


def _erange(emin: float | None, emax: float | None) -> str:
    if emin is None or emax is None:
        return ""
    return f"({emin:.2f}–{emax:.2f})"   # en dash


def scaffold(
    source: str, emin: float | None = None, emax: float | None = None,
    *, status: str = "", stage1: str = "", stage2: str = "", ledger: str = "",
) -> str:
    """Build a fresh notes markdown document for one source."""
    head = f"# {source}"
    er = _erange(emin, emax)
    if er:
        head += f"  {er}"
    return (
        f"{head}\n"
        f"Status: {status}\n\n"
        f"## Stage 1 — Brief review\n"
        f"<!-- stage1:begin -->\n{stage1.strip()}\n<!-- stage1:end -->\n\n"
        f"## Stage 2 — Baseline model\n"
        f"<!-- stage2:begin -->\n{stage2.strip()}\n<!-- stage2:end -->\n\n"
        f"## Decisions & applied history\n"
        f"<!-- ledger:begin -->\n{ledger.strip()}\n<!-- ledger:end -->\n"
    )


def _markers(name: str) -> tuple[str, str]:
    if name not in SECTIONS:
        raise ValueError(f"unknown section {name!r}; expected one of {SECTIONS}")
    return f"<!-- {name}:begin -->", f"<!-- {name}:end -->"


def get_section(md: str, name: str) -> str:
    """Return the text between a section's begin/end markers (stripped),
    or '' if the section isn't present."""
    begin, end = _markers(name)
    m = re.search(re.escape(begin) + r"\n?(.*?)\n?" + re.escape(end), md, re.DOTALL)
    return m.group(1).strip() if m else ""


def set_section(md: str, name: str, content: str) -> str:
    """Replace a section's content (between its markers). Raises if the
    section markers aren't found (the file should be a scaffold)."""
    begin, end = _markers(name)
    pat = re.compile(re.escape(begin) + r"\n?.*?\n?" + re.escape(end), re.DOTALL)
    repl = f"{begin}\n{content.strip()}\n{end}"
    new, n = pat.subn(lambda _m: repl, md, count=1)
    if n == 0:
        raise ValueError(f"section {name!r} markers not found")
    return new


def append_ledger(md: str, entry: str) -> str:
    """Append a new entry to the ledger section, after any existing entries
    (newest last). The ledger is append-only."""
    existing = get_section(md, "ledger")
    body = (existing + "\n\n" + entry.strip()).strip() if existing else entry.strip()
    return set_section(md, "ledger", body)


_STATUS_RE = re.compile(r"^Status:.*$", re.MULTILINE)


def get_status(md: str) -> str:
    """Text of the ``Status:`` line (without the prefix), or ''."""
    m = _STATUS_RE.search(md)
    return m.group(0)[len("Status:"):].strip() if m else ""


def set_status(md: str, status: str) -> str:
    """Replace the ``Status:`` line (or insert one after the title)."""
    line = f"Status: {status}"
    if _STATUS_RE.search(md):
        return _STATUS_RE.sub(lambda _m: line, md, count=1)
    lines = md.split("\n")
    lines.insert(1 if lines else 0, line)
    return "\n".join(lines)


def read_note(notes_dir: Path, source: str) -> str | None:
    p = note_path(notes_dir, source)
    if not p.is_file():
        return None
    return p.read_text()


def write_note(notes_dir: Path, source: str, md: str) -> Path:
    """Atomically write the notes file (temp + rename)."""
    p = note_path(notes_dir, source)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent), suffix=".part")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(md)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return p
