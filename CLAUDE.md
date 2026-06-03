# MOJAVE Cluster Review

This repo holds two related but distinct things:

1. **The original clustering pipeline** — `cluster_code.py` and `find_clusters.py`.
   These produce per-source clustering results saved under `Results/`.
2. **`mojave_review/`** — a pip-installable Dash web app that lets reviewers
   interactively inspect those saved results and leave structured
   *recommendations* (never edits to the on-disk results).

The original pipeline is read-mostly for the web app: the web app loads its
data products but does not run new clustering models.

## Repository layout

```
Clustering_Review/
├── cluster_code.py              # ~6000 LOC of pipeline + interactive matplotlib review
├── find_clusters.py             # CLI driver that produces Results/<source>_<emin>-<emax>/
├── Results/                     # one folder per source (mirror of Drive `Results/`)
│   └── <source>_<emin>-<emax>/
│       ├── <prefix>.merged_win_results.csv         <-- main per-(epoch,clusterID) table
│       ├── <prefix>.merged_win_results.plotdata.npz<-- epoch_info + raw clean components
│       ├── <prefix>.summary_plots.pdf              <-- reference render (read-only)
│       ├── <prefix>.epoch_overplots.mp4            <-- reference render (read-only)
│       ├── backups/backup_NNN_*.{csv,pdf,mp4,json,txt}  <-- alternate models
│       ├── cluster_fits/                                 <-- per-window cluster fits
│       ├── config_win.json
│       ├── history.txt
│       └── run_string.txt
└── mojave_review/               # the web app (pip-installable)
    ├── pyproject.toml
    └── src/mojave_review/
        ├── cli.py                       # `mojave-review` entry point
        ├── app.py                       # Dash factory (points assets_folder at the pkg)
        ├── data/
        │   ├── loader.py                # source/model discovery, CSV+NPZ load
        │   └── fits_cache.py            # MOJAVE URL + on-disk FITS cache
        ├── plots/
        │   ├── summary.py               # Plotly port of make_summary_plots
        │   ├── overlay.py               # FITS + cluster + beam overlay per epoch
        │   └── _extent.py               # initial zoom-box from cluster footprint
        ├── recommendations/
        │   ├── schema.py                # dataclasses for the JSON shape
        │   ├── store.py                 # read/write/list reviewer JSON files
        │   └── apply.py                 # apply a Recommendation to a cluster_df
        ├── assets/                      # Dash auto-loads .js/.css from here
        │   ├── resizable.js             # draggable splitter between panels
        │   └── resizable.css
        └── ui/
            ├── layout.py                # header + body + recommendations panel
            ├── callbacks.py             # source/model/view + selection + summary/overlay
            ├── recommendations_panel.py # the 4-tab bottom panel
            └── recommendations_callbacks.py
```

`<prefix>` = `<source>.<emin>-<emax>` (e.g. `0003-066u.1994.00-2026.00`).
Source folder name = `<source>_<emin>-<emax>` (underscore, not dot).

## Data schemas (worth memorizing)

### `merged_win_results.csv` (one row per epoch × clusterID)

Key columns: `source, band, ep_name, epoch (decimal yr), clusterID,
N_Icc, N_QUcc, avg_x, avg_y, dist, pa, core_x, core_y, fwhm_maj, fwhm_min,
iflux, qflux, uflux, pflux, evpa, select, robust, use_in_fit,
ClusterType, Nclusters, Nepochs, ep_min, ep_max, ref_epoch, origID,
medianFlux, centX, centY, slopeX, slopeY, accelX, accelY, sizeMaj, sizeMin,
sizePA, bmaj, bmin, bpa, inoise, pnoise`.

- `clusterID == -1` → unassigned clean components (still appears as one row per
  epoch for bookkeeping; has avg_x/y = NaN).
- `clusterID == 0` → core (typically). Don't plot PA for the core.
- `clusterID >= 1000` → special / synthetic; treat like unassigned (black `+`).
- `robust`, `use_in_fit`, `select` are booleans (Python booleans after read_csv).

### `merged_win_results.plotdata.npz` (numpy pickle, allow_pickle=True)

Four fields:

- `epoch_info` — structured array, one row per epoch:
  `(epoch_name, epoch_val, band, cc_file, fits_file, inoise, pnoise,
    sigma_cut, sigma_cut_area, bmaj, bmin, bpa, pix_to_mas)`
- `cc_data` — structured array, one row per clean component:
  `(epoch, x, y, stokes, flux, sizex, sizey, group, clusterID)`
- `cc_labels` — int32 array of per-cc cluster labels (parallel to `cc_data`).
- `root_data_dir` — string, where FITS files lived on the machine that
  generated the npz. The web app **ignores this** and fetches FITS from MOJAVE
  instead (see below). `mojave-apply` also overrides it: when regenerating the
  summary PDF + epoch MP4 it passes `$MOJAVE_DATA` (if set) to
  `save_summary_plots`, falling back to this baked-in value only when the env
  var is unset (`cli/apply._resolve_root_data_dir`).

## MOJAVE FITS retrieval

Live URL pattern (from `grab_mojave_image` in `cluster_code.py`):

```
http://www.cv.nrao.edu/2cmVLBA/data/<source>/<epoch_name>/<source>.<band>.<epoch_name>.<stokes>cn.fits.gz
```

`<epoch_name>` is the underscored form: `1995_07_28`.
`<stokes>` ∈ {`i`, `q`, `u`}.
`<band>` is the per-source band code (`u` for U-band).

The web app fetches these on demand and caches under
`~/.mojave_review/cache/<source>/<epoch_name>/...` (overridable via `--cache-dir`).

## Web app: views and conventions

`build_summary_figure(view=...)` produces a 2-row figure for the first four
views, and a **single-plot** figure for `XY Position`:

| View | Top | Bottom |
|---|---|---|
| Position | distance vs epoch (+ polyfit overlay), 1σ error bars | PA vs epoch, 1σ error bars |
| Flux | log₁₀(I flux) vs epoch | log₁₀(Tb) vs epoch (15.4 GHz, z param) |
| Polarization | log₁₀(P flux) vs epoch | EVPA vs epoch |
| Kinematics | speed vs distance | X/Y velocity vectors w/ arrowheads, +x reversed |
| XY Position | *(single plot)* per-cluster centroid track in (x,y) mas vs core, +x reversed, equal scale, 1σ x/y error bars | — |

The 1σ error bars on Position / XY Position come from
`plots/uncertainty.attach_position_uncertainties` (CC-derived `sig_dx/sig_dy/
sig_dist/sig_pa` columns); see [`docs/uncertainty_estimates.md`](docs/uncertainty_estimates.md).
The **Visualize recommendations** checkbox defaults ON. (The old "Show 3σ
outlines" checkbox was removed; the 3σ-drawing code remains in
`overlay.build_overlay_figure` behind `show_3sigma=False`, just no UI toggle.)

### Plotting conventions (always apply to spatial / sky plots)

- **+x to the left** (astronomical convention). Use explicit `range=[hi, lo]`
  to reverse the x-axis rather than `autorange="reversed"` so it composes
  cleanly with `scaleanchor`.
- **Equal mas/pixel** on x[mas] vs y[mas] plots. Initial range from
  `plots/_extent.compute_source_extent` on both axes, PLUS
  `scaleanchor="x"` + `scaleratio=1.0` on the y-axis AND
  `constrain="domain"` on **both** axes. That keeps ellipses (FWHM, 3σ,
  beam) round at every zoom level.
  Reviewer-confirmed trade-off (2026-05-28): the Plotly drag-zoom tool
  is also locked to the panel's current aspect when `scaleanchor` is
  on — there's no native way to keep equal scale AND get a free-form
  drag rectangle, and the reviewer explicitly preferred locked-aspect
  drag over breaking equal scale. Don't try to "fix" this by dropping
  `scaleanchor`; you'd revisit a decision that's already been made.
  (`constrain="range"`, the default, would also force the **post-drag
  range** to expand to match aspect — different mechanism, same lock
  feel; `constrain="domain"` is what we want.)
- **Arrows use `fig.add_annotation(showarrow=True, arrowhead=2, ...)`** —
  never line-mode hacks with a triangle marker at the end.
- Always show a black `×` at `(0,0)` on spatial plots (the core).
- Include `(0,0)` in the auto-range computation.

### Overlay panel (FITS + cluster overlay per epoch)

`overlay_figure_for_epoch(bundle, epoch_int, cache_dir, source_no_band, band)`
returns `(figure, beam_params)`. Layered traces in z-order (bottom to top):

1. Contour of the FITS Stokes-I image. Levels are at `cbase × 2ⁿ` with
   `cbase = 3.5 × inoise` from `epoch_info`. The CLEAN image is already
   convolved with the restoring beam — do NOT apply additional smoothing.
   `line.smoothing=1.0` (Bezier) is the most we add.
2. Clean components, scattered, colored by their cluster (uses
   `cc_labels` mapped through `origID → clusterID`).
3. Per-cluster 3σ inclusion ellipse: `2.548 × FWHM`, dotted outline,
   `rgba(<cluster>, 0.04)` fill — drawn first so the FWHM layers on top.
   Gated by `show_3sigma` (default False, no UI toggle since the checkbox
   was removed).
4. Per-cluster FWHM ellipse: solid outline, `rgba(<cluster>, 0.15)` fill.
5. Black-text cluster numbers at each cluster center (skipped for the
   core).
6. Black `×` at the core (0, 0).
7. Beam ellipse in the lower-left of the initial zoom — handled by a
   **clientside callback** so it tracks the viewport on every zoom/pan
   without a server round-trip. See "Don't / gotchas".

#### Contour image source (header checkboxes)

The contour background (layer 1) has three modes, in precedence order:

- **Stacked image** (checkbox) — overrides everything. The epoch-averaged
  image: every epoch's Stokes-I clean components are shifted to that epoch's
  fitted core, accumulated on one common grid (median `pix_to_mas`), divided
  by the epoch count, and convolved **once** with the **median beam**
  (median `bmaj`/`bmin`/`bpa` across epochs). Built by
  `synthesize_fits.synthesize_stacked_stokes_i` and cached per
  `(source, model, csv_sha)` via `overlay._stacked_axes_for_bundle` since it
  is epoch-independent. The drawn beam + `beam_params` use the median beam;
  `cbase` uses the median `inoise`. The per-epoch cluster overlay (layers
  2–6) still tracks the epoch slider, so you can scrub epochs against the
  stable averaged background. (Experimental.)
- **Use FITS images** (checkbox) — fetch the real CLEAN FITS for the epoch.
- default — synthesize the single epoch from its clean components + own beam
  (`synthesize_stokes_i`). Both synth paths share `_render_image` /
  `_beam_kernel`.

### Cluster styling (ported from cluster_code.py)

```python
cl_colors  = ["b", "g", "r", "m", "y", "gray"]    # cycle by clusterID
cl_markers = ["x","o","s","o","s","p","*","^","v",
              "*","^","v","X","D","P","D","1"]
cl_fill    = ["none","full","none","none","full","none","full","none","full",
              "none","full","none","full","none","full","full","none"]
```

- Non-robust clusters → all slategray (`#708090`), but keep marker/fill from
  the rotation. (Original matplotlib used cyan; on a white background it
  washed out, so the web app moved to slategray for legibility.) They DO
  appear in the legend (alongside robust clusters) so they can be toggled /
  isolated by legend click. The **Hide non-robust clusters** checkbox beside
  the "Summary plots" header (wired to `build_summary_figure(hide_non_robust=)`)
  drops them from both the plots and the legend. The unassigned cluster (-1)
  is treated as non-robust for this checkbox and is hidden too; synthetic
  (`>=1000`) clusters are unaffected. (Experimental — may be revoked.)
- `clusterID == -1` (unassigned) → black `+`, and IS in the legend so it can
  be toggled. `clusterID >= 1000` (synthetic) → black `+`, never in legend.
  `_add_cluster_traces` gates legend membership on `cid == -1 or 0 <= cid < 1000`.
- `use_in_fit == False` → black slash overlay on the marker.
- `select == True` → gold open-diamond overlay.

### Numerical quirks ported from `make_summary_plots`

- **PA de-wrap**: if `|pa[i] - pa[i-1]| > 300°`, nudge by ±360°.
- **PA shift** (`shift_pa` flag): if median |pa| > 120° across non-core
  clusters, add 360 to any PA < -60. Keeps southern jets readable.
- **EVPA de-wrap**: same idea with period 180°, jump 150°.
- **Size floor**: `size = max(sqrt(fwhm_maj * fwhm_min), 0.1)` mas.
- **Tb formula**: `1.22e12 * flux * (1+z) / (15.4² * size²)` K (U-band fixed).
- **Position polyfit** is only drawn if ≥5 valid `use_in_fit` points AND
  (motion is >3σ OR speed < 0.05 with combined error < 0.05 mas/yr).

## Recommendations

Reviewer feedback lives in per-(source, model, reviewer) JSON files at
`<recommendations-dir>/<source>/<model>/<reviewer-slug>.json`. The app
NEVER modifies anything under `Results/`. The on-disk shape (full schema
in [`recommendations/schema.py`](mojave_review/src/mojave_review/recommendations/schema.py)):

```jsonc
{
  "source": "0003-066u", "model": "current",
  "model_sha": "<sha256 of the CSV reviewer saw>",
  "reviewer": "Reviewer Name", "updated_at": "2026-05-28T14:09:32+00:00",
  "source_comment": "...",
  // "I sign off on the model's robust flags as-is": even if the table has
  // stale entries, no derived set_robust edits are emitted while this is true.
  "no_robustness_changes": false,
  "cluster_feedback": {
    "3": {"recommended_robust": false, "comment": "merges with 4"}
  },
  "epoch_feedback": {"2003.10": {"comment": "ragged structure"}},
  "edits": [
    {"op": "change_clusterID", "scope": "single"|"all_epochs", ...},
    {"op": "set_use_in_fit",    "scope": "single"|"epoch",       ...}
  ]
}
```

- `recommended_robust`: `true`=Robust, `false`=Non-robust, `null`=no opinion.
  When ≠ the model's current robust flag for that cluster, a synthetic
  `set_robust` edit is *derived* at render/apply time — it is NOT written
  into the JSON's `edits[]` array (your post-processor should derive it).
- `use_in_fit` scopes: `single` (one point) + `epoch` (whole epoch). No
  "cluster-in-epoch" scope.
- `change_clusterID` scopes: `single` (one row) + `all_epochs` (renumber a
  cluster everywhere, multiple cids in a single batch = merge).

### Panel layout (4 tabs)

`Robustness` (default) — `ID / use-in-fit Edits` — `Source Notes` — `Epoch Notes`.

| Tab | What it shows |
|---|---|
| Robustness | "No changes suggested" checkbox at top + editable table of *eligible* clusters (≥ 5 epochs with `use_in_fit=True`). Columns: Eligible Clusters, Current Robust Status, Recommended Changes (—/Robust/Non-robust), Comment. Cluster 0 (the core) has its dropdown locked to "—" (the core is always robust); its Comment cell stays editable. |
| ID / use-in-fit Edits | Selection-driven action panel (visible only when summary-plot points are selected) + pending-edits list (manual + derived `set_robust` together). When no selection: instruction to click a summary point. Manual edits get a `[remove]` button; derived ones are read-only and tagged "from Clusters tab". |
| Source Notes | One textarea, `source_comment`. |
| Epoch Notes | One editable row per epoch, comment cell. |

The panel auto-saves on every field change. Read-only modes:

- model `current`: editable, autosaves.
- model `backup_NNN`: locked, empty (recommendations only valid against current).
- model `Rec: <slug>`: locked, displays *that* reviewer's recommendations from
  `<recs>/<source>/submitted/<slug>.json` so anyone can view another reviewer's
  submitted feedback. (Only submitted recs surface here; in-progress drafts
  under `<source>/current/` stay private until the reviewer clicks Submit.)

Visual lock = `opacity: 0.7; pointer-events: none` on the whole
`#recommendations-panel`.

### Reset Recommendation dialog

A **Reset Recommendation** button sits beside **Submit** (current model only;
hidden otherwise, managed by `_submit_button_state`). It opens a 3-choice
modal (`#reset-rec-modal`, a custom modal since `dcc.ConfirmDialog` only
offers OK+Cancel):

- **Reset to last submitted** — loads `<recs>/<source>/submitted/<own-slug>.json`,
  rewrites it as the working draft (`current/`), and repopulates the panel.
  Disabled (with a note) when no submission exists.
- **Delete draft & submitted** — `store.delete_recommendation` (the `current/`
  draft) + `store.delete_submission` (the `submitted/` file). The panel is
  blanked; autosave does NOT recreate the draft because the empty rec is never
  written (`Recommendation.is_empty()`), and autosave never writes `submitted/`.
- **Cancel** — closes the modal.

Both actions bump `rec-reset-counter`, an Input on `_submit_button_state`, so
the Submit label re-evaluates (Resubmit ⇄ Submit) without a source/model
change. Reset only ever touches the current reviewer's own files.

### Selection-driven edits

The summary plots carry `customdata=[clusterID, epoch]` on every cluster
point. **`_customdata` must return plain Python lists, NOT a numpy array** —
plotly.py 6 base64-encodes numpy arrays as typed arrays by default, so each
point's `customdata` reaches the browser as a `Float64Array` and Dash relays
it into `clickData` as an *object* (`{"0": cid, "1": epoch}`) instead of a
list; the click callback's `cd[0]`/`cd[1]` indexing then silently fails and
clicking stops selecting. The cluster scatter traces are also SVG `go.Scatter`
(not `Scattergl`) so click hit-testing is reliable and doesn't fight the SVG
gold-halo overlay. Two callbacks write to `dcc.Store(id="selection-store")`:

- **clickData** → toggle that one (cid, epoch) in the store. *The callback
  also resets `clickData` to None* so a repeat click on the same point
  fires (Dash only fires on Input *value change*; identical clickData would
  silently no-op).
- **selectedData** (box/lasso) → replace the store contents.

Selection is only meaningful on Position / Flux / Polarization views; the
event handlers no-op on Kinematics. The store clears automatically on
source or model change.

Highlight: the `cluster_df["select"]` column is set per row from the store
before passing to `build_summary_figure`; the existing gold open-circle
overlay in `_add_cluster_traces` renders the halo. **Open symbols
(`*-open`) use `marker.color` as the OUTLINE, not `marker.line.color` —
setting `marker.color="rgba(0,0,0,0)"` makes the entire halo invisible.**
Use a real color (`"gold"`) and SVG `go.Scatter` (not `Scattergl`) for
the overlay traces.

Action buttons on the Edits tab transform a selection into edits:

| Button | Generated edits |
|---|---|
| Mark use_in_fit=False on selected points | N × `set_use_in_fit / single` |
| Mark whole epoch use_in_fit=False | K × `set_use_in_fit / epoch` (K = unique epochs) |
| Renumber selected points to ID X | N × `change_clusterID / single` |
| Renumber all epochs of selected clusters to ID X | M × `change_clusterID / all_epochs` (M = unique cids; multiple cids ⇒ merge) |

Each click attaches the optional Comment field to every generated edit;
the comment input is reset after apply, the selection persists.

### Visualize-recommendations + multi-reviewer

A header checkbox "Visualize recommendations" controls whether
[`recommendations/apply.py`](mojave_review/src/mojave_review/recommendations/apply.py)
mutates the `cluster_df` before the summary + overlay are built. State
table:

| Model | Checkbox state | Plots show |
|---|---|---|
| `current` | enabled, off (default) | raw current data |
| `current` | enabled, on | current + user's in-progress UI-state recs applied |
| `backup_NNN` | disabled, off | raw backup data |
| `Rec: <slug>` | disabled, on (forced) | current + that reviewer's JSON recs applied |

The model dropdown is populated by `_populate_models`: `current` + any
`backup_*` + a `Rec: <slug>` entry per other-reviewer *submitted* JSON file at
`<recs-dir>/<source>/submitted/*.json` (excluding the current user's slug).
In-progress drafts under `<source>/current/` are never surfaced.

## Running the web app

```bash
cd mojave_review && pip install -e .          # one-time
mojave-review --results-dir ../Results        # opens http://127.0.0.1:8050
```

Useful flags: `--reviewer "Name"`, `--port 8050`, `--no-browser`,
`--cache-dir`, `--recommendations-dir`.

For Drive-mirrored data, reviewers should use Google Drive for Desktop and
point `--results-dir` at the local mirror path — no in-app Drive auth needed.

## Phase plan

1. **Phase 1** — pip-installable local app. ✓ Read-only viewer, ✓ FITS
   overlay, ✓ recommendations capture (selection-driven edits +
   Robustness/Edits/Notes tabs + multi-reviewer view). Keyboard shortcuts
   for the matplotlib-key parity (`n/b/i/a/u/r`) are the remaining
   optional polish item.
2. **Phase 2** — host on the user's university web server with **per-user
   static tokens** (small trusted group, ~6 reviewers). Same codebase;
   deploy differences are reviewer identity (from a `tokens.yaml`
   config keyed by the cookie / `?token=…` URL param) and per-user
   recommendations dirs. Full plan in
   [`docs/deployment_phase2.md`](docs/deployment_phase2.md). Google
   OAuth was evaluated and rejected as overkill at this group size.

### Phase 2 hosting requirements (for the IT conversation)

- Python ≥ 3.10 in user space (`pyenv`/`uv` OK).
- Long-lived process bound to a local port (`gunicorn` + systemd).
- Reverse-proxy line under existing nginx/Apache: `https://<host>/mojave-review/` → `http://127.0.0.1:<port>/`.
- Writable persistent dir (~50 GB) for FITS cache + recommendations.
  Nightly backup of just `recommendations/` desirable.
- Outbound HTTPS allowed to `www.cv.nrao.edu` (FITS only — **no
  Google endpoints needed**).
- No OAuth callback URL needed; no database; no special packages.

## Don't / gotchas

- **Don't write back into `Results/`.** Recommendations are the only output.
- **`grab_mojave_image` in `cluster_code.py` opens local files**, not URLs.
  The web app has its own fetcher; don't try to reuse that function.
- **`.plotdata.npz` uses `allow_pickle=True`**. The CSV is the safer
  authoritative source for the cluster-level table; the npz is required
  only for FITS overlay (epoch_info + cc_data + cc_labels).
- **A backup CSV (`backup_NNN_*.csv`) has no matching npz.** Loader returns
  `plotdata=None` in that case — overlay panel must handle it gracefully.
- **`autorange="reversed"` + explicit `range=[hi, lo]`** fight. Pick one;
  the codebase uses explicit reversed ranges so `scaleanchor` composes.
- **Don't smooth the FITS image before contouring.** CLEAN images are
  already convolved with the restoring beam; additional smoothing blurs
  real structure. Use `line.smoothing` on the contour trace if you need
  to soften jaggy contour lines.
- **Clientside callbacks that "patch" a figure must call `Plotly.restyle`
  directly and return `no_update`**, not return a modified figure. The
  beam-positioning callback (`assets/`/inline in `ui/callbacks.py`) does
  this. Returning a modified figure breaks `uirevision`-driven zoom
  persistence across server callbacks — Dash treats it as a fresh
  figure replacement and resets the user's zoom. See the comment block
  near the clientside callback for the full story.
- **Dash's `assets_folder` defaults to `./assets`** relative to CWD, not
  the package install dir. `app.py` explicitly sets
  `assets_folder=<package-dir>/assets` so `pip install` ships the JS/CSS.
- **`*-open` Plotly markers use `marker.color` as the outline color.**
  Setting `marker.color="rgba(0,0,0,0)"` (transparent) erases the entire
  outline — the marker becomes invisible. Use a real color string. This
  bit the selection-halo overlay (see [`plots/summary.py`](mojave_review/src/mojave_review/plots/summary.py)
  `_add_cluster_traces`). Also: use SVG `go.Scatter` for thin-outline
  overlays — `Scattergl` is unreliable for open symbol outlines.
- **`clickData` doesn't change between identical clicks.** Dash callbacks
  fire on Input value-change, so re-clicking the same point silently
  no-ops. The click-toggle callback in [`ui/callbacks.py`](mojave_review/src/mojave_review/ui/callbacks.py)
  outputs `clickData=None` at the end of every invocation so the next
  click is seen as `None → {...}`. Guard against the resulting re-fire
  with `if not click_data: return no_update`.
- **Dash callbacks with `allow_duplicate=True` get hashed output keys.**
  curl-based smoke tests need the hash suffix (look it up via
  `app.callback_map`); browser dispatch handles routing automatically.
- **Recommendations against non-`current` models are not permitted.**
  Backup CSVs and other-reviewer JSON views are read-only. The
  recommendation panel applies an opacity/pointer-events lock and the
  autosave skips when `model_key != "current"`.

## Known issues

### RESOLVED: epochs "vanish" from the overlay timeline (keyboard arrows)

**Symptom (reviewer-reported, 2026-06):** epochs intermittently appeared to
go missing from the right-hand overlay timeline — stepping seemed to skip
over them. Sometimes they came back on their own, sometimes **Reset view**
fixed it. The decisive detail: **arrow keys sometimes advanced TWO epochs
per press**, dropping back to one per press after clicking a ◀/▶ button or
Reset view; and the dates under the timeline were sometimes wrong.

**Root cause:** `assets/keyboard.js` maps ◀/▶ arrows to clicking
`#epoch-prev` / `#epoch-next`, but it originally listened on `document` in
the **bubble** phase. The epoch control is a `dcc.Slider` whose focusable
handle *also* handles arrow keys natively. So when the slider handle had
focus, one keypress fired BOTH the slider's native step AND the
button-click handler → **+2 epochs**. After clicking a button / Reset view,
focus left the handle, so only `keyboard.js` fired → +1 ("self-heal").
Compounding it, the slider uses `updatemode="mouseup"`, so the native
keyboard move didn't reliably push its value to Dash — desyncing the handle
from the displayed epoch/date ("wrong dates").

**Fix:** `keyboard.js` now listens in the **capture phase** and calls
`stopImmediatePropagation()` before clicking the button, so the arrow never
reaches the slider's native handler. Every arrow press routes through the
single `#epoch-prev`/`#epoch-next` → `_step_epoch` path = exactly one step,
handle stays in sync with the Dash value. Text-field arrows are still
ignored (the editable-target guard returns before the stop). Reset view
remains as a manual backup.

**Earlier dead end (do not repeat):** this was first mis-read as a
contour/trace-render problem, and overlay traces were given stable `uid`s.
That backfired — a `uid` on the contour + plotly 6's base64-encoded `z`
made `Plotly.react` skip the contour redraw, freezing the image. That
commit was reverted. **Do not add `uid`s to the contour trace.**

## Useful local commands

```bash
# Smoke test the loader + figure
python3 -c "
from pathlib import Path
from mojave_review.data.loader import list_sources, load_bundle
from mojave_review.plots.summary import build_summary_figure
srcs = list_sources(Path('Results'))
b = load_bundle(str(srcs[0].folder), 'current')
fig = build_summary_figure(b.cluster_df, view='Position')
print(len(fig.data), 'traces')
"

# Hit a Dash callback without a browser
curl -fsS -X POST http://127.0.0.1:8050/_dash-update-component \
  -H 'Content-Type: application/json' \
  -d '{"output":"summary-graph.figure",
       "outputs":{"id":"summary-graph","property":"figure"},
       "inputs":[
         {"id":"source-picker","property":"value","value":"<absolute folder path>"},
         {"id":"model-picker","property":"value","value":"current"},
         {"id":"view-picker","property":"value","value":"Position"},
         {"id":"vector-scale","property":"value","value":1.0}
       ],
       "changedPropIds":["view-picker.value"]}'
```
