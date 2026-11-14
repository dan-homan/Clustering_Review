"""Project registry + WSGI dispatcher (multi-project step 4).

A project is one directory holding `Results/` (and conventionally
`recommendations/`). Declaring none keeps the historical single-project
behaviour; declaring any mounts each at `/<name>/` behind a dispatcher with
every unmatched path redirected into the default (first) project.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mojave_review.projects import (
    ProjectConfigError, ProjectSpec, build_dispatcher, discover_projects,
    mount_prefix, parse_project_spec, projects_from_config, resolve_projects,
)


def _mk(tmp_path: Path, name: str, *, results=True) -> Path:
    root = tmp_path / name
    (root / ("Results" if results else "NotResults")).mkdir(parents=True)
    return root


# ---------------------------------------------------------------------------
# parse_project_spec
# ---------------------------------------------------------------------------


def test_name_equals_path(tmp_path):
    root = _mk(tmp_path, "BK224")
    spec = parse_project_spec(f"bk224={root}")
    assert spec.name == "bk224"
    assert spec.root == root.resolve()
    assert spec.results_dir == root.resolve() / "Results"
    assert spec.recommendations_dir == root.resolve() / "recommendations"


def test_bare_path_derives_the_name(tmp_path):
    assert parse_project_spec(str(_mk(tmp_path, "BK224"))).name == "bk224"


def test_derived_name_drops_a_dated_suffix(tmp_path):
    """`MOJAVE_1996.00-2026.00` would make an ugly URL, and the epoch range
    isn't what distinguishes one project from another."""
    root = _mk(tmp_path, "MOJAVE_1996.00-2026.00")
    assert parse_project_spec(str(root)).name == "mojave"


def test_path_containing_equals_still_parses_with_a_name(tmp_path):
    root = _mk(tmp_path, "odd=dir")
    spec = parse_project_spec(f"weird={root}")
    assert spec.name == "weird" and spec.root == root.resolve()


def test_tilde_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _mk(tmp_path, "proj")
    assert parse_project_spec("~/proj").root == (tmp_path / "proj").resolve()


def test_symlink_name_wins_over_its_target(tmp_path):
    """A --projects-dir of symlinks is the tidy way to put projects from
    different trees side by side, so the symlink names are the operator's
    chosen identities — resolving first would silently rename them."""
    target = _mk(tmp_path, "MOJAVE_1996.00-2026.00")
    link = tmp_path / "legacy15"
    link.symlink_to(target)
    spec = parse_project_spec(str(link))
    assert spec.name == "legacy15"
    assert spec.label == "legacy15"
    assert spec.root == target.resolve()        # but the data is the target's


@pytest.mark.parametrize("bad", ["", "   ", "name="])
def test_empty_declarations_rejected(bad):
    with pytest.raises(ProjectConfigError):
        parse_project_spec(bad)


@pytest.mark.parametrize("name", ["dashboard", "compare", "assets", "static",
                                  "DASHBOARD"])
def test_reserved_names_rejected(tmp_path, name):
    """These are paths the Dash app itself serves."""
    root = _mk(tmp_path, "p")
    with pytest.raises(ProjectConfigError, match="collides"):
        parse_project_spec(f"{name}={root}")


@pytest.mark.parametrize("name", ["bad name", "has/slash", "-leading",
                                  "with?query", "a#b"])
def test_url_unsafe_names_rejected(tmp_path, name):
    root = _mk(tmp_path, "p")
    with pytest.raises(ProjectConfigError, match="not usable in a URL"):
        parse_project_spec(f"{name}={root}")


def test_missing_directory_rejected(tmp_path):
    with pytest.raises(ProjectConfigError, match="no such directory"):
        parse_project_spec(f"x={tmp_path / 'nope'}")


def test_directory_without_results_rejected(tmp_path):
    root = _mk(tmp_path, "p", results=False)
    with pytest.raises(ProjectConfigError, match="no Results/"):
        parse_project_spec(f"p={root}")


def test_pointing_at_results_itself_is_caught(tmp_path):
    """An easy mistake, since --results-dir takes exactly that path."""
    root = _mk(tmp_path, "p")
    with pytest.raises(ProjectConfigError, match="no Results/"):
        parse_project_spec(f"p={root / 'Results'}")


# ---------------------------------------------------------------------------
# discover_projects / projects_from_config / resolve_projects
# ---------------------------------------------------------------------------


def test_discover_skips_non_projects(tmp_path):
    base = tmp_path / "projects"
    base.mkdir()
    for n in ("alpha", "beta"):
        (base / n / "Results").mkdir(parents=True)
    (base / "notaproject").mkdir()          # no Results/ -> skipped silently
    (base / ".hidden" / "Results").mkdir(parents=True)
    (base / "afile").write_text("x")
    names = [s.name for s in discover_projects(base)]
    assert names == ["alpha", "beta"]


def test_discover_requires_at_least_one(tmp_path):
    base = tmp_path / "empty"
    base.mkdir()
    with pytest.raises(ProjectConfigError, match="no subdirectory"):
        discover_projects(base)


def test_discover_missing_dir(tmp_path):
    with pytest.raises(ProjectConfigError, match="no such directory"):
        discover_projects(tmp_path / "nope")


def test_config_mapping_and_string_forms(tmp_path):
    a, b = _mk(tmp_path, "A"), _mk(tmp_path, "B")
    specs = projects_from_config([
        {"name": "aa", "label": "Project AA", "root": str(a)},
        f"bb={b}",
    ])
    assert [(s.name, s.label) for s in specs] == [("aa", "Project AA"),
                                                  ("bb", "B")]


def test_config_mapping_accepts_path_alias(tmp_path):
    a = _mk(tmp_path, "A")
    assert projects_from_config([{"name": "aa", "path": str(a)}])[0].name == "aa"


def test_config_rejects_junk(tmp_path):
    with pytest.raises(ProjectConfigError, match="must be a list"):
        projects_from_config("not-a-list")
    with pytest.raises(ProjectConfigError, match="no 'root'"):
        projects_from_config([{"name": "x"}])
    with pytest.raises(ProjectConfigError, match="string or mapping"):
        projects_from_config([123])


def test_empty_means_single_project_mode(tmp_path):
    assert resolve_projects() == []
    assert resolve_projects(config_entries=[]) == []
    assert resolve_projects(project_args=[]) == []


def test_first_declared_is_the_default(tmp_path):
    a, b = _mk(tmp_path, "A"), _mk(tmp_path, "B")
    specs = resolve_projects(project_args=[f"bb={b}", f"aa={a}"])
    assert specs[0].name == "bb"


def test_identical_duplicate_collapses(tmp_path):
    a = _mk(tmp_path, "A")
    assert len(resolve_projects(project_args=[f"aa={a}", f"aa={a}"])) == 1


def test_same_name_different_roots_is_an_error(tmp_path):
    a, b = _mk(tmp_path, "A"), _mk(tmp_path, "B")
    with pytest.raises(ProjectConfigError, match="both named"):
        resolve_projects(project_args=[f"x={a}", f"x={b}"])


def test_same_root_different_names_is_an_error(tmp_path):
    a = _mk(tmp_path, "A")
    with pytest.raises(ProjectConfigError, match="declared twice"):
        resolve_projects(project_args=[f"x={a}", f"y={a}"])


# ---------------------------------------------------------------------------
# mount_prefix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mount_root,expected", [
    ("/", "/bk224/"), ("", "/bk224/"), (None, "/bk224/"),
    ("/review/", "/review/bk224/"), ("review", "/review/bk224/"),
    ("/review", "/review/bk224/"),
])
def test_mount_prefix(tmp_path, mount_root, expected):
    spec = ProjectSpec(name="bk224", label="BK224", root=tmp_path)
    assert mount_prefix(spec, mount_root) == expected


# ---------------------------------------------------------------------------
# build_dispatcher
# ---------------------------------------------------------------------------


def _dispatch(tmp_path, names=("alpha", "beta"), **kw):
    specs = [parse_project_spec(str(_mk(tmp_path, n))) for n in names]
    app, apps = build_dispatcher(
        specs, cache_dir=tmp_path / "cache", reviewer="Rev", **kw)
    from werkzeug.test import Client
    return Client(app), apps, specs


def test_dispatcher_needs_a_project():
    with pytest.raises(ProjectConfigError):
        build_dispatcher([])


def test_each_project_is_mounted_and_isolated(tmp_path):
    client, apps, specs = _dispatch(tmp_path)
    assert set(apps) == {"alpha", "beta"}
    for spec in specs:
        r = client.get(f"/{spec.name}/")
        assert r.status_code == 200
        assert apps[spec.name].config.requests_pathname_prefix == f"/{spec.name}/"
        # Each app points at its OWN project directory.
        from mojave_review.ui.urls import REQUESTS_PREFIX_KEY
        assert apps[spec.name].server.config[REQUESTS_PREFIX_KEY] \
            == f"/{spec.name}/"


def test_bootstrap_endpoints_return_json_not_the_catch_all(tmp_path):
    """The failure that makes a browser spin forever: a prefix mismatch sends
    these through Dash's catch-all, which answers with the index HTML."""
    client, _apps, _specs = _dispatch(tmp_path)
    for ep in ("_dash-layout", "_dash-dependencies"):
        r = client.get(f"/alpha/{ep}")
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("application/json"), ep


def test_root_redirects_to_the_default_project(tmp_path):
    client, _apps, _specs = _dispatch(tmp_path)
    r = client.get("/")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/alpha/")


def test_unmatched_paths_fall_into_the_default_project(tmp_path):
    """So root-level bookmarks from before projects existed keep working."""
    client, _apps, _specs = _dispatch(tmp_path)
    r = client.get("/dashboard")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/alpha/dashboard")


def test_root_redirect_preserves_the_query_string(tmp_path):
    """An old /?token=... bookmark must carry its token through."""
    client, _apps, _specs = _dispatch(tmp_path)
    r = client.get("/?token=abc123")
    assert "token=abc123" in r.headers["Location"]


def test_no_redirect_loop_on_a_mounted_prefix(tmp_path):
    """A path already under a mount is handled by that mount, never by the
    fallback — otherwise /alpha/ would redirect to itself forever."""
    client, _apps, _specs = _dispatch(tmp_path)
    assert client.get("/alpha/").status_code == 200


def test_mount_root_shifts_every_project(tmp_path):
    client, apps, _specs = _dispatch(tmp_path, mount_root="/review/")
    assert apps["alpha"].config.requests_pathname_prefix == "/review/alpha/"
    assert client.get("/review/alpha/").status_code == 200
    assert client.get("/").headers["Location"].endswith("/review/alpha/")
