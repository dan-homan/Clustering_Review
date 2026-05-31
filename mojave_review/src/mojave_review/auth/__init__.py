"""Phase 2 authentication primitives.

The web app supports two run-time modes:

* **Single-user** (no ``--tokens-file``): one reviewer, identified by the
  ``--reviewer`` CLI flag. Everything is wide open to whoever can reach
  the port. This is the Phase 1 mode and is the default for local
  development.
* **Token auth** (``--tokens-file path/to/tokens.yaml``): each reviewer
  has a long-lived bearer token stored in ``tokens.yaml``. The token
  enters the app via a ``?token=...`` URL parameter or a cookie set on
  the first valid hit. The token middleware (Phase 2 chunk 3, not yet
  added) resolves a request to ``flask.g.reviewer`` and returns a 403
  page when the token is missing or invalid.

This sub-package is intentionally framework-agnostic so the token store
can be exercised from the command line (``mojave-review-tokens``)
without pulling Dash / Flask into the import graph.
"""
