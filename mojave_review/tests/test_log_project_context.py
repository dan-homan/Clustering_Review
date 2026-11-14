"""Per-project log context (multi-project step 6).

``configure_logging`` runs once, before any app exists, so the project can't
be baked into the formatter. It is resolved per record from the Flask app in
context — the same mechanism ``ui.urls.rel`` uses for the request prefix —
which means no call site anywhere in the package had to change.

Single-project output must stay byte-identical: the tag is empty when no
project is set, not "[]" or a stray space.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from mojave_review._logging import (
    PROJECT_NAME_KEY, configure_logging, get_logger,
)
from mojave_review.app import create_app
from mojave_review.projects import build_dispatcher, parse_project_spec

TOKEN = "T" * 16


@pytest.fixture()
def logfile(tmp_path):
    """Configure package logging to a file, and restore afterwards so the
    handlers don't leak into other tests."""
    path = tmp_path / "app.log"
    configure_logging(path, level="INFO")
    yield path
    root = logging.getLogger("mojave_review")
    for h in list(root.handlers):
        root.removeHandler(h)


def _project(tmp_path, name):
    (tmp_path / name / "Results").mkdir(parents=True)
    return parse_project_spec(str(tmp_path / name))


def _tokens(tmp_path):
    p = tmp_path / "tokens.yaml"
    p.write_text(yaml.safe_dump({"users": [{"token": TOKEN, "name": "Rev"}]}))
    return p


# ---------------------------------------------------------------------------
# Single-project: output unchanged
# ---------------------------------------------------------------------------


def test_no_tag_outside_a_request(logfile):
    get_logger("mojave_review.test").info("startup-ish message")
    line = logfile.read_text().strip()
    assert "startup-ish message" in line
    # No tag at all — not "[]", not a doubled space.
    assert "[" not in line
    assert "INFO    mojave_review.test |" in line


def test_no_tag_for_a_single_project_app(tmp_path, logfile):
    (tmp_path / "Results").mkdir()
    app = create_app(results_dir=tmp_path / "Results",
                     recommendations_dir=tmp_path / "recs",
                     cache_dir=tmp_path / "cache", reviewer="Rev",
                     tokens_path=_tokens(tmp_path), cookie_secure=False)
    assert PROJECT_NAME_KEY not in app.server.config
    app.server.test_client().get(f"/?token={TOKEN}", follow_redirects=False)
    lines = [l for l in logfile.read_text().splitlines() if "session" in l]
    assert lines, "the middleware should have logged a login"
    assert "[" not in lines[0]


# ---------------------------------------------------------------------------
# Multi-project: every request line is attributed
# ---------------------------------------------------------------------------


def _dispatch(tmp_path):
    specs = [_project(tmp_path, "alpha"), _project(tmp_path, "beta")]
    app, apps = build_dispatcher(
        specs, cache_dir=tmp_path / "cache", reviewer="fallback",
        tokens_path=_tokens(tmp_path), cookie_secure=False)
    from werkzeug.test import Client
    return Client(app), apps


def test_each_app_knows_its_project(tmp_path, logfile):
    _client, apps = _dispatch(tmp_path)
    assert apps["alpha"].server.config[PROJECT_NAME_KEY] == "alpha"
    assert apps["beta"].server.config[PROJECT_NAME_KEY] == "beta"


def test_request_lines_carry_the_project_tag(tmp_path, logfile):
    """The point of the whole step: a login under one project must be
    distinguishable in the log from one under another."""
    client, _apps = _dispatch(tmp_path)
    client.get(f"/alpha/?token={TOKEN}", follow_redirects=True)
    tagged = [l for l in logfile.read_text().splitlines() if "session" in l]
    assert tagged, "the middleware should have logged a login"
    assert all("[alpha]" in l for l in tagged), tagged


def test_both_projects_are_attributed_separately(tmp_path, logfile):
    """Each project logs under its own tag. Separate clients so each one
    performs its own url-token login rather than arriving with a cookie
    already set (the cookie is shared across projects by design)."""
    from werkzeug.test import Client

    specs = [_project(tmp_path, "alpha"), _project(tmp_path, "beta")]
    app, _apps = build_dispatcher(
        specs, cache_dir=tmp_path / "cache", reviewer="fallback",
        tokens_path=_tokens(tmp_path), cookie_secure=False)
    for name in ("alpha", "beta"):
        Client(app).get(f"/{name}/?token={TOKEN}", follow_redirects=True)

    logins = [l for l in logfile.read_text().splitlines() if "session" in l]
    assert sum("[alpha]" in l for l in logins) == 1, logins
    assert sum("[beta]" in l for l in logins) == 1, logins


def test_startup_logs_the_mount_table(tmp_path, logfile):
    """Startup is outside any request, so per-request tagging can't cover it;
    the mount table is logged explicitly instead. This is the line you want
    when someone reports the wrong data under a URL."""
    _dispatch(tmp_path)
    text = logfile.read_text()
    assert "mounted project alpha at /alpha/" in text
    assert "mounted project beta at /beta/" in text
    assert str(tmp_path / "alpha") in text


def test_tag_does_not_leak_between_requests(tmp_path, logfile):
    """The tag is resolved per record from the app in context, so a line
    emitted outside a request after serving one must not inherit a project."""
    client, _apps = _dispatch(tmp_path)
    client.get(f"/alpha/?token={TOKEN}", follow_redirects=True)
    get_logger("mojave_review.test").info("after the request")
    line = [l for l in logfile.read_text().splitlines()
            if "after the request" in l][0]
    assert "[alpha]" not in line and "[" not in line


def test_logging_never_breaks_on_a_bad_context(tmp_path, logfile):
    """The filter swallows its own errors — logging must not be the thing
    that fails a request."""
    from mojave_review._logging import _ProjectContextFilter
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    assert _ProjectContextFilter().filter(rec) is True
    assert rec.project_tag == ""
