"""Command-line entry points.

* :func:`main` (this module)       — `mojave-review`, launches the web UI.
* :mod:`mojave_review.cli.apply`  — `mojave-apply`, applies a recommendation JSON.
* :mod:`mojave_review.cli.tokens` — `mojave-review-tokens`, manages auth tokens.

The launcher resolves settings through :mod:`mojave_review.config`, so
every CLI flag has a corresponding ``MOJAVE_REVIEW_*`` env var and
``config.yaml`` entry. The precedence is CLI > env > YAML > defaults,
managed inside :func:`mojave_review.config.load_config`.
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
    # Every default is None so load_config can distinguish "user passed
    # the flag" from "user didn't pass the flag" and layer in env / YAML
    # values without being overridden by argparse fallbacks. The actual
    # defaults live on the Config dataclass.
    p.add_argument(
        "--config-file",
        type=Path,
        default=None,
        help="Path to config.yaml (default: $MOJAVE_REVIEW_CONFIG_FILE "
             "or ~/.mojave_review/config.yaml).",
    )
    p.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="Path to the Results/ directory containing per-source "
             "subfolders. Defaults to ./Results.",
    )
    p.add_argument(
        "--reviewer",
        type=str,
        default=None,
        help="Name attached to recommendation files in single-user mode. "
             "Defaults to $USER. (Ignored in token-auth mode — the "
             "reviewer name comes from the resolved token.)",
    )
    p.add_argument(
        "--recommendations-dir",
        type=Path,
        default=None,
        help="Where to write reviewer JSON files. Defaults to "
             "<results-dir>/../recommendations.",
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Directory for cached MOJAVE FITS downloads. Defaults to "
             "~/.mojave_review/cache.",
    )
    p.add_argument(
        "--fits-data-dir",
        type=Path,
        default=None,
        help="Optional already-on-disk MOJAVE FITS tree "
             "(layout: <source>/<epoch>/<source>.<band>.<epoch>.icn.fits.gz). "
             "When set, the overlay panel reads from here first and only "
             "falls back to fetching from NRAO when a file is missing. "
             "Defaults to $MOJAVE_REVIEW_FITS_DATA_DIR (alias: $MOJAVE_DATA).",
    )
    p.add_argument("--host", default=None,
                   help="Bind address. Default 127.0.0.1.")
    p.add_argument("--port", type=int, default=None,
                   help="TCP port. Default 8050.")
    p.add_argument("--no-browser", action="store_true",
                   default=None,         # None ⇒ "user didn't say"
                   help="Don't auto-open browser.")
    p.add_argument("--debug", action="store_true", default=None)
    p.add_argument(
        "--admin", action="store_true", default=None,
        help="Enable admin-only UI: the Generate-Apply-Command button "
             "(and, in future, the multi-reviewer aggregation dialog).",
    )
    # --- Phase 2 token authentication ---------------------------------
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
        default=None,
        help="Set the auth cookie WITHOUT the Secure attribute. Real "
             "browsers refuse to send Secure cookies over plain HTTP "
             "(including http://localhost), so flip this on for local "
             "HTTP testing only. Never use in production.",
    )
    p.add_argument(
        "--admin-contact",
        type=str,
        default=None,
        help="Free-text shown on the 403 page ('email <this> for a "
             "token'). Defaults to 'the admin'.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Translate argparse into the override dict load_config expects. The
    # --insecure-cookies flag is the inverse of the Config.cookie_secure
    # field, so flip it here rather than threading two booleans through
    # the resolver.
    overrides = {
        "results_dir":         args.results_dir,
        "recommendations_dir": args.recommendations_dir,
        "cache_dir":           args.cache_dir,
        "fits_data_dir":       args.fits_data_dir,
        "tokens_file":         args.tokens_file,
        "admin_contact":       args.admin_contact,
        "host":                args.host,
        "port":                args.port,
        "no_browser":          args.no_browser,
        "debug":               args.debug,
        "admin":               args.admin,
        "reviewer":            args.reviewer,
        "cookie_secure":       (False if args.insecure_cookies else None),
    }

    from ..config import load_config
    cfg = load_config(overrides, config_file=args.config_file)

    # Token-mode safety net: --tokens-file pointing at a missing file
    # should fail fast with an actionable message.
    if cfg.tokens_file is not None and not cfg.tokens_file.is_file():
        raise SystemExit(
            f"tokens-file points at a missing file: {cfg.tokens_file}\n"
            f"Create it with `mojave-review-tokens add <user>` first."
        )

    fits_data_dir_missing = (
        cfg.fits_data_dir is not None and not cfg.fits_data_dir.is_dir()
    )

    from ..app import create_app
    app = create_app(
        results_dir=cfg.results_dir,
        recommendations_dir=cfg.recommendations_dir,
        cache_dir=cfg.cache_dir,
        reviewer=cfg.reviewer,
        admin=cfg.admin,
        fits_data_dir=cfg.fits_data_dir,
        tokens_path=cfg.tokens_file,
        cookie_secure=cfg.cookie_secure,
        admin_contact=cfg.admin_contact,
    )

    url = f"http://{cfg.host}:{cfg.port}"
    if not cfg.no_browser:
        Timer(1.0, lambda: webbrowser.open(url)).start()

    print(f"mojave-review serving on {url}")
    print(f"  results_dir         = {cfg.results_dir}")
    print(f"  recommendations_dir = {cfg.recommendations_dir}")
    print(f"  cache_dir           = {cfg.cache_dir}")
    print(f"  reviewer            = {cfg.reviewer}")
    if cfg.fits_data_dir is not None:
        suffix = ("  (MISSING — will fall back to NRAO fetch)"
                  if fits_data_dir_missing else "")
        print(f"  fits_data_dir       = {cfg.fits_data_dir}{suffix}")
    if cfg.admin:
        print(f"  admin mode          = ON")
    if cfg.tokens_file is not None:
        cookie_mode = ("Secure" if cfg.cookie_secure
                       else "INSECURE (HTTP-only dev)")
        print(f"  auth                = token ({cfg.tokens_file}, "
              f"cookie={cookie_mode})")
    else:
        print(f"  auth                = single-user (no tokens-file)")

    app.run(host=cfg.host, port=cfg.port, debug=bool(cfg.debug))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
