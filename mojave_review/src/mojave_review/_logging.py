"""Package-wide logging configuration.

Two outputs:

* stderr — always on. Picked up by the dev launcher's terminal and by
  systemd-journald under a production deploy.
* rotating file — opt-in via :class:`mojave_review.config.Config`
  ``log_file`` field (or the ``MOJAVE_REVIEW_LOG_FILE`` env var). 5 MB
  per file × 5 backups = 25 MB cap, which is plenty for a small
  reviewer group.

The package logger is rooted at ``"mojave_review"`` and emits at
``INFO`` by default. Child loggers (``mojave_review.auth.middleware``,
``mojave_review.data.loader``, etc.) inherit the configuration. Use
:func:`get_logger(__name__)` inside any module — never
``logging.getLogger()`` directly — so log messages all funnel through
this setup.

Module name is ``_logging.py`` (leading underscore) to avoid shadowing
the stdlib ``logging`` in ``from .. import logging``-style imports.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path


_PKG_LOGGER = "mojave_review"

# Format mirrors what journald already prefixes per line, so a `tail -f`
# of the rotating file looks right next to `journalctl --user-unit ...`.
_FMT = logging.Formatter(
    "%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger of the package root. Pass ``__name__`` so
    messages carry the originating module."""
    if not name or name == _PKG_LOGGER:
        return logging.getLogger(_PKG_LOGGER)
    return logging.getLogger(name)


def configure_logging(
    log_file: Path | None = None,
    *,
    level: str = "INFO",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """Wire the package logger to stderr (always) and optionally to a
    rotating file. Idempotent — repeat calls replace existing handlers
    so reload-under-gunicorn doesn't end up double-logging."""
    root = logging.getLogger(_PKG_LOGGER)
    for h in list(root.handlers):
        root.removeHandler(h)

    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    # Don't propagate to the root logger — we'd otherwise double-log
    # under gunicorn (which configures the root logger for its own
    # access / error streams).
    root.propagate = False

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(_FMT)
    root.addHandler(stderr)

    if log_file is not None:
        log_file = Path(log_file).expanduser()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setFormatter(_FMT)
        root.addHandler(fh)
