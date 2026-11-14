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
from ._logging import PROJECT_NAME_KEY
from .ui.urls import REQUESTS_PREFIX_KEY

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
    proxy_strips_prefix: bool = False,
    cookie_scope_prefix: str | None = None,
    project_nav=None,
    current_project: str | None = None,
    xviii_path: str | None = None,
    astrometry_path: str | None = None,
    source_params_path: str | None = None,
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
    # Behind a reverse-proxy prefix there are two different paths to keep
    # straight, and Dash has a knob for each:
    #
    #   routes_pathname_prefix   — what the APP serves, i.e. the path as it
    #                              arrives once the proxy is done with it;
    #   requests_pathname_prefix — what the BROWSER asks for, used to build
    #                              every URL the page contains (assets,
    #                              callback POSTs, and ui.urls.rel links).
    #
    # Which combination is right depends on whether the proxy strips the
    # prefix before forwarding:
    #
    #   proxy_strips_prefix=False — nginx `proxy_pass http://host:port;` with
    #       NO trailing slash, so the prefix is PRESERVED. The app receives
    #       /mojave-review/... and must serve there too: both prefixes are the
    #       same, which is what `url_base_pathname` sets.
    #
    #   proxy_strips_prefix=True  — nginx `proxy_pass http://host:port/;` WITH
    #       a trailing slash, so the prefix is REMOVED. The app receives plain
    #       /... and must serve at the root, but the page it returns still has
    #       to point at /mojave-review/... or every link escapes the proxied
    #       location. Hence the asymmetric split.
    #
    # Getting it backwards is loud rather than subtle: serving routes under a
    # prefix the proxy has already removed makes every request 404.
    #
    # Until 2026-11-14 the WSGI entry point never passed url_base_prefix at
    # all, so the deployed app ran root-mounted behind a stripping proxy:
    # requests worked, but every rel() link (Dashboard, XVIII Comparison, the
    # dashboard's source deep-links, "Back to review") pointed outside the
    # app, and the post-login redirect sent reviewers to the site root.
    dash_kwargs = {}
    if url_base_prefix:
        if proxy_strips_prefix:
            dash_kwargs["routes_pathname_prefix"] = "/"
            dash_kwargs["requests_pathname_prefix"] = url_base_prefix
        else:
            dash_kwargs["url_base_pathname"] = url_base_prefix
    app = Dash(
        __name__,
        title="MOJAVE Cluster Review",
        suppress_callback_exceptions=True,
        assets_folder=_PACKAGE_ASSETS,
        **dash_kwargs,
    )
    # Stash this app's requests prefix on its own Flask server so ui.urls.rel
    # can find it per-request. dash.get_relative_path reads a process-wide
    # singleton that the LAST Dash(...) built overwrites, which silently gives
    # every project's links the final project's prefix once several apps share
    # a process. See ui/urls.py.
    app.server.config[REQUESTS_PREFIX_KEY] = app.config.requests_pathname_prefix
    # Which project this app serves, for the per-project log tag. None in
    # single-project mode, which leaves log output byte-identical.
    if current_project:
        app.server.config[PROJECT_NAME_KEY] = current_project

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
        # Encodings we are willing to serve, best first. flask-compress 1.24
        # defaults to ["zstd", "br", "gzip", "deflate"] (and ["zstd", "br",
        # "deflate"] when streaming), which quietly made brotli the preferred
        # encoding for any client advertising "br" without "zstd" (Firefox) --
        # not the intent of this block, which has always been about gzip.
        # Dropping "br" keeps compression for every client (zstd for Chrome,
        # gzip otherwise) while leaving the brotli code path unreachable, so a
        # vulnerability in the brotli library is not exercised by this app even
        # though flask-compress forces it into the environment (CVE-2026-111497
        # on brotli 1.2.0; pyproject.toml floors the version too). Set BEFORE
        # init: flask-compress fills its defaults with config.setdefault.
        app.server.config["COMPRESS_ALGORITHM"] = ["zstd", "gzip", "deflate"]
        app.server.config["COMPRESS_ALGORITHM_STREAMING"] = ["zstd", "deflate"]
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
            project_nav=project_nav,
            current_project=current_project,
        )

    register_callbacks(
        app,
        results_dir=results_dir,
        recommendations_dir=recommendations_dir,
        cache_dir=cache_dir,
        reviewer=reviewer,        # closure default — overridden per-request
        admin=admin,
        fits_data_dir=fits_data_dir,
        astrometry_path=astrometry_path,
        source_params_path=source_params_path,
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
        source_params_path=source_params_path,
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
            # Two uses. (1) The session cookie is scoped to this app's path
            # and gets a name derived from it, so sibling apps on one host
            # don't overwrite each other's token; root-mounted keeps the
            # historical ("mr_token", "/") so nothing is invalidated.
            # (2) The middleware works in Flask terms (``request.path``),
            # which is the path AFTER anything upstream has stripped, so it
            # needs the public prefix to redirect the reviewer back to where
            # their browser actually is after a ``?token=`` login.
            url_base_prefix=url_base_prefix,
            proxy_strips_prefix=proxy_strips_prefix,
            # Multi-project: the mount root, so one login covers every
            # project rather than one token prompt per project.
            cookie_scope_prefix=cookie_scope_prefix,
        )

    return app
