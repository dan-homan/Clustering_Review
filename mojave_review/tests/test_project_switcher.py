"""Step 5: the header project switcher, and one login across all projects.

Two halves, both only meaningful in multi-project mode:

* ``build_project_switcher`` renders a ``<details>`` of links to sibling
  mounts — nothing at all when there is only one project, which is the
  overwhelmingly common case.
* the session cookie is scoped to the **mount root** rather than each
  project's own prefix, so a single token works everywhere. Reviewers asked
  for one token and one bookmark; a per-project cookie would 403 them on the
  second project despite a valid token.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mojave_review.auth.middleware import cookie_identity
from mojave_review.projects import build_dispatcher, parse_project_spec
from mojave_review.ui.layout import build_project_switcher

TOKEN = "T" * 16


# ---------------------------------------------------------------------------
# The control itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("nav", [None, [], [("a", "A", "/a/")]])
def test_no_switcher_when_there_is_nothing_to_switch_to(nav):
    """Single-project mode must look exactly as it always has."""
    assert build_project_switcher(nav, "a") is None


def _switcher(current):
    sw = build_project_switcher(
        [("mojave", "MOJAVE 1996-2026", "/mojave/"), ("bk224", "BK224", "/bk224/")],
        current)
    summary, panel = sw.children
    links = [(a.children, a.href) for a in panel.children]
    return summary.children, links


def test_lists_every_project_with_links_to_siblings():
    _summary, links = _switcher("mojave")
    assert [href for _lbl, href in links] == ["/mojave/", "/bk224/"]


def test_marks_the_current_project():
    summary, links = _switcher("bk224")
    assert "BK224" in summary
    current = [lbl for lbl, _h in links if lbl.startswith("● ")]
    others = [lbl for lbl, _h in links if lbl.startswith("○ ")]
    assert current == ["● BK224"]
    assert others == ["○ MOJAVE 1996-2026"]


def test_summary_names_the_current_project():
    """So "which project am I in" is answerable without opening the control."""
    for current, expected in (("mojave", "MOJAVE 1996-2026"), ("bk224", "BK224")):
        summary, _links = _switcher(current)
        assert expected in summary


def test_unknown_current_project_still_renders():
    """Defensive: a nav list that doesn't contain the current name must not
    crash the whole page render."""
    summary, links = _switcher("somethingelse")
    assert len(links) == 2
    assert isinstance(summary, str)


def test_entries_are_plain_links_not_a_callback():
    """Switching is an ordinary navigation: each project keeps its own URL, so
    Back and per-project bookmarks work. No callback, no clientside JS."""
    sw = build_project_switcher([("a", "A", "/a/"), ("b", "B", "/b/")], "a")
    _summary, panel = sw.children
    for entry in panel.children:
        assert entry.href in ("/a/", "/b/")
        assert getattr(entry, "id", None) is None   # nothing for a callback


# ---------------------------------------------------------------------------
# One login for every project
# ---------------------------------------------------------------------------


def test_cookie_identity_at_the_mount_root_is_the_shared_one():
    assert cookie_identity("/") == ("mr_token", "/")
    # A non-root mount root still yields ONE cookie for all projects beneath.
    assert cookie_identity("/review/") == ("mr_token_review", "/review/")


def _dispatch(tmp_path, mount_root="/"):
    roots = []
    for n in ("alpha", "beta"):
        (tmp_path / n / "Results").mkdir(parents=True)
        roots.append(parse_project_spec(str(tmp_path / n)))
    tok = tmp_path / "tokens.yaml"
    tok.write_text(yaml.safe_dump({"users": [{"token": TOKEN, "name": "Rev"}]}))
    app, apps = build_dispatcher(
        roots, mount_root=mount_root, cache_dir=tmp_path / "cache",
        reviewer="fallback", tokens_path=tok, cookie_secure=False)
    from werkzeug.test import Client
    return Client(app), apps


def test_one_token_authenticates_every_project(tmp_path):
    """The regression this guards: scoping the cookie per project meant a
    reviewer clicking from one project to the next got a 403."""
    client, _apps = _dispatch(tmp_path)
    client.get(f"/alpha/?token={TOKEN}", follow_redirects=True)
    assert client.get("/alpha/").status_code == 200
    assert client.get("/beta/").status_code == 200      # no second token
    assert client.get("/alpha/").status_code == 200     # and back again


def test_cookie_is_scoped_to_the_mount_root(tmp_path):
    client, _apps = _dispatch(tmp_path)
    r = client.get(f"/alpha/?token={TOKEN}", follow_redirects=False)
    setc = "; ".join(v for k, v in r.headers.items() if k.lower() == "set-cookie")
    assert "mr_token=" in setc
    assert "Path=/;" in setc or setc.rstrip().endswith("Path=/")
    assert "Path=/alpha/" not in setc


def test_cookie_scope_follows_a_non_root_mount_root(tmp_path):
    client, _apps = _dispatch(tmp_path, mount_root="/review/")
    r = client.get(f"/review/alpha/?token={TOKEN}", follow_redirects=False)
    setc = "; ".join(v for k, v in r.headers.items() if k.lower() == "set-cookie")
    assert "mr_token_review=" in setc
    assert "Path=/review/" in setc


def test_anonymous_is_still_refused_everywhere(tmp_path):
    client, _apps = _dispatch(tmp_path)
    assert client.get("/alpha/").status_code == 403
    assert client.get("/beta/").status_code == 403


def _rendered_switcher(client, prefix):
    """The switcher as a reviewer actually receives it: through the page
    router, over HTTP, in the right app."""
    r = client.post(prefix + "_dash-update-component", json={
        "output": "page-content.children",
        "outputs": {"id": "page-content", "property": "children"},
        "inputs": [{"id": "url", "property": "pathname", "value": prefix},
                   {"id": "dashboard-refresh", "property": "data", "value": None}],
        "changedPropIds": ["url.pathname"]})
    assert r.status_code == 200, r.status_code
    summary, links = [None], []

    def walk(node):
        if isinstance(node, dict):
            props = node.get("props", {})
            kids = props.get("children")
            # The admin Window-N panel is also a <details>, so match the
            # switcher's own summary rather than whichever came last.
            if node.get("type") == "Summary" and isinstance(kids, str) \
                    and kids.startswith("\U0001F5C2"):
                summary[0] = kids
            if node.get("type") == "A" and isinstance(kids, str) \
                    and kids.startswith(("\u25cf ", "\u25cb ")):
                links.append((kids, props["href"]))
            for v in props.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(r.json["response"]["page-content"]["children"])
    return summary[0], links


def test_every_app_renders_the_nav_with_its_own_current_project(tmp_path):
    """Each app shows the full project list but marks itself — the thing that
    would silently break if ``current_project`` were shared or stale."""
    client, apps = _dispatch(tmp_path)
    assert set(apps) == {"alpha", "beta"}
    client.get(f"/alpha/?token={TOKEN}", follow_redirects=True)

    for name in ("alpha", "beta"):
        summary, links = _rendered_switcher(client, f"/{name}/")
        assert summary is not None, f"no switcher rendered under /{name}/"
        assert name in summary.lower(), summary
        assert [h for _l, h in links] == ["/alpha/", "/beta/"]
        marked = [l for l, _h in links if l.startswith("\u25cf ")]
        assert marked == [f"\u25cf {name}"], marked
