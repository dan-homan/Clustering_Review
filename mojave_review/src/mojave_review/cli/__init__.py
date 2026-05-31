"""Command-line entry points.

* :func:`main` (this module)       — `mojave-review`, launches the web UI.
* :mod:`mojave_review.cli.apply`  — `mojave-apply`, applies a recommendation JSON.
"""

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
    p.add_argument(
        "--fits-data-dir",
        type=Path,
        default=None,
        help="Optional already-on-disk MOJAVE FITS tree "
             "(layout: <source>/<epoch>/<source>.<band>.<epoch>.icn.fits.gz). "
             "When set, the overlay panel reads from here first and only "
             "falls back to fetching from NRAO when a file is missing. "
             "Defaults to the MOJAVE_DATA environment variable, if defined.",
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8050)
    p.add_argument("--no-browser", action="store_true", help="Don't auto-open browser.")
    p.add_argument("--debug", action="store_true")
    p.add_argument(
        "--admin", action="store_true",
        help="Enable admin-only UI: the Generate-Apply-Command button "
             "(and, in future, the multi-reviewer aggregation dialog).",
    )
    # --- Phase 2 token authentication ---------------------------------
    # By default the app runs in Phase 1 single-user mode (no auth).
    # Pointing --tokens-file at a real tokens.yaml flips on the Flask
    # before_request middleware that gates every request on a valid
    # token (cookie or ?token= URL param). Tokens are managed with the
    # separate ``mojave-review-tokens`` admin CLI.
    p.add_argument(
        "--tokens-file",
        type=Path,
        default=None,
        help="Enable per-user token auth using this tokens.yaml file. "
             "Without this flag (default) the app runs in single-user "
             "mode and trusts whoever can reach the port.",
    )
    p.add_argument(
        "--insecure-cookies",
        action="store_true",
        help="Set the auth cookie WITHOUT the Secure attribute. Real "
             "browsers refuse to send Secure cookies over plain HTTP "
             "(including http://localhost), so flip this on for local "
             "HTTP testing only. Never use in production.",
    )
    p.add_argument(
        "--admin-contact",
        type=str,
        default="the admin",
        help="Free-text shown on the 403 page ('email <this> for a "
             "token'). Defaults to 'the admin'.",
    )
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

    # FITS data dir: explicit flag wins; otherwise MOJAVE_DATA env if set.
    # Resolve to an absolute path but DON'T mkdir — this is a read-only
    # source the user supplied. If it doesn't exist, silently fall back
    # to NRAO fetch (warn at startup if the path is set-but-missing).
    fits_data_dir: Path | None = None
    fits_data_source = None
    if args.fits_data_dir is not None:
        fits_data_dir = args.fits_data_dir.expanduser().resolve()
        fits_data_source = "--fits-data-dir flag"
    else:
        env_val = os.environ.get("MOJAVE_DATA")
        if env_val:
            fits_data_dir = Path(env_val).expanduser().resolve()
            fits_data_source = "$MOJAVE_DATA env var"
    fits_data_dir_missing = (
        fits_data_dir is not None and not fits_data_dir.is_dir()
    )

    from ..app import create_app

    # Tokens-file path uses the shared resolver from auth.tokens so the
    # MOJAVE_REVIEW_TOKENS_FILE env var works here exactly as it does in
    # the mojave-review-tokens admin CLI. We only *enable* auth when the
    # CLI flag is passed explicitly, though — having a tokens.yaml on
    # disk shouldn't silently flip the launcher into auth mode (per the
    # auth-mode-selection design we landed on).
    tokens_path: Path | None = None
    if args.tokens_file is not None:
        from ..auth.tokens import resolve_tokens_path
        tokens_path = resolve_tokens_path(str(args.tokens_file))
        if not tokens_path.is_file():
            raise SystemExit(
                f"--tokens-file points at a missing file: {tokens_path}\n"
                f"Create it with `mojave-review-tokens add <user>` first."
            )

    app = create_app(
        results_dir=results_dir,
        recommendations_dir=recommendations_dir,
        cache_dir=cache_dir,
        reviewer=reviewer,
        admin=args.admin,
        fits_data_dir=fits_data_dir,
        tokens_path=tokens_path,
        cookie_secure=not args.insecure_cookies,
        admin_contact=args.admin_contact,
    )

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        Timer(1.0, lambda: webbrowser.open(url)).start()

    print(f"mojave-review serving on {url}")
    print(f"  results_dir         = {results_dir}")
    print(f"  recommendations_dir = {recommendations_dir}")
    print(f"  cache_dir           = {cache_dir}")
    print(f"  reviewer            = {reviewer}")
    if fits_data_dir is not None:
        suffix = "  (MISSING — will fall back to NRAO fetch)" if fits_data_dir_missing else ""
        print(f"  fits_data_dir       = {fits_data_dir}  [{fits_data_source}]{suffix}")
    if args.admin:
        print(f"  admin mode          = ON")
    if tokens_path is not None:
        cookie_mode = ("Secure" if not args.insecure_cookies
                       else "INSECURE (HTTP-only dev)")
        print(f"  auth                = token ({tokens_path}, "
              f"cookie={cookie_mode})")
    else:
        print(f"  auth                = single-user (no --tokens-file)")

    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
