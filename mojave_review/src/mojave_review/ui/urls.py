"""Prefix-aware URL helper for in-app links and navigations.

Behind a reverse proxy — or a WSGI dispatcher mounting several projects in one
process — the app may be served under a path prefix (e.g. ``/mojave-review/``
or ``/bk224/``). ``dcc.Location`` navigations (``url.href`` / ``url.pathname``)
and ``html.A`` hrefs must include that prefix or they escape the app and hit
the server root.

**Why this does not simply call ``dash.get_relative_path``.** That module-level
helper reads ``dash._get_paths.CONFIG``, a process-wide singleton that each
``Dash(...)`` constructor overwrites. With one app per project in a single
process the LAST app built wins, so every project's pages render links pointing
at whichever project was constructed last — observed directly: MOJAVE's header
linked to ``/bk224/dashboard``. Dash's own asset and callback URLs are
unaffected (those are rendered from each app's own config), so the breakage is
confined to this helper, which makes it easy to miss.

So the prefix is taken from the Flask app handling the *current request*.
``create_app`` stashes it in ``server.config[REQUESTS_PREFIX_KEY]``, and a
dispatcher guarantees the right Flask app is in context for each request.
Falls back to ``dash.get_relative_path`` outside a request context (layout
introspection, unit tests) and then to the plain path.
"""

from __future__ import annotations

# Flask config key holding this app's requests_pathname_prefix. Set by
# mojave_review.app.create_app; read here.
REQUESTS_PREFIX_KEY = "MOJAVE_REVIEW_REQUESTS_PREFIX"


def _join(requests_pathname: str, path: str) -> str:
    """Mirror ``dash._get_paths.app_get_relative_path`` so behaviour is
    identical whichever branch of :func:`rel` produced the prefix."""
    if path == "":
        return "/" if requests_pathname == "/" else requests_pathname
    if not path.startswith("/"):
        # Dash raises here; keep the input rather than crash a page render.
        return path
    return "/".join([requests_pathname.rstrip("/"), path.lstrip("/")])


def rel(path: str) -> str:
    """Return ``path`` prefixed with the current app's request pathname prefix
    (no-op at root). Use for every in-app href / ``url.href`` navigation."""
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            prefix = current_app.config.get(REQUESTS_PREFIX_KEY)
            if prefix:
                return _join(prefix, path)
    except Exception:
        pass
    try:
        from dash import get_relative_path

        return get_relative_path(path)
    except Exception:
        return path
