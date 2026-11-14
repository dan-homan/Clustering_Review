# MOJAVE Cluster Review — Changelog

Changes are grouped by feature area, newest first.

---

## 2026-11-15 — Fix: the new sync guard swallowed ssh's password prompt

Reported within minutes of shipping the project-aware sync: the script printed
its header and then appeared to hang.

The remote-project-root guard ran `ssh … "test -d …" 2>/dev/null`. ssh writes
its password and key-passphrase prompts to **stderr**, so suppressing stderr
made an authentication prompt invisible — the script was not hung, it was
waiting for input nobody could see.

- stderr is no longer redirected on that probe. Noise is the lesser evil.
- The failure paths are now distinguished, since "can't reach the host" and
  "that project isn't there" want different fixes: a second `ssh … true`
  decides which, and each gets its own message. The path error also names the
  app's `mounted project` startup log as the authority for the project name.
- `REMOTE`, `REMOTE_DATA` and `SSH` became overridable
  (`${VAR:-default}`), so an alternate host or a test harness needs no edit.
  Verified all three guard outcomes against a fake-ssh harness: root present
  proceeds, root missing explains the path, host unreachable surfaces ssh's own
  message *and* the `ssh-add` hint.
- Also noted in the script: on webby `/home/homand/mojave-review` is a symlink
  into the `/var/www/html/` vhost tree, so the short path is the stable way to
  name the data root.

---

## 2026-11-15 — `server_sync/sync_server.sh` is project-aware

The admin's actual by-hand sync — three legs, including the two-way Unison
merge of `recommendations/` — still assumed the old flat server layout. (The
earlier entry today documented `deploy/sync-results.sh`, which pushes
`Results/` only and is *not* the admin workflow.)

- **`PROJECT` is now the first argument and is mandatory**:
  `sync_server.sh MOJAVE run`. No default, deliberately — the old layout would
  otherwise be assumed silently, stranding data in a tree the app never reads
  and pointing Unison at a root its archive no longer matches. The
  workstation and server names need not agree
  (`MOJAVE_1996.00-2026.00` → `MOJAVE`); the app's `mounted project` startup
  log is the authority.
- `REMOTE_BASE` is derived as `$REMOTE_DATA/$PROJECT`, and the script
  **refuses when the remote project root doesn't exist** rather than letting
  rsync create `…/data/<typo>/Results` and report success.
- **One Unison profile per project.** Unison keys its archive on the root
  pair, so one profile reused across projects would thrash a single archive
  between two remote roots — each switch reading as "everything was deleted
  over there". New `server_sync/mojave-recs-webby-PROJECT.prf` template; the
  script derives the profile name from `PROJECT`.
- `sync_server_omenHP.sh` given the same treatment, so the two don't drift —
  they now differ only in host, ssh args and profile prefix.
- **The hazard documented in both READMEs.** The server's `recommendations/`
  just moved. With the new root in the profile, Unison reconciles from scratch
  (safe). With the *old* root, the path is gone — Unison either errors, or if
  something recreated an empty directory there, reads its archive as "remote
  deleted everything" and proposes deleting the **workstation** copy.
  So: install the new profiles, `preview` and read the plan, then `run` — and
  **don't use `auto` until an interactive run has succeeded**, since it
  answers those prompts for you.
- `docs/syncing_results.md` now opens by distinguishing the two tools, and
  carries a by-hand admin section; `server_sync/README.md` gained
  Multi-project, the first-run hazard, and support-file notes.
- New gotcha recorded: **profile drift is silent.** The active
  `~/.unison/*.prf` and the repo reference can disagree indefinitely, since
  Unison only reads the active one. `ignore = Path _admin` matters especially
  — leg 3 pushes `_admin/` one-way with `--delete`, so if Unison also syncs it
  two-way the legs fight. A `diff` recipe is in the README.

---

## 2026-11-15 — Sync safety + docs for installing from GitHub

Prompted by setting up the second project's sync. Two latent ways to lose data
or waste an afternoon, plus the documentation a stranger needs.

### `rsync --delete` could have destroyed `recommendations/`

`sync-results.sh` mirrors with `--delete`, so everything in `TARGET_DIR` that
isn't in `SOURCE_DIR` is removed. Point `TARGET_DIR` one level short — at the
project root instead of its `Results/` — and it deletes `recommendations/` and
`notes/`, the only irreplaceable data on the server. **`MAX_DELETE` does not
help**: that mistake removes about six entries, far under the default cap of
1000. Confirmed with a dry-run against a mock tree:

```
*deleting recommendations/0003-066u/mll.json
*deleting recommendations/
*deleting notes/
```

Multi-project made the slip likelier, since `TARGET_DIR` gained a path segment
(`data/MOJAVE/Results`, not `data/Results`). The script now **refuses** a
target containing `recommendations`, `notes`, `config`, `tokens.yaml`,
`fits_cache` or `logs`, with `ALLOW_UNSAFE_TARGET=1` as the override. Verified
across four cases: project root refused, correct `Results/` passes,
non-existent target (first run) passes, override honoured.

### The sync script couldn't run from a macOS sender

`--info=stats2,progress2` arrived in rsync 3.1; macOS ships openrsync ("rsync
version 2.6.9 compatible"), which aborts with a usage dump **before
transferring anything**. Since the Drive/Dropbox mirror often lives on a Mac,
that made the sender unusable there. The flag is now version-probed, falling
back to `--stats --verbose`. Verified both branches against the local
openrsync.

### Per-project sync

- `deploy/mojave-results-sync@.service` / `@.timer` — templated units, so one
  file serves every project: `mojave-results-sync@bk224.timer` reads
  `~/.config/mojave-results-sync-bk224.conf`. Adding a project is a config
  file plus one `enable`. The non-templated pair still works for one project.
- `deploy/mojave-results-sync.conf.example` rewritten around one-file-per-
  project, with the `TARGET_DIR` rule boxed where it can't be skimmed past.
- No "sync all projects" mode, deliberately: a single `--delete` mirror
  spanning project roots *is* the mistake above.

### Documentation

- **`docs/syncing_results.md`** (new) — the two rules, sender setup,
  per-project config, scheduling, adding a project, a table of what is and
  isn't synced, the safety rails and what each does *not* cover, and
  troubleshooting keyed to actual error text.
- **`deploy/config.yaml.example`** rewritten for multi-project: the two modes
  marked mutually exclusive, every key annotated, and the reverse-proxy keys
  flagged as the ones that break the Denison deployment if set. Verified by
  booting the WSGI app from the shipped file with only paths rewritten.
- **`README.md`** — install now works for a fresh clone: a `uv` path for hosts
  without a modern system Python, a "check it works" step, a table of the two
  optional supporting data files and what happens without them, a
  several-projects section, the new flags, and a table of every doc under
  `docs/`.

---

## 2026-11-14 — Fix: one unusable project no longer takes down the whole service

Found immediately on the first real multi-project deploy. `data/BK224` had not
been synced to the server yet, and `resolve_projects` raising at WSGI import
killed gunicorn's workers — so the proxy returned "service not available" and
**MOJAVE's reviewers lost access too**, over a project they don't use.

Aborting is the wrong trade-off under gunicorn: the usual cause is operational
rather than a mistake (an rsync that hasn't run, a directory moved
mid-migration), and the blast radius was every working project.

- New `projects.resolve_projects_tolerant`, used by `wsgi.py`: a project that
  can't be used is **skipped and logged at ERROR**, and the rest are served.
  A name/root clash is likewise downgraded from fatal to first-wins-and-report.
- It still **fails closed when nothing is usable**, since a dispatcher with no
  projects can't serve anything either way. Declaring no projects at all
  remains plain single-project mode, not an error.
- **The CLI keeps the strict `resolve_projects`** — there the operator is
  reading the error as it happens, and quietly serving less than they asked
  for would be worse than refusing.
- The resulting journal is explicit about both halves:

  ```
  ERROR mojave_review.wsgi     | project skipped: Project 'bk224': no such directory: …/data/BK224
  INFO  mojave_review.wsgi     | WSGI multi-project: 1 projects (default mojave) under /
  INFO  mojave_review.projects | mounted project mojave at /mojave/  (root=…, label='MOJAVE 1994-2026')
  ```

  The skipped project is simply absent from the switcher.
- 7 tests added to `tests/test_projects.py` (405 total), covering a missing
  root, a root without `Results/`, a malformed entry, both duplicate kinds,
  the nothing-usable failure, and that strict resolution is unchanged.

---

## 2026-11-14 — Multi-project step 6: per-project log context

With several projects in one process, `mojave_review.auth.middleware | session
established` doesn't say *which* project. Now it does:

```
2026-11-14T14:55:06 INFO  mojave_review.projects | mounted project mojave at /mojave/  (root=…)
2026-11-14T14:55:06 INFO  mojave_review.projects | mounted project bk224 at /bk224/  (root=…)
2026-11-14T14:55:06 INFO  [mojave] mojave_review.auth.middleware | session established …
2026-11-14T14:55:06 INFO  [bk224]  mojave_review.auth.middleware | session established …
```

- `_logging._ProjectContextFilter` resolves the project **per record** from the
  Flask app in context — the same mechanism `ui.urls.rel` uses — because
  `configure_logging` runs once before any app exists, so it can't be baked
  into the formatter. Being a filter means **no call site changed**: every
  existing `get_logger(__name__)` call in the package gets the tag for free.
- Attached to the **handlers**, not the logger: a logger's filters only see
  records logged directly through it, and child loggers propagate up to the
  package root's handlers without passing its filters.
- **Single-project output is byte-identical** — the tag is empty, not `[]` or a
  stray space. `create_app` stashes the project name only when given one.
- Startup happens outside any request, so `build_dispatcher` logs the mount
  table explicitly: the one place the name → path → root mapping is stated,
  which is what you want when someone reports the wrong data under a URL.
- The formatter carries `defaults={"project_tag": ""}`, so a record reaching a
  handler whose filter was skipped formats cleanly instead of raising a
  KeyError and being dropped. This also removed an ordering dependence found
  while probing the tests: the filter *mutates* the shared record, so
  whichever handler ran first was silently supplying the attribute to the
  rest — which made the tests unable to detect a missing filter.
- 8 tests in `tests/test_log_project_context.py` (398 total).

---

## 2026-11-14 — Multi-project: browser-verified, and the memory question settled

Closes the last open items on the step-3 checklist.

- **Verified in a real browser** (both projects mounted): clicking between
  projects works, and in BK224's Position view drag-zoom holds equal aspect,
  the resizable divider works and ←/→ epoch stepping behaves — exercising
  `equal_aspect.js`, `subplot_resize.js`, `xy_epoch.js` and `keyboard.js`
  under a path prefix. Consistent with inspection: all five clientside scripts
  are purely DOM-based (element IDs, `.js-plotly-plot`, `Plotly.*` on drawn
  figures) with no `fetch`, no `XMLHttpRequest`, no `window.location` and no
  constructed URLs, so a path prefix has nothing to break.
- **Memory measured, and it settles one-process-vs-process-per-project
  decisively.** Peak RSS after building the apps and serving each a request:
  MOJAVE alone 175.2 MB, BK224 alone 172.2 MB, **both together 175.8 MB**. The
  libraries (dash, pandas, scipy, astropy, plotly) dominate at ~160 MB, so a
  second project costs about **0.6 MB** — an app instance is a layout plus a
  callback registry, and the layout is rebuilt per request anyway. A process
  per project would have cost ~172 MB each: ~347 MB for these two against
  ~176 MB for one process, and ~172 MB for every project after that.
- Still not separately exercised: the heavier edit paths (selection → edits →
  recommendations autosave), where most of the `allow_duplicate` outputs live.
  The callback graph initialising and the plots responding is good evidence
  those keys resolve per app, since a mismatch kills the whole graph for a
  mode rather than failing quietly.

---

## 2026-11-14 — Multi-project step 5: header switcher + one shared login

- **Project switcher in the header.** `ui.layout.build_project_switcher`
  renders a native `<details>`/`<summary>` disclosure listing every project as
  a link to its mount, the current one marked `●` and named in the summary.
  **Nothing is rendered below two projects**, so single-project mode looks
  exactly as it always has.
- Deliberately plain links rather than a `dcc.Dropdown`: switching becomes an
  ordinary navigation — no callback, no clientside JS — and each project keeps
  its own URL, so Back and per-project bookmarks work. HTML has no "dropdown
  of links"; `<details>` is the closest scriptless equivalent, and the admin
  Window-N panel already uses one.
- **One login covers every project.** New
  `install_token_middleware(cookie_scope_prefix=)`, set by `build_dispatcher`
  to the **mount root** instead of each project's own prefix, so the cookie is
  `("mr_token", "/")` and a single `?token=…` works everywhere. This closes the
  question left open by step 4: with projects at root level the common mount
  root *is* `/`, so cookie path cannot distinguish them — and should not,
  because the chosen design is one shared token per reviewer. Scoping per
  project would have met a reviewer clicking into their second project with a
  403 despite holding a valid token.
- Verified over HTTP with one cookie jar against both production projects:
  login at `/mojave/`, then `/bk224/` and back to `/mojave/`, all
  authenticated; anonymous still 403 on both; and each project's page renders
  the full list with itself marked.
- 14 tests in `tests/test_project_switcher.py` (390 total), confirmed to fail
  when the cookie is scoped per project (4 failures), the switcher appears for
  a single project (1), the current project is unmarked (2), or every app is
  told the same `current_project` (1).
- **Labels default to the directory basename**, so MOJAVE shows as
  `MOJAVE_1996.00-2026.00`. Use `label:` in the config `projects:` block, or
  name a symlink, for something shorter.

---

## 2026-11-14 — Multi-project step 4: project registry + dispatcher

Several review projects served from one process, each with its own `Results/`,
`recommendations/` and `notes/`. New `src/mojave_review/projects.py`.

- **A project is one directory** holding `Results/` (and conventionally
  `recommendations/`). Both production projects already have that shape, so
  nothing had to move. `source_run_param.csv` / `astrometry_mojave.dat` are
  found by the per-project discovery added in step 1, because the project root
  *is* `results_dir.parent`.
- **Three ways to declare them**, composable: `--project <name>=<path>`
  (repeatable), `--projects-dir <dir>` (every subdirectory with a `Results/`),
  and a `projects:` block in `config.yaml` taking either the string or a
  `{name, label, root}` mapping. The CLI flattens its declarations into the
  config layer, so the usual CLI > env > yaml precedence applies.
- **Names become URL segments**, so they're validated as such, and refused
  when they'd shadow a path the app serves (`dashboard`, `compare`, `assets`,
  `static`). Derived from the basename when not given —
  `MOJAVE_1996.00-2026.00` → `mojave`. A symlink's own name wins over its
  target's, which makes a `--projects-dir` of symlinks a sane way to gather
  projects that live in different trees.
- **Mounting**: `/mojave/`, `/bk224/`, and **every** unmatched path redirects
  into the default (first) project with path and query preserved — `/` →
  `/mojave/`, `/dashboard` → `/mojave/dashboard`, `/?token=…` →
  `/mojave/?token=…`. Root-level bookmarks from before projects existed keep
  working, which matters because the Denison proxy passes them through
  verbatim.
- **Single-project mode is byte-identical.** Declaring no projects skips the
  dispatcher entirely and mounts one app at the root; verified by running
  `mojave-review --results-dir Results` from a project directory and checking
  the banner and behaviour are unchanged. `results_dir` is no longer required
  to exist when `projects` is set, since the built-in default `Results` won't
  exist beside a multi-project config.
- **Loud failures, not silent ones:** a missing directory, a directory with no
  `Results/` (including the easy mistake of pointing at `Results/` itself), a
  reserved or URL-unsafe name, the same name for two roots, or the same root
  under two names all raise at startup. `--source-params` /
  `--astrometry-file` are refused alongside `--project`, since they name one
  file and would apply one project's table to all of them.
- Verified end-to-end through the real CLI with both production projects:
  484 vs 26 sources, in-app links confined to their own project,
  `_dash-layout` returning JSON on both, and the root redirect carrying a
  token.
- 47 tests in `tests/test_projects.py`, confirmed to fail when the
  reserved-name check, the `Results/` requirement, the path-preserving
  fallback, or duplicate detection is removed.
- **Not yet done:** the header project switcher (step 5), and the shared-token
  cookie scoping it needs — each app currently gets its own prefix-derived
  cookie, so a reviewer would present their token once per project.

---

## 2026-11-14 — Multi-project step 3: dispatcher mounting proved, `rel()` fixed

Step 3 of [`docs/multi_project.md`](docs/multi_project.md). The prefix
mechanism itself shipped earlier today; this is the de-risking that was still
outstanding, run against **both real projects** (MOJAVE and BK224) mounted
behind `werkzeug` `DispatcherMiddleware` at `/mojave/` and `/bk224/` with a
redirect at `/`.

- **A WSGI dispatcher strips the mount segment exactly like a stripping
  proxy**, so a mounted app wants `proxy_strips_prefix=True`. No new mechanism
  was needed; the flag's meaning generalises to "something upstream removed
  the prefix", whether nginx or a dispatcher.
- **Verified:** index + all 18 referenced assets load under each prefix; asset
  URLs carry the right project prefix; `_dash-layout` / `_dash-dependencies`
  return JSON rather than the catch-all index; source lists are per-project
  and disjoint (484 vs 26); astrometry resolves per project (MOJAVE
  `(10 / 37 epochs)`, BK224 `(no astrometry file)`); and `/` redirects to the
  default project carrying the query string, so old `?token=…` bookmarks keep
  working.
- **Bug found and fixed: `ui.urls.rel()` was not per-app.** It called
  `dash.get_relative_path`, which reads `dash._get_paths.CONFIG` — a
  process-wide singleton that every `Dash(...)` constructor overwrites. With
  one app per project the last app built wins, so MOJAVE's header rendered
  `/bk224/dashboard`. Dash's own asset and callback URLs come from each app's
  own config and were never affected, which is what makes this easy to miss:
  the page loads fine and only the navigation is wrong. `rel()` now reads the
  prefix from the Flask app handling the current request (stashed in
  `server.config` by `create_app`), falling back to the Dash helper outside a
  request context. Harmless single-project — root-mounted still returns bare
  paths.
- 8 tests in `tests/test_multi_app_urls.py`, including one that pins the
  premise (the singleton really is overwritten) so the suite keeps its meaning
  if Dash changes. Verified they fail when `rel()` is reverted to the
  singleton.
- **Still unverified**, and recorded as such in the checklist: clientside
  script behaviour in a real browser under a prefix, the `allow_duplicate`
  hashed output keys (the most likely remaining hazard, since they are derived
  per app instance), token-auth independence, and the memory footprint.

---

## 2026-11-14 — `proxy_strips_prefix`, and what the Denison proxy actually does

**Correction to this entry as first written.** It claimed two latent
production bugs (in-app links and the post-login redirect pointing outside the
app). That diagnosis was **wrong** — see "What the proxy really does" below.
The code is sound and inert by default, but it fixes nothing on the current
deployment. Recording the misdiagnosis because the reasoning that produced it
is an easy trap to fall into twice.

### What shipped

- New `proxy_strips_prefix` config key (`--proxy-strips-prefix`,
  `MOJAVE_REVIEW_PROXY_STRIPS_PREFIX`), selecting between Dash's two prefix
  knobs:

  | | `routes_pathname_prefix` (what the app serves) | `requests_pathname_prefix` (what the browser asks for) |
  |---|---|---|
  | `false` (proxy preserves the prefix) | `/mojave-review/` | `/mojave-review/` |
  | `true` (proxy strips it) | `/` | `/mojave-review/` |

- `wsgi.py` now passes `url_base_prefix` (and the new key) to `create_app`;
  previously it passed neither, so the config key was read but ignored.
- `auth/middleware.public_path()` builds the post-login redirect against the
  browser-visible path, and the session cookie is scoped to the public prefix.
- **Root-hosted behaviour is byte-identical** — both default to off, so a local
  `mojave-review --results-dir Results` and any existing session are
  unaffected. This is what makes the change safe to carry unused.
- 24 tests in `tests/test_proxy_prefix.py`.

### What the proxy really does

Deploying the two keys broke the site in two different ways, and the pattern of
failures identified the proxy:

| config | result on webby |
|---|---|
| both keys absent | **works** |
| `proxy_strips_prefix: true` | page spins forever |
| `proxy_strips_prefix: false` | immediate 404 |

No simple prefix-mapped proxy explains all three (a stripping proxy predicts
true=works/false=404; a preserving one predicts the reverse; and **neither**
predicts "absent works", since the root-relative `/_dash-component-suites/…`
URLs a root-mounted app emits would fall outside a `location /mojave-review/`
and 404). Reproducing all three required this shape, since confirmed by
loading `https://mojave.denison.edu/dashboard` and `/` directly — both serve
the app:

```nginx
location = /mojave-review/ { proxy_pass http://127.0.0.1:8050/; }  # strips, entry path only
location   /               { proxy_pass http://127.0.0.1:8050;  }  # everything else verbatim
```

Only the bare entry URL is translated; every other path reaches the app
unchanged. So the app is **correctly root-mounted** on this host, in-app links
and the login redirect have always worked (via the broad rule), and
`url_base_prefix` must stay unset there. The spin was
`/mojave-review/_dash-layout` falling through Dash's catch-all and returning
the index HTML instead of JSON; the 404 was routes mounted under a prefix the
entry rule had already stripped.

`docs/deployment_phase2.md` documented a single prefix-preserving `location`
that this deployment does not have; it now describes the real arrangement.
## 2026-11-14 — Per-project data files and per-app session cookie

Groundwork for serving several projects from one install (see
[`docs/multi_project.md`](docs/multi_project.md)); both halves are worth having
on a single-project deployment too.

- **`source_run_param.csv` and `astrometry_mojave.dat` are now configurable.**
  New `source_params_file` / `astrometry_file` config keys, with
  `MOJAVE_REVIEW_SOURCE_PARAMS_FILE` / `MOJAVE_REVIEW_ASTROMETRY_FILE` env
  vars and `--source-params` / `--astrometry-file` flags. Previously neither
  had a config key at all — they were found by searching the filesystem.
- **cwd is now the LAST discovery slot, not the first**: `results_dir` parent →
  `results_dir` → cwd. The old order meant whichever directory the app started
  in won, which silently applies one project's redshifts to another when one
  process serves several — and already meant running the app from the wrong
  directory picked up the wrong file. Keeping cwd last preserves the
  convenience of a one-off local run without letting it override a project's
  own file.
- **An explicit path is returned even when the file is missing**, so a typo
  reads as "no redshifts" against the path the operator named rather than
  quietly falling back to a discovered file somewhere else.
- **The auth session cookie is scoped per app.** `auth/middleware.py` used a
  single `COOKIE_NAME = "mr_token"` and omitted `path=` on both `set_cookie`
  calls, so Flask applied `path="/"`. Two apps on one host therefore shared one
  cookie slot: visiting project B overwrote project A's token, returning to A
  gave a 403, and with separate `tokens.yaml` files the reviewer would
  ping-pong indefinitely. `cookie_identity(url_base_prefix)` now derives
  `(name, path)` — root-mounted keeps the historical `("mr_token", "/")` so no
  existing session breaks, while a prefix gets
  `("mr_token_<slug>", "<prefix>")`.
- Reproduced and fixed end-to-end against two live servers sharing one cookie
  jar (cookies ignore the port, so that is a faithful browser): returning to
  the first project was a **403 before** and a **200 after**, with both
  cookies coexisting in the jar.
- Verified both projects side by side: MOJAVE loads 409 redshifts plus its
  astrometry table; BK224 loads its own 25, reports `(no astrometry file)` and
  disables that option (it will light up by itself if a table is added later),
  and its Flux view reads `Tb host-frame [K]` — proving BK224's own CSV, since
  MOJAVE's does not contain its sources.
- 28 tests in `tests/test_project_scoping.py`. One existing test
  (`test_astrometry.py::test_find_file_search_order`) asserted the old
  cwd-first order and was updated to the new contract.

---

## 2026-11-09 — Auto-balance can give back surplus assignments

- **The gap.** Auto-balance's *add* side always respected the review target
  (`slots = review_target - len(committed)`), but nothing ever removed an
  assignment a source had stopped needing. With reviews landing over months,
  sources accumulated more committed reviewers than the target: on the
  production sample, **40 of 87 open sources were over target, with 44
  recoverable queue entries**. Nothing was under target, so the add side was
  already a no-op and the button had nothing to do.
- **New "Trim surplus assignments" checkbox** (off by default) on the
  auto-balance modal → `assignments.plan_surplus_prune`, returning a
  `SurplusPlan(removals, residual)` that feeds the existing `apply_removals`.
  Committed is defined exactly as the add side defines it (submitted ∪
  outstanding), so the two halves can never fight.
- **Submitted reviews are untouchable, and only *pending* assignments are
  removable.** An assignment with a started `current/` draft is left alone —
  dropping the queue entry would orphan work the reviewer may not notice was
  abandoned. On the real data this costs nothing: both at-risk drafts sat on
  sources that also had pending entries to give instead.
- **The heaviest-loaded reviewer donates first**, by remaining *weighted*
  load (outstanding work only, decremented as the plan is built), mirroring
  the add side picking the lightest. A stable alphabetical order was tried and
  rejected: on the real sample it took 14 from one reviewer and emptied their
  entire remaining queue (weight 191 → 0) while leaving another at 243.
  Heaviest-first spreads the same 44 removals 11/7/6/6/6/5/2/1 and leaves
  remaining load in a 101–204 band.
- **Sources that stay over target are reported, not hidden.** Four sources
  (`1504+377u`, `1611+343u`, `1954+513u`, `2128-123u`) have **three
  submitted** reviews — over target by submissions alone, which no queue edit
  can undo. The preview lists them as "ready to aggregate" so its arithmetic
  reconciles with the dashboard's Reviews column instead of looking wrong.
- Runs after the always-on finalized prune, against the post-prune store, and
  is re-planned from the fresh store on Apply like every other removal.
  Ignores "Only unassigned sources" (that checkbox scopes additions). Paused
  reviewers are not preferred donors — the design keeps their queue for their
  return.
- 20 tests in `tests/test_surplus_prune.py`, verified to fail when draft
  protection or load-aware donor choice is removed.

---

## 2026-11-05 — Brotli: pin ≥ 1.2.1 and stop serving `br` (CVE-2026-111497)

Prompted by a Denison sysadmin report of brotli 1.2.0 ("HTTP2Shell") in the
venvs behind `mojave.denison.edu`.

- **Brotli cannot be removed.** It is a hard, non-optional dependency of
  Flask-Compress on CPython (`brotli; platform_python_implementation != "PyPy"`),
  and we declare `flask-compress` directly. Every other installed package that
  mentions brotli (urllib3, httpx, aiohttp, fonttools) lists it only under an
  extra we don't request. Deleting it wouldn't crash the app — `Compress()` sits
  in a `try/except` — it would silently serve **everything uncompressed**, which
  the `app.py` comment flags as dominating felt latency when scrubbing epochs.
- **The site was in fact serving Brotli**, and preferring it over gzip.
  Flask-Compress 1.24 defaults `COMPRESS_ALGORITHM` to
  `["zstd", "br", "gzip", "deflate"]` (and `["zstd", "br", "deflate"]` when
  streaming) and we never overrode it, so any client advertising `br` without
  `zstd` — Firefox among them — got brotli. That was never the intent of that
  block, which has always been about gzip.
- **`app.py` now sets both algorithm lists before init** (flask-compress fills
  its defaults with `config.setdefault`, so pre-setting wins), leaving the
  brotli code path unreachable: `["zstd", "gzip", "deflate"]` and
  `["zstd", "deflate"]`. Verified by response headers — `br` → no encoding,
  `br, gzip` → gzip, `zstd, br, gzip` → zstd. Compression is intact for every
  real client (5.3× on the index page); only a client advertising `br` *alone*
  loses it, and no browser does that.
- **`pyproject.toml` floors `brotli>=1.2.1` as an explicit direct dependency.**
  This is the part that protects the server: flask-compress requires bare
  unversioned `brotli`, which pip/uv consider *satisfied* by an installed 1.2.0,
  so without the floor a later `pip install -e .` leaves a freshly patched host
  vulnerable again. PyPI ships
  `brotli-1.2.1-cp312-cp312-manylinux2014_x86_64.whl`, matching webby's
  linux/py3.12, so it resolves there with no compile.
- **Deploy note:** the floor only takes effect when the venv is reinstalled —
  `pip install -e .` on the host. An rsync of `Results/` does not carry it.
- **Not addressed here:** the brotli we decompress rather than compress, i.e.
  urllib3/requests decoding a `br` response while fetching FITS from
  `cv.nrao.edu`. And the laptop's own conda-managed brotli: conda-forge lists
  the whole 1.2.1 family for osx-arm64 but every solve (classic and libmamba)
  refuses with `PackagesNotFoundInChannelsError: brotli-bin=1.2.1`, while plain
  `conda update` stays on 1.2.0 and wants to churn ~15 unrelated packages plus
  conda and openssl. Left alone deliberately: the laptop ships no packages to
  the server, and `br` is now disabled there too. A project-local venv is the
  clean route if the dev env needs to be current.

---

## 2026-11-02 — Model switches keep the plots' zoom

- **Switching the model no longer zooms the plots back out.** `model_key` was
  part of both the summary and overlay `uirevision` keys, so every
  `current` ⇄ `backup_NNN` ⇄ `alt_model_NNN` ⇄ `Rec: <slug>` switch handed
  Plotly a fresh key and the view snapped to the default extent — which defeats
  the point of switching, since comparing two models of the same source is only
  meaningful at a fixed zoom. The keys are now scoped to (source, view):
  `summary:<folder>:<view>:<reset-counter>`,
  `summary-right:<folder>:<view>:<reset-counter>`,
  `overlay:<folder>:<reset-counter>`.
- **What still flushes**, because it should: a source change, a view change
  (Position vs Kinematics don't share axis meanings), and the ↺ Reset view
  buttons via their counters.
- **Placeholders carry the key too.** `overlay_figure_for_epoch` now passes
  `uirevision` into every `_empty_overlay` it returns (no plotdata / epoch index
  out of range / FITS fetch failed), so a model or epoch with nothing to draw
  doesn't make Plotly discard the retained view on the way through — the same
  trick the compare page already uses to step through a blank epoch.
- **Known trade-off.** Plotly keys its retained UI edits by trace *index*, and
  legend hide/isolate state is one of them, so switching to a model whose
  cluster set differs can land a hidden-trace flag on a different cluster.
  ↺ Reset view is the one-click flush. This is not new behaviour: every other
  data-changing input (visualize, hide-non-robust, pending edits) already
  leaves `uirevision` untouched while changing the trace list.

---

## 2026-10-29 — Absolute astrometric reference position

- **New "Astrometric reference" header checkbox.** Every position the app plots
  is an offset from the core, so the origin rides along with whatever the core
  fit did in that epoch's map. Where an absolute astrometric position of the
  core exists, ticking this puts `(0,0)` at a point **fixed on the sky** — the
  source's mean astrometric position — so the core is free to wander and the
  features carry absolute-frame motion. Off by default.
- **The data.** `astrometry_mojave.dat`, a fixed-layout file kept next to the
  production data (gitignored), parsed by the new `data/astrometry.py`. Found
  like `source_run_param.csv` (cwd → `results_dir` parent → `results_dir`) or
  via `--astrometry-file`; without it the checkbox is disabled everywhere.
  Joined to our epochs by **date** (`20211023` → `ep_name` `2021_10_23`), not
  by the decimal epoch, which differs from ours in the last digits. Covers 271
  of the 487 sources under `Results/`, usually a minority of each one's epochs.
- **The arithmetic.** With `corr(e) = astro(e) − mean(astro)`, the reference in
  epoch *e*'s map frame is `core(e) − corr(e)`, so a plotted offset becomes
  `avg − core + corr(e)` and the core itself plots at `+corr(e)`. The sign is
  the part worth checking: the origin is the mean, so a core displaced `+corr`
  in absolute terms must *appear* displaced by `+corr`. `dx` is +east, `dy`
  +north; the file's RA offsets **and RA errors** are coordinate angles and get
  the `cos δ` factor (verified: at dec +81.6 the RA error column runs ~6.5× the
  Dec one, and the ratio collapses to ~1 once scaled). Corrections are ~0.1 mas
  rms, up to ~0.7 mas. The mean is over every epoch in the **file**, so the
  frame is a source-level constant that never shifts when a model gains epochs.
- **It rewrites `core_x`/`core_y` and nothing else** — that is all it takes,
  since every consumer derives positions from `avg − core`: summary views, PA,
  motion fits, uncertainties, the overlay image/CCs/ellipses, the synthesized
  and stacked contours, the zoom extent. Applied **last** in
  `_resolve_df_for_plot`, after recommendations, because a `change_clusterID`
  to 0 re-cores an epoch.
- **1σ bars gain a third term.** The origin is now known only to the accuracy
  of the astrometric fit, so that error is added in quadrature — but **only
  where finite**, since `np.hypot` would propagate an uncovered epoch's NaN and
  wipe its bars. The core, whose `σ_d`/`σ_pa` were NaN because `d ≡ 0`, now has
  real ones.
- **Stacked-image bug fixed on the way.** `_stacked_axes_for_bundle` re-reads
  the bundle from disk, so the caller's shifted dataframe was invisible to it
  and the contour background stayed core-aligned while the axes and the CC
  overlay moved. It now takes `overlay.core_reference_key(df)` in its cache key
  and re-applies those cores inside — which also fixes the same latent bug for
  a re-coring edit.
- **Coverage is reported twice**, because it is partial in two directions: the
  status span beside the checkbox (`(10 / 37 epochs)` — epochs that actually
  *join*; `(none for this source)` with the option disabled where there is
  nothing to apply), and a fourth line on the overlay badge naming the frame of
  the epoch on screen (`Origin: astrometric (mean of 10 epochs) · core offset
  +0.095, +0.044 mas` / `Origin: fitted core (no astrometry this epoch)`). The
  summary badge reads `<source> · astrometric ref`, and the black × hover says
  "reference" rather than "core" where they differ.
- **The core is drawn in the XY panel** now that it can be off the origin —
  gated on the DATA (`_has_xy_offset`, `> 1e-6 mas`) rather than on the
  checkbox, so the plain core frame and `/compare` still hide it instead of
  piling one marker per epoch under the origin ×, and any future reference
  shift gets it for free. Uncovered epochs stay in the track at exactly
  `(0,0)`: there the reference genuinely *is* the core, and a gap would misjoin
  the dotted connector. Kinematics is deliberately not extended — core drift is
  not feature motion, and a partially covered fit mixes real offsets with
  zero-offset epochs.
- **Not folded into `uirevision`**, so ticking the box keeps the current zoom
  and the shift is visible in place. Scope is the main review page (both
  summary panes + overlay); `/compare` stays core-referenced (XVIII has no
  astrometry, so the panels would sit in different frames and break the shared
  extent), and Window-N frames carry no `ep_name` to join on.
- Full rationale in `docs/astrometric_reference.md`.

---

## 2026-10-11 — Window-N rerun command skips `update_cluster_df.py` run strings

- **The bug.** `build_rerun_command` read `Results/<source>/run_string.txt`
  verbatim and assumed it was a `find_clusters.py` command. The new
  `src/update_cluster_df.py` (rebuilds the merged CSV from the cached fits
  without re-fitting) overwrites that file with its **own** command line, so
  after running it the Window-N panel drafted a rerun built from
  `update_cluster_df.py` flags — a command that can't do the rerun.
- **The fix.** New `window_fits.find_clusters_run_string(folder)` returns the
  most recent genuine `find_clusters.py` run string as `(text, path)`:
  `run_string.txt` when it is one, else the highest-numbered
  `backups/backup_NNN_run_string.txt` that is (`update_cluster_df.py` copies the
  prior run string there before overwriting, and backup numbers increase with
  time, so the first hit scanning down is the most recent). Detection matches
  the script **basename**, like `src/rerun.py`, so `../src/find_clusters.py`
  and absolute paths count. `build_rerun_command` starts from that; everything
  downstream (flag stripping, `--recalc_IDs`, `--N_win_file`) is unchanged.
- **The drafted command says so.** When the run string came from a backup, a
  comment line names it — the command predates the saved CSV, and the reviewer
  should see that rather than wonder why it looks stale.
- **`src/rerun.py` has the same exposure** (it `sys.exit`s with "does not look
  like a find_clusters.py command"), left alone — that's pipeline code and it
  fails loudly rather than silently.

---

## 2026-09-18 — "Hide uncertain motions" actually hides them

- **The checkbox no longer keeps marginal slow motions.** The gate behind
  `_MotionFit.significant` was ported verbatim from `cluster_code.py`:
  `SNR ≥ 3 OR (speed < 0.05 AND sqrt(var_vx+var_vy) < 0.05)`. The second clause
  is there so genuinely stationary clusters survive — a non-moving cluster has
  a low SNR by construction — but it cuts on the best-fit speed and the slope
  errors *separately*, in absolute terms, so it also admitted marginal
  detections. On `2022+171u` all three fits cleared it (cluster 1 at 2.2σ,
  cluster 2 at 1.5σ) and ticking the box produced a byte-identical figure,
  which read as a broken checkbox.
- **Slow now means *confidently* slow**: `speed + 3·speed_err <
  SLOW_SPEED_MAS_YR` (0.05 mas/yr), i.e. the motion has to clear the threshold
  at 3σ, not merely in its best-fit value. The ≥3σ branch is unchanged, and so
  is the default (unticked) behaviour of drawing every qualifying fit.
- **Effect.** `2022+171u` hides cluster 1 and keeps the core and cluster 2
  (0.010 ± 0.0065 mas/yr — a confident non-detection). Across all 3306 motion
  fits in `MOJAVE_1996.00-2026.00`, 273 (8.3%) newly hide when the box is
  ticked; every one is sub-3σ *and* sub-0.05 mas/yr.
- **The app is now deliberately stricter than the pipeline.** The reference
  `summary_plots.pdf` renders under `Results/` still show those marginal
  motions; the equivalent change in `cluster_code.py` is the gate at its line
  3038, left alone by request.

---

## 2026-09-16 — Recommendations panel: robustness picks no longer lost on load

- **Switching source could silently wipe a reviewer's non-robust picks.** The
  Robustness radios reach the rest of the panel through a `dcc.Store`
  (`cluster-feedback-table`) filled by a separate bridge callback. Dash's
  `getReadyCallbacks` blocks a callback only on pending **Input** edges — State
  is never waited on — so a callback reading that store dispatches in the *same*
  batch as the bridge and sees the previous source's rows, or nothing at all on
  a fresh page load. `_load_for_source` publishes the rendered rows and
  `edits-store` in one response, so on any source with staged edits the resync
  fired against the stale store, rebuilt every radio at the model's current
  status, and autosave wrote that wiped panel over `current/<slug>.json`. The
  picks vanished; comments and edits survived (production: `1828+487u`,
  `1514-241u`).
- **The stores are now triggers, never a data source.** `_resync_table_with_
  effective` and `_autosave` rebuild their rows from the live components
  (`_cluster_rows_from_components`), the dodge `_do_submit` already used. The
  stores stay as Inputs so a radio click or blurred comment still fires a save,
  but their payloads are ignored — which also fixes a latent second case where a
  source switch saved the previous source's feedback under the new source's
  name. Wiring pinned by `tests/test_rec_panel_state_sources.py`.

---

## 2026-08-31 — Auto-balance prunes work on finalized sources

- **Nobody is left reviewing a source whose Stage 3 is already applied.** Every
  auto-balance now drops queue entries on a **finalized** source the reviewer
  never submitted — pending *and* unsubmitted `current/` drafts (the draft file
  itself is untouched; only the queue entry goes). No opt-out, and every removal
  is listed in the preview modal before Apply commits it. Submitted records stay
  (they credit completed work), as do `manual_reviews` credits.
- **Pure planner + committer**: `plan_finalized_prune` returns the
  `(reviewer, source)` removals, `apply_removals` commits them via the existing
  `remove_assignment`. The prune mutates the in-memory store before additions
  are planned, so the balancer schedules against post-prune queues; Apply
  re-plans from the freshly-loaded store, so a review submitted between preview
  and apply is never pruned.

---

## 2026-08-05 — Comment audit: in-flight vs lost, and a Stage-2 warning

- **Open ≠ lost.** The first production sweep flagged 263 comments, but 155 were
  open submissions still awaiting Stage 3 — in flight, not lost. `SourceAudit`
  now splits `missing` (from an archived `considered/` or `applied/` submission,
  which nothing in the app surfaces, so an unrecorded one is gone) from
  `open_only` (a live `submitted/` rec, rendered under "Open suggestions" and
  destined for the Stage-3 ledger entry). The CLI lists only the lost ones and
  summarizes the rest; `--include-open` lists them; `--apply` never writes an
  in-flight comment. Late submissions on a `final` source are ordinary open work.
- **The real gap was Stage 2.** All 46 archived-but-unrecorded comments in
  production were the builder's own: the Stage-2 apply writes no ledger entry
  and the Stage-2 prose is hand-written, so skipping "Seed from submission
  summary" leaves them only in `applied/<date>__<slug>.json`. **"Save & set
  Stage 2 done" now warns** with the specific comments and an **"Add them to
  Stage 2 notes"** button (`notes.audit.builder_comments_missing`) that appends
  them in the canonical bullet form. Non-blocking — the save has already
  happened. Chosen over auto-appending a Stage-2 ledger entry, which would break
  the ledger-is-Stage-3-only invariant.
- **Phantom sources.** `_source_dirs` skipped `_`-prefixed directories but not
  dot-dirs, so four `.unison.*.tmp` sync-scratch directories were enumerated as
  sources, polluting the audit and reviewer stats. Their contents were stale
  copies of already-considered submissions, so nothing was at risk.
- **Post-split production sweep**: 108 lost across 64 sources (62 Stage-3
  `considered/`, 46 Stage-2 `applied/`), 151 in flight.

---

## 2026-08-05 — Stage 3 always records reviewer comments

- **The usual way a round ends was dropping every reviewer comment.**
  `stage3_ledger_entry` took no comments at all, so the apply-with-decisions
  path wrote none into `notes/<source>.md` — and applying also moves
  `submitted/*.json` into `considered/<date>/`, so the prose left the app
  entirely. The other two Stage-3 endings (finalize-with-no-changes, Needs
  Discussion) did fold them in, which is why it looked inconsistent.
  `reviewer_comments_block` is now shared by every Stage-3 ledger renderer and
  all three endings pass the round's comments, so a future path can't drop them.
- **One extraction, one rendering.** `notes.render` grows `CommentEntry` /
  `comment_entries`, used by every path *and* by the audit tool, so they always
  agree on what "recorded" means. Only `submitted/` counts; unsubmitted
  `current/` drafts stay private.
- **No duplicates.** A comment already in the notes isn't repeated
  (`unrecorded_entries`), so a dated note the admin already pasted is left
  alone. Matching is on the comment **prose**, not our bullet format — the
  Stage-2 section quotes it as `Cluster 3: …` prose, a dated note as
  `- (rv) cl 3: …`. Short comments (< 12 non-space chars) additionally need
  their cluster/epoch identifier on the same line, since "OK" alone proves
  nothing; a *reworded* comment is recorded again, since preserving the
  reviewer's own words is the point.
- **New `mojave-review-audit-notes`** recovers what the old path lost: rebuilds
  the comment set from every submission on disk (`submitted/`,
  `considered/<date>/`, Stage-2 `applied/<date>__<slug>.json`; `__aggregated`
  skipped — those are admin decisions, not reviewer prose) and reports each
  comment whose text is nowhere in the notes. `--apply` appends a dated
  "Recovered reviewer comments" entry, origin as a **suffix** so re-runs
  recognize it (idempotent). Against the live production mirror: 30 raw hits, 28
  already recorded as Stage-2 prose (the reason matching is prose-level), 2
  genuine.

---

## 2026-08-05 — XY panel: active-epoch squares + epoch-range filter

- **The XY centroid track now shows which points are the current epoch.** The
  Position view's bottom panel has no epoch axis to carry the vertical epoch
  line, so the track points at the Epoch overlay's epoch get a cyan open square
  instead. Filled into an empty placeholder trace; the epoch arrives from the
  existing clientside epoch-line callbacks via `window.mojaveXY.setEpoch`.
- **Zooming the distance panel in time thins the XY plot.** Points outside the
  epoch range visible in the distance-vs-epoch panel above are hidden — on busy
  sources (`0415+379u`: 389 tracks / 1481 points) every epoch at once is
  unreadable. No UI toggle and no opt-out: unzoomed, the top panel autoranges
  over every point, so it's a no-op until the reviewer zooms. Filters on **x
  (epoch) only**, so a distance zoom hides nothing, and the XY axis ranges still
  span every point so the frame is fixed and survivors don't move (reviewer
  decisions).
- **Both are `Plotly.restyle` on the drawn figure, never a server rebuild** —
  cheap enough to scrub epochs with, and `uirevision`/zoom plus the letterboxed
  domains survive. Each XY trace carries `meta = {xy, ep, x0, y0}` (the drawn
  arrays are lossy once masked), which keeps `assets/xy_epoch.js` stateless: a
  rebuild ships fresh originals, so there's no cache to invalidate. Applies to
  both main panes and both `/compare` Position panels.
- **Two hazards found against real Plotly**, both now in `CLAUDE.md`: a pass
  arriving during our own restyle was dropped (stale mask after a reset — now
  remembered and re-run), and comparing against `gd.data` instead of
  `gd._fullData` made every trace look changed after each rebuild (plotly 6
  leaves arrays base64-encoded in `gd.data`), costing a full pointless redraw.

---

## 2026-07-22 — Compare page: consistent vector frame + zoom across epochs

- **Kinematics vector panels now share a frame, not just a scale.** Sharing only
  the arrow scale (previous entry) wasn't enough: each panel still autoranged to
  its own footprint, so equal speeds rendered at different on-screen lengths
  (and a large shared scale could overflow a small panel). Both panels are now
  framed to the same shared box (`build_summary_figure(kin_extent=)` =
  `_shared_extent`), so a vector of a given length means the same speed on both
  sides — always, with or without the display-lock.
- **Locked zoom survives blank epochs.** Stepping through an epoch where one side
  has no data (XVIII goes blank past ~2013) used to reset the zoom, because the
  blank map had no `uirevision`. `_blank` / `_empty_overlay` now carry the
  panel's overlay `uirevision`, so a locked, zoomed-in view stays put as you
  step from epoch to epoch.

---

## 2026-07-22 — Compare page: shared Kinematics vector scale

- **Both compare panels now use one velocity-vector scale.** A vector of a
  given length means the same speed on the XVIII and clustering sides — the two
  panels previously auto-scaled independently (off each side's own median
  speed), so identical motions drew at different lengths, which was confusing.
  The shared absolute scale is computed over BOTH sides' motion fits
  (`summary.kinematics_vector_stats` + `shared_vector_scale_abs`) and passed as
  `build_summary_figure(vector_scale_abs=)`. New `vector_scale_abs` param fixes
  the base arrow scale (mas per mas/yr) instead of per-panel auto-fitting; the
  per-panel scale slider still multiplies on top.

---

## 2026-07-22 — Position hover shows fitted speed

- **Distance-vs-epoch tooltips now report the fitted proper motion.** Hovering a
  point in the Position view's top panel appends the cluster's fitted speed in
  `mas/yr` (± 1σ) and, when the source redshift is known, the apparent speed in
  units of `c` (β_app ± 1σ) — the same information the Kinematics speed-vs-
  distance plot already shows. Shared `_beta_str` / `_motion_hover_extra`
  helpers in `plots/summary.py`; the β line now carries its propagated 1σ error
  in the Kinematics hovers too.

---

## 2026-07-22 — XVIII comparison page: shared controls + point markers

- **Point-like fits render as a bold `+`.** Any component whose size
  (`√(fwhm_maj·fwhm_min)`) is 0 or below 0.05 mas now draws a bold `+` in the
  cluster colour at its location instead of an invisible collapsed ellipse
  (`plots/overlay.POINT_SIZE_MAS`). Applies to every overlay; XVIII Gaussian
  fits are occasionally exactly point-like.
- **Shared FITS toggle.** The per-panel "Use FITS" checkboxes are replaced by a
  single `cmp-use-fits` above both panels, so the choice drives both sides at
  once.
- **Contour base set to 3σ everywhere.** `cbase_factor` default is now 3.0
  (`cbase = 3.0 × inoise`) in every overlay path — main page and both compare
  panels. Close enough to the previous 3.5×, so the short-lived per-page 3×
  toggle was removed.
- **Lock display areas.** New `cmp-lock-axes` mirrors zoom/pan between the two
  panels (clientside), so a change to one produces the same change on the other
  and they always frame the same plotting area. Ranges are synced by reading the
  source graph's live `_fullLayout` (not the `relayoutData` payload, which the
  equal-aspect letterbox clobbers with a domain-only event) — the fix for the
  lock not working on the Epoch overlay panels. Domains are left per-panel.

## 2026-07-21 — XVIII comparison page

- **New `/compare` page** (read-only, header link on the review page): the old
  MOJAVE Paper XVIII Gaussian fits side-by-side with the current clustering
  fits. Two panels, each a clone of the main page's right pane — a view
  selector (Position / Position Angle / Flux / Kinematics / Epoch overlay; no
  Polarization). Sources offered = present in both datasets **and** finalized
  (Stage 3 done).
- Both panels share **one epoch stepper** (same epoch shown on both sides; a
  side lacking that epoch shows a blank map) and an **identical XY zoom box**.
  The shared epoch draws a vertical marker on each side's summary epoch-axis
  views.
- The XVIII table (`MOJAVE_XVIII_apjac230ft4_mrt.txt`) is bundled with the
  package and parsed into our cluster-DataFrame schema (`data/xviii.py`), so the
  existing summary + overlay renderers work unchanged. `--xviii-table` overrides
  the bundled copy.
- The XVIII overlay reuses the same clean-component (or FITS) background image
  as the clustering side, with the XVIII Gaussian FWHM ellipses drawn on top and
  the clean components shown as faint grey context dots.
- **Active-epoch vertical marker**: added to the main page's right-hand summary
  pane (it already existed on the left pane) for the epoch-axis views.

---

## 2026-07-09 — Admin workflow polish

- **Auto-balance**: crediting prior completed reviews is now opt-in (unchecked
  by default). Normal balancing schedules on current pending load only; the
  "Consider completed reviews" checkbox restores the old first-round behaviour.
- **Admin source picker ordering**: triage groups are now — needs discussion
  (‼), ready to aggregate (★, open + ≥ 2 reviews), **stage 1 / stage 2**
  (baseline work), open needs-review sources, finalized. Stage 1/2 sources
  now appear ahead of the needs-review set so admins see in-progress baselines
  without scrolling past all the open sources.
- **Stage 2 done → target date**: clicking "Save & set Stage 2 done" now
  automatically sets the source's reviewer target date to today + 14 days
  (the standard two-week review window), saved into `assignments.json`.
- **Window-N overlay equal aspect**: the admin Window-N review overlay now
  correctly preserves equal X/Y scale on zoom/pan, matching the behaviour of
  the standard epoch overlay. The `equal_aspect.js` letterbox was not wired
  to `nwin-overlay-graph`; fixed.

---

## 2026-07-06 — Source picker triage ordering (admin)

- Admin source picker previously listed all non-flagged sources
  alphabetically. Now sorted into triage groups: needs discussion (‼), ready
  to aggregate (★), everything else — each group alphabetical.

---

## 2026-07-03 — Summary plot colour + auto-balance checkbox

- Unassigned / non-robust cluster traces changed from gray to **darkorange**
  so they are clearly distinct from robust clusters at a glance.
- Dashboard **"Only unassigned sources"** checkbox added to the auto-balance
  modal: fills slots only on sources no reviewer is currently slated to
  review, while still counting all existing assignments as load.

---

## 2026-07-01 — Letterbox equal-aspect zoom (overlay + summary XY)

- **Epoch overlay**: replaced `scaleanchor` (which locks the zoom box to a
  fixed aspect) with a **letterbox** mechanism (`equal_aspect.js`). Equal
  mas/pixel is re-imposed after every draw or zoom by narrowing an axis
  domain, so the plot box gets padding on its short side rather than
  constraining the drag shape. Free-form zoom now works on the overlay.
- **Summary Position XY panel**: same treatment — the bottom centroid-track
  subplot drops `scaleanchor` and gains the horizontal-only letterbox (the
  vertical split is owned by the resizable divider).
- **Self-healing**: a bad domain (e.g. from a Plotly double-click reset) can
  never stick — the script recomputes on both `plotly_afterplot` and
  `plotly_relayout`, and the re-entrancy guard is bulletproofed so it can
  never latch on.
- **Reset view** buttons added to both the overlay and the summary panel;
  these bump the `uirevision` counter, the only way to flush a stuck domain.

---

## 2026-07-01 — Split-pane view selectors

- Left and right summary panes now each have a **dropdown view selector**
  where the panel title used to be. The right pane can show any summary view
  (Position, PA, Flux, Polarization, Kinematics) or the epoch overlay, for
  side-by-side comparisons.
- Right pane is **read-only for selection** — highlights follow along, but
  clicks don't originate edits.
- PA view restored as an independent panel; XY centroid track moved to the
  bottom of the Position view.

---

## 2026-06-30 — Overlay: MOJAVE montage link

- Each epoch's header in the overlay panel now links to its MOJAVE archive
  montage page (opens in a new tab), for quick reference to the full-quality
  published image.

---

## 2026-06-28 — Robustness tab UX fix

- Robust/Non-robust controls are now **inline radio buttons** inside the
  Robustness tab, replacing a dropdown that rendered off-screen inside the
  scrollable panel. Comment fields are plain text inputs.

---

## 2026-06-27 — Dashboard bug fixes

- Fixed dashboard admin apply actions (auto-balance, rebalance, redistribute)
  that were visually appearing unsaved, reloading endlessly, or firing
  spuriously on render. Apply now refreshes the page in place.

---

## 2026-06-25 — Server deployment (Phase 2)

- **Per-user static token auth**: `mojave-review-tokens` CLI (`add`,
  `rotate`, `revoke`, `list`, `show`, `url`). Tokens live in `tokens.yaml`;
  a reviewer hits a `?token=…` URL once and gets a long-lived cookie.
- **Config file**: `config.yaml` / `MOJAVE_REVIEW_*` env vars for all paths
  and settings, so production deployments need no CLI flags.
- **WSGI entry point** (`mojave_review.wsgi`) for gunicorn; systemd unit
  template included.
- **Rotating-file logging** with auth audit trail.
- **Reverse-proxy path-prefix** support: `--url-base-prefix /mojave-review/`
  makes every in-app link prefix-aware.
- **Manual rsync** workflow for syncing `Results/` from laptop to server.
- Deployment runbook in `deploy/README.md`.

---

## 2026-06-20 — Assignment Dashboard

A new **Assignment Dashboard** (`/dashboard`) page tracks reviewer workload
and lets the admin manage assignments.

- **Difficulty scoring**: each source gets a score (`N_epochs × mean features
  per epoch`) mapped to a ★–★★★★★ rating; balancing weight = √score.
- **Auto-balance** (LPT scheduler): fills open reviewer slots on Stage-2-done
  sources, with preview before apply.
- **Top-up rebalance**: moves pending assignments to even out load; optional
  "Consider completed reviews" weighting.
- **Redistribute**: spread one reviewer's pending queue across the pool
  (break coverage).
- **Move / Reassign / Set target dates / Manage team / Pause reviewers**.
- **Stat tiles**: team-wide and per-reviewer counts (total, finalized, ready
  for completion, ready for review, Stage 1/2).
- **My queue** and **Sample Status** tables with pagination.
- **Reviewer summary** (admin): expandable per-reviewer queue + lifetime
  completed count.
- Source names are deep-links that select the source in the main picker.
- Assignments stored in `recommendations/_admin/assignments.json` (syncs with
  the rest of `recommendations/`).

---

## 2026-06-15 — Stage 3 aggregation

- **Admin aggregation panel**: reconciles all reviewers' submitted
  recommendations for a source into one model. Robustness decisions, ID/use-
  in-fit edits, and reviewer comments shown side-by-side.
- **Live preview**: "Preview aggregated" checkbox applies the composed
  recommendation to both summary panes in real time.
- **Apply**: writes `stage3/aggregated.json` + sidecar meta, generates a
  copy-paste `mojave-apply --stage3-meta` command. The app never shells out
  directly.
- **No-changes finalize**: bookkeeping fully in-app when there are no model
  changes (no `Results/` mutation).
- **Needs Discussion** flag: marks a source for further discussion without
  finalizing; visible to all reviewers.
- **Repeat applies**: every Stage-3 apply appends a new ledger entry and
  backup; run index tracked.
- **Dated note box** with "Seed from submissions" and "Add dated note to log".

---

## 2026-06-12 — Stage 2 editor (admin)

- Admin can edit the Stage 2 notes section directly in the app.
- Two save buttons: **Save Stage 2 notes** (marks in-progress) and **Save &
  set Stage 2 done** (opens source for reviewer recommendations).
- "Seed from submission summary" pre-fills the editor from the admin's own
  submitted recommendation.

---

## 2026-06-10 — Source notes panel

- Read-only in-app view of per-source lab notebook (`notes/<source>.md`).
- Notes seeded from a Google Docs Markdown export of the Step-1 review
  spreadsheet; `mojave-review-notes seed` CLI.
- Stage 2 notes section imported automatically.

---

## 2026-06-07 — Summary + overlay enhancements

- **Kinematics view**: zero-anchored speed/distance axes, median-based vector
  scaling, `beta_app` on hover (requires `source_run_param.csv` redshift).
- **Per-source redshift**: `source_run_param.csv` → host-frame Tb `(1+z)` and
  apparent speed `β_app` on Kinematics hovers.
- **Active-epoch marker**: thin vertical line on epoch-axis subplots links the
  overlay's current epoch to the summary plots while scrubbing; clientside
  (no server round-trip, preserves zoom).
- **Source picker badges**: `[N]` / `[stage 1]` / `[stage 2]` / `[final]`;
  stage locks recommendations for non-admin reviewers.
- **Log axes** on Flux / Tb / P-flux panels; epoch-name on hover.
- **Subplot badges** replace panel titles (tracked to axis domain so they
  survive the resizable divider).
- **Resizable left-pane divider**: drag to rebalance the two summary subplots.
- **Stacked overlay image**: accumulate all epochs' CCs on a common grid,
  convolve with the median beam; toggled by a header checkbox.
- **Synthesis-first overlay**: single-epoch CC synthesis is now the default
  (no FITS fetch needed); "Use FITS images" checkbox falls back to real CLEAN
  FITS.
- `mojave-apply` conditional plot regeneration (PDF/MP4 opt-in).

---

## 2026-06-03 — Window-N review panel (admin)

- Web replacement for `find_clusters.py --editN`. Browses cached per-window
  cluster fits (`cluster_fits/*.npz`) without running new clustering.
- **BIC\* vs N** curve + **N-per-window strip chart** (click to jump).
- **Epoch overlay** for each (window, N, epoch) using the standard overlay
  renderer.
- **Fixed source-wide zoom** covering all candidate clusters of all windows.
- **Keyboard shortcuts**: ←/→ step windows, ↑/↓ step N, `r` records choice.
- **Draggable divider** between BIC* chart and overlay.
- **Per-window comments** saved alongside N choices.
- **"Generate rerun command"** builds the `find_clusters.py --N_win_file`
  command from `run_string.txt`.
- Choices saved to `recommendations/<source>/nwin_edits/nwin_choices.json`.

---

## 2026-05-31 — Robust flag consistency

- `robust` is now enforced as a **per-cluster** property: `mojave-apply`
  normalises any per-epoch inconsistencies (canonical = earliest epoch; core
  always forced True). `mojave-review-audit-robust` CLI audits and optionally
  repairs existing CSVs.
- Overlay collapses per-epoch robust flags to the earliest epoch so cluster
  styling doesn't flicker while scrubbing.

---

## 2026-05-30 — Reset Recommendation dialog

- Button beside Submit (current model only) opens a 3-choice modal: **Reset
  to last submitted**, **Delete draft & submitted**, or **Cancel**.

---

## 2026-05-29 — Multi-reviewer view + `Rec:` dropdown

- Model dropdown now includes a `Rec: <slug>` entry per other reviewer's
  **submitted** JSON. Selecting it shows their recommendation applied to the
  current model (read-only).
- Visualize-recommendations checkbox gated per model: on by default for
  `current`; forced on for `Rec:` entries; disabled for backups.

---

## 2026-05-28 — Initial release

- Pip-installable Dash web app (`mojave-review`) loading clustering pipeline
  results from `Results/` without re-running clustering.
- **Summary plots**: Position, PA, Flux, Polarization, Kinematics views
  (Plotly port of `make_summary_plots`).
- **Epoch overlay**: FITS or synthesized Stokes-I contours + clean components
  coloured by cluster + FWHM ellipses + beam.
- **Recommendations panel** (4 tabs): Robustness, ID/use-in-fit Edits, Source
  Notes, Epoch Notes. Auto-saves on every change.
- **Selection-driven edits**: click or box/lasso on summary points to select,
  then renumber cluster IDs or toggle `use_in_fit`.
- **`mojave-apply`** CLI: applies a reviewer's JSON recommendation to a source
  CSV, backs up the prior model, optionally regenerates plots.
- Backup and alt-model support: all prior models browseable in the dropdown.
