"""Per-project foundations for serving several projects from one install.

Two things, both prerequisites for the path-prefix multi-project deployment
(see ``docs/multi_project.md``) and both worth having single-project:

1. ``source_run_param.csv`` / ``astrometry_mojave.dat`` are configurable, and
   discovery no longer looks at the current working directory FIRST.
2. The auth session cookie is scoped per app, so two apps on one host don't
   overwrite each other's token.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mojave_review.auth.middleware import COOKIE_NAME, cookie_identity
from mojave_review.data.astrometry import find_astrometry_file
from mojave_review.data.source_params import find_source_params


# ---------------------------------------------------------------------------
# 1. Discovery order + explicit override
# ---------------------------------------------------------------------------


_FINDERS = [
    pytest.param(find_source_params, "source_run_param.csv", id="source_params"),
    pytest.param(find_astrometry_file, "astrometry_mojave.dat", id="astrometry"),
]


@pytest.fixture()
def project(tmp_path):
    """Two project trees, each with its own data file, plus a cwd that has a
    third copy — the configuration that used to go wrong."""
    a = tmp_path / "proj_a"
    b = tmp_path / "proj_b"
    for p in (a, b):
        (p / "Results").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    return a, b, elsewhere


@pytest.mark.parametrize("finder,fname", _FINDERS)
def test_finds_the_file_at_the_project_root(project, finder, fname):
    a, _b, _e = project
    (a / fname).write_text("x")
    assert finder(a / "Results") == a / fname


@pytest.mark.parametrize("finder,fname", _FINDERS)
def test_finds_the_file_inside_results_dir(project, finder, fname):
    a, _b, _e = project
    (a / "Results" / fname).write_text("x")
    assert finder(a / "Results") == a / "Results" / fname


@pytest.mark.parametrize("finder,fname", _FINDERS)
def test_project_root_beats_results_dir(project, finder, fname):
    a, _b, _e = project
    (a / fname).write_text("root")
    (a / "Results" / fname).write_text("inner")
    assert finder(a / "Results") == a / fname


@pytest.mark.parametrize("finder,fname", _FINDERS)
def test_cwd_is_only_a_last_resort(project, monkeypatch, finder, fname):
    """The regression that matters. cwd used to be searched FIRST, so with one
    process serving several projects whichever directory it started in would
    win for all of them — project B silently reading project A's redshifts,
    with no error and nothing in the log."""
    a, b, _e = project
    (a / fname).write_text("A's own file")
    (b / fname).write_text("B's own file")
    # Start the process inside project A, then ask about project B.
    monkeypatch.chdir(a)
    assert finder(b / "Results") == b / fname, "must not pick up A's file"
    assert finder(a / "Results") == a / fname


@pytest.mark.parametrize("finder,fname", _FINDERS)
def test_cwd_still_works_when_the_project_has_no_file(project, monkeypatch,
                                                      finder, fname):
    """Keeping cwd as the final fallback preserves the convenience of a
    one-off local run; it just can't override the project's own file."""
    _a, b, elsewhere = project
    (elsewhere / fname).write_text("ambient")
    monkeypatch.chdir(elsewhere)
    assert finder(b / "Results") == elsewhere / fname


@pytest.mark.parametrize("finder,fname", _FINDERS)
def test_explicit_override_wins_over_every_discovery_slot(project, monkeypatch,
                                                          finder, fname):
    a, _b, elsewhere = project
    (a / fname).write_text("discoverable")
    (a / "Results" / fname).write_text("also discoverable")
    (elsewhere / fname).write_text("ambient")
    monkeypatch.chdir(elsewhere)
    pinned = elsewhere / "pinned.dat"
    pinned.write_text("the one the operator named")
    assert finder(a / "Results", pinned) == pinned


@pytest.mark.parametrize("finder,fname", _FINDERS)
def test_explicit_override_is_returned_even_if_missing(project, finder, fname):
    """A typo'd path must not silently fall back to a discovered file: the
    operator asked for a specific file and should see it not load, against the
    name they gave."""
    a, _b, _e = project
    (a / fname).write_text("discoverable")
    typo = a / "nope" / "typo.csv"
    assert finder(a / "Results", typo) == typo


@pytest.mark.parametrize("finder,fname", _FINDERS)
def test_none_when_nothing_anywhere(project, monkeypatch, finder, fname):
    _a, b, elsewhere = project
    monkeypatch.chdir(elsewhere)
    assert finder(b / "Results") is None


def test_absent_astrometry_is_a_normal_state(project, monkeypatch):
    """BK224 has no astrometry file yet (it may later). That must disable the
    feature, not error."""
    from mojave_review.data.astrometry import astrometry_for, load_astrometry
    _a, b, elsewhere = project
    monkeypatch.chdir(elsewhere)
    path = find_astrometry_file(b / "Results")
    assert path is None
    table = load_astrometry(path)
    assert table == {}
    assert astrometry_for(table, "0017+200u") is None


def test_missing_redshift_file_means_z_unknown(project, monkeypatch):
    from mojave_review.data.source_params import load_redshifts, redshift_for
    _a, b, elsewhere = project
    monkeypatch.chdir(elsewhere)
    table = load_redshifts(find_source_params(b / "Results"))
    assert table == {}
    assert redshift_for(table, "0017+200u") is None


# ---------------------------------------------------------------------------
# 2. Per-app cookie identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", [None, "/", "  ", ""])
def test_root_mounted_keeps_the_historical_cookie(prefix):
    """An existing single-project deployment must not log everyone out on
    upgrade, so root-mounted keeps ("mr_token", "/") exactly."""
    assert cookie_identity(prefix) == (COOKIE_NAME, "/")


def test_prefix_gives_a_distinct_name_and_path():
    name, path = cookie_identity("/mojave-review/")
    assert name == "mr_token_mojave_review"
    assert path == "/mojave-review/"


def test_two_projects_do_not_share_a_cookie_slot():
    """The actual bug: the browser keys cookies on (domain, path, name), so
    two apps using one name at path="/" overwrite each other. Verified
    end-to-end against two live servers sharing one cookie jar — before the
    fix, returning to the first project 403'd."""
    a = cookie_identity("/proj-a/")
    b = cookie_identity("/proj-b/")
    assert a != b
    assert a[0] != b[0], "names must differ"
    assert a[1] != b[1], "paths must differ"


def test_prefix_is_normalised_to_leading_and_trailing_slash():
    for raw in ("mojave-review", "/mojave-review", "mojave-review/",
                "  /mojave-review/  "):
        assert cookie_identity(raw) == ("mr_token_mojave_review",
                                        "/mojave-review/")


def test_cookie_name_stays_a_valid_token():
    """Cookie names are RFC 6265 tokens: no "/", no spaces, no separators."""
    for raw in ("/a/b/", "/weird name/", "/Mixed-Case/", "/a.b+c/"):
        name, _path = cookie_identity(raw)
        assert name.replace("_", "").isalnum(), name
        assert " " not in name and "/" not in name


def test_nested_prefixes_differ():
    """A project mounted under another's prefix still gets its own slot —
    note the PATH alone would not save it, since /review/ is a prefix of
    /review/bk224/, which is why the name differs too."""
    outer = cookie_identity("/review/")
    inner = cookie_identity("/review/bk224/")
    assert outer[0] != inner[0]


def test_install_accepts_the_prefix_and_scopes_the_cookie(tmp_path):
    """End-to-end through Flask: the Set-Cookie header carries both halves."""
    import yaml
    from flask import Flask
    from mojave_review.auth.middleware import install_token_middleware

    tokens = tmp_path / "tokens.yaml"
    tokens.write_text(yaml.safe_dump(
        {"users": [{"token": "T" * 16, "name": "Reviewer"}]}))
    server = Flask(__name__)

    @server.route("/proj-a/")
    def _index():
        return "ok"

    install_token_middleware(
        server, tokens_path=tokens, cookie_secure=False,
        url_base_prefix="/proj-a/",
    )
    client = server.test_client()
    r = client.get("/proj-a/?token=" + "T" * 16, follow_redirects=False)
    assert r.status_code == 302
    setc = "; ".join(r.headers.getlist("Set-Cookie"))
    assert "mr_token_proj_a=" in setc
    assert "Path=/proj-a/" in setc
