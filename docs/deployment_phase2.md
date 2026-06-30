# Phase 2 deployment plan — university web server

Reference for hosting `mojave-review` on a university web server with
**per-user static tokens** for authorization. ~5-6 expected reviewers.

This document is structured so you can lift sections directly into an
email to IT, a wiki page, or a project log. The implementation work is
not yet done — this is the plan to revisit when Phase 2 deployment is
ready to begin.

## High-level architecture

```
   Reviewer's laptop
   ┌─────────────────────────┐
   │   browser, opens         │
   │   bookmarked URL:       │
   │   https://host/mojave-  │
   │   review/?token=...     │
   └────────────┬────────────┘
                │ TLS
                ▼
   University web frontend (nginx/Apache)
   ┌─────────────────────────────────────────┐
   │ - TLS termination                       │
   │ - Reverse-proxy /mojave-review/ ──┐     │
   └───────────────────────────────────┼─────┘
                                       │ http://127.0.0.1:<port>
                                       ▼
   App server process (mojave-review)
   ┌─────────────────────────────────────────┐
   │ Python + gunicorn + Dash                │
   │ - Token middleware (every request):     │
   │   1. cookie present + valid → set       │
   │      g.reviewer = username, continue    │
   │   2. ?token=... in URL → validate, set  │
   │      cookie, set g.reviewer             │
   │   3. neither → 403 "ask <you> for URL"  │
   │ - Reads:  /data/Results/                │
   │ - Writes: /data/recommendations/        │
   │ - Caches: /data/fits_cache/             │
   └────────────────┬────────────────────────┘
                    │
                    └── outbound HTTPS → www.cv.nrao.edu (FITS only)
```

No outbound to Google. No OAuth project. The reverse proxy doesn't even
need to forward `X-Forwarded-For` — auth is purely in the URL/cookie.

## What IT needs to provision

| Item | Requirement | Why |
|---|---|---|
| Python ≥ 3.10 | In user space (`pyenv` / `uv`); no sudo install needed | App runs in a venv. |
| Long-lived process on a local TCP port | Bound to `127.0.0.1`, port assigned to me | The Dash app under `gunicorn`. |
| Reverse-proxy line | `https://<host>/mojave-review/` → `http://127.0.0.1:<port>/` with HTTPS at the proxy. WebSocket upgrade headers helpful but not required. | One nginx/Apache stanza. |
| Process manager | `systemd --user` service or supervisord, restart-on-failure | Auto-start, auto-recovery. |
| Writable persistent dir | ~50 GB; nightly backup of just the `recommendations/` subdir | Cache + recommendations. Cache regenerable; recommendations not. |
| Outbound HTTPS allowed | `www.cv.nrao.edu` (FITS only) | Live FITS fetch. No Google endpoints required. |
| TLS cert | Issued by their existing pipeline | Standard for any HTTPS endpoint. |
| (Optional) Allowlist incoming source IP range | E.g. university subnet only, as a belt-and-braces layer in front of the token | Reduces brute-force exposure. |

**Things the OAuth-based plan needed that this one doesn't:**

- ❌ Register an OAuth callback URL
- ❌ Allow outbound HTTPS to `accounts.google.com` / `oauth2.googleapis.com`
- ❌ Forward `X-Forwarded-For` headers

## Code work to do at Phase-2 time

In order, smallest-first:

1. **`mojave-review-tokens` CLI** (~80 LOC): subcommands `add <user>`,
   `revoke <user>`, `list`, `show <user>`. Writes to `tokens.yaml` in
   the config dir. Tokens are 24-byte URL-safe random strings prefixed
   with the username for human readability (`alice-aZbXc7Yw3qP8...`).
   One row per user; revoke = delete row.
2. **Token middleware** (~50 LOC): Flask `before_request` hook that
   resolves a request to a `reviewer` value (from cookie or
   `?token=…`), sets `flask.g.reviewer`, sets the cookie on first
   valid token-in-URL hit, returns a tiny "Access denied — your
   token isn't recognized. Email <you> to get a new one." HTML page
   on failure. Cookie is `HttpOnly`, `Secure`, `SameSite=Lax`, 30-day
   rolling expiration.
3. **Replace `--reviewer` flag** with the resolved `g.reviewer`
   everywhere (same code path already exists; just changes the
   source). Per-user recommendation file naming is unchanged.
4. **Config file watcher**: reload `tokens.yaml` on disk change so
   adding/revoking users doesn't require an app restart.
5. **Configurable paths** via env vars or a `config.yaml` (so IT can
   adjust paths without touching code).
6. **`logging` instead of `print()`** with a rotating file handler in
   `/data/logs/`.
7. **`gunicorn` worker config** — one or two synchronous workers; FITS
   fetches stay in a thread pool.
8. **Nightly Drive→server sync** of `Results/` via either a Drive
   service account or a manual rsync from your machine.

Estimated effort: **3–5 days of focused dev** (vs. 1–2 weeks for OAuth).

## On-disk layout

```
/etc/mojave-review/
├── tokens.yaml              ← which usernames map to which tokens
└── config.yaml              ← paths, port, cookie TTL

/data/
├── Results/                 ← synced from Drive
├── recommendations/         ← per-user JSON output (backed up nightly)
│   └── _admin/
│       ├── assignments.json ← roster + assignments + target dates + credits
│       └── backups/         ← rotating snapshots of assignments.json (last 10)
├── fits_cache/              ← grows over time; regeneratable
└── logs/
```

`recommendations/_admin/assignments.json` is part of the
`recommendations/` tree, so it is covered by the nightly backup **and**
by any sync you run on that directory — which is what makes the
laptop→server roster/assignment workflow below work. Every write also
drops a timestamped copy into `_admin/backups/` (last 10 kept), so a
mis-applied rebalance is recoverable by restoring one of those files;
they sync with the rest of the tree.

### `tokens.yaml` example

~5 lines per user:

```yaml
users:
  - name: alice
    token: alice-aZbXc7Yw3qP8mNkR0vL9fEgT
    note: "primary reviewer; joined 2026-06-01"
  - name: bob
    token: bob-T9hQ2rJpX4cKvB8nLm0sFwYz
  - name: chris
    token: chris-ePvD8gWa6oRcZj1tHy7uMxNb
```

## Managing the team & assignments from your laptop

The deployed server holds `tokens.yaml`; your local machine does not. So
the dashboard builds its reviewer roster from the **union** of three
signals (`dashboard.known_reviewers`):

1. `tokens.yaml` `name:` fields (only present on the server),
2. anyone who has submitted a review on disk (auto-discovered, syncs in
   with `recommendations/`),
3. a **manually-curated roster** — the `team_members` list in
   `recommendations/_admin/assignments.json` (schema v4), editable from
   the dashboard's **👥 Manage team** modal (Add member / Remove).

This lets you do all team and assignment management from a local
staging copy and push it up with your usual `recommendations/` sync —
no need to edit anything directly on the server:

1. Run the app locally against your synced `recommendations/`
   (`mojave-review --results-dir ./Results --recommendations-dir
   ./recommendations --reviewer <you> --admin`).
2. In **👥 Manage team**, add teammates who haven't submitted yet, then
   use the assignment tools (below) to lay out the work. All of this
   writes only `recommendations/_admin/assignments.json`.
3. Sync `recommendations/` to the server. The deployed app reads the
   same file and every reviewer sees their queue.

**Assignment tools (all preview-then-apply, all move only `assignments`):**

- **🔀 Auto-balance** — fill open review slots across the active pool
  (additions only; won't move existing work).
- **⚖ Top-up rebalance** — *move* PENDING assignments to even out load,
  so a reviewer added *after* seeding (the common case) gets a fair share
  instead of nothing. Minimal churn; submitted / in-progress work never
  moves.
- **🏖 Redistribute (break)** — spread one reviewer's PENDING queue across
  the rest of the pool by load (not all-onto-one), optionally pausing
  them, for when someone steps away.
- **↪ Reassign queue** — bulk-move one reviewer's whole queue to a single
  other reviewer (someone explicitly takes over).
- **↔ Move a source** — reassign one source, for fine-tuning.
- **📅 Set target dates** / **✓ Credit my Stage-2 reviews** — as above.

Because each write snapshots the prior file into `_admin/backups/`, a
rebalance you don't like is one file-restore away from undone.

**Name-matching is the one rule that matters.** A reviewer's queue is
looked up by name, and on the server that name comes from their token
(`tokens.yaml` `name:`). So a name you add in **Manage team** must match
the corresponding `tokens.yaml` `name:` **exactly**, or the assignment
won't resolve to that reviewer's logged-in identity. Decide the
canonical names once (e.g. `alice`, `bob`) and use them in both places.
The Manage-team modal repeats this caveat inline.

## Reviewer's experience

1. **First time**: you email them
   `https://yourhost.edu/mojave-review/?token=alice-aZbXc7Yw3qP8mNkR0vL9fEgT`
   once. They click. App validates the token, sets a cookie, redirects
   them to the clean URL (`/mojave-review/`). They bookmark it.
2. **Every subsequent visit**: cookie is present, app validates it,
   they're in.
3. **New device or new browser**: they re-visit the bookmarked-with-
   token URL once, cookie set, done.
4. **Cookie expires after 30 days of inactivity**: they re-visit the
   bookmark with token, fresh cookie.
5. **They lose the URL**: email you, you send it again
   (`mojave-review-tokens show alice`).
6. **You suspect a leak**: `mojave-review-tokens revoke alice;
   mojave-review-tokens add alice`, email them the new URL.

The token in the URL is the only delivery channel — reviewers never see
a password, never sign in, never type anything.

## Laptop-staging deployment (Path A)

Before the university-host bring-up below, the app is staged on a Linux
Mint laptop on the LAN. To keep that step small, the laptop deploy:

- runs gunicorn directly on `0.0.0.0:8050` (no nginx, no TLS),
- mounts the app at the root path (no `url_base_pathname`),
- sets `cookie_secure: false` so browsers actually send the auth
  cookie over plain HTTP.

Reviewers hit `http://<laptop-ip>:8050/?token=...`. Everything else
(tokens, recommendations, config schema, logging, audit trail) is
identical to the prod plan below.

Concrete artifacts + bring-up steps for the laptop live in
[`../deploy/README.md`](../deploy/README.md).

The migration from laptop to university host is a single coherent
change: add nginx + TLS + `/mojave-review/` URL prefix +
`cookie_secure: true` + a `--bind 127.0.0.1:8050` flip in the systemd
unit, all at once. The artifacts under `deploy/` are written so each
of those flips is a one- or two-line edit.

## Bring-up sequence

Each step is independently verifiable; nothing external to Google in
the loop:

1. **Step 0 — Talk to IT.** Confirm the provisioning above. The asks
   are minimal (port + proxy + disk + outbound FITS).
2. **Step 1 — Stage the box.** Python venv installed, persistent dir
   mounted, systemd `--user` service in place. App runs without a
   tokens file, refuses everyone with 403, only reachable from
   `localhost`. Verify by SSH tunnel.
3. **Step 2 — Wire the reverse proxy.** Public URL works. Still 403
   for everyone (no tokens yet).
4. **Step 3 — Generate your own token.** `mojave-review-tokens add
   homand`, visit your bookmarked URL, confirm login works. Click
   around to verify the existing app works end-to-end on the server.
5. **Step 4 — Sync `Results/`.** Manual rsync from a sender machine
   holding the Drive desktop mirror, scheduled by a systemd `--user`
   timer. Concrete artifacts (script, config, service + timer units)
   are under [`../deploy/`](../deploy/) — see "On the SENDER
   machine — Results/ sync" in [`../deploy/README.md`](../deploy/README.md).
   A Drive service account is the alternative if you ever want to
   skip the sender hop, at the cost of an outbound HTTPS allowance
   to Google APIs.
6. **Step 5 — Issue tokens to the rest of the group.** Five `add`
   calls, five emails with bookmarked URLs. Done.

## Talking points for the IT conversation

Lift these directly into an email:

- "I want to run a Python app under my user account on `<host>`. Can
  you confirm I can run a long-lived process bound to a local port?"
- "Can you add a reverse-proxy stanza mapping
  `https://<host>/mojave-review/` to that local port, with HTTPS
  terminated at the proxy?"
- "Can I have a writable persistent directory (~50 GB to start)? Can
  the `<path>/recommendations/` subdir be on the nightly backup
  rotation?"
- "Can outbound HTTPS to `www.cv.nrao.edu` be allowed from this
  server?" (That's the only outbound destination needed.)
- "Is there a hostname pattern I should request for this, or do you
  assign it?"
- "How are TLS certs handled — do you provision them automatically or
  do I request one per host?"
- (Optional) "Can you restrict incoming connections to our university
  IP range as a defense-in-depth layer? The application also enforces
  per-user authorization, so this isn't required, but if it's easy on
  your side I'd take it."

## Risks of this scheme

So we're not caught off-guard:

- **Bookmark / URL leakage**: if alice forwards her bookmarked URL to
  bob, bob can log in as alice. Mitigation: tell reviewers not to
  share, rotate any specific token you suspect, audit `logs/access.log`
  if it ever matters.
- **Cookie theft**: standard web hygiene — `Secure`/`HttpOnly` cookies
  + HTTPS proxy prevents the usual sniff/XSS attacks. Low risk for a
  small trusted group on TLS-only access.
- **No verified identity**: if alice gives bob her URL, the app will
  record bob's edits as alice's recommendations. Same caveat as any
  token-based auth. With ~6 reviewers you trust, this is fine.

## Why this over alternatives

| Option | User setup | What you maintain | Friction | Identity quality |
|---|---|---|---|---|
| IP allowlist | Tell you their IP | YAML mapping `IP → username`, updated when IPs change | **Ongoing — IPs drift** | Weak (IP ≠ identity) |
| **Per-user static token** (chosen) | Bookmark the URL you mail them once | YAML mapping `token → username`, updated only when adding/removing users | One-time | Solid (token = secret = identity) |
| Google OAuth + email allowlist | One-time Google sign-in | YAML list of approved emails | One-time (initial sign-in) | Strongest |

The IP-based scheme was rejected because of operational churn (home
internet IPs change, office NATs share IPs, travel/hotspot scenarios
break access). The token scheme is the same low-friction-for-users as
IP-based without the maintenance burden.

OAuth would give the strongest identity guarantee but requires a Google
Cloud project, OAuth callback registration, and an additional outbound
HTTPS path. For ~6 trusted reviewers it's overkill.

## See also

- [`product_decisions.md`](product_decisions.md) — the rest of the
  project's locked-in decisions.
- [`../CLAUDE.md`](../CLAUDE.md) — architecture reference for the
  code that this Phase-2 plan will wrap.
