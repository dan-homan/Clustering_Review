"""Token store: a YAML file mapping usernames to bearer tokens.

The on-disk shape (the one ``mojave-review-tokens`` writes and the
middleware reads):

    users:
      - name: alice
        token: alice-aZbXc7Yw3qP8mNkR0vL9fEgT
        note: "primary reviewer; joined 2026-06-01"
        created: "2026-06-01T10:00:00+00:00"
      - name: bob
        token: bob-T9hQ2rJpX4cKvB8nLm0sFwYz
        note: ""
        created: "2026-06-02T09:30:00+00:00"

Two layers of API:

* :class:`TokenStore` for callers that already hold the parsed YAML
  in memory.
* The module-level :func:`load_store`, :func:`save_store` and
  :func:`resolve_tokens_path` for callers that just want to do
  "open, mutate, write back".

Token format. Each token is ``"<username>-<24 url-safe random bytes>"``
— the username prefix is human-readable so admins can eyeball the
``tokens.yaml`` file and tell entries apart at a glance, and the random
suffix is the actual secret (``secrets.token_urlsafe(24)`` ≈ 32 chars
of URL-safe base64). Treat the full token, *not* just the suffix, as
sensitive.

Default location. ``~/.mojave_review/tokens.yaml`` — the same
``~/.mojave_review/`` tree the FITS cache uses. Override with the
``MOJAVE_REVIEW_TOKENS_FILE`` env var or the ``--tokens-file`` CLI flag
(precedence: CLI > env > default).
"""

from __future__ import annotations

import os
import re
import secrets
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml


# ---------------------------------------------------------------------------
# Token generation
# ---------------------------------------------------------------------------

# A token's suffix carries 24 raw bytes of entropy. ``secrets.token_urlsafe``
# returns ~1.33 chars per byte, so a 24-byte token is ~32 chars after the
# username prefix and the connecting dash.
_TOKEN_SUFFIX_BYTES = 24

# Usernames must be a-z, 0-9, ., _, - and 1–32 chars. The token format
# embeds them with a leading dash separator, so we forbid leading/trailing
# dashes in the username to keep the split unambiguous. We additionally
# forbid "." and ".." outright — the username flows through as a
# directory component under recommendations/, where those names would
# both be ambiguous *and* a directory-traversal hazard.
_USERNAME_RE = re.compile(r"^[a-z0-9._][a-z0-9._-]{0,30}[a-z0-9._]$|^[a-z0-9_]$")
_FORBIDDEN_USERNAMES = {".", ".."}


def generate_token(username: str) -> str:
    """Return ``"<username>-<random>"`` with 24 bytes of URL-safe entropy."""
    return f"{username}-{secrets.token_urlsafe(_TOKEN_SUFFIX_BYTES)}"


def validate_username(name: str) -> None:
    """Raise ``ValueError`` if ``name`` is not a legal token-store username."""
    if (not isinstance(name, str)
            or name in _FORBIDDEN_USERNAMES
            or not _USERNAME_RE.match(name)):
        raise ValueError(
            f"Invalid username {name!r}. Must be 1–32 chars from "
            f"a-z / 0-9 / . / _ / -, may not start or end with '-', "
            f"and may not be '.' or '..'."
        )


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


_DEFAULT_TOKENS_PATH = Path.home() / ".mojave_review" / "tokens.yaml"
_ENV_VAR = "MOJAVE_REVIEW_TOKENS_FILE"


def resolve_tokens_path(cli_arg: str | os.PathLike | None) -> Path:
    """Pick the tokens file path, with precedence CLI > env > default.

    Returns the path the *user wrote*, with ``~`` expanded but symlinks
    left intact (calling ``.resolve()`` would mangle e.g. ``/tmp/...``
    into ``/private/tmp/...`` on macOS, which is surprising in error
    messages and breaks straight string comparisons).

    Does not check whether the path exists — the caller decides whether
    a missing file means "single-user mode" (the launcher's choice) or
    "create an empty one" (the ``mojave-review-tokens`` CLI's choice).
    """
    if cli_arg:
        return Path(cli_arg).expanduser()
    env = os.environ.get(_ENV_VAR)
    if env:
        return Path(env).expanduser()
    return _DEFAULT_TOKENS_PATH


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class User:
    """One row in tokens.yaml."""

    name: str
    token: str
    note: str = ""
    created: str = ""              # ISO-8601 with UTC offset

    @staticmethod
    def fresh(name: str, note: str = "") -> "User":
        validate_username(name)
        return User(
            name=name,
            token=generate_token(name),
            note=note,
            created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def to_dict(self) -> dict:
        # asdict preserves field order, which keeps the YAML readable.
        return asdict(self)


@dataclass
class TokenStore:
    """In-memory representation of tokens.yaml.

    Mutations return mutated dataclasses (or booleans); the caller is
    responsible for calling :meth:`save` when ready. That separation lets
    the CLI bulk-edit several rows in one shot without churning the file.
    """

    users: list[User] = field(default_factory=list)

    # --- queries ---------------------------------------------------------

    def __len__(self) -> int:
        return len(self.users)

    def __iter__(self) -> Iterable[User]:
        return iter(self.users)

    def by_name(self, name: str) -> User | None:
        for u in self.users:
            if u.name == name:
                return u
        return None

    def by_token(self, token: str) -> User | None:
        """Constant-time-ish match against a presented bearer token."""
        if not token:
            return None
        # secrets.compare_digest avoids leaking timing info between a
        # known-good and a known-bad token. The dict lookup that
        # preceded it leaks (key in dict) but not the secret comparison.
        for u in self.users:
            if secrets.compare_digest(u.token, token):
                return u
        return None

    # --- mutations -------------------------------------------------------

    def add(self, name: str, note: str = "") -> User:
        """Insert a new user with a freshly-generated token. Raises if
        the name already exists."""
        validate_username(name)
        if self.by_name(name) is not None:
            raise ValueError(f"User {name!r} already exists.")
        u = User.fresh(name, note=note)
        self.users.append(u)
        return u

    def rotate(self, name: str) -> User:
        """Issue a new token for an existing user, in place. Raises if
        the name is unknown."""
        u = self.by_name(name)
        if u is None:
            raise KeyError(f"No such user: {name!r}")
        u.token = generate_token(u.name)
        return u

    def revoke(self, name: str) -> bool:
        """Drop ``name`` from the store. Returns True if a row was
        removed, False if the name wasn't present."""
        for i, u in enumerate(self.users):
            if u.name == name:
                del self.users[i]
                return True
        return False


# ---------------------------------------------------------------------------
# YAML I/O
# ---------------------------------------------------------------------------


def load_store(path: Path) -> TokenStore:
    """Parse ``tokens.yaml``. A missing file returns an empty store; a
    file with no ``users:`` key also returns an empty store. Anything
    else raises a :class:`ValueError` with the underlying parse error so
    the CLI can show a clean message."""
    if not path.is_file():
        return TokenStore()
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Could not parse {path}: {e}") from e
    rows = raw.get("users") or []
    if not isinstance(rows, list):
        raise ValueError(f"{path}: 'users' must be a list, got {type(rows).__name__}.")
    users: list[User] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{path}: users[{i}] must be a mapping.")
        if "name" not in row or "token" not in row:
            raise ValueError(f"{path}: users[{i}] missing required 'name' or 'token'.")
        users.append(User(
            name=str(row["name"]),
            token=str(row["token"]),
            note=str(row.get("note") or ""),
            created=str(row.get("created") or ""),
        ))
    return TokenStore(users=users)


def save_store(store: TokenStore, path: Path) -> None:
    """Write ``store`` to ``path`` atomically. Creates parent dirs and
    sets mode 0600 on the file itself — tokens are bearer secrets."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"users": [u.to_dict() for u in store.users]}
    # Atomic write: render to a temp file in the same dir, fsync, rename.
    # If the process dies mid-write the original tokens.yaml is still
    # intact and an orphan .tmp is left behind for the admin to clean.
    fd, tmp_name = tempfile.mkstemp(prefix=".tokens.", suffix=".tmp",
                                    dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(payload, f, sort_keys=False, default_flow_style=False)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
