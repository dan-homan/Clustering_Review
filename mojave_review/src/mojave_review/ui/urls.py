"""Prefix-aware URL helper for in-app links and navigations.

Behind a reverse proxy the app may be served under a path prefix (e.g.
``/mojave-review/``). ``dcc.Location`` navigations (``url.href`` /
``url.pathname``) and ``html.A`` hrefs must include that prefix or they
escape the app and hit the server root. ``dash.get_relative_path`` prepends
the app's ``requests_pathname_prefix``; :func:`rel` wraps it with a safe
fallback (the plain path) for contexts where no app is registered yet —
unit tests / layout introspection — so those keep working at root.
"""

from __future__ import annotations


def rel(path: str) -> str:
    """Return ``path`` prefixed with the app's request pathname prefix
    (no-op at root). Use for every in-app href / ``url.href`` navigation."""
    try:
        from dash import get_relative_path
        return get_relative_path(path)
    except Exception:
        return path
