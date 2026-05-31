"""Per-request reviewer-name resolution.

The callback bodies in ``ui/*.py`` used to close over a single
``reviewer`` string captured at app-startup from the ``--reviewer``
CLI flag. That works in Phase 1 single-user mode (one launcher per
reviewer) but loses identity in Phase 2 token-auth mode, where a
single process serves multiple reviewers and the identity comes from
the per-request cookie or ``?token=…``.

:func:`current_reviewer` is the small shim that lets the same callback
body work in both modes. Token mode: the middleware in
``auth/middleware.py`` set ``flask.g.reviewer`` on this request, so we
return that. Single-user mode (or any caller that's outside a Flask
request context, e.g. ``app.layout`` rendered from a test script): we
return the fallback the caller passed in.

The fallback is the ``--reviewer`` CLI value, threaded as a closed-over
``reviewer`` keyword arg through the existing ``register_callbacks`` /
``register`` signatures. Nothing about the function signatures needs to
change to add token-auth identity — only the function *bodies* swap
``reviewer`` for ``current_reviewer(reviewer)``.
"""

from __future__ import annotations

from flask import g, has_request_context


def current_reviewer(fallback: str) -> str:
    """Reviewer name for the in-flight request.

    Returns ``flask.g.reviewer`` when the request flowed through the
    token middleware (Phase 2). Returns ``fallback`` when there's no
    request context, or when the middleware didn't run (Phase 1
    single-user mode). The fallback is never the empty string —
    callers default to ``"anonymous"`` upstream.
    """
    if has_request_context():
        from_g = getattr(g, "reviewer", None)
        if from_g:
            return str(from_g)
    return fallback
