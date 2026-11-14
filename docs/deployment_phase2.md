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
| Reverse-proxy line | `https://<host>/mojave-review/` → `http://127.0.0.1:<port>` **(no trailing slash — preserve the prefix)**, HTTPS at the proxy. Launch the app with `--url-base-prefix /mojave-review/` so Dash serves under the same path. See "Path-prefix hosting" below. | One nginx/Apache stanza. |
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

## Path-prefix hosting (reverse proxy)

The app can be served either at a **root path** (a dedicated subdomain,
e.g. `https://mojave-review.denison.edu/`) or under a **path prefix**
(e.g. `https://www.denison.edu/mojave-review/`). Pick one:

- **Root path (simplest):** launch with no prefix; proxy the subdomain to
  `http://127.0.0.1:<port>`. Nothing else to configure — all in-app links
  are already root-relative.
- **Path prefix:** set `url_base_prefix` (config / `--url-base-prefix` /
  `MOJAVE_REVIEW_URL_BASE_PREFIX`) **and** tell the app whether the proxy
  strips it with `proxy_strips_prefix`. Both proxy styles work; what matters
  is that the app knows which one it is behind.

  Dash has two separate knobs and `create_app` picks the pair for you:

  | | `routes_pathname_prefix` (what the app serves) | `requests_pathname_prefix` (what the browser asks for) |
  |---|---|---|
  | `proxy_strips_prefix: false` | `/mojave-review/` | `/mojave-review/` |
  | `proxy_strips_prefix: true` | `/` | `/mojave-review/` |

  **Prefix preserved** — `proxy_pass` with **no** trailing slash:

  ```nginx
  location /mojave-review/ {
      proxy_pass http://127.0.0.1:8050;   # NO trailing slash → keep prefix
      proxy_set_header Host              $host;
      proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
  }
  ```
  → `proxy_strips_prefix: false` (the default).

  **Prefix stripped** — `proxy_pass` **with** a trailing slash
  (`proxy_pass http://127.0.0.1:8050/;`). The app then receives plain `/…`,
  so it must serve at the root while still building links with the prefix
  → `proxy_strips_prefix: true`.

  Getting it backwards is loud: routes under a prefix the proxy already
  removed makes every request 404, and a restart with the key flipped undoes
  it.

  **How to tell which you have** without reading the nginx config: open the
  live page source and find `requests_pathname_prefix` in the
  `_dash-config` blob. If it is `"/"` while the public URL has a prefix, the
  app is root-mounted — meaning the proxy strips, and the app has not been
  told. The token-bootstrap URL is `https://<host>/mojave-review/?token=…`
  either way.

  **This bit was wrong in production until 2026-11-14.** `wsgi.py` never
  passed `url_base_prefix` to `create_app`, so the deployed app ran
  root-mounted behind a stripping proxy. Pages and callbacks worked (every
  request was stripped on the way in), but two things were quietly broken:
  every `rel()` link — "📋 Assignment Dashboard", "⚖ XVIII Comparison", the
  dashboard's `?source=` deep-links, "← Back to review" — resolved to
  `https://<host>/dashboard` etc., **outside** the proxied location; and the
  post-`?token=` redirect sent reviewers to the site root. Login still
  appeared to work because the cookie was set (at `Path=/`) before that
  redirect, so a second click of the bookmark succeeded — which is probably
  why it went unreported.

The local launcher always talks to the bound port directly, so it opens
`http://127.0.0.1:<port>/mojave-review/` (or `…/` at root) for you.

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
  (won't move existing work). It also **removes unreviewed assignments on
  finalized sources** (never-started or drafted-but-not-submitted), so nobody
  spends time on a source whose Stage 3 is already applied; submitted records
  stay, and the preview lists every removal before you commit. An **"Only unassigned
  sources"** checkbox restricts it to sources nobody currently holds an
  outstanding (not-yet-submitted) assignment for — use it to hand out
  newly Stage-2-done sources without also topping up the second slot on
  partially-assigned ones. Existing assignments still count as load
  either way.
- **⚖ Top-up rebalance** — *move* PENDING assignments to even out load,
  so a reviewer added *after* seeding (the common case) gets a fair share
  instead of nothing. Minimal churn; submitted / in-progress work never
  moves. A **"Consider completed reviews"** checkbox balances
  `completed + pending` instead of pending-only, so reviewers who did a
  lot of pre-system work get a lighter first-round share (run it once
  with the box checked for the first round; leave it off afterward).
- **🏖 Redistribute (break)** — spread one reviewer's PENDING queue across
  the rest of the pool by load (not all-onto-one), optionally pausing
  them, for when someone steps away.
- **↪ Reassign queue** — bulk-move one reviewer's whole queue to a single
  other reviewer (someone explicitly takes over).
- **↔ Move a source** — reassign one source, for fine-tuning.
- **📅 Set target dates** / **✓ Credit my Stage-2 reviews** — as above.

Because each write snapshots the prior file into `_admin/backups/`, a
rebalance you don't like is one file-restore away from undone.

The admin dashboard also shows a **Reviewer summary** card: one
expandable row per reviewer with their current-queue breakdown
(pending / in-progress / submitted, plus pending-load) and their
**lifetime completed** reviews — including finalized sources whose
submissions live in `considered/` / `applied/` — with the finalized
count broken out. A `✎N` marker flags reviewers drafting sources not
assigned to them.

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

## Updating the deployed app (code + dependencies)

The `Results/` rsync and the **code** are two different things. The sync
described in Step 4 carries `Results/` only (`server_sync/server_update_exclude.txt`
is a filter list relative to `Results/`); it never touches the checkout or the
venv. So a code update on the host is its own step:

```bash
cd /var/www/html/mojave.denison.edu/Clustering_Review   # the checkout
git pull
cd mojave_review && uv pip install -e .                 # see note on uv below
sudo systemctl restart mojave                           # unit name on webby
```

(The plan above describes a `systemd --user` unit; webby ended up with a
system-level unit called `mojave`, hence `sudo systemctl restart mojave`. The
earlier sections have not been reconciled with that.)

**The venv on webby was created by `uv venv`, so it has NO `pip` in it** —
`.venv/bin/` holds `python`, the console scripts and `gunicorn`, and that is
all. Use `uv pip …`, which manages a pip-less venv perfectly well; run it from
`mojave_review/` so it auto-detects the adjacent `.venv`, or pass
`--python .venv/bin/python`. `uv` is installed under the deploying user
(`~/.local/bin` or `~/.cargo/bin`), so `sudo` will not find it — do the install
unprivileged and only the `systemctl` step with `sudo`. `python -m ensurepip`
would add pip, but there is no reason to.

To change ONE package rather than re-resolve everything — the right blast radius
for a security patch on a live host — skip the editable reinstall:

```bash
cd /var/www/html/mojave.denison.edu/Clustering_Review/mojave_review
uv pip install 'brotli>=1.2.1'
sudo systemctl restart mojave
.venv/bin/python -c "import importlib.metadata as m; print(m.version('brotli'))"
```

Restart even when the app no longer *calls* the upgraded library: an already
imported module stays resident in the gunicorn workers until they recycle.

### A dependency change is inert until the venv is reinstalled

This is the easy one to miss, and it has a security edge:

- **pip and uv treat an unversioned requirement as satisfied by whatever is
  already installed.** `flask-compress` requires bare `brotli`, so an installed
  brotli 1.2.0 satisfies it forever — `uv pip install -e .` will not upgrade it,
  and nor will the `-U` form unless the spec itself changed.
- **uv's cache does not undermine the floor.** `~/.cache/uv/archive-v0/...`
  keeps unpacked wheels, so a vulnerable version sits there after the venv is
  clean — but a cached 1.2.0 does not satisfy `brotli>=1.2.1`, so it is not
  reused. The cache is a scan finding, not a reinfection route.
- **Therefore: when a package is reported vulnerable, add a floor to
  `pyproject.toml` rather than patching the venv by hand.** Hand-patching works
  until the next reinstall and then silently drifts back. A floor
  (`brotli>=1.2.1`) makes the installed 1.2.0 *unsatisfying*, so the ordinary
  reinstall above fixes it and keeps fixing it.
- **And the floor does nothing until that reinstall runs on the host.** Landing
  it in git is half the job.

### When IT reports a vulnerable package

Worked example: brotli 1.2.0 / CVE-2026-111497, reported 2026-11-05. The
sequence that turned out to matter:

1. **Establish how it got there** — direct dependency, *hard* transitive one, or
   an extra nobody requested? `importlib.metadata` answers it for the whole env:

   ```bash
   python3 -c "
   import importlib.metadata as md
   for d in md.distributions():
       hits = [r for r in (d.requires or []) if r.lower().startswith('brotli')]
       if hits: print(d.metadata['Name'], d.version, hits)"
   ```

   Brotli came back as a hard, non-optional dependency of Flask-Compress on
   CPython — so "just remove it" was not available. Everything else that
   mentioned it (urllib3, httpx, aiohttp, fonttools) listed it only under an
   extra we don't install, which is worth saying out loud in the reply: those
   copies are not in play.
2. **Establish whether the app actually exercises it.** Do not guess from the
   library being importable — check the behaviour. For a response-compression
   library, the response headers are the ground truth:

   ```bash
   curl -s -o /dev/null -D - -H "Accept-Encoding: br" http://127.0.0.1:8050/ \
       | grep -i content-encoding
   ```

   It did: flask-compress 1.24 defaults to preferring `br` over gzip, which no
   one had noticed. That answered IT's open question ("unclear if the site even
   uses Brotli") with a fact instead of a shrug.
3. **Distinguish compressing our own output from decompressing someone
   else's input** — very different risk profiles, and IT will want to know
   which. We compress our own response bodies; the only inbound-decompression
   path is urllib3/requests decoding a `br` response while fetching FITS from
   `cv.nrao.edu`.
4. **Then fix it in both directions**: floor the version in `pyproject.toml`
   *and*, where the library is optional at runtime, stop invoking it (here:
   dropping `br` from `COMPRESS_ALGORITHM` / `COMPRESS_ALGORITHM_STREAMING` in
   `app.py`, which keeps zstd and gzip so nothing gets slower).

### Housekeeping that shows up in scans

- **Abandoned venvs are scanned forever.** A `venv_save/` left behind from an
  earlier Python keeps reporting whatever it contains. Delete it rather than
  patch it.
- **The uv cache is scanned too.** `~/.cache/uv/archive-v0/...` holds unpacked
  wheels, so a vulnerable version lingers there after the venv is clean. `uv
  cache prune` drops unused entries and `uv cache clean <package>` targets one
  — confirm the spelling with `uv cache --help` on the host, since uv is not
  installed on the dev laptop and these were not exercised there.
- **The laptop dev environment does not feed the host** — the only channel is
  `Results/`, and the venv is built on the host from `pyproject.toml`. So a
  stale package on a developer machine cannot re-introduce itself to the server
  on its own; the thing that can is an un-floored dependency in git.

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
