# Running several projects from one install — options

> **Status: proposal / design notes, 2026-11-09. Nothing here is implemented.**
> The app today serves exactly one project: one `Results/` tree, one
> `recommendations/` tree. This file captures the options for serving several,
> so the thinking isn't lost. No decision has been made; the questions at the
> bottom are genuinely open.

"Project" here means an independent review effort with its own sources, models,
recommendations and notes — e.g. a second frequency band, a different sample, or
a sandbox copy of the production data.

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

## Option A — one process per project, own port

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

## Option B — one project-aware app with a project picker

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

## Option C — one gunicorn process, several apps mounted at different paths

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

## Option A → C is a one-way door that stays open

C's directory layout is a **superset** of A's. Lay the directories out as below
and A works immediately; moving to C later changes only `wsgi.py` and needs no
data migration. So "start with A, move to C if the port/proxy friction annoys
you" is a legitimate plan, not a hedge.

## Proposed layout (either option)

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

The principle worth holding to: **one directory per project containing
everything project-scoped, and nothing project-scoped outside it.** That makes
"add a project" a copy-the-skeleton operation, "archive a project" a `tar` of one
directory, and keeps the discovery rules honest, since `results_dir.parent` is
then the project root.

## Open questions

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

## If option C is chosen — prove these first

A throwaway two-project setup should demonstrate, before any real deployment:

- [ ] Both apps load their own `results_dir` and source list, with no bleed.
- [ ] Assets (`/assets/*.js`, `.css`) resolve under each prefix — the clientside
      scripts (`equal_aspect.js`, `xy_epoch.js`, `keyboard.js`,
      `subplot_resize.js`) all load and work in both.
- [ ] Callback POSTs reach the right app — in particular the `allow_duplicate`
      hashed output keys, which differ per app instance.
- [ ] `ui/urls.rel()` produces correct in-app links (dashboard, `/compare`) in
      both, including the `?source=` deep links.
- [ ] Token auth is independent: a token valid for project A gets a 403 on
      project B when the token files are separate.
- [ ] `source_run_param.csv` and `astrometry_mojave.dat` resolve per project —
      the landmine above. Test with *different* files per project and confirm
      the redshifts differ in the Flux view's Tb axis label and values.
- [ ] Memory footprint measured, to confirm C actually beats A by enough to
      justify the extra moving part.

## See also

- [`deployment_phase2.md`](deployment_phase2.md) — the current single-project
  deployment, the config layering (CLI > env > yaml > default) that all of this
  builds on, and the "Updating the deployed app" procedure.
- [`../CLAUDE.md`](../CLAUDE.md) — architecture reference; the recommendations
  store-bridge note explains why option B's risk concentrates where it does.
