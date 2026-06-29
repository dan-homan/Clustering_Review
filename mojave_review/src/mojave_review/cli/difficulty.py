"""``mojave-review-difficulty`` — per-source review-load score.

Prints a sortable table of every source under ``--results-dir``,
showing ``N_epochs``, mean features per epoch, the composite score
(``N_epochs × mean_features``), the balance weight (sqrt(score) — what
the later auto-balance algorithm will consume), and a star rating with
an optional ⚠ outlier flag. A summary footer reports the score
distribution and per-star counts so any one source can be located in
context without re-running with ``--json``.

    mojave-review-difficulty --results-dir ./Results
    mojave-review-difficulty --results-dir ./Results --sort score
    mojave-review-difficulty --results-dir ./Results --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from ..data.difficulty import score_all, score_stats
from ..data.loader import list_sources


def _format_table(rows: list[dict]) -> str:
    headers = ["source", "N_epochs", "mean_feat", "score", "bal_w", "rating"]
    widths = [
        max(len(headers[0]), max((len(r["source"]) for r in rows), default=0)),
        max(len(headers[1]), 8),
        max(len(headers[2]), 9),
        max(len(headers[3]), 7),
        max(len(headers[4]), 6),
        max(len(headers[5]), 8),
    ]
    header_line = (
        f"{headers[0]:<{widths[0]}}  "
        f"{headers[1]:>{widths[1]}}  "
        f"{headers[2]:>{widths[2]}}  "
        f"{headers[3]:>{widths[3]}}  "
        f"{headers[4]:>{widths[4]}}  "
        f"{headers[5]:<{widths[5]}}"
    )
    sep = "-" * len(header_line)
    out = [header_line, sep]
    for r in rows:
        rating = "★" * r["stars"] + ("  ⚠" if r["outlier"] else "")
        out.append(
            f"{r['source']:<{widths[0]}}  "
            f"{r['n_epochs']:>{widths[1]}d}  "
            f"{r['mean_features']:>{widths[2]}.2f}  "
            f"{r['score']:>{widths[3]}.1f}  "
            f"{r['balance_weight']:>{widths[4]}.1f}  "
            f"{rating:<{widths[5]}}"
        )
    return "\n".join(out)


def _format_summary(stats: dict) -> str:
    if not stats["count"]:
        return ""
    by_star = stats.get("by_star", {})
    star_part = "  ".join(
        f"{'★' * n} {by_star.get(n, 0)}" for n in range(1, 6)
    )
    outlier_part = f" ({stats['n_outliers']} ⚠)" if stats["n_outliers"] else ""
    lines = [
        "",
        f"— Population: {stats['count']} sources —",
        f"  score:  p25={stats.get('p25', stats['median']):.0f}  "
        f"median={stats['median']:.0f}  "
        f"p75={stats.get('p75', stats['median']):.0f}  "
        f"max={stats['max']:.0f}",
        f"  stars:  {star_part}{outlier_part}",
    ]
    return "\n".join(lines)


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
    rows = [asdict(d) for d in scored]
    if args.sort == "score":
        rows.sort(key=lambda r: r["score"], reverse=True)
    elif args.sort == "epochs":
        rows.sort(key=lambda r: r["n_epochs"], reverse=True)
    else:
        rows.sort(key=lambda r: r["source"])

    if args.json:
        stats = score_stats([d.score for d in scored])
        print(json.dumps({"sources": rows, "stats": stats}, indent=2))
    else:
        print(_format_table(rows))
        print(_format_summary(score_stats([d.score for d in scored])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
