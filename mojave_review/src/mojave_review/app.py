"""Dash application factory."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from dash import Dash, Input, Output, dcc, html

from .auth.middleware import install_token_middleware
from .auth.runtime import current_reviewer
from .ui.compare import build_compare_page
from .ui.compare_callbacks import register_compare_callbacks
from .ui.dashboard import build_dashboard_page
from .ui.dashboard_callbacks import register_dashboard_callbacks
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
    url_base_prefix: str | None = None,
    xviii_path: str | None = None,
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
    # Behind a reverse-proxy prefix, url_base_pathname makes Dash serve its
    # routes/assets under the prefix; the in-app links use ui.urls.rel
    # (dash.get_relative_path) so they match. None ⇒ served at root.
    dash_kwargs = {}
    if url_base_prefix:
        dash_kwargs["url_base_pathname"] = url_base_prefix
    app = Dash(
        __name__,
        title="MOJAVE Cluster Review",
        suppress_callback_exceptions=True,
        assets_folder=_PACKAGE_ASSETS,
        **dash_kwargs,
    )

    # gzip every response, including the /_dash-update-component callback
    # payloads. The overlay figure is ~hundreds of KB of float/JSON per epoch;
    # uncompressed that dominates the felt latency when scrubbing epochs over a
    # network. gzip typically cuts it 3-5x. The laptop deploy is gunicorn with
    # no reverse proxy, so nothing else compresses; on the university nginx
    # host this is harmless (nginx won't re-gzip an already-gzipped response).
    # Soft dependency: skip gracefully if flask-compress isn't installed yet
    # (e.g. an env that hasn't `pip install -e .`'d since this landed).
    try:
        from flask_compress import Compress
        Compress(app.server)
    except Exception:
        pass
    # Dynamic layout: re-rendered on every /_dash-layout fetch (typically
    # once per page load). Inside a Flask request context the layout
    # reads ``flask.g.reviewer`` via current_reviewer(); outside one
    # (e.g. during a unit-test introspection of app.layout) it falls
    # back to the CLI-supplied ``reviewer`` value captured here.
    #
    # Multi-page wrapper: dcc.Location + page-content. The router callback
    # below picks between the review page (path "/") and the dashboard
    # page (path "/dashboard"). Each visit re-renders the chosen page
    # fresh from disk — no in-page refresh callback needed, the
    # dashboard tables are recomputed on every navigation. State does
    # not survive across same-tab navigation; the dashboard link in the
    # review header uses target="_blank" so reviewers can keep both
    # views open in separate tabs.
    def _layout():
        return html.Div([
            dcc.Location(id="url", refresh=False),
            # Admin write-then-apply actions (dashboard balancing / target
            # dates / team edits) bump this store, which re-renders the
            # page IN PLACE via _route (below). We can't refresh by
            # navigating url.href: the admin is already on /dashboard, so a
            # same-path href is no pathname change and the page (with its
            # open modal) shows stale data — the write succeeded on disk
            # but looked like a no-op. (A refresh=True Location instead
            # caused an endless reload loop, since it re-navigates on every
            # mount.) An in-place re-render reads fresh from disk, closes
            # the modal, and never touches the browser URL.
            dcc.Store(id="dashboard-refresh", data=0),
            html.Div(id="page-content"),
        ])
    app.layout = _layout

    @app.callback(Output("page-content", "children"),
                  Input("url", "pathname"),
                  Input("dashboard-refresh", "data"))
    def _route(pathname, _refresh):
        rev = current_reviewer(reviewer)
        # rstrip("/") handles both /dashboard and /dashboard/ ; endswith
        # composes cleanly with a future url_base_pathname prefix
        # (e.g. /mojave-review/dashboard).
        if pathname and pathname.rstrip("/").endswith("/dashboard"):
            return build_dashboard_page(
                results_dir=results_dir,
                recommendations_dir=recommendations_dir,
                reviewer=rev,
                admin=admin,
                tokens_path=tokens_path,
            )
        if pathname and pathname.rstrip("/").endswith("/compare"):
            return build_compare_page(
                results_dir=results_dir,
                recommendations_dir=recommendations_dir,
                reviewer=rev,
                admin=admin,
                xviii_path=xviii_path,
            )
        return build_layout(
            results_dir=results_dir,
            reviewer=rev,
            admin=admin,
            recommendations_dir=recommendations_dir,
        )

    register_callbacks(
        app,
        results_dir=results_dir,
        recommendations_dir=recommendations_dir,
        cache_dir=cache_dir,
        reviewer=reviewer,        # closure default — overridden per-request
        admin=admin,
        fits_data_dir=fits_data_dir,
    )

    # Admin-only dashboard callbacks (auto-balance + reassign-queue).
    # Registered unconditionally — the components they bind to are only
    # rendered when admin=True, so the callbacks are inert for reviewers.
    # Registering only-when-admin would mean the callbacks aren't in the
    # callback map for a deployment that toggles between modes without
    # a restart, but more importantly Dash's allow_duplicate output
    # checks happen at registration time, so unregistered callbacks
    # would mean the dashboard layout is broken if admin re-enables
    # mid-process. Inert is safer.
    register_dashboard_callbacks(
        app,
        results_dir=results_dir,
        recommendations_dir=recommendations_dir,
        tokens_path=tokens_path,
        reviewer=reviewer,
    )

    # Comparison page (XVIII Gaussian fits vs current clustering). Registered
    # unconditionally — components exist only when /compare is mounted, so the
    # callbacks are inert on the other pages (suppress_callback_exceptions).
    register_compare_callbacks(
        app,
        results_dir=results_dir,
        recommendations_dir=recommendations_dir,
        cache_dir=cache_dir,
        reviewer=reviewer,
        admin=admin,
        fits_data_dir=fits_data_dir,
        xviii_path=xviii_path,
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
