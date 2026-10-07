"""``mojave-review-audit-robust`` — find / repair bad ``robust`` flags.

Scans every source's *current* ``merged_win_results.csv`` for two independent
defects and, with ``--apply``, repairs both in one pass:

1. **Inconsistent across epochs** — a long-standing bug let a cluster's
   ``robust`` flag differ between its own epochs. Repair: collapse to a single
   flag (earliest-epoch value; the core forced robust), the same normalization
   ``mojave-apply`` now runs.
2. **Robust but ineligible** — ``robust=True`` on a cluster with fewer than 5
   ``use_in_fit=True`` epochs, which the eligibility rule forbids (and which
   leaves the cluster unreviewable: the Robustness tab lists eligible clusters
   only). Repair: demote to ``robust=False``. Nothing caught these before —
   ``apply_recommendation`` only reconsiders clusters an *edit* touched, so a
   cluster born this way in the pipeline was never re-checked (three in the
   sample: ``0306+102u`` cl 9, ``1127-145u`` cl 9, ``0415+379u`` cl 999 — the
   last a row parked by the 999-overlap rule that inherited the core's flag).

The reverse case — an eligible cluster marked non-robust — is NOT a defect;
that is the normal reviewer judgement.

The prior CSV is backed up under ``backups/`` and the repair is logged to
``history.txt``. Dry-run by default. CSV-only — no production code / FITS data
needed, so it's safe to run anywhere (incl. the server).

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
    ELIGIBILITY_MIN_FIT, robust_inconsistencies, robust_ineligible,
    _normalize_robust_per_cluster, _normalize_robust_eligibility,
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
    n_inconsistent = 0
    n_ineligible = 0
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
        thin = robust_ineligible(df)
        if not bad and not thin:
            continue
        n_src += 1
        n_inconsistent += len(bad)
        n_ineligible += len(thin)
        parts = []
        if bad:
            parts.append(f"{len(bad)} inconsistent")
        if thin:
            parts.append(f"{len(thin)} robust-but-ineligible")
        print(f"{src.folder.name}: {', '.join(parts)} cluster(s)")
        for cid, vals in sorted(bad.items()):
            print(f"    cluster {cid}: robust values {vals}")
        for cid, n_fit in sorted(thin.items()):
            print(f"    cluster {cid}: robust=True but only {n_fit} "
                  f"use_in_fit=True epoch(s) (< {ELIGIBILITY_MIN_FIT})")

        if args.apply:
            # Consistency first: it picks the earliest-epoch value, which the
            # eligibility pass may then overrule with False. Both mutate
            # df['robust'] only.
            hist = _normalize_robust_per_cluster(df)
            hist += _normalize_robust_eligibility(df)
            idx = _next_backup_index(src.folder / "backups")
            _backup_existing(src.folder, src.file_prefix, idx)   # moves prior CSV
            df.to_csv(csv, index=False)
            header = (f"# {date.today().isoformat()} robust-flag repair "
                      f"(prior CSV -> backups/backup_{idx})")
            _append_history(src.folder, header, hist)
            n_repaired += 1
            print(f"    -> repaired; prior CSV saved as backups/backup_{idx}_*")

    print()
    found = (f"{n_inconsistent} inconsistent + {n_ineligible} "
             f"robust-but-ineligible cluster(s)")
    if args.apply:
        print(f"Repaired {found} across {n_repaired} source(s).")
    else:
        print(f"Found {found} across {n_src} source(s).")
        if n_src:
            print("(dry run — re-run with --apply to repair)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
