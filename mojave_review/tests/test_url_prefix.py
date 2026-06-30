"""url_base_prefix normalisation (config) + the rel() URL helper."""

from __future__ import annotations

import pytest

from mojave_review.config import load_config


def _cfg(tmp_path, **overrides):
    # results_dir must exist; everything else can default.
    overrides.setdefault("results_dir", tmp_path)
    return load_config(overrides, config_file=tmp_path / "nope.yaml", env={})


@pytest.mark.parametrize("raw,expected", [
    ("mojave-review", "/mojave-review/"),
    ("/mojave-review", "/mojave-review/"),
    ("/mojave-review/", "/mojave-review/"),
    ("  /a/b  ", "/a/b/"),
])
def test_prefix_normalised_to_leading_trailing_slash(tmp_path, raw, expected):
    cfg = _cfg(tmp_path, url_base_prefix=raw)
    assert cfg.url_base_prefix == expected


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_blank_prefix_is_none(tmp_path, raw):
    cfg = _cfg(tmp_path, url_base_prefix=raw)
    assert cfg.url_base_prefix is None


def test_rel_falls_back_to_plain_path_without_app():
    # Outside any Dash app context, rel() returns the path unchanged so
    # introspection / tests work at root.
    from mojave_review.ui.urls import rel
    assert rel("/dashboard") == "/dashboard"
    assert rel("/") == "/"
