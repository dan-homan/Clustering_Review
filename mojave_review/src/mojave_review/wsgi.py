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


_config = load_config()

# Configure logging first so any error in create_app gets captured.
configure_logging(_config.log_file, level=_config.log_level)
_log = get_logger("mojave_review.wsgi")
_log.info("WSGI startup  reviewer_fallback=%s  admin=%s  auth=%s  "
          "log_file=%s",
          _config.reviewer, _config.admin,
          "token" if _config.tokens_file else "single-user",
          _config.log_file or "<stderr only>")

_app = create_app(
    results_dir=_config.results_dir,
    recommendations_dir=_config.recommendations_dir,
    cache_dir=_config.cache_dir,
    reviewer=_config.reviewer,
    admin=_config.admin,
    fits_data_dir=_config.fits_data_dir,
    tokens_path=_config.tokens_file,
    cookie_secure=_config.cookie_secure,
    admin_contact=_config.admin_contact,
)

# What gunicorn looks for.
application = _app.server
