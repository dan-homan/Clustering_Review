"""``mojave-review-difficulty`` — per-source review-load score.

Prints a sortable table of every source under ``--results-dir``,
showing ``N_epochs``, mean features per epoch, the composite score
(``N_epochs × mean_features``), and a star rating (quintile across the
scored population). Useful for sanity-checking the formula before any
of it drives an assignments UI, and as a one-shot sortable view of
"which sources are the heaviest lift right now".

    mojave-review-difficulty --results-dir ./Results
    mojave-review-difficulty --results-dir ./Results --sort score
    mojave-review-difficulty --results-dir ./Results --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..data.difficulty import score_all, star_ratings
from ..data.loader import list_sources


def _format_table(rows: list[dict]) -> str:
    headers = ["source", "N_epochs", "mean_feat", "score", "rating"]
    widths = [
        max(len(headers[0]), max((len(r["source"]) for r in rows), default=0)),
        max(len(headers[1]), 8),
        max(len(headers[2]), 9),
        max(len(headers[3]), 7),
        max(len(headers[4]), 6),
    ]
    line = (
        f"{headers[0]:<{widths[0]}}  "
        f"{headers[1]:>{widths[1]}}  "
        f"{headers[2]:>{widths[2]}}  "
        f"{headers[3]:>{widths[3]}}  "
        f"{headers[4]:<{widths[4]}}"
    )
    sep = "-" * len(line)
    out = [line, sep]
    for r in rows:
        out.append(
            f"{r['source']:<{widths[0]}}  "
            f"{r['n_epochs']:>{widths[1]}d}  "
            f"{r['mean_features']:>{widths[2]}.2f}  "
            f"{r['score']:>{widths[3]}.1f}  "
            f"{'★' * r['rating']:<{widths[4]}}"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mojave-review-difficulty")
    p.add_argument("--results-dir", type=Path, required=True,
                   help="the Results/ directory to scan")
    p.add_argument("--sort", choices=["score", "name", "epochs"],
                   default="score",
                   help="sort order (default: score, descending)")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of a table")
    args = p.parse_args(argv)

    if not args.results_dir.is_dir():
        print(f"results dir not found: {args.results_dir}", file=sys.stderr)
        return 2

    sources = list_sources(args.results_dir)
    if not sources:
        print(f"no sources found under {args.results_dir}", file=sys.stderr)
        return 1

    scored = score_all(sources)
    ratings = star_ratings([s.score for s in scored])

    rows = [
        {
            "source": s.source,
            "folder": s.folder,
            "n_epochs": s.n_epochs,
            "mean_features": s.mean_features,
            "score": s.score,
            "rating": r,
        }
        for s, r in zip(scored, ratings)
    ]
    if args.sort == "score":
        rows.sort(key=lambda r: r["score"], reverse=True)
    elif args.sort == "epochs":
        rows.sort(key=lambda r: r["n_epochs"], reverse=True)
    else:
        rows.sort(key=lambda r: r["source"])

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(_format_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
