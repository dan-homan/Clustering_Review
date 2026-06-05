"""``mojave-review-prune-drafts`` — remove already-applied ``current/`` drafts.

Old ``mojave-apply`` archived ``submitted/`` but left the matching ``current/``
draft behind, so a now-applied recommendation kept showing as a pending draft.
This removes ``current/<slug>.json`` drafts whose content equals an
already-applied recommendation for the same source (safe: a draft edited after
the apply won't match and is kept). Dry-run by default.

    mojave-review-prune-drafts --recommendations-dir ./recommendations        # preview
    mojave-review-prune-drafts --recommendations-dir ./recommendations --apply # delete
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..recommendations.store import prune_applied_current_drafts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mojave-review-prune-drafts")
    p.add_argument("--recommendations-dir", type=Path, required=True,
                   help="the recommendations/ directory")
    p.add_argument("--apply", action="store_true",
                   help="actually delete the drafts (default: dry-run preview)")
    args = p.parse_args(argv)

    if not args.recommendations_dir.is_dir():
        print(f"recommendations dir not found: {args.recommendations_dir}",
              file=sys.stderr)
        return 2

    pruned = prune_applied_current_drafts(
        args.recommendations_dir, execute=args.apply)
    verb = "Removed" if args.apply else "Would remove"
    print(f"{verb} {len(pruned)} already-applied current/ draft(s)"
          + (":" if pruned else "."))
    for src, slug in pruned:
        print(f"  {src}/current/{slug}.json")
    if pruned and not args.apply:
        print("\n(dry run — re-run with --apply to delete)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
