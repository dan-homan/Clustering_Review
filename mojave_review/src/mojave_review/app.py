"""Dash application factory."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from dash import Dash

from .auth.middleware import install_token_middleware
from .auth.runtime import current_reviewer
from .ui.layout import build_layout
from .ui.callbacks import register_callbacks

# Dash auto-loads .js / .css from this dir at app start.
_PACKAGE_ASSETS = str(Path(__file__).resolve().parent / "assets")


def _package_version() -> str:
    """Best-effort installed package version, for the freshness banner."""
    try:
        return version("mojave-review")
    except PackageNotFoundError:
        return "dev"


def create_app(
    results_dir: Path,
    recommendations_dir: Path,
    cache_dir: Path,
    reviewer: str,
    admin: bool = False,
    fits_data_dir: Path | None = None,
    tokens_path: Path | None = None,
    cookie_secure: bool = True,
    admin_contact: str = "the admin",
) -> Dash:
    """Build the Dash app.

    ``tokens_path`` controls the authentication mode:

    * ``None``  — Phase 1 single-user mode. The reviewer name is the
      value of the ``reviewer`` argument (from ``--reviewer`` on the
      CLI). Anyone who can reach the port can use the app.
    * ``Path`` — Phase 2 token-auth mode. Every request must carry a
      valid token (cookie or ``?token=...``) that resolves through
      ``tokens.yaml``. The middleware sets ``flask.g.reviewer`` per
      request, and ``app.layout`` + the callbacks read from it via
      :func:`mojave_review.auth.runtime.current_reviewer`. The
      ``reviewer`` argument is the single-user fallback used when
      there's no request context (or in Phase 1).
    """
    app = Dash(
        __name__,
        title="MOJAVE Cluster Review",
        suppress_callback_exceptions=True,
        assets_folder=_PACKAGE_ASSETS,
    )
    # Dynamic layout: re-rendered on every /_dash-layout fetch (typically
    # once per page load). Inside a Flask request context the layout
    # reads ``flask.g.reviewer`` via current_reviewer(); outside one
    # (e.g. during a unit-test introspection of app.layout) it falls
    # back to the CLI-supplied ``reviewer`` value captured here.
    def _layout():
        return build_layout(
            results_dir=results_dir,
            reviewer=current_reviewer(reviewer),
            admin=admin,
        )
    app.layout = _layout

    register_callbacks(
        app,
        results_dir=results_dir,
        recommendations_dir=recommendations_dir,
        cache_dir=cache_dir,
        reviewer=reviewer,        # closure default — overridden per-request
        admin=admin,
        fits_data_dir=fits_data_dir,
    )

    # Prevent the browser from caching the index HTML.
    #
    # Dash already cache-busts /assets/* (via ?m=<mtime>) and the bundled
    # /_dash-component-suites/* (via versioned filenames). But it serves
    # the index page itself with NO Cache-Control header, so the browser
    # is free to apply its default heuristic cache (typically hours). If
    # the layout grows a new component between redeploys — like the
    # ``overlay-reset-counter`` Store added in this round — a stale
    # index can land in a browser whose script tags point at OLD Dash
    # component-suite URLs. The reconciliation between cached scripts
    # and fresh /_dash-layout JSON can then silently fail and leave the
    # overlay panel frozen on whichever epoch was loaded when the cache
    # was poisoned. The shipping a no-store header on the HTML keeps
    # browsers in sync with the running app at the small cost of one
    # fetch of a tiny HTML page per visit.
    #
    # We also send the X-Mojave-Review-Version header so reviewers /
    # admin can confirm what build the server is running from the
    # browser devtools' Network tab.
    pkg_version = _package_version()

    @app.server.after_request
    def _no_cache_html(resp):  # noqa: ANN001 — Flask response
        if (resp.mimetype or "").startswith("text/html"):
            resp.headers["Cache-Control"] = "no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        resp.headers["X-Mojave-Review-Version"] = pkg_version
        return resp

    # Phase 2: install token middleware if a tokens file was given.
    # Order matters — we register this *after* the cache-control hook
    # so the no-cache headers are also applied to the 403 page (the
    # browser shouldn't cache "you're locked out" either).
    if tokens_path is not None:
        install_token_middleware(
            app.server,
            tokens_path=tokens_path,
            cookie_secure=cookie_secure,
            admin_contact=admin_contact,
        )

    return app
