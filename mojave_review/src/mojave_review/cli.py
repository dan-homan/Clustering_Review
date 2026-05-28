"""Command-line entry point for mojave-review."""

from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path
from threading import Timer


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mojave-review",
        description="Interactive web review tool for MOJAVE clustering results.",
    )
    p.add_argument(
        "--results-dir",
        type=Path,
        default=Path("Results"),
        help="Path to the Results/ directory containing per-source subfolders.",
    )
    p.add_argument(
        "--reviewer",
        type=str,
        default=None,
        help="Name attached to recommendation files. Defaults to $USER.",
    )
    p.add_argument(
        "--recommendations-dir",
        type=Path,
        default=None,
        help="Where to write reviewer JSON files. Defaults to <results-dir>/../recommendations.",
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Directory for cached MOJAVE FITS downloads. Defaults to ~/.mojave_review/cache.",
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8050)
    p.add_argument("--no-browser", action="store_true", help="Don't auto-open browser.")
    p.add_argument("--debug", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    results_dir = args.results_dir.expanduser().resolve()
    if not results_dir.is_dir():
        raise SystemExit(f"results-dir not found: {results_dir}")

    recommendations_dir = (
        args.recommendations_dir.expanduser().resolve()
        if args.recommendations_dir
        else results_dir.parent / "recommendations"
    )
    cache_dir = (
        args.cache_dir.expanduser().resolve()
        if args.cache_dir
        else Path.home() / ".mojave_review" / "cache"
    )
    recommendations_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    import os

    reviewer = args.reviewer or os.environ.get("USER") or "anonymous"

    from .app import create_app

    app = create_app(
        results_dir=results_dir,
        recommendations_dir=recommendations_dir,
        cache_dir=cache_dir,
        reviewer=reviewer,
    )

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        Timer(1.0, lambda: webbrowser.open(url)).start()

    print(f"mojave-review serving on {url}")
    print(f"  results_dir         = {results_dir}")
    print(f"  recommendations_dir = {recommendations_dir}")
    print(f"  cache_dir           = {cache_dir}")
    print(f"  reviewer            = {reviewer}")

    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
