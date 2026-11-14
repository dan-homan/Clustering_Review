# Running several projects from one install — options

> **Status: option C chosen 2026-11-14; not yet implemented.** The app today
> serves exactly one project: one `Results/` tree, one `recommendations/` tree.
> The options below are kept for the reasoning; **"Decisions" and "What is
> actually left to build" at the bottom are the current plan.**

"Project" here means an independent review effort with its own sources, models,
recommendations and notes — e.g. a second frequency band, a different sample, or
a sandbox copy of the production data.

## Decisions (2026-11-14)

Answers to the four questions, and what they settle:

1. **The second project is BK224**, at
   `~/Dropbox/Research/Shared_Projects/Clustering_CC/BK224` (a different
   Dropbox tree from MOJAVE's — not a problem, since every path is configured
   absolutely). 26 sources, **zero overlap** with MOJAVE's 487.
2. **Different reviewers per project, if it's cheap.** It is *nearly* free
   under option C — one `tokens_file` per project config — except for the
   cookie collision described below, which has to be fixed first.
3. **No cross-project admin view wanted** → **option B is out.**
4. **Assume a new port is friction; build without one** → **option C it is**,
   and option A is off the table rather than merely second choice.

### BK224 already has the proposed layout

This is the part that makes the job small — no migration, no restructuring:

```
BK224/
├── Results/                  # 26 sources, same <source>_<emin>-<emax> naming
├── recommendations/          # with _admin/assignments.json (v5, target 2, empty)
├── notes/                    # 25 .md files — already the SIBLING of
│                             #   recommendations/, which is exactly what
│                             #   notes_dir_for() derives
├── source_run_param.csv      # at the project root = results_dir.parent,
│                             #   i.e. discovery slot #2
└── full_ccfiles/, sources.txt
```

So the layout sketched below is descriptive, not aspirational. Nothing needs
to move, and **no symlink beside `MOJAVE_1996.00-2026.00` is needed** — the
config points at absolute paths. (A symlink wouldn't hurt, it just buys
nothing.)

Two consequences of the **zero source overlap**:

- **Sharing `cache_dir` is harmless but buys no deduplication.** Worth keeping
  shared anyway for operational simplicity; just don't count it as a win.
- **Per-project redshift files are mandatory, not a nicety.** BK224's
  `source_run_param.csv` covers an entirely disjoint source set, so under the
  current cwd-first discovery BK224 would match *nothing* in MOJAVE's file:
  every source silently falls back to z=0, Tb switches from host-frame to
  observed and β_app disappears from the Kinematics hovers. Partly visible
  (the Tb axis title changes from `Tb host-frame [K]` to `Tb obs [K]`), which
  is lucky rather than by design.

**BK224 has no `astrometry_mojave.dat`**, and that needs no special handling:
`load_astrometry(None)` returns `{}`, the header checkbox renders disabled with
`(no astrometry file)`, and every plot keeps the fitted-core reference. Already
the correct behaviour.

## Decisions, round 2 (2026-11-14, after steps 1–2)

Settled once the token question was revisited:

1. **One shared token per reviewer**, not one per project. Reviewers won't
   manage several links; the admin handles per-project assignments instead.
2. **Projects are switched inside the app**, via a header control.
3. **Declared by `--project <name>=<path>` (repeatable) and/or
   `--projects-dir <dir>`**; the server declares the same thing in a
   `projects:` block in `config.yaml`, since the WSGI entry reads no flags.
   Both forms are wanted because MOJAVE and BK224 live in *different* trees
   (`Clustering_CC/` vs `Shared_Projects/Clustering_CC/`), so a single parent
   directory can't see both without a symlink.
4. **A bare `--results-dir` stays exactly as it is**: one project at `/`, no
   project control in the header, cookie `("mr_token", "/")`. Multi-project is
   strictly opt-in. This is the daily local workflow and must not drift.

### The switcher does NOT require option B

This is the decision that keeps the work small. With a **shared** token the
cookie should be scoped to the **common mount root**, not to each project —
and once one login covers every project beneath that root, the switcher is
just a header dropdown of links to sibling prefixes:

```
Project: [ MOJAVE 1996–2026 ▾ ]   →  href=".../bk224/"
```

Same `html.A(href=rel(...))` pattern as the existing Dashboard and XVIII
Comparison links. The reviewer-visible difference from an in-place swap is a
page load on switch and a URL that changes per project — the latter arguably a
feature, since each project stays bookmarkable and "which project am I in" is
answerable from the address bar. Option B's cost (every dir-using closure in
`register_callbacks` becoming per-request, the `_redshift_map` / `_astrometry`
startup loads, and a new dimension through the recommendations store-bridge
wiring) buys only the absence of that page load.

**Consequence for step 2's work:** the per-project cookie built on 2026-11-14
is the wrong *default* for this deployment. `cookie_identity` should be passed
the **mount root** (one cookie for all projects), not each project's own
prefix. A reviewer clicking through to a project whose path the cookie doesn't
cover would get a 403 with a perfectly valid token. The per-project form stays
available for the case where token files genuinely differ — it's a call-site
choice, not a code change.

### Mount at ROOT-LEVEL paths — then IT does nothing

> **Supersedes the "mount inside `/mojave-review/`" plan below (2026-11-14),**
> once the real Denison proxy was identified — see the deployment doc. Its
> entry rule translates **only** the bare `/mojave-review/`; sub-paths under it
> arrive at the app unstripped, so `/mojave-review/bk224/` would require a
> proxy change after all. But a broad `location /` already proxies every other
> path verbatim, so **root-level** project paths need no proxy change at all:
>
> ```
> /            -> redirect to the default project
> /mojave/     -> project 1   (proxied verbatim by the broad rule)
> /bk224/      -> project 2
> ```
>
> Old `/mojave-review/?token=…` bookmarks keep working: the entry rule strips
> them to `/`, which redirects on to the default project. Nothing has to be
> reissued. Each app instance takes its project path as its own
> `routes_pathname_prefix` / `requests_pathname_prefix`, and the shared cookie
> goes at `/` (the common mount root here *is* the site root — so the
> per-project cookie name from step 2 earns its keep, since the path alone no
> longer separates the projects).

### (superseded) Mount inside the existing prefix — then IT does nothing

The current public path is `/mojave-review/`, and reviewers have bookmarks
with `?token=…` on it. Mounting projects *beneath* that same prefix:

```
/mojave-review/            -> redirect to the default project
/mojave-review/mojave/     -> project 1
/mojave-review/bk224/      -> project 2
```

means the existing nginx stanza (`location /mojave-review/`) keeps working
untouched — **no new port, no new proxy rule, no IT request**, which was
requirement 4. Old bookmarks still land on `/mojave-review/`, which redirects
to the default project, so nothing has to be reissued and the shared cookie is
set at the same path it is today. Moving to a fresh root like `/review/` would
mean reissuing every link for no benefit.

### Blocking unknown: does the deployed app actually get its prefix?

`wsgi.py` never passes `url_base_prefix` to `create_app` (noticed in
`a26df83`, deliberately left alone). That matters now, because the dispatcher
depends on getting these prefixes right, and the two possibilities imply
opposite nginx setups:

- If nginx **preserves** the prefix (what `deployment_phase2.md` prescribes:
  `proxy_pass http://127.0.0.1:8050;` with no trailing slash), then a
  root-mounted Dash app would 404 on every request — yet the site works, so
  this cannot be the whole story.
- If nginx **strips** it (trailing slash), the app serves at root and requests
  work, but `rel()` returns bare paths, so the Dashboard / XVIII links and the
  post-login redirect would point outside the proxied location and break.

Something must therefore be supplying the prefix, or the actual proxy config
differs from the docs. **One diagnostic settles it:** on the live site, open
the page source and look for `requests_pathname_prefix` in the Dash config
blob — `"/mojave-review/"` means the prefix is reaching Dash somehow,
`"/"` means it isn't. A quick cross-check is whether the
"📋 Assignment Dashboard" header link actually works when clicked on the
server. Resolve this before step 4.

## The cookie was not scoped per app  *(FIXED 2026-11-14)*

**This was the one real blocker for "different reviewers per project"**, found
while checking whether it was cheap. Kept here because the failure mode is
worth recognising if it ever resurfaces.

`auth/middleware.py` uses a single module constant `COOKIE_NAME = "mr_token"`,
and both `set_cookie` calls omit `path=`, so Flask applies its default
`path="/"`. Two apps on one host therefore share one cookie slot:

1. Reviewer opens project A's bookmark → `mr_token=<token A>` at `/`.
2. Reviewer opens project B's bookmark (`?token=…`) → `mr_token=<token B>`,
   **overwriting** A's.
3. Reviewer returns to project A → the cookie now holds B's token, which A's
   `tokens.yaml` doesn't contain → **403**, and clicking A's bookmark again
   just flips the problem to B.

They'd ping-pong indefinitely. With a *shared* `tokens.yaml` the collision is
harmless (one token is valid everywhere), which is the fallback if this isn't
fixed — so the choice between shared and per-project reviewers really is a
choice about whether to do this bit of work.

The fix (shipped) does **both halves**, because they fail in different
directions:

- `path=<the app's url prefix>` on both `set_cookie` calls, so the browser only
  sends each project's cookie to that project's paths — this is what actually
  prevents the collision; **and**
- a per-app cookie name (`mr_token_<slug>`), because a path-scoped cookie is
  invisible to a differently-prefixed sibling and so hard to diagnose without a
  distinguishing name. It also covers a root-mounted app, whose `path="/"`
  matches every request, and nested prefixes — `/review/` *is* a path-prefix of
  `/review/bk224/`, so path alone would not separate them.

`cookie_identity(url_base_prefix)` returns the pair;
`install_token_middleware(url_base_prefix=)` threads it in alongside the
`cookie_secure` / `cookie_max_age_days` it already closes over.

## What is actually left to build

In dependency order. Each step is independently testable, and the first two are
worth doing on their own merits even if multi-project stalled.

1. ~~**Make the discovered files configured.**~~ **DONE 2026-11-14.**
   `source_params_file` and `astrometry_file` are `Config` keys (plus
   `MOJAVE_REVIEW_SOURCE_PARAMS_FILE` / `MOJAVE_REVIEW_ASTROMETRY_FILE` and
   `--source-params` / `--astrometry-file`), threaded through `create_app` →
   `register_callbacks` / `register_compare_callbacks`. Discovery remains the
   fallback but **cwd is now last**, not first. An explicit path is returned
   even when absent, so a typo surfaces rather than silently falling back.
   Verified both projects side by side: MOJAVE loads 409 redshifts + its
   astrometry, BK224 loads its own 25 and correctly reports
   `(no astrometry file)`.
2. ~~**Scope the auth cookie per app.**~~ **DONE 2026-11-14.**
   `auth.middleware.cookie_identity(url_base_prefix)` → `(name, path)`;
   `install_token_middleware(url_base_prefix=)` uses it for both `set_cookie`
   calls and the read. Root-mounted is unchanged, so no existing session
   breaks. The collision was reproduced end-to-end (two live servers, one
   cookie jar: step 4 returned 403 before, 200 after).
3. ~~**Split the Dash prefix knobs.**~~ **DONE 2026-11-14.** Landed on `main`
   as part of the single-project prefix work (`ecebfa7`): `proxy_strips_prefix`
   selects between the symmetric pair (`url_base_pathname`) and the asymmetric
   one (`routes="/"` + `requests=<prefix>`). A WSGI dispatcher strips the mount
   segment exactly like a stripping proxy, so a mounted app wants
   `proxy_strips_prefix=True` — the flag means "something upstream removed the
   prefix", whether that is nginx or `DispatcherMiddleware`.

   **Proved end-to-end with both real projects** mounted behind
   `DispatcherMiddleware` (`/mojave/`, `/bk224/`, redirect at `/`): index and
   all 18 referenced assets load under each prefix, asset URLs carry the right
   project prefix, `_dash-layout` / `_dash-dependencies` return JSON (not the
   catch-all index), source lists are per-project and disjoint (484 vs 26),
   in-app links stay within their own project, and astrometry resolves
   per-project — MOJAVE `(10 / 37 epochs)`, BK224 `(no astrometry file)`.

   **One real bug surfaced, and is fixed.** `ui.urls.rel()` called
   `dash.get_relative_path`, which reads `dash._get_paths.CONFIG` — a
   process-wide singleton that every `Dash(...)` constructor overwrites. With
   one app per project the LAST app built wins, so MOJAVE's header rendered
   `/bk224/dashboard`. Dash's own asset and callback URLs are built from each
   app's own config and were never affected, which is what makes it easy to
   miss: the page loads fine and only the navigation is wrong. `rel()` now
   takes the prefix from the Flask app handling the current request
   (`create_app` stashes it in `server.config`), falling back to the Dash
   helper outside a request context. Pinned by
   `tests/test_multi_app_urls.py`, which also asserts the premise (that the
   singleton really is overwritten) so the test keeps its meaning if Dash
   changes.
4. ~~**A project registry + dispatcher.**~~ **DONE 2026-11-14** —
   `src/mojave_review/projects.py`.
   - `ProjectSpec(name, label, root)`; `results_dir` / `recommendations_dir`
     derived by convention (`<root>/Results`, `<root>/recommendations`), and
     the two data tables found by the existing per-project discovery because
     `<root>` *is* `results_dir.parent`. So nothing needs configuring per
     project beyond its root.
   - Declared by `--project <name>=<path>` (repeatable), `--projects-dir
     <dir>` (every subdirectory holding a `Results/`), or a `projects:` block
     in `config.yaml` taking either form. The CLI flattens its declarations
     into the config layer so CLI > env > yaml precedence is the usual one.
   - Names: explicit, else derived from the path's basename —
     `MOJAVE_1996.00-2026.00` → `mojave`. Validated as a URL segment, and
     refused when they collide with a path the app serves (`dashboard`,
     `compare`, `assets`, `static`). **A symlink's own name wins over its
     target's**, which is what makes a `--projects-dir` of symlinks a sane way
     to gather projects from different trees.
   - `build_dispatcher` mounts each at `<mount-root>/<name>/` with
     `proxy_strips_prefix=True` and redirects **every** unmatched path into the
     default (first) project, path and query preserved: `/` → `/mojave/`,
     `/dashboard` → `/mojave/dashboard`, `/?token=…` → `/mojave/?token=…`. So
     root-level bookmarks from before projects existed keep working. No loop
     is possible, since a path already under a mount is handled by that mount.
   - Declaring **no** projects keeps single-project mode byte-identical:
     one app at the root, no dispatcher. Verified by running the daily
     `mojave-review --results-dir Results` and diffing the banner + behaviour.
   - `results_dir` is no longer required to exist in multi-project mode (each
     project validates its own `Results/`), since the built-in default
     `Results` almost never exists next to a multi-project config file.
   - `--source-params` / `--astrometry-file` are **refused** alongside
     `--project`: they name one file, and silently applying one project's
     table to all of them is exactly the bug step 1 removed.
   - 47 tests in `tests/test_projects.py`; verified they fail when the
     reserved-name check, the `Results/` requirement, the path-preserving
     fallback, or duplicate detection is removed.
5. ~~**The header project control.**~~ **DONE 2026-11-14.**
   - `ui.layout.build_project_switcher(project_nav, current_project)` renders
     a native `<details>`/`<summary>` disclosure listing every project as a
     plain link to its mount, the current one marked `●` and named in the
     summary. Returns **None** below two projects, so single-project mode
     renders no control at all.
   - Deliberately not a `dcc.Dropdown`: the entries are links to sibling
     mounts, so switching is an ordinary navigation — no callback, no
     clientside JS, no round-trip — and each project keeps its own URL, so
     Back and per-project bookmarks behave as reviewers expect. (HTML has no
     "dropdown of links"; `<details>` is the closest scriptless equivalent,
     and the admin Window-N panel already uses one.)
   - `project_nav` is `[(name, label, href), ...]` — plain data, so `ui`
     never imports `projects` and `projects` can keep importing `app`.
   - **One login for every project.** New
     `install_token_middleware(cookie_scope_prefix=)`, which `build_dispatcher`
     sets to the **mount root** rather than each project's prefix, so the
     cookie is `("mr_token", "/")` and a single token covers everything. This
     was the open question from step 4: with projects at root level the common
     mount root *is* `/`, so cookie path cannot separate them — and it should
     not, because the chosen design is one shared token per reviewer. Scoping
     per project would have met a reviewer clicking into their second project
     with a 403 despite a valid token.
   - 14 tests in `tests/test_project_switcher.py`; verified they fail when the
     cookie is scoped per project (4), the switcher shows for a single project
     (1), the current project is unmarked (2), or every app is told the same
     `current_project` (1).
   - **Labels default to the directory basename**, so MOJAVE reads as
     `MOJAVE_1996.00-2026.00` in the header. Set `label:` in the config
     `projects:` block (or name the symlink) for something shorter.
6. **Per-project log context**, since `configure_logging` is global and log
   lines would otherwise not say which project they came from.
7. **Prove it with the checklist below** before touching webby.

Deployment-side, already fine: `deploy/sync-results.sh` reads
`$MOJAVE_SYNC_CONF` (falling back to
`~/.config/mojave-results-sync.conf`), so a second project is a second conf
file plus a second systemd timer — no script change.

## What already defines a project

The `Config` dataclass (`config.py`) has 16 fields, but only four are
project-scoped:

| Field | Scope |
|---|---|
| `results_dir` | **project** |
| `recommendations_dir` | **project** |
| `tokens_file` | **project** — or shared; see the open questions |
| `log_file` | **project** (or one file with project context added) |
| `cache_dir`, `fits_data_dir` | **shareable** — see below |
| `host`, `port`, `url_base_prefix`, `cookie_secure`, `admin_contact` | host / instance |
| `reviewer`, `admin`, `log_level`, `no_browser`, `debug` | instance |

That is a smaller surface than it feels like, and it's the main reason this is
tractable at all.

`cache_dir` is **positively worth sharing**: the FITS cache path is keyed by the
real MOJAVE data identity
(`<source>/<epoch_name>/<source>.<band>.<epoch_name>.<stokes>cn.fits.gz`), which
is project-independent. Two projects covering overlapping sources should hit the
same cache. Same argument for `fits_data_dir` if an on-disk FITS tree ever
exists.

## Two couplings that are not config, and will bite

### 1. `notes/` is derived, not configured

`notes.store.notes_dir_for()` returns `recommendations_dir.parent / "notes"`.
Wherever `recommendations/` goes, the notes tree is pinned as its sibling —
there is no independent knob. Not a problem, but it means `recommendations_dir`
silently decides a second directory's location, so the two must stay together
when a project is laid out, moved or archived.

### 2. `source_run_param.csv` and `astrometry_mojave.dat` are *discovered*

Neither has a `Config` field. Both are located by searching, in order:

1. **the current working directory**
2. `results_dir.parent`
3. `results_dir`

(`data/source_params.find_source_params`, `data/astrometry.find_astrometry_file`.
`--astrometry-file` and `--xviii-table` exist as CLI-only pass-throughs, not
config keys.)

With one project, cwd-first is a convenience. With several it is a correctness
bug waiting to happen: whichever directory the process starts in wins for
*every* project in that process, so project B can silently inherit project A's
redshifts and astrometry. The failure is quiet — subtly wrong brightness
temperatures, wrong β_app, a reference frame built from the wrong source's
astrometric positions — with nothing in the log.

**This is a prerequisite, not an option.** Whichever design is chosen, make both
paths real per-project config keys and drop or demote the cwd rule. Small and
self-contained; worth doing even if multi-project never happens, since it also
removes a footgun from the single-project case (running the app from the wrong
directory today already picks up the wrong file).

## Option A — one process per project, own port  *(not chosen)*

One `config.yaml`, one systemd unit, one port, one `url_base_prefix` and one
nginx `location` block per project.

- **Zero app-code change.** The boring, certain option.
- Full isolation: separate memory, separate crash domain, separate restarts.
  Could even run different code versions per project.
- Cost is operational, not technical:
  - **each new project is an IT ask** (a port opened, a proxy stanza added);
  - full interpreter footprint per project — dash, pandas, scipy, astropy
    imported separately in each;
  - reviewers get N bookmarks and N tokens.

## Option B — one project-aware app with a project picker  *(ruled out twice: no cross-project view wanted, and the switcher turned out not to need it — see Decisions, round 2)*

One process, one port, one token per reviewer, and the only design that can
offer a **cross-project admin view** ("everyone's queue, across all projects").

This is the large refactor, and is hard to justify *unless* that cross-project
view is actually wanted:

- `results_dir` / `recommendations_dir` are captured in closures at
  `register_callbacks` time, and `_redshift_map` / `_astrometry` are loaded once
  at startup. All of it would have to become a function of the active project.
- Making the active project a per-request value is at least a known pattern —
  token auth already does exactly that with `flask.g.reviewer` — but it touches
  essentially every callback.
- The recommendations panel's store-bridge wiring is delicate (see the
  "stores are triggers, never a data source" note in `CLAUDE.md`); threading
  another dimension through it is where the risk concentrates.

## Option C — one gunicorn process, several apps mounted at different paths  *(CHOSEN)*

`wsgi.py` currently calls `create_app()` once and exposes
`application = _app.server`. Instead it would build one app **per project**,
each with its own dirs and tokens, behind a small WSGI dispatcher routing on
path prefix:

```
/review/mojave/   ->  app built from projects/mojave/config.yaml
/review/cgrabs/   ->  app built from projects/cgrabs/config.yaml
```

Why this is attractive:

- **One port, forever, and exactly one nginx `location /review/`.** Adding a
  project becomes a config file plus a restart — no IT involvement again, ever.
  Given the proxy conversation had to happen once already, this is probably the
  dominant consideration.
- **Shared library memory**: one interpreter, one copy of
  dash/pandas/scipy/astropy. Per-app overhead is just the layout and the
  callback registry — and the layout is rebuilt per request anyway, so it isn't
  a large static blob.
- **Shared data caches, safely.** `_load_bundle_cached` is a module-level
  `lru_cache` keyed on `(folder, model_key, file-fingerprint, …)` with `folder`
  an absolute path, and `_stacked_axes_for_bundle` likewise. Two projects
  therefore coexist with no key collisions.
- **No change to app internals.** Each app instance is constructed exactly as
  today, with its own closures. Nothing threads through callbacks.

Honest caveats:

- **Not quite zero code change.** `create_app` passes a single
  `url_base_pathname`, which sets *both* of Dash's prefix knobs. Mounting behind
  a dispatcher wants them split — `routes_pathname_prefix` (what the app serves)
  vs `requests_pathname_prefix` (what the browser asks for) — which is exactly
  what Dash provides for this case. So: one new kwarg plus a dispatcher module.
- **The prefix interaction is unverified.** This is the fiddly part of any Dash
  mounting scheme and it has *not* been tested here. Prove it with a throwaway
  two-project setup before committing (checklist below).
- **One crash domain, one restart** for all projects.
- `_logging.configure_logging` is global, so log lines would not say which
  project they came from without adding that context.
- **`clear_bundle_cache()` is process-wide.** The "↻ Reload" button in project A
  would also wipe project B's bundle cache. Harmless — a performance hiccup, not
  a correctness problem, since the next access re-reads from disk — but worth
  knowing before someone reports "the app went slow when a colleague clicked
  something".

## Option A → C is a one-way door that stays open  *(moot, but kept)*

C's directory layout is a **superset** of A's. Lay the directories out as below
and A works immediately; moving to C later changes only `wsgi.py` and needs no
data migration. So "start with A, move to C if the port/proxy friction annoys
you" is a legitimate plan, not a hedge.

## Layout (BK224 already matches this)

```
/var/www/html/mojave.denison.edu/
├── Clustering_Review/          # one code checkout, one .venv   (shared)
├── shared/
│   ├── fits_cache/             # project-independent; real disk win
│   └── tokens.yaml             # iff one team reviews all projects
└── projects/
    ├── mojave-15ghz/
    │   ├── config.yaml         # results_dir, recommendations_dir, prefix, …
    │   ├── Results/
    │   ├── recommendations/
    │   ├── notes/              # NOT free — sibling of recommendations/
    │   ├── source_run_param.csv
    │   ├── astrometry_mojave.dat
    │   └── logs/
    └── <second-project>/       # same shape
```

**The everyday local invocation is unaffected by the discovery reorder.**
Running `mojave-review --results-dir Results --reviewer homand --admin` from
inside a project directory makes `results_dir.parent` equal `.`, i.e. the cwd —
so the project root is search slot 1 *and* slot 3, and the result is identical
either way (verified: finds both files, loads 409 redshifts). The reorder only
changes the case nobody types deliberately: standing in one project's
directory while pointing `--results-dir` at another's.

The principle worth holding to: **one directory per project containing
everything project-scoped, and nothing project-scoped outside it.** That makes
"add a project" a copy-the-skeleton operation, "archive a project" a `tar` of one
directory, and keeps the discovery rules honest, since `results_dir.parent` is
then the project root.

## Open questions — ANSWERED 2026-11-14

Kept for the reasoning; see "Decisions" at the top for the outcome.

1. **What is the second project?** A different band or epoch range of the same
   sources, a different sample entirely, or a staging/sandbox copy? If the
   sources overlap, redshifts and astrometry are shareable and the layout above
   duplicates them unnecessarily. If it's a sandbox, deliberate isolation with
   throwaway tokens may be the point.
2. **Same reviewers, or different teams?** This decides `tokens.yaml`. One
   shared file means a reviewer's single token works everywhere — much nicer for
   them — but everyone can then see every project. Per-project token files give
   access control at the cost of N bookmarks per person.
3. **Does an admin ever want a cross-project view?** This is the one capability
   only option B provides. If each project is its own effort with its own team
   and schedule, A and C are strictly better.
4. **How much friction is a new port plus an nginx stanza?** The swing factor
   between A and C. Fast turnaround and two or three projects total → A's
   simplicity wins and C is over-engineering. Slow turnaround, or projects
   coming and going → C pays for itself immediately.

## Prove these before deploying to webby

A throwaway two-project setup should demonstrate, before any real deployment:

- [x] Both apps load their own `results_dir` and source list, with no bleed
      (484 vs 26 sources, disjoint).
- [x] Assets resolve under each prefix — all 18 URLs the index references, in
      both projects.
- [x] Clientside scripts work under a prefix. Confirmed in a real browser
      (BK224, Position view): drag-zoom holds equal aspect, the resizable
      divider works, and ←/→ epoch stepping behaves — exercising
      `equal_aspect.js`, `subplot_resize.js`, `xy_epoch.js` and
      `keyboard.js`. Consistent with inspection: all five scripts are purely
      DOM-based (element IDs, `.js-plotly-plot`, `Plotly.*` on drawn figures)
      with no `fetch`, no `XMLHttpRequest`, no `window.location` and no
      constructed URLs — so there is nothing for a path prefix to break.
- [x] Callback POSTs reach the right app — the page-content router and the
      astrometry-status callback over HTTP on both, plus real browser use
      (source selection, view switching, epoch stepping). The
      `allow_duplicate` hashed output keys are derived per app instance; a
      mismatch kills the whole client callback graph for that mode, so the
      graph initialising and the plots responding is the evidence that they
      resolve. **Not separately exercised:** the heavier edit paths
      (selection → edits → recommendations autosave), which is where most of
      the `allow_duplicate` outputs live.
- [x] `ui/urls.rel()` produces correct in-app links in both — this is the one
      that initially FAILED; see step 3 above. The dashboard's `?source=`
      deep-links go through the same helper, so they are covered by the fix,
      but were not separately exercised.
- [x] **One token covers every project** (the design that was chosen, rather
      than per-project token files): visit A, then B, then A again, all
      authenticated from a single `?token=` login. Tested over HTTP with one
      cookie jar. Per-project token files remain possible — pass each app its
      own `tokens_path` and let `cookie_scope_prefix` default — but nothing
      exercises that path.
- [x] `source_run_param.csv` and `astrometry_mojave.dat` resolve per project.
      Confirmed via the astrometry status span, which distinguishes the two
      failure modes that matter: MOJAVE `(10 / 37 epochs)` (its own file
      loaded) vs BK224 `(no astrometry file)`. Note `(none for this source)`
      is a THIRD state — file loaded, that source absent — and mistaking it
      for a failure cost a test iteration.
- [x] Memory footprint measured — and it settles the A-vs-C question
      decisively. Peak RSS after building the apps and serving each a request
      and a `_dash-layout`:

      | mounted | peak RSS |
      |---|---|
      | MOJAVE alone | 175.2 MB |
      | BK224 alone | 172.2 MB |
      | both together | **175.8 MB** |

      The libraries (dash, pandas, scipy, astropy, plotly) dominate at
      ~160 MB, so a **second project costs about 0.6 MB** — the app instance
      is a layout plus a callback registry, and the layout is rebuilt per
      request anyway. Option A would have paid ~172 MB per project: ~347 MB
      for these two against ~176 MB for one process, and ~172 MB for every
      project after that.

## See also

- [`deployment_phase2.md`](deployment_phase2.md) — the current single-project
  deployment, the config layering (CLI > env > yaml > default) that all of this
  builds on, and the "Updating the deployed app" procedure.
- [`../CLAUDE.md`](../CLAUDE.md) — architecture reference; the recommendations
  store-bridge note explains why option B's risk concentrates where it does.
