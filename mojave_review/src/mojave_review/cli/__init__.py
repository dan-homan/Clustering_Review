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
    p.add_argument(
        "--astrometry-file",
        type=Path,
        default=None,
        help="Override the absolute-astrometry table (astrometry_mojave.dat) "
             "behind the 'Astrometric reference' checkbox. Defaults to the "
             "first of <results-dir>/../, <results-dir>/ or ./ that has one; "
             "without it the option is disabled.",
    )
    p.add_argument(
        "--project", action="append", default=None, metavar="NAME=PATH",
        help="Serve several projects from one process. Repeatable. PATH is a "
             "project directory holding Results/ and recommendations/; NAME "
             "becomes its URL segment (derived from the directory name when "
             "omitted, e.g. MOJAVE_1996.00-2026.00 -> 'mojave'). Each project "
             "is mounted at /<name>/ and / redirects to the first one. "
             "Without this (or --projects-dir) the app serves one project at "
             "the root exactly as before.",
    )
    p.add_argument(
        "--projects-dir", type=Path, default=None, metavar="DIR",
        help="Serve every immediate subdirectory of DIR that contains a "
             "Results/ directory as a project. Shorthand for repeating "
             "--project when the project directories are siblings; combines "
             "with it.",
    )
    p.add_argument(
        "--source-params",
        type=Path,
        default=None,
        help="Override the per-source parameter table "
             "(source_run_param.csv), which supplies the redshifts behind "
             "host-frame Tb and beta_app. Defaults to the first of "
             "<results-dir>/../, <results-dir>/ or ./ that has one; without "
             "it every source is treated as z unknown.",
    )
    p.add_argument(
        "--xviii-table",
        type=Path,
        default=None,
        help="Override the MOJAVE Paper XVIII MRT table used by the "
             "comparison page (/compare). Defaults to the copy bundled "
             "with the package.",
    )
    p.add_argument("--host", default=None,
                   help="Bind address. Default 127.0.0.1.")
    p.add_argument("--port", type=int, default=None,
                   help="TCP port. Default 8050.")
    p.add_argument(
        "--proxy-strips-prefix", action="store_true", default=None,
        help="Set when the reverse proxy REMOVES --url-base-prefix before "
             "forwarding (nginx proxy_pass with a trailing slash). The app "
             "then serves at the root but still builds links with the "
             "prefix. Without this the prefix is assumed to be preserved.",
    )
    p.add_argument("--url-base-prefix", default=None,
                   help="Public path prefix when behind a reverse proxy "
                        "(e.g. /mojave-review/). Default: served at root.")
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
    # --- Logging ------------------------------------------------------
    p.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Write a rotating log to this path (5 MB × 5 backups). "
             "stderr is always also written. Default: stderr only.",
    )
    p.add_argument(
        "--log-level",
        type=str,
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Package log level. Default INFO.",
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
        "url_base_prefix":     args.url_base_prefix,
        "proxy_strips_prefix": args.proxy_strips_prefix,
        "no_browser":          args.no_browser,
        "debug":               args.debug,
        "admin":               args.admin,
        "reviewer":            args.reviewer,
        "cookie_secure":       (False if args.insecure_cookies else None),
        "log_file":            args.log_file,
        "log_level":           args.log_level,
    }

    # Flatten the CLI project declarations into the normal config layer, so
    # they follow the usual CLI > env > yaml precedence AND so load_config can
    # see that this is multi-project mode (where a top-level results_dir is
    # neither needed nor checked). --projects-dir is scanned here rather than
    # later for the same reason.
    from ..projects import ProjectConfigError, discover_projects
    try:
        cli_projects = [str(a) for a in (args.project or [])]
        if args.projects_dir:
            cli_projects += [str(spec.root)
                             for spec in discover_projects(args.projects_dir)]
    except ProjectConfigError as e:
        raise SystemExit(f"project configuration error: {e}")
    overrides["projects"] = cli_projects or None

    from ..config import load_config
    cfg = load_config(overrides, config_file=args.config_file)

    # Wire logging as early as possible so any startup error gets
    # captured into the rotating file if one was configured.
    from .._logging import configure_logging, get_logger
    configure_logging(cfg.log_file, level=cfg.log_level)
    log = get_logger("mojave_review.cli")
    log.info("mojave-review starting  host=%s  port=%s  reviewer=%s  admin=%s  "
             "auth=%s",
             cfg.host, cfg.port, cfg.reviewer, cfg.admin,
             "token" if cfg.tokens_file else "single-user")

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

    # Settings every project shares. results_dir / recommendations_dir are
    # per-project and therefore NOT in here.
    from ..projects import mount_prefix, resolve_projects
    shared = dict(
        cache_dir=cfg.cache_dir,
        reviewer=cfg.reviewer,
        admin=cfg.admin,
        fits_data_dir=cfg.fits_data_dir,
        tokens_path=cfg.tokens_file,
        cookie_secure=cfg.cookie_secure,
        admin_contact=cfg.admin_contact,
        xviii_path=str(args.xviii_table) if args.xviii_table else None,
    )
    try:
        projects = resolve_projects(config_entries=cfg.projects)
    except ProjectConfigError as e:
        raise SystemExit(f"project configuration error: {e}")

    if projects:
        # Per-project data tables are DISCOVERED from each project root, so
        # the --source-params / --astrometry-file overrides (which name a
        # single file) are meaningless here and would silently apply one
        # project's table to all of them.
        for flag, val in (("--source-params", args.source_params),
                          ("--astrometry-file", args.astrometry_file)):
            if val:
                raise SystemExit(
                    f"{flag} cannot be combined with --project/--projects-dir: "
                    f"it names one file, but each project discovers its own "
                    f"from its project root. Remove the flag, or run that "
                    f"project on its own with --results-dir."
                )
        from ..projects import build_dispatcher
        app, _apps = build_dispatcher(
            projects, mount_root=cfg.url_base_prefix or "/", **shared)
    else:
        from ..app import create_app
        app = create_app(
            results_dir=cfg.results_dir,
            recommendations_dir=cfg.recommendations_dir,
            url_base_prefix=cfg.url_base_prefix,
            proxy_strips_prefix=cfg.proxy_strips_prefix,
            astrometry_path=(str(args.astrometry_file)
                             if args.astrometry_file else None),
            source_params_path=(str(args.source_params)
                                if args.source_params else None),
            **shared,
        )

    # The launcher hits the bound port directly (root), even when a public
    # url_base_prefix is set for the reverse-proxy deployment.
    url = f"http://{cfg.host}:{cfg.port}{cfg.url_base_prefix or '/'}"
    if not cfg.no_browser:
        Timer(1.0, lambda: webbrowser.open(url)).start()

    print(f"mojave-review serving on {url}")
    if projects:
        print(f"  projects            = {len(projects)} "
              f"(default: {projects[0].name})")
        for spec in projects:
            print(f"    {mount_prefix(spec, cfg.url_base_prefix or '/'):14s} "
                  f"{spec.root}")
    else:
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

    if projects:
        # A dispatcher is a bare WSGI callable, not a Dash app, so there is no
        # .run(). Werkzeug's dev server is what Dash would have used anyway.
        from werkzeug.serving import run_simple
        run_simple(cfg.host, cfg.port, app,
                   use_reloader=bool(cfg.debug), use_debugger=bool(cfg.debug))
    else:
        app.run(host=cfg.host, port=cfg.port, debug=bool(cfg.debug))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
