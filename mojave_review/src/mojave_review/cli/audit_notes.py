"""``mojave-review-audit-notes`` — find (and recover) reviewer comments that
never made it into ``notes/<source>.md``.

Stage-3 applies with decisions used to drop the round's reviewer comments while
archiving the submissions out of ``submitted/`` — so the prose survived only in
``considered/<date>/*.json``. This scans every submission on disk and reports
each comment whose text is nowhere in the source's notes file. Comments already
recorded — by any Stage-3 path or by a dated note you pasted yourself — are
skipped, so the report is the real gap and ``--apply`` is safe to re-run.

Only comments from ARCHIVED submissions (``considered/``, ``applied/``) count
as missing: nothing in the app shows those any more, so an unrecorded one is
lost. Comments in OPEN ``submitted/`` recommendations are in flight, not lost —
the notes panel renders them live and the Stage-3 ending will record them — so
they are summarized, not listed, unless you ask for ``--include-open``. That
also covers reviews submitted after a source was finalized: they wait for the
next Stage-3 round like any other open work.

    # what's actually lost (writes nothing)
    mojave-review-audit-notes --recommendations-dir ./recommendations

    # ...plus the in-flight comments, for a full picture
    mojave-review-audit-notes --recommendations-dir ./recommendations --include-open

    # append a dated "Recovered reviewer comments" ledger entry per source
    # (archived comments only — in-flight ones are left for Stage 3)
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
    p.add_argument("--include-open", action="store_true",
                   help="also list comments from open (unarchived) "
                        "submissions — in flight, not lost; never written")
    p.add_argument("--apply", action="store_true",
                   help="append the missing ARCHIVED comments to "
                        "notes/<source>.md (default: report only)")
    args = p.parse_args(argv)

    recs = args.recommendations_dir
    if not recs.is_dir():
        print(f"recommendations dir not found: {recs}", file=sys.stderr)
        return 2
    notes_dir = args.notes_dir or notes_dir_for(recs)
    if args.apply and not notes_dir.is_dir():
        print(f"notes dir not found: {notes_dir}", file=sys.stderr)
        return 2

    # Always collect both classes — the tail count of in-flight comments has to
    # cover sources that have ONLY those, not just the ones we list.
    audits = audit_all(recs, notes_dir, sources=args.source, include_open=True)
    today = _date.today().isoformat()
    total = n_open = 0
    n_sources = 0

    for a in audits:
        if a.n_missing:
            n_sources += 1
            note = "" if a.notes_exists else "  [no notes file yet]"
            print(f"{a.source}: {a.n_missing} of {a.found} comment(s) "
                  f"lost — archived, not in notes{note}")
            for line, origin in a.missing:
                print(f"    {line}   [{origin}]")
            total += a.n_missing
            if args.apply:
                path = apply_recovery(notes_dir, a, date=today,
                                      author=args.author)
                print(f"    -> appended to {path}")
        n_open += a.n_open
        if args.include_open and a.n_open:
            print(f"{a.source}: {a.n_open} comment(s) in flight "
                  f"(open submissions — Stage 3 will record them)")
            for line, _ in a.open_only:
                print(f"    {line}")

    if not total:
        print("No lost comments — every archived reviewer comment is in notes/.")
    else:
        verb = "Recovered" if args.apply else "Lost"
        print(f"\n{verb}: {total} comment(s) across {n_sources} source(s).")
        if not args.apply:
            print("(report only — re-run with --apply to append them to notes/)")
    if n_open and not args.include_open:
        print(f"\n{n_open} further comment(s) sit in open submissions — in "
              f"flight, not lost. Re-run with --include-open to see them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
