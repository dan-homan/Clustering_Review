"""Reverse-proxy path prefix: the two Dash knobs, and what depends on them.

Dash separates ``routes_pathname_prefix`` (what the app serves, i.e. the path
*after* the proxy is done) from ``requests_pathname_prefix`` (what the browser
asks for, used to build every URL in the page). Which combination is right
depends on whether the proxy strips the prefix.

Until 2026-11-14 ``wsgi.py`` never passed ``url_base_prefix`` at all, so the
deployed app ran root-mounted behind a *stripping* proxy. Requests worked, but
every ``rel()`` link pointed outside the proxied location and the post-login
redirect sent reviewers to the site root. Both are pinned here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mojave_review.app import create_app
from mojave_review.auth.middleware import normalise_prefix, public_path

PREFIX = "/mojave-review/"


# ---------------------------------------------------------------------------
# Prefix normalisation + path translation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    (None, "/"), ("", "/"), ("   ", "/"), ("/", "/"),
    ("mojave-review", PREFIX), ("/mojave-review", PREFIX),
    ("mojave-review/", PREFIX), ("  /mojave-review/  ", PREFIX),
])
def test_normalise_prefix(raw, expected):
    assert normalise_prefix(raw) == expected


def test_public_path_prepends_only_when_the_proxy_strips():
    # Stripping: the app sees /dashboard, the browser is at the prefixed path.
    assert public_path("/dashboard", PREFIX, True) == "/mojave-review/dashboard"
    assert public_path("/", PREFIX, True) == PREFIX
    # Not stripping: request.path already carries the prefix.
    assert public_path("/mojave-review/dashboard", PREFIX, False) \
        == "/mojave-review/dashboard"
    # No prefix at all: unchanged either way.
    assert public_path("/dashboard", None, True) == "/dashboard"
    assert public_path("/dashboard", None, False) == "/dashboard"


def test_public_path_does_not_double_an_existing_prefix():
    """Defensive: a proxy that only strips some paths, or a direct hit on the
    bound port, must not yield /mojave-review/mojave-review/..."""
    assert public_path(PREFIX, PREFIX, True) == PREFIX
    assert public_path("/mojave-review/dashboard", PREFIX, True) \
        == "/mojave-review/dashboard"


# ---------------------------------------------------------------------------
# How the prefix reaches Dash
# ---------------------------------------------------------------------------


def _app(tmp_path, **kw):
    (tmp_path / "Results").mkdir(exist_ok=True)
    return create_app(
        results_dir=tmp_path / "Results",
        recommendations_dir=tmp_path / "recs",
        cache_dir=tmp_path / "cache",
        reviewer="Rev", **kw)


def test_root_hosted_is_unchanged(tmp_path):
    app = _app(tmp_path)
    assert app.config.routes_pathname_prefix == "/"
    assert app.config.requests_pathname_prefix == "/"


def test_non_stripping_proxy_sets_both_prefixes_together(tmp_path):
    """`proxy_pass http://host:port;` (no trailing slash) preserves the
    prefix, so the app must serve there too."""
    app = _app(tmp_path, url_base_prefix=PREFIX, proxy_strips_prefix=False)
    assert app.config.routes_pathname_prefix == PREFIX
    assert app.config.requests_pathname_prefix == PREFIX


def test_stripping_proxy_splits_the_prefixes(tmp_path):
    """`proxy_pass http://host:port/;` (trailing slash) removes the prefix, so
    the app serves at the root but still builds prefixed URLs."""
    app = _app(tmp_path, url_base_prefix=PREFIX, proxy_strips_prefix=True)
    assert app.config.routes_pathname_prefix == "/"
    assert app.config.requests_pathname_prefix == PREFIX


@pytest.mark.parametrize("strips", [True, False])
def test_in_app_links_always_carry_the_public_prefix(tmp_path, strips):
    """Whatever the proxy does, rel() links must be browser-usable. This is
    the regression: root-mounted, they came out as bare /dashboard."""
    app = _app(tmp_path, url_base_prefix=PREFIX, proxy_strips_prefix=strips)
    assert app.get_relative_path("/dashboard") == "/mojave-review/dashboard"
    assert app.get_relative_path("/compare") == "/mojave-review/compare"
    assert app.get_relative_path("/") == PREFIX


def test_root_hosted_links_stay_bare(tmp_path):
    app = _app(tmp_path)
    assert app.get_relative_path("/dashboard") == "/dashboard"


# ---------------------------------------------------------------------------
# The post-login redirect, through Flask
# ---------------------------------------------------------------------------


def _token_server(tmp_path, **kw):
    """A Flask app with the middleware installed, standing in for the Dash
    server. Routes mirror what the real app serves."""
    from flask import Flask
    from mojave_review.auth.middleware import install_token_middleware

    tokens = tmp_path / "tokens.yaml"
    tokens.write_text(yaml.safe_dump(
        {"users": [{"token": "T" * 16, "name": "Reviewer"}]}))
    server = Flask(__name__)

    @server.route("/")
    @server.route("/dashboard")
    def _ok():
        return "ok"

    install_token_middleware(
        server, tokens_path=tokens, cookie_secure=False, **kw)
    return server


def test_login_redirect_returns_into_the_app_behind_a_stripping_proxy(tmp_path):
    """The app sees "/" (post-strip) but the browser is at /mojave-review/.
    Redirecting to "/" would send the reviewer to the site root — which is
    what happened in production until this was wired up."""
    server = _token_server(tmp_path, url_base_prefix=PREFIX,
                           proxy_strips_prefix=True)
    r = server.test_client().get("/?token=" + "T" * 16,
                                 follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"] == PREFIX

    r = server.test_client().get("/dashboard?token=" + "T" * 16,
                                 follow_redirects=False)
    assert r.headers["Location"] == "/mojave-review/dashboard"


def test_login_redirect_is_untouched_without_a_stripping_proxy(tmp_path):
    server = _token_server(tmp_path, url_base_prefix=PREFIX,
                           proxy_strips_prefix=False)
    r = server.test_client().get("/dashboard?token=" + "T" * 16,
                                 follow_redirects=False)
    assert r.headers["Location"] == "/dashboard"


def test_login_redirect_preserves_other_query_params(tmp_path):
    server = _token_server(tmp_path, url_base_prefix=PREFIX,
                           proxy_strips_prefix=True)
    r = server.test_client().get(
        "/?token=" + "T" * 16 + "&source=0003-066u", follow_redirects=False)
    loc = r.headers["Location"]
    assert loc.startswith(PREFIX)
    assert "source=0003-066u" in loc
    assert "token=" not in loc


def test_cookie_is_scoped_to_the_public_prefix(tmp_path):
    """The browser decides whether to send a cookie, and it only knows public
    paths — so the cookie path is the prefix regardless of stripping."""
    server = _token_server(tmp_path, url_base_prefix=PREFIX,
                           proxy_strips_prefix=True)
    r = server.test_client().get("/?token=" + "T" * 16,
                                 follow_redirects=False)
    setc = "; ".join(
        v for k, v in r.headers.items() if k.lower() == "set-cookie")
    assert "Path=/mojave-review/" in setc


def test_cookie_defaults_to_root_when_unprefixed(tmp_path):
    """Root-hosted keeps Path=/ so an upgrade doesn't log anyone out."""
    server = _token_server(tmp_path)
    r = server.test_client().get("/?token=" + "T" * 16,
                                 follow_redirects=False)
    setc = "; ".join(
        v for k, v in r.headers.items() if k.lower() == "set-cookie")
    assert "Path=/" in setc and "Path=/mojave-review/" not in setc


# ---------------------------------------------------------------------------
# Config plumbing
# ---------------------------------------------------------------------------


def test_config_key_round_trips(tmp_path):
    from mojave_review.config import load_config
    cfg = load_config({"results_dir": tmp_path,
                       "url_base_prefix": "mojave-review",
                       "proxy_strips_prefix": True},
                      config_file=tmp_path / "none.yaml", env={})
    assert cfg.url_base_prefix == PREFIX
    assert cfg.proxy_strips_prefix is True


def test_config_key_reads_from_env(tmp_path):
    from mojave_review.config import load_config
    cfg = load_config({"results_dir": tmp_path},
                      config_file=tmp_path / "none.yaml",
                      env={"MOJAVE_REVIEW_URL_BASE_PREFIX": "/x/",
                           "MOJAVE_REVIEW_PROXY_STRIPS_PREFIX": "true"})
    assert cfg.url_base_prefix == "/x/"
    assert cfg.proxy_strips_prefix is True


def test_config_default_is_false(tmp_path):
    from mojave_review.config import load_config
    cfg = load_config({"results_dir": tmp_path},
                      config_file=tmp_path / "none.yaml", env={})
    assert cfg.proxy_strips_prefix is False
