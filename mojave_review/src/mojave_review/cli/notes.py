"""``mojave-review-notes`` — manage the per-source notes lab-notebook.

Currently one subcommand:

    mojave-review-notes seed --from <google-doc-export.md> \
        --results-dir ./Results [--notes-dir ./notes] \
        [--recommendations-dir ./recommendations] [--force]

Seeds notes/<source>.md (Stages 1-2) from the exported Google doc. See
docs/review_workflow.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..notes import store
from ..notes.seed import seed_notes


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mojave-review-notes")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="seed notes/<source>.md from a Google-doc export")
    s.add_argument("--from", dest="doc", type=Path, required=True,
                   help="path to the exported Google doc (Markdown or plain text)")
    s.add_argument("--results-dir", type=Path, required=True,
                   help="Results/ dir, to resolve designations to source folders")
    s.add_argument("--notes-dir", type=Path, default=None,
                   help="output notes dir (default: <recommendations-dir>/../notes)")
    s.add_argument("--recommendations-dir", type=Path, default=None,
                   help="used to derive the default notes dir")
    s.add_argument("--force", action="store_true",
                   help="overwrite existing notes files")
    s.add_argument("--include-stage2", action="store_true",
                   help="also import the doc's existing Step 2 notes "
                        "(default: Stage 1 only; Stage 2 is added via the app)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "seed":
        return _seed(args)
    return 2


def _seed(args) -> int:
    if not args.doc.is_file():
        print(f"doc not found: {args.doc}", file=sys.stderr)
        return 2
    notes_dir = args.notes_dir
    if notes_dir is None:
        recs = args.recommendations_dir or (args.results_dir.parent / "recommendations")
        notes_dir = store.notes_dir_for(recs)
    text = args.doc.read_text()
    res = seed_notes(text, notes_dir, args.results_dir,
                     force=args.force, include_stage2=args.include_stage2)

    print(f"notes dir: {notes_dir}")
    print(f"  wrote   {len(res.written)} file(s)")
    if res.skipped_existing:
        print(f"  skipped {len(res.skipped_existing)} existing "
              f"(use --force to overwrite)")
    if res.skipped_empty:
        print(f"  skipped {len(res.skipped_empty)} with no notes "
              f"(use --include-empty to scaffold them)")
    if res.unmatched:
        print(f"  {len(res.unmatched)} designation(s) had no matching source "
              f"folder under --results-dir")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
