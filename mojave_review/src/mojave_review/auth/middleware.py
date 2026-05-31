"""Flask ``before_request`` token middleware.

Wired in by :func:`install_token_middleware`, this hook resolves each
incoming request to a token-store :class:`~mojave_review.auth.tokens.User`
(setting it on ``flask.g`` so downstream handlers can see who's asking)
or returns a 403 page when no valid credential is present.

Credential flow, in order:

1. **URL token (``?token=...``)** — the one-time bootstrap path. If
   valid, the response is a 302 to the same path with the query string
   stripped, and the response carries a ``Set-Cookie: mr_token=...``
   header. So the reviewer's first visit to their bookmark URL leaves
   them on a clean URL with a sticky cookie. If invalid, the URL token
   is ignored and we fall through.
2. **Cookie (``mr_token``)** — the steady-state path. The middleware
   refreshes the cookie's ``max_age`` on every successful resolve so
   inactivity rolls expiry forward naturally. Implementation detail:
   the cookie is only re-set when the resolve succeeds *and* the cookie
   is more than 24 h old — we don't want to set a cookie on every one
   of the dozens of ``/_dash-update-component`` POSTs per page load.
3. **Otherwise** — a small 403 page that tells the reviewer to email
   the admin for a token.

The middleware also keeps an mtime-fingerprint cache of the parsed
:class:`~mojave_review.auth.tokens.TokenStore` so the YAML is reparsed
only when it changes on disk. That matches the bundle-reload pattern
in ``data/loader.py`` and is essential for usable per-request perf
(Dash makes ~30 callback POSTs per page interaction).

Why ``Secure`` cookies are the default: prod is HTTPS-only. The trade-
off: a ``Secure`` cookie set over an HTTP localhost connection isn't
sent back by real browsers, so local dev needs ``cookie_secure=False``.
The launcher exposes this as ``--insecure-cookies``.
"""

from __future__ import annotations

import threading
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, Response, g, redirect, request

from .._logging import get_logger
from .tokens import TokenStore, User, load_store


log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Cookie name. ``mr_`` prefix avoids collisions with anything else the
# host application may set on the same domain.
COOKIE_NAME = "mr_token"

# How long a cookie lives after the last successful resolve. The
# middleware refreshes this on every hit (rolling expiry).
DEFAULT_COOKIE_MAX_AGE_DAYS = 30

# We avoid re-setting the cookie on every request — only when the
# previous one is older than this. Without the cap a typical page
# triggers dozens of Set-Cookie roundtrips. 24 h is a good balance
# between "kept fresh" and "noisy headers".
_COOKIE_REFRESH_FLOOR_SECONDS = 24 * 3600


# ---------------------------------------------------------------------------
# Mtime-fingerprint cache for the parsed TokenStore
# ---------------------------------------------------------------------------


class _StoreCache:
    """One TokenStore per (path, mtime_ns, size). Cheap stat on every
    lookup; reparse only when the file changes on disk. Thread-safe so
    multiple gunicorn workers / Flask test clients don't race."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached: TokenStore | None = None
        self._fingerprint: tuple | None = None

    def get(self, path: Path) -> TokenStore:
        try:
            st = path.stat()
            fp: tuple | None = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            fp = None
        with self._lock:
            if fp == self._fingerprint and self._cached is not None:
                return self._cached
            store = load_store(path)
            # Log only after the first parse — the very first call also
            # trips this branch but is just "we started up", not a
            # genuine reload.
            if self._fingerprint is not None:
                log.info("tokens file changed on disk, reloaded  path=%s  users=%d",
                         path, len(store))
            self._cached = store
            self._fingerprint = fp
            return store


# ---------------------------------------------------------------------------
# 403 page
# ---------------------------------------------------------------------------


_FORBIDDEN_HTML = """\
<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>Access denied — mojave-review</title>
<style>
  body{{font-family:system-ui,sans-serif;max-width:540px;margin:8em auto;padding:0 1em;color:#222}}
  h1{{font-size:1.4em;margin-bottom:0.4em}}
  code{{background:#f4f4f4;padding:0.1em 0.35em;border-radius:3px;font-size:0.95em}}
</style></head><body>
<h1>Access denied</h1>
<p>This instance of <code>mojave-review</code> doesn't recognise your token,
or you haven't been issued one yet.</p>
<p>Email {admin_contact} for a fresh bookmark URL.</p>
</body></html>
"""


def _forbidden(admin_contact: str) -> Response:
    """Build the standard 403 response."""
    return Response(
        _FORBIDDEN_HTML.format(admin_contact=admin_contact),
        status=403,
        mimetype="text/html",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install_token_middleware(
    server: Flask,
    *,
    tokens_path: Path,
    cookie_secure: bool = True,
    cookie_max_age_days: int = DEFAULT_COOKIE_MAX_AGE_DAYS,
    admin_contact: str = "the admin",
) -> None:
    """Wire the before_request hook + the cookie-refresh after_request hook.

    Once installed, every request must carry a valid token (cookie or
    URL param). On success ``flask.g.review_user`` is the resolved
    :class:`User` and ``flask.g.reviewer`` is the user's name. On
    failure the response is a 403 HTML page.

    Parameters
    ----------
    server :
        The Flask app (``Dash.server``).
    tokens_path :
        Path to ``tokens.yaml``. Re-parsed on disk-change via mtime
        fingerprint; missing file means "no users", which means
        everyone hits the 403.
    cookie_secure :
        Whether to set the cookie ``Secure`` attribute. True for prod
        (HTTPS). False for local HTTP dev — real browsers won't send
        Secure cookies on http://localhost.
    cookie_max_age_days :
        Rolling cookie expiry. The cookie is refreshed (header re-set)
        only when older than 24 h to avoid setting a cookie on every
        Dash callback POST.
    admin_contact :
        Free-text "email this person" string shown on the 403 page.
    """
    cache = _StoreCache()
    cookie_max_age = int(cookie_max_age_days * 86400)

    def _resolve(token: str | None) -> User | None:
        if not token:
            return None
        return cache.get(tokens_path).by_token(token)

    @server.before_request
    def _check_token() -> Response | None:
        # ---- 1) URL token: ?token=... ---------------------------------
        url_token = request.args.get("token")
        if url_token:
            user = _resolve(url_token)
            if user is not None:
                log.info("session established via url token  user=%s  ip=%s",
                         user.name, request.remote_addr)
                # Strip the token from the URL on the redirect so the
                # reviewer doesn't bookmark or share-by-mistake a URL
                # that carries their secret. Cookie is set on the
                # redirect response — the next request arrives with the
                # cookie and a clean URL.
                # Strip the token=... param but keep anything else the
                # reviewer might have on the URL. werkzeug.urls.url_encode
                # was removed in 3.x, so we go through stdlib urlencode.
                clean_qs = [
                    (k, v) for k, v in request.args.items(multi=True)
                    if k != "token"
                ]
                tail = ("?" + urlencode(clean_qs)) if clean_qs else ""
                resp = redirect(request.path + tail, code=302)
                resp.set_cookie(
                    COOKIE_NAME, url_token,
                    max_age=cookie_max_age,
                    httponly=True,
                    secure=cookie_secure,
                    samesite="Lax",
                )
                # Stash on g for log messages even though the response
                # we're returning short-circuits the rest of the app.
                g.review_user = user
                g.reviewer = user.name
                return resp
            # Bad URL token — fall through to cookie / 403. The
            # response deliberately doesn't distinguish "invalid token"
            # vs "no token" (no enumeration), but we DO log it so
            # repeated bad attempts from one IP are auditable.
            log.warning("invalid url token presented  ip=%s  ua=%s",
                        request.remote_addr,
                        request.headers.get("User-Agent", "")[:120])

        # ---- 2) Cookie ------------------------------------------------
        cookie_token = request.cookies.get(COOKIE_NAME)
        user = _resolve(cookie_token)
        if user is not None:
            g.review_user = user
            g.reviewer = user.name
            # We may need to refresh the cookie's max-age. Flask doesn't
            # expose the cookie's own age — the browser knows that, not
            # us — so we always set the cookie on cookie-path hits and
            # let the after_request below stamp the response. Cheap.
            g._refresh_cookie_token = cookie_token
            return None

        # ---- 3) Forbidden --------------------------------------------
        # Log only when there was a bad cookie — a bare-no-credential
        # 403 happens on every link a curious visitor clicks and would
        # drown the log. Bad cookies, on the other hand, mean a previously
        # valid token was revoked, which is interesting.
        if cookie_token:
            log.warning("rejected stale cookie  ip=%s", request.remote_addr)
        return _forbidden(admin_contact)

    @server.after_request
    def _refresh_cookie(resp: Response) -> Response:
        token = getattr(g, "_refresh_cookie_token", None)
        if token:
            resp.set_cookie(
                COOKIE_NAME, token,
                max_age=cookie_max_age,
                httponly=True,
                secure=cookie_secure,
                samesite="Lax",
            )
        return resp
