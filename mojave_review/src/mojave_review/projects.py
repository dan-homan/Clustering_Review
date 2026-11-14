"""Project registry: serving several review projects from one process.

A *project* is an independent review effort with its own sources, models,
recommendations and notes. On disk it is one directory holding the standard
layout, which both production projects already have::

    <root>/
    ├── Results/              # <source>_<emin>-<emax> folders
    ├── recommendations/      # per-source reviewer JSON (+ _admin/)
    ├── notes/                # derived: recommendations_dir.parent / "notes"
    ├── source_run_param.csv  # discovered via results_dir.parent
    └── astrometry_mojave.dat # optional, discovered the same way

Only ``Results/`` is required. ``recommendations/`` is created on demand (the
config layer mkdirs it), and the two data tables are found by the usual
per-project discovery — ``<root>`` is ``results_dir.parent``, the first search
slot — so nothing has to be configured per project. See
``docs/multi_project.md``.

Projects are mounted under their own path segment and dispatched by a WSGI
middleware, one Dash app each:

    /            → redirect to the default (first) project
    /mojave/     → project "mojave"
    /bk224/      → project "bk224"

**Why one app per project rather than one project-aware app:** every
directory is captured in closures at ``register_callbacks`` time and the
redshift / astrometry tables load once at startup, so making the active
project a per-request value would mean unpicking every callback. Mounting
separate apps costs one interpreter's worth of shared library memory and
leaves the app internals untouched. (Reviewer decision; the full comparison is
in ``docs/multi_project.md``.)

**What is shared and what is not.** Per project: ``Results/``,
``recommendations/`` (and therefore ``notes/``), the two data tables. Shared
across projects: the FITS cache (keyed by real MOJAVE data identity, so it is
project-independent) and ``tokens.yaml`` — one token per reviewer works
everywhere, which is what reviewers wanted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ._logging import get_logger

_log = get_logger(__name__)

__all__ = [
    "ProjectSpec", "ProjectConfigError",
    "parse_project_spec", "discover_projects", "projects_from_config",
    "resolve_projects", "resolve_projects_tolerant", "build_dispatcher",
]


class ProjectConfigError(ValueError):
    """A project declaration that can't be used. Raised at startup so a typo
    fails loudly rather than producing a half-mounted deployment."""


# A project name becomes a URL path segment and part of the session-cookie
# name, so keep it to characters that are safe in both without escaping.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]*$")

# Path segments the app itself serves. A project with one of these names would
# mount at a path the Dash app also wants, so refuse it rather than let the
# shadowing be discovered in production. ("_dash-*" can't match _NAME_RE.)
_RESERVED_NAMES = frozenset({"assets", "dashboard", "compare", "static"})


@dataclass(frozen=True)
class ProjectSpec:
    """One project: its URL segment, display label, and root directory."""
    name: str          # URL segment + identity, e.g. "bk224"
    label: str         # shown in the project switcher, e.g. "BK224"
    root: Path         # the project directory

    @property
    def results_dir(self) -> Path:
        return self.root / "Results"

    @property
    def recommendations_dir(self) -> Path:
        return self.root / "recommendations"


def _default_name(root: Path) -> str:
    """Derive a URL-safe name from a directory basename.

    ``MOJAVE_1996.00-2026.00`` → ``mojave``: the leading alphanumeric run,
    lowercased. Long dated directory names make ugly URLs, and the epoch range
    is not what distinguishes one project from another. Falls back to a
    sanitised form of the whole basename when there is no leading word.
    """
    base = root.name
    m = re.match(r"^[A-Za-z0-9]+", base)
    if m and not m.group(0).isdigit():
        return m.group(0).lower()
    cleaned = re.sub(r"[^A-Za-z0-9._~-]+", "-", base).strip("-._~").lower()
    return cleaned or "project"


def _validate(name: str, root: Path) -> None:
    if not _NAME_RE.match(name):
        raise ProjectConfigError(
            f"Project name {name!r} is not usable in a URL. Use letters, "
            f"digits, '.', '_', '~' or '-', starting with a letter or digit."
        )
    if name.lower() in _RESERVED_NAMES:
        raise ProjectConfigError(
            f"Project name {name!r} collides with a path the app serves "
            f"({', '.join(sorted(_RESERVED_NAMES))}). Pick another name."
        )
    if not root.is_dir():
        raise ProjectConfigError(f"Project {name!r}: no such directory: {root}")
    if not (root / "Results").is_dir():
        raise ProjectConfigError(
            f"Project {name!r} at {root} has no Results/ directory. A project "
            f"root holds Results/ and recommendations/ — did you point at the "
            f"Results/ directory itself by mistake?"
        )


def parse_project_spec(arg: str) -> ProjectSpec:
    """Parse one ``--project`` value: ``name=path``, or a bare ``path`` whose
    name is derived from its basename.

    The ``=`` is split on the FIRST occurrence only, so a path containing one
    still works when a name is given.
    """
    text = (arg or "").strip()
    if not text:
        raise ProjectConfigError("Empty --project value.")
    if "=" in text:
        name, _, path = text.partition("=")
        name, path = name.strip(), path.strip()
        if not path:
            raise ProjectConfigError(f"--project {arg!r}: no path after '='.")
    else:
        path, name = text, ""
    given = Path(path).expanduser()
    # Resolve so two spellings of one directory are recognised as duplicates.
    try:
        root = given.resolve()
    except OSError:
        root = given
    # Name and label come from the path AS GIVEN, not the resolved target: a
    # --projects-dir of symlinks is the tidy way to put projects from
    # different trees side by side, and the symlink names are the operator's
    # chosen identities. Resolving first would silently rename a symlink
    # "legacy" to whatever its target directory happens to be called.
    name = name or _default_name(given)
    _validate(name, root)
    return ProjectSpec(name=name, label=given.name or root.name, root=root)


def discover_projects(projects_dir: Path) -> list[ProjectSpec]:
    """Every immediate subdirectory of ``projects_dir`` that holds a
    ``Results/`` directory, sorted by name.

    Subdirectories without ``Results/`` are skipped silently — this is a
    scan, so unrelated directories alongside the projects are expected, and
    only an explicit ``--project`` deserves a hard error.
    """
    base = Path(projects_dir).expanduser()
    if not base.is_dir():
        raise ProjectConfigError(f"--projects-dir: no such directory: {base}")
    out: list[ProjectSpec] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not (child / "Results").is_dir():
            continue
        out.append(parse_project_spec(str(child)))
    if not out:
        raise ProjectConfigError(
            f"--projects-dir {base}: no subdirectory contains a Results/ "
            f"directory."
        )
    return out


def projects_from_config(entries) -> list[ProjectSpec]:
    """Parse a ``projects:`` block from config.yaml. Each entry is either a
    ``name=path`` / path string, or a mapping::

        projects:
          - name: mojave
            label: MOJAVE 1996-2026
            root: /data/MOJAVE_1996.00-2026.00
          - bk224=/data/BK224
    """
    if not entries:
        return []
    if not isinstance(entries, (list, tuple)):
        raise ProjectConfigError(
            "config 'projects' must be a list, got "
            f"{type(entries).__name__}.")
    out: list[ProjectSpec] = []
    for entry in entries:
        if isinstance(entry, str):
            out.append(parse_project_spec(entry))
            continue
        if not isinstance(entry, dict):
            raise ProjectConfigError(
                f"config 'projects' entry must be a string or mapping, got "
                f"{type(entry).__name__}.")
        root = entry.get("root") or entry.get("path")
        if not root:
            raise ProjectConfigError(
                f"config 'projects' entry {entry!r} has no 'root'.")
        spec = parse_project_spec(
            f"{entry['name']}={root}" if entry.get("name") else str(root))
        label = entry.get("label")
        out.append(ProjectSpec(name=spec.name,
                               label=str(label) if label else spec.label,
                               root=spec.root))
    return out


def resolve_projects_tolerant(
    *, project_args=None, projects_dir=None, config_entries=None,
) -> tuple[list[ProjectSpec], list[str]]:
    """Like :func:`resolve_projects`, but a project that can't be used is
    SKIPPED rather than fatal. Returns ``(specs, problems)``.

    For the production WSGI entry point. Aborting startup on one bad
    declaration blacks out the reviewers of every *working* project, which is
    the wrong trade-off when the usual cause is operational rather than a
    mistake — a project whose rsync hasn't run yet, or a directory moved
    mid-migration. Each problem is returned for the caller to log at ERROR,
    and the project is simply absent from the switcher.

    The interactive CLI keeps using the strict :func:`resolve_projects`: there
    the operator is reading the error as it happens, and quietly serving less
    than they asked for would be worse than refusing.

    Raises :class:`ProjectConfigError` only when NOTHING is usable, since a
    dispatcher with no projects can't serve anything either way. Declaring no
    projects at all is still just "single-project mode" — ``([], [])``.
    """
    problems: list[str] = []
    specs: list[ProjectSpec] = []

    # Config entries and explicit --project args are per-entry recoverable.
    for entry in (config_entries or []):
        try:
            specs.extend(projects_from_config([entry]))
        except ProjectConfigError as e:
            problems.append(str(e))
    if projects_dir:
        try:
            specs.extend(discover_projects(Path(projects_dir)))
        except ProjectConfigError as e:
            problems.append(str(e))
    for arg in (project_args or []):
        try:
            specs.append(parse_project_spec(str(arg)))
        except ProjectConfigError as e:
            problems.append(str(e))

    declared_anything = bool(config_entries or projects_dir or project_args)
    if not specs and declared_anything:
        raise ProjectConfigError(
            "no usable project among those declared: " + "; ".join(problems))

    # De-duplicate with the same rules as the strict path, but downgrade a
    # conflict to a skip-with-complaint rather than a failure.
    by_name: dict[str, ProjectSpec] = {}
    by_root: dict[Path, str] = {}
    for spec in specs:
        if spec.name in by_name and by_name[spec.name].root != spec.root:
            problems.append(
                f"skipping {spec.root}: name {spec.name!r} is already used by "
                f"{by_name[spec.name].root}")
            continue
        prior = by_root.get(spec.root)
        if prior and prior != spec.name:
            problems.append(
                f"skipping {spec.name!r}: {spec.root} is already mounted as "
                f"{prior!r}")
            continue
        by_name[spec.name] = spec
        by_root[spec.root] = spec.name
    return list(by_name.values()), problems


def resolve_projects(
    *, project_args=None, projects_dir=None, config_entries=None,
) -> list[ProjectSpec]:
    """Combine every declaration form into one ordered, de-duplicated list.

    Order is config entries, then ``--projects-dir`` discoveries, then
    explicit ``--project`` arguments — so an explicit CLI name can override a
    scanned one for the same root, and the FIRST entry is the default project
    (what ``/`` redirects to). Returns ``[]`` when nothing is declared, which
    the callers read as "single-project mode".
    """
    specs: list[ProjectSpec] = []
    specs.extend(projects_from_config(config_entries))
    if projects_dir:
        specs.extend(discover_projects(Path(projects_dir)))
    for arg in (project_args or []):
        specs.append(parse_project_spec(str(arg)))

    by_name: dict[str, ProjectSpec] = {}
    by_root: dict[Path, str] = {}
    for spec in specs:
        if spec.name in by_name and by_name[spec.name].root != spec.root:
            raise ProjectConfigError(
                f"Two different projects are both named {spec.name!r}: "
                f"{by_name[spec.name].root} and {spec.root}. Give one an "
                f"explicit name with --project <name>=<path>."
            )
        prior = by_root.get(spec.root)
        if prior and prior != spec.name:
            raise ProjectConfigError(
                f"{spec.root} is declared twice, as {prior!r} and "
                f"{spec.name!r}. Remove one declaration."
            )
        by_name[spec.name] = spec
        by_root[spec.root] = spec.name
    # dict preserves insertion order, so the default project is still first.
    return list(by_name.values())


# ---------------------------------------------------------------------------
# WSGI dispatcher
# ---------------------------------------------------------------------------


def mount_prefix(spec: ProjectSpec, mount_root: str = "/") -> str:
    """Public path a project is served at, e.g. ``/bk224/``."""
    base = (mount_root or "/").strip()
    if not base.startswith("/"):
        base = "/" + base
    return f"{base.rstrip('/')}/{spec.name}/"


def build_dispatcher(
    projects: list[ProjectSpec],
    *,
    mount_root: str = "/",
    app_factory=None,
    **create_app_kwargs,
):
    """Build one Dash app per project and return ``(wsgi_app, {name: Dash})``.

    Every unmatched path is redirected into the **default (first)** project
    with its path and query preserved, so paths that worked before projects
    existed keep working: ``/`` → ``/mojave/``, ``/dashboard`` →
    ``/mojave/dashboard``, and an old ``/?token=…`` bookmark carries its token
    through. No loop is possible, because any path that already starts with a
    mounted prefix is handled by that mount instead.

    Each app is told it is mounted under its prefix with
    ``proxy_strips_prefix=True``: ``DispatcherMiddleware`` strips the mount
    segment before the app sees it, exactly as a stripping reverse proxy does,
    so the app serves at the root while building links with the public prefix.

    ``app_factory`` is injectable for tests; it defaults to
    :func:`mojave_review.app.create_app`.
    """
    if not projects:
        raise ProjectConfigError("build_dispatcher needs at least one project.")
    from werkzeug.middleware.dispatcher import DispatcherMiddleware
    from werkzeug.utils import redirect as _redirect

    if app_factory is None:
        from .app import create_app as app_factory  # local: avoids a cycle

    default_prefix = mount_prefix(projects[0], mount_root)
    # Shared across every app: the switcher's entries, and the cookie scope.
    nav = [(spec.name, spec.label, mount_prefix(spec, mount_root))
           for spec in projects]
    scope = (mount_root or "/").strip() or "/"

    apps: dict[str, object] = {}
    mounts: dict[str, object] = {}
    for spec in projects:
        prefix = mount_prefix(spec, mount_root)
        dash_app = app_factory(
            results_dir=spec.results_dir,
            recommendations_dir=spec.recommendations_dir,
            url_base_prefix=prefix,
            proxy_strips_prefix=True,
            # One login for all projects: the cookie is scoped to the common
            # mount root, not to each project's own path. Reviewers asked for
            # a single token and a single bookmark.
            cookie_scope_prefix=scope,
            project_nav=nav,
            current_project=spec.name,
            **create_app_kwargs,
        )
        apps[spec.name] = dash_app
        # DispatcherMiddleware keys on the prefix WITHOUT a trailing slash.
        mounts[prefix.rstrip("/")] = dash_app.server
        # Startup happens outside any request, so per-request tagging can't
        # cover it — log the mount table explicitly instead. This is the only
        # place the name→path mapping is stated, and it is what you want in
        # the log when a reviewer reports the wrong data under a URL.
        _log.info("mounted project %s at %s  (root=%s%s)",
                  spec.name, prefix, spec.root,
                  "" if spec.label == spec.root.name else f", label={spec.label!r}")

    def _fallback(environ, start_response):
        path = environ.get("PATH_INFO", "/") or "/"
        qs = environ.get("QUERY_STRING", "")
        target = default_prefix.rstrip("/") + path
        if qs:
            target = f"{target}?{qs}"
        return _redirect(target, code=302)(environ, start_response)

    return DispatcherMiddleware(_fallback, mounts), apps
