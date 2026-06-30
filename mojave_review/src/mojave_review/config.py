"""Resolved runtime configuration.

The config has three layers, highest-priority wins:

    CLI flag  >  environment variable  >  config.yaml  >  built-in default

The launcher (``mojave-review``) calls :func:`load_config` with the
argparse Namespace; the WSGI entry point (``mojave_review.wsgi``)
calls :func:`load_config` with no CLI overrides at all, so a
production gunicorn deployment can be driven entirely from
``/etc/mojave-review/config.yaml`` plus ``MOJAVE_REVIEW_*`` env vars
in the systemd unit.

YAML format
-----------

.. code-block:: yaml

    # Paths
    results_dir: /data/Results
    recommendations_dir: /data/recommendations
    cache_dir: /data/fits_cache
    fits_data_dir: /data/mojave_fits   # optional

    # Authentication
    tokens_file: /etc/mojave-review/tokens.yaml
    admin_contact: "homand@university.edu"
    cookie_secure: true

    # Bind
    host: 127.0.0.1
    port: 8050

    # Single-user fallback (ignored in token mode)
    reviewer: anonymous
    admin: false

Environment variables
---------------------

* ``MOJAVE_REVIEW_CONFIG_FILE`` — path to config.yaml (default
  ``~/.mojave_review/config.yaml`` if it exists).
* ``MOJAVE_REVIEW_RESULTS_DIR``
* ``MOJAVE_REVIEW_RECOMMENDATIONS_DIR``
* ``MOJAVE_REVIEW_CACHE_DIR``
* ``MOJAVE_REVIEW_FITS_DATA_DIR`` (alias: ``MOJAVE_DATA`` — kept for
  back-compat with Phase 1)
* ``MOJAVE_REVIEW_TOKENS_FILE``
* ``MOJAVE_REVIEW_ADMIN_CONTACT``
* ``MOJAVE_REVIEW_COOKIE_SECURE`` (``true``/``false``)
* ``MOJAVE_REVIEW_HOST``
* ``MOJAVE_REVIEW_PORT``
* ``MOJAVE_REVIEW_REVIEWER``
* ``MOJAVE_REVIEW_ADMIN`` (``true``/``false``)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class Config:
    """Fully resolved runtime configuration.

    All paths are absolute. ``recommendations_dir`` and ``cache_dir``
    are mkdir'd at config-resolution time; ``results_dir`` is required
    to already exist; ``fits_data_dir`` is left alone (read-only).
    """

    # Paths
    results_dir: Path = Path("Results")
    recommendations_dir: Path | None = None     # resolved post-init
    cache_dir: Path | None = None               # resolved post-init
    fits_data_dir: Path | None = None

    # Auth
    tokens_file: Path | None = None
    admin_contact: str = "the admin"
    cookie_secure: bool = True

    # Bind
    host: str = "127.0.0.1"
    port: int = 8050
    # Public path prefix when served behind a reverse proxy
    # (e.g. ``/mojave-review/``). ``None`` ⇒ served at root. Normalised
    # post-init to have leading + trailing slashes. Passed to Dash as
    # ``url_base_pathname`` so its routes/assets and all in-app links
    # (via ``ui.urls.rel``) carry the prefix. Pair with an nginx
    # ``proxy_pass`` that PRESERVES the prefix (no trailing slash on the
    # upstream), so the path the browser uses matches the path Dash serves.
    url_base_prefix: str | None = None

    # Identity & roles
    reviewer: str | None = None                 # None ⇒ resolve to $USER
    admin: bool = False

    # Logging (see mojave_review/_logging.py). ``log_file=None`` means
    # stderr only — fine for dev; production typically wants a rotating
    # file under /data/logs/.
    log_file: Path | None = None
    log_level: str = "INFO"

    # CLI-only conveniences (env / yaml may carry them, but they only
    # matter to the ``mojave-review`` launcher — not to the WSGI entry).
    no_browser: bool = False
    debug: bool = False


# ---------------------------------------------------------------------------
# Type coercion (env vars + YAML scalars come in as strings)
# ---------------------------------------------------------------------------


_BOOL_TRUE = {"1", "true", "yes", "on", "y", "t"}
_BOOL_FALSE = {"0", "false", "no", "off", "n", "f"}


def _coerce(name: str, value: Any) -> Any:
    """Map a raw value to the type Config expects for ``name``. Raises
    :class:`ValueError` on something unparseable so misconfiguration
    fails fast and audibly."""
    if value is None:
        return None
    f = next(f for f in fields(Config) if f.name == name)
    target = f.type
    # Strip Optional[...] wrappers for the type check
    if isinstance(target, str):
        # PEP 563 strings — rough check, but our types are simple here
        is_bool = "bool" in target
        is_int = "int" in target and "bool" not in target
        is_path = "Path" in target
    else:
        is_bool = target is bool
        is_int = target is int
        is_path = target is Path
    if is_bool:
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in _BOOL_TRUE:
            return True
        if s in _BOOL_FALSE:
            return False
        raise ValueError(f"Bad boolean for {name!r}: {value!r}")
    if is_int:
        return int(value)
    if is_path:
        if isinstance(value, Path):
            return value
        return Path(str(value)).expanduser()
    # Strings & misc — let the dataclass take whatever was given.
    return value if isinstance(value, str) else str(value)


# ---------------------------------------------------------------------------
# Sources: YAML, env, CLI
# ---------------------------------------------------------------------------


_FIELD_NAMES = {f.name for f in fields(Config)}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Could not parse {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level must be a mapping.")
    # Reject typos early so they don't silently get ignored.
    unknown = set(raw.keys()) - _FIELD_NAMES
    if unknown:
        raise ValueError(
            f"{path}: unknown config key(s): {sorted(unknown)}. "
            f"Allowed keys: {sorted(_FIELD_NAMES)}."
        )
    return {k: _coerce(k, v) for k, v in raw.items() if v is not None}


# Mapping: Config field name -> primary env var (and optional aliases).
_ENV_MAP: dict[str, tuple[str, ...]] = {
    "results_dir":         ("MOJAVE_REVIEW_RESULTS_DIR",),
    "recommendations_dir": ("MOJAVE_REVIEW_RECOMMENDATIONS_DIR",),
    "cache_dir":           ("MOJAVE_REVIEW_CACHE_DIR",),
    "fits_data_dir":       ("MOJAVE_REVIEW_FITS_DATA_DIR", "MOJAVE_DATA"),
    "tokens_file":         ("MOJAVE_REVIEW_TOKENS_FILE",),
    "admin_contact":       ("MOJAVE_REVIEW_ADMIN_CONTACT",),
    "cookie_secure":       ("MOJAVE_REVIEW_COOKIE_SECURE",),
    "host":                ("MOJAVE_REVIEW_HOST",),
    "port":                ("MOJAVE_REVIEW_PORT",),
    "url_base_prefix":     ("MOJAVE_REVIEW_URL_BASE_PREFIX",),
    "reviewer":            ("MOJAVE_REVIEW_REVIEWER",),
    "admin":               ("MOJAVE_REVIEW_ADMIN",),
    "log_file":            ("MOJAVE_REVIEW_LOG_FILE",),
    "log_level":           ("MOJAVE_REVIEW_LOG_LEVEL",),
}


def _read_env(env: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field_name, candidates in _ENV_MAP.items():
        for env_name in candidates:
            val = env.get(env_name)
            if val:
                out[field_name] = _coerce(field_name, val)
                break
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


_DEFAULT_CONFIG_FILE = Path.home() / ".mojave_review" / "config.yaml"


def load_config(
    cli_overrides: dict[str, Any] | None = None,
    *,
    config_file: str | os.PathLike | None = None,
    env: dict[str, str] | None = None,
) -> Config:
    """Merge layered config sources into a fully-resolved :class:`Config`.

    Precedence (highest wins):

    1. ``cli_overrides`` — values explicitly set on the command line.
    2. Environment variables (see :data:`_ENV_MAP`).
    3. ``config.yaml`` (path: ``config_file`` arg or
       ``MOJAVE_REVIEW_CONFIG_FILE`` env var or ``~/.mojave_review/
       config.yaml``).
    4. Built-in :class:`Config` defaults.

    Paths are expanded but not resolved (no symlink mangling). The
    ``results_dir`` is required to exist; ``recommendations_dir`` and
    ``cache_dir`` are created if missing.
    """
    env = env or dict(os.environ)
    overrides = {k: v for k, v in (cli_overrides or {}).items() if v is not None}

    # Resolve the config file path itself (also layered).
    if config_file is not None:
        cfg_path = Path(config_file).expanduser()
    elif env.get("MOJAVE_REVIEW_CONFIG_FILE"):
        cfg_path = Path(env["MOJAVE_REVIEW_CONFIG_FILE"]).expanduser()
    else:
        cfg_path = _DEFAULT_CONFIG_FILE

    yaml_layer = _read_yaml(cfg_path)
    env_layer = _read_env(env)

    merged: dict[str, Any] = {}
    for f in fields(Config):
        v = overrides.get(f.name, env_layer.get(f.name, yaml_layer.get(f.name)))
        if v is not None:
            merged[f.name] = v

    cfg = Config(**merged)

    # ---- Post-resolution: path normalisation + sanity checks ----------
    cfg.results_dir = Path(cfg.results_dir).expanduser()
    if not cfg.results_dir.is_dir():
        raise SystemExit(f"results_dir not found: {cfg.results_dir}")

    if cfg.recommendations_dir is None:
        cfg.recommendations_dir = cfg.results_dir.parent / "recommendations"
    cfg.recommendations_dir = Path(cfg.recommendations_dir).expanduser()
    cfg.recommendations_dir.mkdir(parents=True, exist_ok=True)

    if cfg.cache_dir is None:
        cfg.cache_dir = Path.home() / ".mojave_review" / "cache"
    cfg.cache_dir = Path(cfg.cache_dir).expanduser()
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)

    if cfg.fits_data_dir is not None:
        cfg.fits_data_dir = Path(cfg.fits_data_dir).expanduser()
        # Don't create — read-only source. If it's missing the overlay
        # path silently falls back to NRAO fetch; the launcher prints a
        # warning in its banner.

    if cfg.tokens_file is not None:
        cfg.tokens_file = Path(cfg.tokens_file).expanduser()

    if cfg.reviewer is None:
        cfg.reviewer = env.get("USER") or "anonymous"

    # Normalise the public path prefix to /…/ (Dash requires both slashes).
    # A blank / whitespace-only / bare-"/" value means "served at root" →
    # None (so Dash gets no url_base_pathname and rel() is a no-op).
    p = str(cfg.url_base_prefix or "").strip().strip("/")
    cfg.url_base_prefix = f"/{p}/" if p else None

    return cfg
