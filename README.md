# MOJAVE Cluster Review

A web-based review tool for MOJAVE radio jet clustering results, plus a
command-line tool that applies reviewer recommendations to the on-disk
results.

This repo contains:

- `cluster_code.py`, `find_clusters.py` — the production clustering
  pipeline (read-only here; kept locally for reference).
- `Results/` — per-source clustering outputs (local-only mirror of a
  shared Google Drive folder).
- `mojave_review/` — the **pip-installable package** with two console
  scripts:
    - `mojave-review` — the interactive web app for reviewing clustering
      models and producing structured *recommendations*.
    - `mojave-apply` — a CLI that applies a recommendation JSON to a
      Results folder, with full backup + plot regeneration + history
      logging.

Reviewer feedback is **always written to a separate recommendations
directory**. The app never modifies `Results/` directly — only
`mojave-apply` does that, and only when the author runs it explicitly.

The detailed architecture reference is in [`CLAUDE.md`](CLAUDE.md); this
README covers installation and day-to-day usage.

---

## Installation

### Requirements

- Python ≥ 3.10.
- A copy of `Results/` — the clustering output. The app only reads it.
- For `mojave-apply` only (applying a recommendation, not reviewing): a
  **writable** `Results/` plus `find_clusters.py` and `cluster_code.py`
  discoverable from the same parent directory — one symbol,
  `save_summary_plots`, is imported to regenerate the PDF + MP4.

Dependencies are declared in `mojave_review/pyproject.toml` and install
automatically; nothing else is needed. Everything is pure-Python plus
numpy/scipy/pandas/astropy/plotly wheels.

### Install the package

```bash
git clone <this-repo> Clustering_Review
cd Clustering_Review/mojave_review
pip install -e .
```

That puts `mojave-review` and `mojave-apply` on your PATH; both take
`--help`. Drop the `-e` for a non-development install
(`pip install ./mojave_review`).

On a host with no system Python ≥ 3.10 and no root, [`uv`](https://docs.astral.sh/uv/)
is the path of least resistance — it fetches a prebuilt interpreter with no
compiler or system packages required:

```bash
uv python install 3.12
uv venv --python 3.12
uv pip install -e ./mojave_review        # note: `uv venv` creates no pip
```

### Check it works

```bash
cd <the directory holding Results/>
mojave-review --results-dir Results --reviewer "Your Name"
```

It opens a browser at `http://127.0.0.1:8050`. Run it **from the directory
that holds `Results/`** if you want `source_run_param.csv` picked up
automatically — see "Supporting data files" below.

Run the test suite with `cd mojave_review && python3 -m pytest tests -q`
(405 tests, a couple of seconds, no data or network needed).

### Supporting data files

Two optional tables sit **beside** `Results/`, not inside it, and are found
automatically by searching the parent of `results_dir`, then `results_dir`,
then the current directory:

| file | what it does when present | when absent |
|---|---|---|
| `source_run_param.csv` | per-source redshift → host-frame Tb, `beta_app` | every source treated as z unknown; Tb reads `Tb obs [K]` |
| `astrometry_mojave.dat` | absolute core positions → "Astrometric reference" option | option shown disabled, `(no astrometry file)` |

Pin either explicitly with `--source-params` / `--astrometry-file`, or the
`source_params_file` / `astrometry_file` config keys. Neither is required —
the app works without both.

---

## Running the web app

```bash
mojave-review --results-dir <path-to-Results>
```

A browser tab opens to `http://127.0.0.1:8050`. The header prints the
four paths the app resolved:

```
mojave-review serving on http://127.0.0.1:8050
  results_dir         = /Users/me/work/Results
  recommendations_dir = /Users/me/work/recommendations
  cache_dir           = /Users/me/.mojave_review/cache
  reviewer            = me
```

### Common flags

| Flag | Default | What it does |
|---|---|---|
| `--results-dir` | `./Results` (cwd-relative) | Where per-source folders live. |
| `--recommendations-dir` | `<results-dir>/../recommendations` | Where your review JSON files go. |
| `--reviewer "Your Name"` | `$USER` | Tag attached to every recommendation file. Used to slug a filename per reviewer. |
| `--cache-dir` | `~/.mojave_review/cache` | On-disk cache for MOJAVE FITS images fetched from NRAO. |
| `--fits-data-dir` | `$MOJAVE_DATA` env if set, else unused | Path to an already-on-disk MOJAVE FITS tree (`<source>/<epoch>/<source>.<band>.<epoch>.icn.fits.gz`). When set, the overlay reads these directly instead of fetching from NRAO. Missing files silently fall back to fetch+cache. If you already run the production pipeline, just `export MOJAVE_DATA=/path/to/your/data` once. |
| `--port` | `8050` | HTTP port. |
| `--no-browser` | off | Don't auto-open a browser tab. |
| `--admin` | off | Show the **Generate Apply Command** button + the Stage-3 aggregation and Window-N panels, and the dashboard's balancing actions. Only enable on the machine where you'll be running `mojave-apply`. |
| `--source-params` | discovered | Pin `source_run_param.csv` instead of searching for it. |
| `--astrometry-file` | discovered | Pin `astrometry_mojave.dat` instead of searching for it. |
| `--project NAME=PATH` | — | Serve several projects from one process; repeatable. See "Several projects at once" below. |
| `--projects-dir DIR` | — | Serve every subdirectory of `DIR` that holds a `Results/` as a project. |

If you want stable paths regardless of where you launch from, pass
absolute paths for `--results-dir` and `--recommendations-dir`.

### Several projects at once

A *project* is an independent review effort — a different sample, band or
epoch range — with its own sources, recommendations and notes. One directory
holds each:

```
<root>/
├── Results/              ← required
├── recommendations/      ← created on demand
├── notes/                ← must be a SIBLING of recommendations/
├── source_run_param.csv  ← optional
└── astrometry_mojave.dat ← optional
```

Serve two of them:

```bash
mojave-review --project mojave=~/data/MOJAVE_1996.00-2026.00 \
              --project bk224=~/data/BK224
```

Each is mounted at its own path (`/mojave/`, `/bk224/`), `/` redirects to the
first, and the header grows a project switcher. The name becomes the URL
segment and is derived from the directory basename when you omit it
(`MOJAVE_1996.00-2026.00` → `mojave`). If the project directories happen to be
siblings, `--projects-dir ~/data` finds them all.

Passing **no** `--project` leaves everything exactly as it was: one project at
the root, no switcher.

Per project: `Results/`, `recommendations/`, `notes/`, the two data tables.
Shared: the FITS cache (keyed by MOJAVE data identity, so project-independent)
and — on a token-auth deployment — `tokens.yaml`, so one token per reviewer
covers every project.

Rationale and the deployment shape are in
[`docs/multi_project.md`](docs/multi_project.md).

### What the web app does

- Browse sources, switch between `current` and any `backup_NNN`.
- View summary plots (Position / Flux / Polarization / Kinematics) and
  the per-epoch FITS overlay side-by-side.
- Switch the overlay's contour background between the per-epoch image,
  the real CLEAN **FITS** image, or a **Stacked image** (all epochs
  averaged and convolved with the median beam) via the header checkboxes.
  Hide non-robust clusters from the summary plots with the checkbox beside
  "Summary plots".
- Click summary-plot points (or box/lasso-select) to mark them for
  edits, then turn the selection into structured recommendations
  (`change_clusterID`, `set_use_in_fit`) from the Edits tab.
- Mark cluster robustness in the Robustness tab.
- Leave comments per source / cluster / epoch.
- Tick **Visualize recommendations** to see the summary + overlay
  re-rendered with your in-progress edits applied (no on-disk side
  effects).
- **Submit Recommendation** freezes your current draft into
  `<recs>/<source>/submitted/<your-slug>.json` and pops a modal with
  a copy-pasteable notebook block.
- **Reset Recommendation** (beside Submit) opens a dialog to either reset
  your draft back to your last submitted version, or delete your draft and
  submission for this source. Only touches your own files.
- Other reviewers' submissions on a shared `recommendations/` folder
  appear in the model dropdown as `Rec: <slug>` for read-only
  side-by-side review.
- With `--admin` and local `cluster_fits/` files: the **Window-N review**
  panel replaces `find_clusters.py --editN` — scrub the per-window cluster
  fits across candidate N values (with the BIC* curve and the current
  model for reference), record per-window N choices (autosaved to
  `<recs>/<source>/nwin_edits/nwin_choices.json`), and generate the
  `find_clusters.py ... --N_win_file` rerun command.

---

## Applying a recommendation

`mojave-apply` does the on-disk work. It mirrors the save behavior of
`find_clusters.py --show_results` but is driven by a JSON file instead
of interactive matplotlib events.

```bash
mojave-apply \
    --results-dir <path-to-Results> \
    --source <source-folder-name> \
    --recommendation <path-to-recommendation.json>
```

You can copy that command directly out of the web app: launch with
`--admin`, click **Generate Apply Command** in the header, and copy.

### What it does (in order)

1. Loads the existing `current.csv` and `.plotdata.npz`.
2. Warns if the JSON's `model_sha` doesn't match the current CSV
   (recommendation was made against a now-superseded model).
3. Asks for confirmation (skipped with `--no-confirm`).
4. Renames the old CSV into `backups/backup_NNN_*` and copies the
   PDF / MP4 / `config_win.json` / `run_string.txt` alongside.
5. Writes the modified CSV with `change_clusterID` /
   `set_use_in_fit` / robust changes applied. **The `.plotdata.npz`
   is never rewritten** — the npz is interpreted alongside the csv via
   the immutable `origID` column, so edits to the three editable
   columns propagate automatically.
6. Appends one line per edit to `history.txt`, plus a timestamped
   header.
7. Regenerates the summary PDF + epoch MP4 via the production
   `save_summary_plots`. The FITS/CC root passed to it is taken from the
   **`MOJAVE_DATA`** environment variable when set (the FITS tree on the
   machine running `mojave-apply`), falling back to the `root_data_dir`
   baked into the `.plotdata.npz` otherwise. Set `export MOJAVE_DATA=...`
   if the npz was generated on a different machine, so the regenerated
   plots can find the images.
8. Archives the JSON to `<recs>/<source>/applied/<date>__<slug>.json`, and
   **removes the now-applied `current/<slug>.json` draft** (so the reviewer no
   longer sees an applied recommendation as a pending draft).
9. Prints a copy-pasteable notebook summary block.

**No-op fast path.** If the recommendation makes no on-disk change (no
`clusterID` / `robust` / `use_in_fit` difference — comments don't alter the
CSV), steps 4–5 and 7 are **skipped**: no backup, no CSV rewrite, no plot
regeneration (and `find_clusters` isn't even imported). It still writes a
`history.txt` line (`Concluded recommendation … — no changes`) and archives the
JSON (step 8). This lets you *always* conclude a Step 2 review with
`mojave-apply` — even a "looks good, no changes" one — cheaply and without
junk backups.

**Cleaning up stale drafts (backlog).** Recommendations applied before the
draft-removal in step 8 left their `current/` draft behind. `mojave-review-prune-drafts`
removes `current/<slug>.json` drafts whose content **equals** an already-applied
recommendation for the same source (a draft edited after the apply differs and
is kept). Dry-run by default:

```bash
mojave-review-prune-drafts --recommendations-dir <recs>          # preview
mojave-review-prune-drafts --recommendations-dir <recs> --apply  # delete
```

### Flags

| Flag | Default | What it does |
|---|---|---|
| `--results-dir` | required | Same `Results/` directory the web app reads. |
| `--source` | required | Source folder name, e.g. `0003-066u_1994.00-2026.00`. |
| `--recommendation` | required | Path to the JSON. |
| `--recommendations-dir` | `<results-dir>/../recommendations` | Where to archive the JSON after success. |
| `--production-code-dir` | `<results-dir>/../` | Where `find_clusters.py` lives so its `save_summary_plots` can be imported. |
| `--no-confirm` | off | Skip the confirmation prompt. |

`mojave-apply --help` lists all options.

---

## Where files live

```
<your-work-dir>/
├── Results/                            ← clustering output (read by mojave-review)
│   └── 0003-066u_1994.00-2026.00/
│       ├── *.merged_win_results.csv
│       ├── *.merged_win_results.plotdata.npz
│       ├── *.summary_plots.pdf
│       ├── *.epoch_overplots.mp4
│       └── backups/
├── recommendations/                    ← all reviewer output
│   └── 0003-066u/
│       ├── current/
│       │   └── <reviewer-slug>.json    ← in-progress, autosaved
│       ├── submitted/
│       │   └── <reviewer-slug>.json    ← finalized via Submit
│       └── applied/
│           └── <YYYY-MM-DD>__<slug>.json
└── ~/.mojave_review/cache/             ← MOJAVE FITS image cache (machine-local)
```

Drive-mirrored workflow: keep `Results/` and `recommendations/` inside
the same shared folder so multiple reviewers see each other's submitted
files appear in the `Rec: <slug>` dropdown.

---

## Sharing with other reviewers

The simplest setup:

1. **You** share the `Results/` *and* `recommendations/` folders via
   Google Drive (Drive for Desktop syncs them as local mirrors).
2. **Reviewers** install the package locally (`pip install -e .` against
   the same repo or just `pip install` from a packaged wheel), then
   point `mojave-review` at their local Drive mirror:

   ```bash
   mojave-review --results-dir "~/Library/CloudStorage/GoogleDrive-.../MOJAVE/Results"
   ```

3. They review, click Submit; the submission JSON syncs back through
   Drive and shows up for everyone else.
4. **You** run `mojave-apply` (or use the admin button) to apply the
   chosen recommendation. The CLI archives the JSON automatically, so
   the next time someone refreshes the dropdown they see only
   in-flight submissions.

---

## Troubleshooting

- **`results-dir not found`** — typo in `--results-dir`, or you're not
  in the directory you think you are. Look at the printed path.
- **Web app starts but the model dropdown is empty** — the source
  folders aren't following the `<source>_<emin>-<emax>` naming pattern.
  Check `Results/` against any sample source.
- **FITS images don't load in the overlay** — the app fetches from
  `http://www.cv.nrao.edu/2cmVLBA/data/...` on demand. Verify outbound
  HTTP is allowed. First view of an epoch may take a couple of seconds.
- **`mojave-apply: could not import save_summary_plots`** — pass
  `--production-code-dir` pointing at the directory containing
  `find_clusters.py` + `cluster_code.py`.

---

## Deeper documentation

[`CLAUDE.md`](CLAUDE.md) is the architecture reference: file layout,
data schemas, plot conventions, schema for the recommendation JSON,
gotchas. Worth a read before making non-trivial changes.

Under [`docs/`](docs/):

| | |
|---|---|
| [`deployment_phase2.md`](docs/deployment_phase2.md) | Serving it to a group: host setup, reverse proxy, per-user tokens. Includes what a real proxy turned out to be doing, which is not what the plan assumed. |
| [`syncing_results.md`](docs/syncing_results.md) | Getting `Results/` onto the server safely, per project. **Read before setting up a sync** — it mirrors with `--delete`. |
| [`multi_project.md`](docs/multi_project.md) | Why several projects are laid out and mounted the way they are. |
| [`review_workflow.md`](docs/review_workflow.md) | The three-stage review process the app implements. |
| [`plot_conventions.md`](docs/plot_conventions.md) | Astronomical plotting conventions — apply to any new plot. |
| [`uncertainty_estimates.md`](docs/uncertainty_estimates.md) | How the 1σ position error bars are derived. |
| [`astrometric_reference.md`](docs/astrometric_reference.md) | The absolute-astrometry origin option. |
| [`product_decisions.md`](docs/product_decisions.md) | Locked decisions and the phase plan. |

Deployment templates — systemd units, config and sync examples — are in
[`deploy/`](deploy/), each annotated inline.

The original matplotlib review flow that this tool replaces is
`find_clusters.py --show_results`, with the underlying functions in
`cluster_code.py`.
