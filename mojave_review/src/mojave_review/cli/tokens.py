"""``mojave-review-tokens`` — admin CLI for the per-user token store.

Subcommands:

* ``add <user> [--note ...]``     issue a fresh token for a new user
* ``rotate <user>``               keep the user, replace their token
* ``revoke <user>``               drop the user (no token will resolve to them)
* ``list``                        all users (default: tokens hidden — use ``--show-tokens``)
* ``show <user>``                 print one user's full record + bookmark URL
* ``url <user> [--base-url ...]`` print just the bookmark URL (for an email)

All commands target the file resolved by
``mojave_review.auth.tokens.resolve_tokens_path`` (precedence ``--tokens-file``
> ``MOJAVE_REVIEW_TOKENS_FILE`` env var > ``~/.mojave_review/tokens.yaml``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from ..auth.tokens import (
    TokenStore,
    User,
    load_store,
    resolve_tokens_path,
    save_store,
)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _print_user_row(u: User, show_token: bool = True) -> None:
    """One line per user — terse and grep-friendly."""
    tok = u.token if show_token else "•" * 12
    note = f"  ({u.note})" if u.note else ""
    created = f"  [{u.created}]" if u.created else ""
    print(f"  {u.name:24s}  {tok}{created}{note}")


def _print_user_block(u: User) -> None:
    """Full record, multi-line. Used by ``show``."""
    print(f"name:    {u.name}")
    print(f"token:   {u.token}")
    if u.note:
        print(f"note:    {u.note}")
    if u.created:
        print(f"created: {u.created}")


def _bookmark_url(base_url: str, token: str) -> str:
    """Build ``<base_url>?token=<token>``, percent-encoding the token. If
    ``base_url`` already carries a query string we append cleanly."""
    parts = urlsplit(base_url)
    existing = parts.query
    token_q = f"token={quote(token, safe='')}"
    new_query = f"{existing}&{token_q}" if existing else token_q
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def _cmd_add(args: argparse.Namespace) -> int:
    path = resolve_tokens_path(args.tokens_file)
    store = load_store(path)
    try:
        u = store.add(args.user, note=args.note or "")
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    save_store(store, path)
    print(f"Added user {u.name!r}. Token:")
    print()
    print(f"  {u.token}")
    if args.base_url:
        print()
        print(f"Bookmark URL:  {_bookmark_url(args.base_url, u.token)}")
    print()
    print(f"Wrote {path}")
    return 0


def _cmd_rotate(args: argparse.Namespace) -> int:
    path = resolve_tokens_path(args.tokens_file)
    store = load_store(path)
    try:
        u = store.rotate(args.user)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    save_store(store, path)
    print(f"Rotated token for {u.name!r}. New token:")
    print()
    print(f"  {u.token}")
    if args.base_url:
        print()
        print(f"Bookmark URL:  {_bookmark_url(args.base_url, u.token)}")
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    path = resolve_tokens_path(args.tokens_file)
    store = load_store(path)
    if not store.revoke(args.user):
        print(f"error: no such user {args.user!r}", file=sys.stderr)
        return 2
    save_store(store, path)
    print(f"Revoked {args.user!r}. Wrote {path}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    path = resolve_tokens_path(args.tokens_file)
    store = load_store(path)
    if len(store) == 0:
        print(f"(no users — {path})")
        return 0
    print(f"{len(store)} user(s) in {path}:")
    for u in store:
        _print_user_row(u, show_token=args.show_tokens)
    if not args.show_tokens:
        print()
        print("  (pass --show-tokens to reveal full bearer tokens)")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    path = resolve_tokens_path(args.tokens_file)
    store = load_store(path)
    u = store.by_name(args.user)
    if u is None:
        print(f"error: no such user {args.user!r}", file=sys.stderr)
        return 2
    _print_user_block(u)
    if args.base_url:
        print()
        print(f"Bookmark URL:  {_bookmark_url(args.base_url, u.token)}")
    return 0


def _cmd_url(args: argparse.Namespace) -> int:
    path = resolve_tokens_path(args.tokens_file)
    store = load_store(path)
    u = store.by_name(args.user)
    if u is None:
        print(f"error: no such user {args.user!r}", file=sys.stderr)
        return 2
    print(_bookmark_url(args.base_url, u.token))
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mojave-review-tokens",
        description="Manage the per-user token store for mojave-review.",
    )
    parser.add_argument(
        "--tokens-file",
        metavar="PATH",
        help="Path to tokens.yaml (default: $MOJAVE_REVIEW_TOKENS_FILE or "
             "~/.mojave_review/tokens.yaml).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Issue a fresh token for a new user.")
    p_add.add_argument("user", help="Username (a-z 0-9 . _ -, 1-32 chars).")
    p_add.add_argument("--note", help="Optional free-text note.")
    p_add.add_argument("--base-url",
                       help="If given, also print the bookmark URL "
                            "https://<host>/?token=<token>.")
    p_add.set_defaults(func=_cmd_add)

    p_rot = sub.add_parser("rotate",
                           help="Issue a new token for an existing user "
                                "(use after a leak or as a routine rotation).")
    p_rot.add_argument("user")
    p_rot.add_argument("--base-url",
                       help="If given, also print the bookmark URL "
                            "for the new token.")
    p_rot.set_defaults(func=_cmd_rotate)

    p_rev = sub.add_parser("revoke", help="Drop the user from the store.")
    p_rev.add_argument("user")
    p_rev.set_defaults(func=_cmd_revoke)

    p_lst = sub.add_parser("list", help="List users in the store.")
    p_lst.add_argument("--show-tokens", action="store_true",
                       help="Reveal full bearer tokens (default: redacted).")
    p_lst.set_defaults(func=_cmd_list)

    p_shw = sub.add_parser("show", help="Print one user's full record.")
    p_shw.add_argument("user")
    p_shw.add_argument("--base-url",
                       help="If given, also print the bookmark URL.")
    p_shw.set_defaults(func=_cmd_show)

    p_url = sub.add_parser("url",
                           help="Print just the bookmark URL for a user "
                                "(handy for piping into an email).")
    p_url.add_argument("user")
    p_url.add_argument("--base-url", required=True,
                       help="The deploy's https URL, e.g. "
                            "https://yourhost.edu/mojave-review/.")
    p_url.set_defaults(func=_cmd_url)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
