"""ui.urls.rel must resolve PER APP, not from Dash's process-wide singleton.

``dash.get_relative_path`` reads ``dash._get_paths.CONFIG``, which every
``Dash(...)`` constructor overwrites. With one app per project in a single
process (the multi-project deployment) the LAST app built wins, so every
project's pages render links pointing at whichever project was constructed
last. Observed against two real projects: MOJAVE's header linked to
``/bk224/dashboard``.

Dash's own asset and callback URLs are built from each app's own config and
were never affected, which is exactly what makes this easy to miss — the page
loads fine and only the navigation links are wrong.
"""

from __future__ import annotations

import pytest

from mojave_review.app import create_app
from mojave_review.ui.urls import REQUESTS_PREFIX_KEY, rel


def _app(tmp_path, name, prefix):
    root = tmp_path / name
    (root / "Results").mkdir(parents=True, exist_ok=True)
    return create_app(
        results_dir=root / "Results",
        recommendations_dir=root / "recs",
        cache_dir=tmp_path / "cache",
        reviewer="Rev",
        url_base_prefix=prefix,
        proxy_strips_prefix=True,      # as a dispatcher mount behaves
    )


def test_each_app_stashes_its_own_prefix(tmp_path):
    a = _app(tmp_path, "mojave", "/mojave/")
    b = _app(tmp_path, "bk224", "/bk224/")
    assert a.server.config[REQUESTS_PREFIX_KEY] == "/mojave/"
    assert b.server.config[REQUESTS_PREFIX_KEY] == "/bk224/"


def test_rel_follows_the_app_handling_the_request(tmp_path):
    """The regression. Build two apps, then resolve rel() inside each one's
    request context: the FIRST app must still produce its own prefix even
    though the second app was constructed afterwards."""
    a = _app(tmp_path, "mojave", "/mojave/")
    b = _app(tmp_path, "bk224", "/bk224/")

    with a.server.test_request_context("/"):
        assert rel("/dashboard") == "/mojave/dashboard"
        assert rel("/compare") == "/mojave/compare"
        assert rel("/") == "/mojave/"
    with b.server.test_request_context("/"):
        assert rel("/dashboard") == "/bk224/dashboard"
        assert rel("/") == "/bk224/"
    # And back again — not a one-shot.
    with a.server.test_request_context("/"):
        assert rel("/dashboard") == "/mojave/dashboard"


def test_dash_singleton_really_is_overwritten(tmp_path):
    """Pin the premise, so this test still means something if Dash changes:
    the module-level helper returns the LAST-built app's prefix regardless of
    which app is in context."""
    from dash import get_relative_path
    _a = _app(tmp_path, "mojave", "/mojave/")
    _b = _app(tmp_path, "bk224", "/bk224/")
    assert get_relative_path("/dashboard") == "/bk224/dashboard"
    with _a.server.test_request_context("/"):
        # The singleton is still wrong here...
        assert get_relative_path("/dashboard") == "/bk224/dashboard"
        # ...which is precisely why rel() must not use it.
        assert rel("/dashboard") == "/mojave/dashboard"


def test_root_mounted_app_is_unaffected(tmp_path):
    root = tmp_path / "solo"
    (root / "Results").mkdir(parents=True)
    app = create_app(results_dir=root / "Results",
                     recommendations_dir=root / "recs",
                     cache_dir=tmp_path / "cache", reviewer="Rev")
    assert app.server.config[REQUESTS_PREFIX_KEY] == "/"
    with app.server.test_request_context("/"):
        assert rel("/dashboard") == "/dashboard"
        assert rel("/") == "/"


def test_rel_outside_a_request_context_still_works(tmp_path):
    """Layout introspection and unit tests call rel() with no request in
    flight; it must not raise."""
    assert isinstance(rel("/dashboard"), str)


@pytest.mark.parametrize("path,expected", [
    ("/dashboard", "/mojave/dashboard"),
    ("/", "/mojave/"),
    ("", "/mojave/"),
])
def test_join_matches_dash_semantics(tmp_path, path, expected):
    a = _app(tmp_path, "mojave", "/mojave/")
    with a.server.test_request_context("/"):
        assert rel(path) == expected
