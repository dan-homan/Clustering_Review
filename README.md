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

- Python ≥ 3.10
- A read-only copy of `Results/` (typically mirrored from Google Drive
  via Drive for Desktop).
- For `mojave-apply` only: a writable copy of `Results/` plus
  `find_clusters.py` and `cluster_code.py` discoverable from the same
  parent directory (one symbol — `save_summary_plots` — is imported to
  regenerate the PDF + MP4).

### Install the package

```bash
git clone <this-repo>
cd <this-repo>/mojave_review
pip install -e .
```

That gives you `mojave-review` and `mojave-apply` on your PATH. Both are
verifiable with `--help`.

For a non-development install just drop the `-e`:

```bash
pip install ./mojave_review
```

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
| `--port` | `8050` | HTTP port. |
| `--no-browser` | off | Don't auto-open a browser tab. |
| `--admin` | off | Show the **Generate Apply Command** button + (future) aggregation dialog. Only enable on the machine where you'll be running `mojave-apply`. |

If you want stable paths regardless of where you launch from, pass
absolute paths for `--results-dir` and `--recommendations-dir`.

### What the web app does

- Browse sources, switch between `current` and any `backup_NNN`.
- View summary plots (Position / Flux / Polarization / Kinematics) and
  the per-epoch FITS overlay side-by-side.
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
- Other reviewers' submissions on a shared `recommendations/` folder
  appear in the model dropdown as `Rec: <slug>` for read-only
  side-by-side review.

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
   `save_summary_plots`.
8. Archives the JSON to `<recs>/<source>/applied/<date>__<slug>.json`.
9. Prints a copy-pasteable notebook summary block.

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

The original matplotlib review flow that this tool replaces is
`find_clusters.py --show_results`, with the underlying functions in
`cluster_code.py`.
