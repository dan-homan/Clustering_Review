"""Package-wide logging configuration.

Two outputs:

* stderr — always on. Picked up by the dev launcher's terminal and by
  systemd-journald under a production deploy.
* rotating file — opt-in via :class:`mojave_review.config.Config`
  ``log_file`` field (or the ``MOJAVE_REVIEW_LOG_FILE`` env var). 5 MB
  per file × 5 backups = 25 MB cap, which is plenty for a small
  reviewer group.

With several projects mounted in one process (see ``projects.py``) every line
is tagged with the project handling the request — ``[bk224] …`` — resolved per
record by :class:`_ProjectContextFilter` from the Flask app in context. Single-
project output is unchanged, tag included: the tag is empty when no project is
set.

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

# Flask config key holding the project name an app belongs to. Set by
# ``app.create_app`` when it is told one (i.e. by the multi-project
# dispatcher); read by :class:`_ProjectContextFilter` at emit time.
PROJECT_NAME_KEY = "MOJAVE_REVIEW_PROJECT"

# Format mirrors what journald already prefixes per line, so a `tail -f`
# of the rotating file looks right next to `journalctl --user-unit ...`.
#
# ``project_tag`` is "" in single-project mode, so that output stays
# byte-identical, and "[bk224] " when several projects share the process.
# ``defaults`` (3.10+) guarantees ``project_tag`` is formattable even if no
# filter has run — a record reaching a handler whose filter was somehow
# skipped would otherwise raise a formatting KeyError and be dropped with a
# "--- Logging error ---" on stderr. It also removes an ordering dependence:
# the filter MUTATES the shared record, so whichever handler runs first was
# silently supplying the attribute for the others.
_FMT = logging.Formatter(
    "%(asctime)s %(levelname)-7s %(project_tag)s%(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    defaults={"project_tag": ""},
)


class _ProjectContextFilter(logging.Filter):
    """Tag each record with the project whose app is handling the request.

    With several projects mounted in one process, a line like
    ``mojave_review.auth.middleware | session established`` is ambiguous —
    and ``configure_logging`` runs once, before any app exists, so the
    project cannot be baked into the formatter.

    So it is resolved per record from the Flask app in context, the same
    mechanism ``ui.urls.rel`` uses for the request prefix. A filter rather
    than a wrapper means **no call site changes**: every existing
    ``get_logger(__name__).info(...)`` across the package picks the tag up for
    free.

    Attached to the HANDLERS, not the logger: a filter on a logger only sees
    records logged directly through it, while child loggers
    (``mojave_review.data.loader`` and friends) propagate up to the package
    root's handlers without passing its filters.

    Records emitted outside a request — startup, shutdown, background work —
    get an empty tag rather than a misleading one.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.project_tag = ""
        try:
            from flask import current_app, has_app_context

            if has_app_context():
                name = current_app.config.get(PROJECT_NAME_KEY)
                if name:
                    record.project_tag = f"[{name}] "
        except Exception:
            # Logging must never be the thing that breaks a request.
            pass
        return True


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

    project_filter = _ProjectContextFilter()

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(_FMT)
    stderr.addFilter(project_filter)
    root.addHandler(stderr)

    if log_file is not None:
        log_file = Path(log_file).expanduser()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setFormatter(_FMT)
        fh.addFilter(project_filter)
        root.addHandler(fh)
