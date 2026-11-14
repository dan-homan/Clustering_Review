"""WSGI entry point for production gunicorn deploys.

Reads config from env + config.yaml (no CLI args available under
gunicorn) and exposes the underlying Flask app object as
``application`` — the name gunicorn looks for by default.

Recommended invocation (Phase 2 chunk 8 will land an example
systemd unit that wraps this in its ExecStart):

.. code-block:: bash

    gunicorn -w 2 -b 127.0.0.1:8050 mojave_review.wsgi:application

The ``-w 2`` (two workers) is sensible for the small reviewer group;
each worker holds its own bundle cache and tokens-store cache. State
that *must* be shared across workers (just ``recommendations/`` JSON
files in this app) lives on disk.

Misconfiguration fails the import — gunicorn won't bind, so a bad
config can't silently masquerade as a healthy deploy.
"""

from __future__ import annotations

from ._logging import configure_logging, get_logger
from .app import create_app
from .config import load_config
from .projects import build_dispatcher, resolve_projects_tolerant


_config = load_config()

# Configure logging first so any error in create_app gets captured.
configure_logging(_config.log_file, level=_config.log_level)
_log = get_logger("mojave_review.wsgi")
_log.info("WSGI startup  reviewer_fallback=%s  admin=%s  auth=%s  "
          "log_file=%s",
          _config.reviewer, _config.admin,
          "token" if _config.tokens_file else "single-user",
          _config.log_file or "<stderr only>")

# Multi-project: when config.yaml declares `projects:`, build one app per
# project behind a dispatcher instead of a single root-mounted app. Shared
# settings (cache, tokens, identity) come from the same config; per-project
# directories come from each project root.
# Tolerant resolution: a project that can't be used (data not synced yet, a
# path typo, a directory moved mid-migration) is skipped and logged rather
# than taking down the reviewers of every working project. Fails only when
# nothing at all is usable.
_projects, _project_problems = resolve_projects_tolerant(
    config_entries=_config.projects)
for _problem in _project_problems:
    _log.error("project skipped: %s", _problem)
_shared = dict(
    cache_dir=_config.cache_dir,
    reviewer=_config.reviewer,
    admin=_config.admin,
    fits_data_dir=_config.fits_data_dir,
    tokens_path=_config.tokens_file,
    cookie_secure=_config.cookie_secure,
    admin_contact=_config.admin_contact,
)

if _projects:
    _log.info("WSGI multi-project: %d projects (default %s) under %s",
              len(_projects), _projects[0].name,
              _config.url_base_prefix or "/")
    # The dispatcher IS the WSGI callable gunicorn serves; there is no single
    # Dash app to take .server from.
    application, _apps = build_dispatcher(
        _projects, mount_root=_config.url_base_prefix or "/", **_shared)
else:
    _app = create_app(
        results_dir=_config.results_dir,
        recommendations_dir=_config.recommendations_dir,
        astrometry_path=(str(_config.astrometry_file)
                         if _config.astrometry_file else None),
        source_params_path=(str(_config.source_params_file)
                            if _config.source_params_file else None),
        # url_base_prefix was previously omitted here, so the config key was
        # read and then ignored. Both default to off, which is the correct
        # setting for the Denison deployment (see docs/deployment_phase2.md);
        # they exist for a proxy or dispatcher that really does mount the app
        # under a prefix.
        url_base_prefix=_config.url_base_prefix,
        proxy_strips_prefix=_config.proxy_strips_prefix,
        **_shared,
    )
    # What gunicorn looks for.
    application = _app.server

# ``application`` is set in both branches above — the dispatcher directly, or
# the single app's Flask server.
