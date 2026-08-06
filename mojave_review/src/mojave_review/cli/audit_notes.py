"""``mojave-review-audit-notes`` — find (and recover) reviewer comments that
never made it into ``notes/<source>.md``.

Stage-3 applies with decisions used to drop the round's reviewer comments while
archiving the submissions out of ``submitted/`` — so the prose survived only in
``considered/<date>/*.json``. This scans every submission on disk (open and
archived) and reports each comment whose text is nowhere in the source's notes
file. Comments already recorded — by any Stage-3 path or by a dated note you
pasted yourself — are skipped, so the report is the real gap and ``--apply`` is
safe to re-run.

    # what's missing (writes nothing)
    mojave-review-audit-notes --recommendations-dir ./recommendations

    # append a dated "Recovered reviewer comments" ledger entry per source
    mojave-review-audit-notes --recommendations-dir ./recommendations --apply
"""

from __future__ import annotations

import argparse
import sys
from datetime import date as _date
from pathlib import Path

from ..notes.audit import apply_recovery, audit_all
from ..notes.store import notes_dir_for


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mojave-review-audit-notes")
    p.add_argument("--recommendations-dir", type=Path, required=True,
                   help="the recommendations/ directory")
    p.add_argument("--notes-dir", type=Path, default=None,
                   help="notes dir (default: <recommendations-dir>/../notes)")
    p.add_argument("--source", action="append", default=None, metavar="SOURCE",
                   help="audit only this source (repeatable)")
    p.add_argument("--author", default="",
                   help="name to credit in the recovery entry heading")
    p.add_argument("--apply", action="store_true",
                   help="append the missing comments to notes/<source>.md "
                        "(default: report only)")
    args = p.parse_args(argv)

    recs = args.recommendations_dir
    if not recs.is_dir():
        print(f"recommendations dir not found: {recs}", file=sys.stderr)
        return 2
    notes_dir = args.notes_dir or notes_dir_for(recs)
    if args.apply and not notes_dir.is_dir():
        print(f"notes dir not found: {notes_dir}", file=sys.stderr)
        return 2

    audits = audit_all(recs, notes_dir, sources=args.source)
    if not audits:
        print("Every reviewer comment is recorded in notes/ — nothing missing.")
        return 0

    today = _date.today().isoformat()
    total = 0
    for a in audits:
        note = "" if a.notes_exists else "  [no notes file yet]"
        print(f"{a.source}: {a.n_missing} of {a.found} comment(s) "
              f"not recorded{note}")
        for line, origin in a.missing:
            print(f"    {line}   [{origin}]")
        total += a.n_missing
        if args.apply:
            path = apply_recovery(notes_dir, a, date=today, author=args.author)
            print(f"    -> appended to {path}")

    verb = "Recovered" if args.apply else "Missing"
    print(f"\n{verb}: {total} comment(s) across {len(audits)} source(s).")
    if not args.apply:
        print("(report only — re-run with --apply to append them to notes/)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
