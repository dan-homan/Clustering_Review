"""``mojave-review-audit-robust`` — find / repair per-epoch robust inconsistencies.

A long-standing bug let a cluster's ``robust`` flag differ across its epochs in
the saved ``merged_win_results.csv``. This scans every source's *current* CSV
for such inconsistencies and, with ``--apply``, repairs them: each affected
cluster is collapsed to a single flag (earliest-epoch value; the core forced
robust) via the same normalization ``mojave-apply`` now runs. The prior CSV is
backed up under ``backups/`` and the repair is logged to ``history.txt``.
Dry-run by default. CSV-only — no production code / FITS data needed, so it's
safe to run anywhere (incl. the server).

    mojave-review-audit-robust --results-dir ./Results            # preview
    mojave-review-audit-robust --results-dir ./Results --apply    # repair
    mojave-review-audit-robust --results-dir ./Results --source 0003-066u_1994.00-2026.00
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from ..data.loader import list_sources
from ..recommendations.apply import (
    robust_inconsistencies, _normalize_robust_per_cluster,
)
from .apply import _next_backup_index, _backup_existing, _append_history


def _csv_path(src) -> Path:
    return src.folder / f"{src.file_prefix}.merged_win_results.csv"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mojave-review-audit-robust")
    p.add_argument("--results-dir", type=Path, required=True,
                   help="the Results/ directory to scan")
    p.add_argument("--source", default=None,
                   help="limit to one source folder name (default: all)")
    p.add_argument("--apply", action="store_true",
                   help="repair the CSVs (default: dry-run preview)")
    args = p.parse_args(argv)

    if not args.results_dir.is_dir():
        print(f"results dir not found: {args.results_dir}", file=sys.stderr)
        return 2

    sources = list_sources(args.results_dir)
    if args.source:
        sources = [s for s in sources if s.folder.name == args.source]
        if not sources:
            print(f"source not found: {args.source}", file=sys.stderr)
            return 2

    n_src = 0
    n_clusters = 0
    n_repaired = 0
    for src in sources:
        csv = _csv_path(src)
        if not csv.is_file():
            continue
        try:
            df = pd.read_csv(csv)
        except Exception as e:           # never let one bad file stop the sweep
            print(f"  ! could not read {src.folder.name}: {e}", file=sys.stderr)
            continue
        bad = robust_inconsistencies(df)
        if not bad:
            continue
        n_src += 1
        n_clusters += len(bad)
        print(f"{src.folder.name}: {len(bad)} inconsistent cluster(s)")
        for cid, vals in sorted(bad.items()):
            print(f"    cluster {cid}: robust values {vals}")

        if args.apply:
            hist = _normalize_robust_per_cluster(df)   # mutates df['robust'] only
            idx = _next_backup_index(src.folder / "backups")
            _backup_existing(src.folder, src.file_prefix, idx)   # moves prior CSV
            df.to_csv(csv, index=False)
            header = (f"# {date.today().isoformat()} robust-consistency repair "
                      f"(prior CSV -> backups/backup_{idx})")
            _append_history(src.folder, header, hist)
            n_repaired += 1
            print(f"    -> repaired; prior CSV saved as backups/backup_{idx}_*")

    print()
    if args.apply:
        print(f"Repaired {n_clusters} inconsistent cluster(s) across "
              f"{n_repaired} source(s).")
    else:
        print(f"Found {n_clusters} inconsistent cluster(s) across {n_src} "
              f"source(s).")
        if n_src:
            print("(dry run — re-run with --apply to repair)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
